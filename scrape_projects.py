#!/usr/bin/env python3
"""
Scrape all repositories/projects for every host from the ecosyste.ms API.

Reads hosts from a JSONL file produced by scrape_hosts.py (or fetches them
live if no hosts file is given) and pages through each host's /repositories
endpoint, writing results to per-host JSONL files in an output directory.

NOTE on the API (repos.ecosyste.ms):
  • The hosts endpoint uses a fixed page size (~100).  Do NOT send per_page
    for that endpoint — the server rejects it with a 500.
  • Pagination is link-based: each response includes a Link header with
    a "next" URL.  This scraper follows those next-URLs directly rather
    than constructing page-number URLs, which avoids known server issues.
  • Use --repos-per-page with small values (e.g. 10) if the default page
    size causes timeouts.

Usage examples:
    # Recommended two-step approach:
    python scrape_hosts.py -o hosts.jsonl
    python scrape_projects.py --hosts hosts.jsonl --output-dir projects/

    # Scrape a single host for testing:
    python scrape_projects.py --hosts hosts.jsonl --only-hosts codeberg.org

    # Only repos updated since a date (server-side filter):
    python scrape_projects.py --hosts hosts.jsonl --updated-after 2025-01-01T00:00:00Z

    # Active non-fork repos, sorted by most recently updated:
    python scrape_projects.py --hosts hosts.jsonl --updated-after 2024-01-01T00:00:00Z \
        --no-fork --sort updated_at --order desc

    # Slow, polite scraping (avoids rate limiting):
    python scrape_projects.py --hosts hosts.jsonl --delay 2.0

    # Faster with multiple parallel workers:
    python scrape_projects.py --hosts hosts.jsonl --concurrency 4 --delay 0.5

Server-side filters (applied by the API, reduce data volume):
    --updated-after    only repos with updated_at >= this ISO-8601 datetime
    --created-after    only repos with created_at >= this ISO-8601 datetime
    --sort             field to sort by (e.g. updated_at, created_at, pushed_at)
    --order            asc or desc (default: API decides)
    --no-fork          exclude forked repositories
    --archived         include only archived repositories

Speed controls:
    --delay            seconds between requests per worker (default: 1.0)
    --concurrency      parallel host workers (default: 1)
    --repos-per-page   per_page hint for repos endpoint (default: API decides)
    --timeout          HTTP request timeout in seconds (default: 60)
"""

import argparse
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse

import requests

BASE_URL = "https://repos.ecosyste.ms/api/v1"
DEFAULT_OUTPUT_DIR = "projects"
DEFAULT_DELAY = 1.0
DEFAULT_CONCURRENCY = 1
DEFAULT_MAX_RETRIES = 5
DEFAULT_BACKOFF = 2.0
DEFAULT_TIMEOUT = 60

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)
_log_lock = Lock()


def parse_link_header(header: str) -> dict:
    """Parse a Link header into {rel: url}."""
    links = {}
    if not header:
        return links
    for part in header.split(","):
        m = re.match(r'\s*<([^>]+)>;\s*rel="([^"]+)"', part.strip())
        if m:
            links[m.group(2)] = m.group(1)
    return links


def make_session(mailto: Optional[str], user_agent: str) -> requests.Session:
    session = requests.Session()
    ua = f"{user_agent} mailto:{mailto}" if mailto else user_agent
    session.headers.update({"User-Agent": ua})
    if mailto:
        session.headers["From"] = mailto
    return session


