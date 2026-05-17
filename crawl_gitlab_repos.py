#!/usr/bin/env python3
"""
Crawler for any GitLab-compatible instance (gitlab.com, GNOME, Salsa, Drupal, …).

Fetches public projects sorted by id ascending from --from-id (or saved max_id),
stopping when no more results. State file tracks max_id per run.

Usage:
    python crawl_gitlab_repos.py                                    # gitlab.com
    python crawl_gitlab_repos.py --host gitlab.gnome.org           # GNOME
    python crawl_gitlab_repos.py --host salsa.debian.org           # Salsa
    python crawl_gitlab_repos.py --host git.drupalcode.org         # Drupal
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


USER_AGENT = "ecosyste-ms-cli/1.0 (research; +https://ecosyste.ms)"


def load_state(path):
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def save_state(path, state):
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def get_json(url, retries=3, delay=1.0):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read()), dict(resp.headers.items())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = int(exc.headers.get("Retry-After", 60))
                print(f"Rate limited, sleeping {wait}s...", file=sys.stderr)
                time.sleep(wait)
            elif attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
            else:
                raise
        except Exception:
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
            else:
                raise
    return None, {}


def crawl(state, api_base):
    after_id = state.get("max_id", 0)
    total = 0

    while True:
        params = {"per_page": 100, "order_by": "id", "sort": "asc",
                  "archived": "false", "simple": "true", "id_after": after_id}
        url = f"{api_base}/projects?{urllib.parse.urlencode(params)}"
        repos, _ = get_json(url)
        if not repos:
            break

        for repo in repos:
            yield repo
            total += 1

        after_id = repos[-1]["id"]
        state["max_id"] = after_id
        print(f"  {total} repos, max_id={after_id}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Crawl GitLab-compatible instance as NDJSON")
    parser.add_argument("--host",      default="gitlab.com", help="GitLab hostname")
    parser.add_argument("--from-id",   type=int, help="Override starting id")
    parser.add_argument("--state-file", default=None, help="State file (default: .{host}_state.json)")
    parser.add_argument("-o", "--output", default=None, help="Output file (default: {host_slug}_repos.ndjson)")
    args = parser.parse_args()

    slug       = args.host.replace(".", "_")
    state_path = Path(args.state_file or f".{slug}_state.json")
    output     = args.output or f"{slug}_repos.ndjson"
    api_base   = f"https://{args.host}/api/v4"

    state = load_state(state_path)
    if args.from_id is not None:
        state["max_id"] = args.from_id

    out = sys.stdout if output == "-" else open(output, "w", encoding="utf-8")
    emitted = 0
    try:
        for repo in crawl(state=state, api_base=api_base):
            out.write(json.dumps(repo) + "\n")
            emitted += 1
    finally:
        if output != "-":
            out.close()
        save_state(state_path, state)

    print(f"Done: {emitted} repos emitted → {output}", file=sys.stderr)


if __name__ == "__main__":
    main()
