#!/usr/bin/env python3
"""
Standalone crawler for bitbucket.org repositories.

This mirrors the repos.ecosyste.ms Bitbucket crawl strategy:
  - start from /2.0/repositories?pagelen=100
  - follow the API-provided "next" URL
  - persist that continuation URL
  - resume from it on the next run

Note: Bitbucket has deprecated the global public-repository listing endpoint.
If you provide --workspace, the script will use the workspace-scoped endpoint
instead, which still works.

Output is NDJSON, one repository record per line.
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


START_URL = "https://api.bitbucket.org/2.0/repositories?pagelen=100"
WORKSPACE_URL = "https://api.bitbucket.org/2.0/repositories/{workspace}?pagelen=100"
USER_AGENT = "ecosyste-ms-cli/1.0 (research; +https://ecosyste.ms)"


def load_state(path):
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def save_state(path, state):
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def auth_header():
    user = os.environ.get("BITBUCKET_USER")
    key = os.environ.get("BITBUCKET_KEY")
    if not user or not key:
        return None
    token = base64.b64encode(f"{user}:{key}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def get_json(url, retries=3, delay=1.0):
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    auth = auth_header()
    if auth:
        headers["Authorization"] = auth

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read()), dict(resp.headers.items())
        except urllib.error.HTTPError as exc:
            if exc.code == 410:
                message = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    "Bitbucket's global public repository crawl endpoint is deprecated "
                    f"and now returns HTTP 410 Gone. Response: {message}"
                ) from exc
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


def crawl(state, start_url, max_pages=None):
    pages = 0
    total = 0
    url = state.get("next_url") or start_url

    while url:
        if max_pages is not None and pages >= max_pages:
            break

        payload, _headers = get_json(url)
        if not payload:
            break

        repos = payload.get("values", [])
        next_url = payload.get("next")

        for repo in repos:
            yield repo
            total += 1

        state["next_url"] = next_url
        pages += 1
        url = next_url

        print(
            f"Fetched page {pages}, next_url={'set' if next_url else 'end'}, total_emitted={total}",
            file=sys.stderr,
        )

        if not repos:
            break


def main():
    parser = argparse.ArgumentParser(
        description="Crawl bitbucket.org repositories as NDJSON"
    )
    parser.add_argument(
        "--workspace",
        help="Use the supported workspace-scoped endpoint instead of the deprecated global one",
    )
    parser.add_argument(
        "--state-file",
        default=".bitbucket_crawl_state.json",
        help="JSON file used to persist the next Bitbucket pagination URL",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        help="Stop after this many API pages instead of crawling to exhaustion",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Ignore any saved continuation URL and start from the beginning",
    )
    parser.add_argument("-o", "--output", default="-")
    args = parser.parse_args()

    state_path = Path(args.state_file)
    state = {} if args.reset else load_state(state_path)
    start_url = (
        WORKSPACE_URL.format(workspace=args.workspace) if args.workspace else START_URL
    )
    out = open(args.output, "w", encoding="utf-8") if args.output != "-" else sys.stdout

    emitted = 0
    try:
        for repo in crawl(state=state, start_url=start_url, max_pages=args.max_pages):
            out.write(json.dumps(repo) + "\n")
            emitted += 1
            if emitted % 1000 == 0:
                print(f"  {emitted} repositories emitted...", file=sys.stderr)
    finally:
        if args.output != "-":
            out.close()
        save_state(state_path, state)

    print(f"Done: {emitted} repositories emitted.", file=sys.stderr)


if __name__ == "__main__":
    main()
