#!/usr/bin/env python3
"""
Crawler for public HuggingFace Hub repositories (models, datasets, spaces).

Uses the HuggingFace Hub API with cursor-based pagination (Link header).
Saves the cursor to a state file so runs can be resumed.
Stops after 3 consecutive failed requests.

Output: NDJSON, one record per line.

Usage:
    python crawl_hf_repos.py [--type models|datasets|spaces] [options]
    python crawl_hf_repos.py --type all [options]   # crawl all three types

Options:
    --type      Repo type: models, datasets, spaces, or all (default: models)
    --sort      Sort field: lastModified or createdAt (default: lastModified)
    --limit     Page size, max 1000 (default: 1000)
    --state     State file for cursor persistence (default: .hf_crawl_state.json)
    --output    Output NDJSON file (default: hf_{type}.ndjson)
    --reset     Ignore saved cursor and start from the beginning
    --delay     Seconds between requests (default: 0.5)

Env vars:
    HF_TOKEN    HuggingFace API token (optional, increases rate limits)
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API_BASE   = "https://huggingface.co/api"
USER_AGENT = "ecosyste-ms-cli/1.0 (research; +https://ecosyste.ms)"
MAX_FAILURES = 3


def load_state(path):
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def save_state(path, state):
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def parse_next_link(link_header):
    """Extract the next-page URL from a Link header."""
    if not link_header:
        return None
    m = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
    return m.group(1) if m else None


def get_json(url, token=None, retries=MAX_FAILURES):
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    failures = 0
    while failures < retries:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
                next_url = parse_next_link(resp.headers.get("Link", ""))
                return data, next_url
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = int(exc.headers.get("Retry-After", 60))
                print(f"Rate limited, sleeping {wait}s …", file=sys.stderr)
                time.sleep(wait)
                # don't count 429 as a failure — just wait and retry
                continue
            failures += 1
            print(f"HTTP {exc.code} (failure {failures}/{retries}): {url}", file=sys.stderr)
            if failures >= retries:
                raise SystemExit(f"Stopped after {retries} consecutive failures.")
            time.sleep(2 ** failures)
        except Exception as exc:
            failures += 1
            print(f"Error (failure {failures}/{retries}): {exc}", file=sys.stderr)
            if failures >= retries:
                raise SystemExit(f"Stopped after {retries} consecutive failures.")
            time.sleep(2 ** failures)

    raise SystemExit("Unexpected exit from retry loop.")


def crawl_type(repo_type, args, state, token):
    cursor_key = f"{repo_type}_cursor"
    start_url = (
        f"{API_BASE}/{repo_type}"
        f"?limit={args.limit}&sort={args.sort}&direction=-1"
    )

    url = state.get(cursor_key) or start_url
    if args.reset:
        url = start_url
        state.pop(cursor_key, None)

    output = args.output or f"hf_{repo_type}.ndjson"
    mode   = "a" if (not args.reset and Path(output).exists()) else "w"

    total = 0
    print(f"[{repo_type}] Starting from: {url}", file=sys.stderr)

    with open(output, mode, encoding="utf-8") as fh:
        while url:
            print(f"[{repo_type}] Fetching page …", file=sys.stderr)
            items, next_url = get_json(url, token=token)

            if not items:
                print(f"[{repo_type}] Empty page – done.", file=sys.stderr)
                break

            public = [r for r in items if not r.get("private", False)]
            for repo in public:
                fh.write(json.dumps(repo) + "\n")
            total += len(public)
            print(
                f"[{repo_type}] {len(public)} public / {len(items)} total "
                f"(cumulative: {total})",
                file=sys.stderr,
            )

            state[cursor_key] = next_url or None
            save_state(Path(args.state), state)

            if not next_url:
                break

            url = next_url
            time.sleep(args.delay)

    print(f"[{repo_type}] Done: {total} repos written to {output}", file=sys.stderr)


def main():
    import os

    parser = argparse.ArgumentParser(description="Crawl HuggingFace Hub repos as NDJSON")
    parser.add_argument("--type",   default="models",
                        choices=["models", "datasets", "spaces", "all"])
    parser.add_argument("--sort",   default="lastModified",
                        choices=["lastModified", "createdAt"])
    parser.add_argument("--limit",  type=int, default=1000)
    parser.add_argument("--state",  default=".hf_crawl_state.json")
    parser.add_argument("--output", default=None,
                        help="Output file (default: hf_{type}.ndjson). "
                             "Ignored when --type=all.")
    parser.add_argument("--reset",  action="store_true")
    parser.add_argument("--delay",  type=float, default=0.5)
    args = parser.parse_args()

    token = os.environ.get("HF_TOKEN")
    if token:
        print("Using HF_TOKEN for authentication.", file=sys.stderr)

    state_path = Path(args.state)
    state = {} if args.reset else load_state(state_path)

    types = ["models", "datasets", "spaces"] if args.type == "all" else [args.type]

    for repo_type in types:
        if args.type == "all":
            args.output = f"hf_{repo_type}.ndjson"
        crawl_type(repo_type, args, state, token)


if __name__ == "__main__":
    main()