def get_page(
    session: requests.Session,
    url: str,
    params: dict,
    timeout: int,
    max_retries: int,
    backoff: float,
    base_delay: float,
) -> Tuple[List, Dict]:
    """
    GET url with params, retry on transient errors.
    Returns (items_list, link_rels_dict).
    """
    wait = base_delay
    for attempt in range(max_retries + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", wait))
                with _log_lock:
                    log.warning("Rate limited – sleeping %.1fs …", retry_after)
                time.sleep(retry_after)
                wait *= backoff
                continue
            resp.raise_for_status()
            links = parse_link_header(resp.headers.get("Link", ""))
            data = resp.json()
            if isinstance(data, dict) and "error" in data:
                with _log_lock:
                    log.warning("API error at %s: %s", url, data["error"])
                return [], {}
            return data if isinstance(data, list) else [], links
        except requests.RequestException as exc:
            if attempt == max_retries:
                raise
            with _log_lock:
                log.warning("Error (%s) – retry %d/%d in %.1fs …",
                            exc, attempt + 1, max_retries, wait)
            time.sleep(wait)
            wait *= backoff
    return [], {}  # unreachable


def host_output_path(output_dir: Path, host_name: str) -> Path:
    safe = re.sub(r'[^\w.\-]', '_', host_name)
    return output_dir / f"{safe}.jsonl"


def scrape_host_repositories(
    host: dict,
    output_dir: Path,
    repos_per_page: Optional[int],
    delay: float,
    mailto: Optional[str],
    user_agent: str,
    max_retries: int,
    backoff: float,
    timeout: int,
    resume: bool,
    repo_filters: dict,
) -> dict:
    """
    Scrape all repositories for one host using link-based pagination.

    Strategy:
    - First request omits the 'page' parameter (avoids a known server bug
      where page=1 triggers 500 errors on some backends).
    - Each subsequent page is fetched using the 'next' URL advertised in the
      Link header — no manual page-number construction.
    - If --resume is set, the first request probes page counts to skip ahead.
    - Server-side filters (updated_after, created_after, sort, order, fork,
      archived) are passed on the first request; they are preserved in the
      Link-header next-URLs returned by the server so no extra handling needed.
    """
    host_name = host["name"]
    repos_url = (
        host.get("repositories_url")
        or f"{BASE_URL}/hosts/{host_name}/repositories"
    )
    out_path = host_output_path(output_dir, host_name)
    session = make_session(mailto, user_agent)

    # Build params for the first request: filters + optional per_page (no page number)
    first_params: dict = {k: v for k, v in repo_filters.items() if v is not None}
    if repos_per_page:
        first_params["per_page"] = repos_per_page
    if mailto:
        first_params["mailto"] = mailto

    # Resume support: skip ahead by fetching the right page number.
    # We follow link headers from page 1 until we reach the right page.
    existing = 0
    skip_pages = 0
    if resume and out_path.exists():
        existing = sum(1 for _ in out_path.open())
        if existing:
            # Probe the first page to learn the server's page size
            probe, probe_links = get_page(
                session, repos_url, first_params,
                timeout, max_retries, backoff, delay,
            )
            if probe:
                page_size = len(probe)
                skip_pages = existing // page_size
                with _log_lock:
                    log.info("[%s] Resume: %d saved, page_size=%d → skip %d pages",
                             host_name, existing, page_size, skip_pages)
                # Fast-forward: follow 'next' links until we reach the right page
                current_links = probe_links
                for _ in range(skip_pages - 1):
                    if "next" not in current_links:
                        break
                    _, current_links = get_page(
                        session, current_links["next"], {},
                        timeout, max_retries, backoff, delay,
                    )
                    time.sleep(delay)
                # The 'next' link now points to our resume page
                if skip_pages > 0 and "next" in current_links:
                    first_params = {}  # will use next_url below
                    repos_url = current_links["next"]
            time.sleep(delay)

    mode = "a" if (resume and out_path.exists() and existing > 0) else "w"
    total_saved = existing
    page_num = skip_pages + 1

    with _log_lock:
        log.info("[%s] Starting (reported repos: %s, from page: %d)",
                 host_name, host.get("repositories_count", "?"), page_num)

    try:
        next_url: Optional[str] = repos_url
        params = first_params
        with out_path.open(mode) as fh:
            while next_url:
                repos, links = get_page(
                    session, next_url, params,
                    timeout, max_retries, backoff, delay,
                )
                # After the first request, always follow Link headers directly
                params = {}

                if not repos:
                    break

                for repo in repos:
                    fh.write(json.dumps(repo) + "\n")
                total_saved += len(repos)

                with _log_lock:
                    log.info("[%s] page %d → %d repos (total: %d)",
                             host_name, page_num, len(repos), total_saved)

                next_url = links.get("next")
                page_num += 1
                if next_url:
                    time.sleep(delay)

    except Exception as exc:
        with _log_lock:
            log.error("[%s] Failed on page %d: %s", host_name, page_num, exc)
        return {"host": host_name, "status": "error",
                "saved": total_saved, "error": str(exc)}

    with _log_lock:
        log.info("[%s] Done – %d repositories → %s", host_name, total_saved, out_path)
    return {"host": host_name, "status": "ok", "saved": total_saved}


def load_hosts_from_file(path: Path) -> List[Dict]:
    hosts = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                hosts.append(json.loads(line))
    return hosts


def fetch_all_hosts(
    mailto: Optional[str],
    user_agent: str,
    delay: float,
    timeout: int,
    max_retries: int,
    backoff: float,
) -> List[Dict]:
    """Fetch all hosts from the API (never sends per_page — server rejects it)."""
    session = make_session(mailto, user_agent)
    hosts = []
    page = 1
    while True:
        params: dict = {"page": page}
        if mailto:
            params["mailto"] = mailto
        log.info("Fetching hosts page %d …", page)
        batch, links = get_page(
            session, f"{BASE_URL}/hosts", params,
            timeout, max_retries, backoff, delay,
        )
        if not batch:
            break
        hosts.extend(batch)
        if "next" not in links:
            break
        page += 1
        time.sleep(delay)
    log.info("Loaded %d hosts.", len(hosts))
    return hosts


def scrape_projects(
    hosts_file: Optional[str],
    output_dir: str,
    repos_per_page: Optional[int],
    delay: float,
    concurrency: int,
    mailto: Optional[str],
    user_agent: str,
    max_retries: int,
    backoff: float,
    timeout: int,
    resume: bool,
    only_hosts: List[str],
    skip_hosts: List[str],
    repo_filters: dict,
) -> None:
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    if hosts_file:
        log.info("Loading hosts from %s …", hosts_file)
        hosts = load_hosts_from_file(Path(hosts_file))
    else:
        log.info("No hosts file – fetching from API …")
        hosts = fetch_all_hosts(mailto, user_agent, delay, timeout, max_retries, backoff)

    if only_hosts:
        only_set = {h.lower() for h in only_hosts}
        hosts = [h for h in hosts if h["name"].lower() in only_set]
        log.info("Filtered to %d host(s)", len(hosts))
    if skip_hosts:
        skip_set = {h.lower() for h in skip_hosts}
        before = len(hosts)
        hosts = [h for h in hosts if h["name"].lower() not in skip_set]
        log.info("Skipped %d host(s); %d remaining", before - len(hosts), len(hosts))

    log.info("Scraping %d host(s)  concurrency=%d  delay=%.2fs  timeout=%ds  filters=%s",
             len(hosts), concurrency, delay, timeout,
             {k: v for k, v in repo_filters.items() if v is not None} or "none")

    results: List[Dict] = []

    if concurrency == 1:
        for host in hosts:
            result = scrape_host_repositories(
                host, out_path, repos_per_page, delay, mailto,
                user_agent, max_retries, backoff, timeout, resume, repo_filters,
            )
            results.append(result)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(
                    scrape_host_repositories,
                    host, out_path, repos_per_page, delay, mailto,
                    user_agent, max_retries, backoff, timeout, resume, repo_filters,
                ): host["name"]
                for host in hosts
            }
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    name = futures[future]
                    log.error("[%s] Unhandled: %s", name, exc)
                    results.append({"host": name, "status": "error", "error": str(exc)})

    ok = [r for r in results if r.get("status") == "ok"]
    errors = [r for r in results if r.get("status") == "error"]
    total = sum(r.get("saved", 0) for r in results)
    log.info("=" * 60)
    log.info("Summary: %d OK  %d errors  %d total repositories saved",
             len(ok), len(errors), total)
    for r in errors:
        log.warning("  FAILED: %s – %s", r["host"], r.get("error", ""))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape all repositories/projects from the ecosyste.ms API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--hosts", default=None,
                        help="Path to hosts JSONL file (from scrape_hosts.py). "
                             "If omitted, hosts are fetched from the API.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help=f"Directory for per-host JSONL output (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--repos-per-page", type=int, default=None,
                        help="per_page hint for the repositories endpoint. "
                             "Omit to use the API default. Use small values "
                             "(e.g. 10) if the API times out on large pages.")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help=f"Seconds between requests per worker (default: {DEFAULT_DELAY}). "
                             "Increase to reduce rate limiting / blocking.")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                        help=f"Parallel host workers (default: {DEFAULT_CONCURRENCY}). "
                             "Use 1 for sequential cautious scraping.")
    parser.add_argument("--mailto", default=None,
                        help="Email for the polite (priority) pool, e.g. you@example.com")
    parser.add_argument("--user-agent", default="ecosyste.ms-scraper/1.0",
                        help="Custom User-Agent string.")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES,
                        help=f"Max retries on transient errors (default: {DEFAULT_MAX_RETRIES})")
    parser.add_argument("--backoff", type=float, default=DEFAULT_BACKOFF,
                        help=f"Exponential backoff multiplier (default: {DEFAULT_BACKOFF})")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"HTTP request timeout seconds (default: {DEFAULT_TIMEOUT})")
    parser.add_argument("--resume", action="store_true",
                        help="Resume a previous run, skipping already-saved pages.")
    parser.add_argument("--only-hosts", nargs="+", metavar="HOST",
                        help="Only scrape these host names (space-separated, case-insensitive).")
    parser.add_argument("--skip-hosts", nargs="+", metavar="HOST",
                        help="Skip these host names (space-separated, case-insensitive).")
    # --- Server-side repository filters ---
    filter_group = parser.add_argument_group(
        "repository filters",
        "Passed directly to the API; reduce the volume of data returned.",
    )
    filter_group.add_argument("--updated-after", default=None, metavar="DATETIME",
                              help="Only repos with updated_at >= this value "
                                   "(ISO-8601, e.g. 2025-01-01T00:00:00Z). "
                                   "Note: updated_at covers metadata changes; "
                                   "pushed_at (last code push) is not filterable server-side.")
    filter_group.add_argument("--created-after", default=None, metavar="DATETIME",
                              help="Only repos with created_at >= this value (ISO-8601).")
    filter_group.add_argument("--sort", default=None,
                              help="Field to sort results by, e.g. updated_at, "
                                   "created_at, pushed_at, stargazers_count.")
    filter_group.add_argument("--order", default=None, choices=["asc", "desc"],
                              help="Sort direction (asc or desc).")
    filter_group.add_argument("--no-fork", action="store_true", default=False,
                              help="Exclude forked repositories.")
    filter_group.add_argument("--archived", action="store_true", default=False,
                              help="Include only archived repositories.")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable debug logging.")

    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    scrape_projects(
        hosts_file=args.hosts,
        output_dir=args.output_dir,
        repos_per_page=args.repos_per_page,
        delay=args.delay,
        concurrency=args.concurrency,
        mailto=args.mailto,
        user_agent=args.user_agent,
        max_retries=args.max_retries,
        backoff=args.backoff,
        timeout=args.timeout,
        resume=args.resume,
        only_hosts=args.only_hosts or [],
        skip_hosts=args.skip_hosts or [],
        repo_filters={
            "updated_after": args.updated_after,
            "created_after": args.created_after,
            "sort":          args.sort,
            "order":         args.order,
            "fork":          False if args.no_fork else None,
            "archived":      True  if args.archived else None,
        },
    )


if __name__ == "__main__":
    main()
