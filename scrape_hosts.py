#!/usr/bin/env python3
"""
Scrape all hosts from the ecosyste.ms Repositories API.

The API uses a fixed page size (100) and does NOT accept a custom per_page
parameter — passing one causes a 500 error.  Pagination is driven entirely
by the Link header returned with each response.

Usage:
    python scrape_hosts.py [options]

Output:
    JSONL file (one JSON object per line) with all host records.

Speed controls:
    --delay        seconds to sleep between requests (default 1.0)
    --max-retries  max retry attempts on transient errors (default 5)
    --backoff      exponential backoff multiplier (default 2.0)
    --timeout      HTTP request timeout in seconds (default 60)
"""

import argparse
import json
import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

BASE_URL = "https://repos.ecosyste.ms/api/v1"
DEFAULT_OUTPUT = "hosts.jsonl"
DEFAULT_DELAY = 1.0
DEFAULT_MAX_RETRIES = 5
DEFAULT_BACKOFF = 2.0
DEFAULT_TIMEOUT = 60

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


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


def fetch_page(
    session: requests.Session,
    url: str,
    params: dict,
    timeout: int,
    max_retries: int,
    backoff: float,
    base_delay: float,
) -> Tuple[List, Dict]:
    """Fetch one API page with retries. Returns (items, link_rels)."""
    wait = base_delay
    for attempt in range(max_retries + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", wait))
                log.warning("Rate limited – sleeping %.1fs …", retry_after)
                time.sleep(retry_after)
                wait *= backoff
                continue
            resp.raise_for_status()
            links = parse_link_header(resp.headers.get("Link", ""))
            data = resp.json()
            if isinstance(data, dict) and "error" in data:
                log.warning("API error at %s: %s", url, data["error"])
                return [], {}
            return data, links
        except requests.RequestException as exc:
            if attempt == max_retries:
                raise
            log.warning("Error (%s) – retry %d/%d in %.1fs …",
                        exc, attempt + 1, max_retries, wait)
            time.sleep(wait)
            wait *= backoff
    return [], {}  # unreachable


def scrape_hosts(
    output: str,
    delay: float,
    mailto: Optional[str],
    user_agent: str,
    max_retries: int,
    backoff: float,
    timeout: int,
    resume: bool,
    start_page: int,
) -> None:
    session = make_session(mailto, user_agent)
    out_path = Path(output)

    page = start_page
    if resume and out_path.exists():
        existing = sum(1 for _ in out_path.open())
        if existing:
            log.info("Resume: %d records exist, probing API page size …", existing)
            _, probe = fetch_page(
                session, f"{BASE_URL}/hosts",
                {"page": 1, **({"mailto": mailto} if mailto else {})},
                timeout, max_retries, backoff, delay,
            )
            m = re.search(r"per_page=(\d+)", probe.get("first", ""))
            page_size = int(m.group(1)) if m else 100
            page = (existing // page_size) + 1
            log.info("API page size: %d → resuming from page %d", page_size, page)
            time.sleep(delay)

    mode = "a" if (resume and out_path.exists()) else "w"
    total_saved = 0

    with out_path.open(mode) as fh:
        while True:
            params: dict = {"page": page}
            if mailto:
                params["mailto"] = mailto

            log.info("Fetching hosts page %d …", page)
            hosts, links = fetch_page(
                session, f"{BASE_URL}/hosts", params,
                timeout, max_retries, backoff, delay,
            )

            if not hosts:
                log.info("Empty page – done.")
                break

            for host in hosts:
                fh.write(json.dumps(host) + "\n")
            total_saved += len(hosts)
            log.info("  → %d hosts (total saved: %d)", len(hosts), total_saved)

            if "next" not in links:
                break

            page += 1
            time.sleep(delay)

    log.info("Done. %d hosts written to %s", total_saved, output)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape all hosts from the ecosyste.ms Repositories API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT,
                        help=f"Output JSONL file (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help=f"Sleep between requests in seconds (default: {DEFAULT_DELAY}). "
                             "Increase to reduce rate limiting.")
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
                        help="Append to existing output file, skipping already-fetched pages.")
    parser.add_argument("--start-page", type=int, default=1,
                        help="Page number to start from (default: 1).")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable debug logging.")

    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    scrape_hosts(
        output=args.output,
        delay=args.delay,
        mailto=args.mailto,
        user_agent=args.user_agent,
        max_retries=args.max_retries,
        backoff=args.backoff,
        timeout=args.timeout,
        resume=args.resume,
        start_page=args.start_page,
    )


if __name__ == "__main__":
    main()
