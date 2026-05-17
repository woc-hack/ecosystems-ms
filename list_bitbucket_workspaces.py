#!/usr/bin/env python3
"""
List all Bitbucket workspaces accessible to the authenticated user.

Uses the current supported endpoint:
  GET https://api.bitbucket.org/2.0/user/workspaces

Authentication:
  - preferred: BITBUCKET_TOKEN (Bearer token)
  - fallback:  BITBUCKET_USER + BITBUCKET_KEY (Basic auth)

Output is NDJSON, one workspace access record per line.
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


START_URL = "https://api.bitbucket.org/2.0/user/workspaces"
USER_AGENT = "ecosyste-ms-cli/1.0 (research; +https://ecosyste.ms)"


def load_state(path):
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def save_state(path, state):
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def auth_header():
    token = os.environ.get("BITBUCKET_TOKEN")
    if token:
        return f"Bearer {token}"

    user = os.environ.get("BITBUCKET_USER")
    key = os.environ.get("BITBUCKET_KEY")
    if user and key:
        token = base64.b64encode(f"{user}:{key}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    return None


def get_json(url, retries=3, delay=1.0):
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    auth = auth_header()
    if not auth:
        raise RuntimeError(
            "Missing Bitbucket credentials. Set BITBUCKET_TOKEN or "
            "BITBUCKET_USER and BITBUCKET_KEY."
        )
    headers["Authorization"] = auth

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read()), dict(resp.headers.items())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = int(exc.headers.get("Retry-After", 60))
                print(f"Rate limited, sleeping {wait}s...", file=sys.stderr)
                time.sleep(wait)
            elif attempt < retries - 1 and exc.code >= 500:
                time.sleep(delay * (attempt + 1))
            else:
                message = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Bitbucket API error {exc.code}: {message}") from exc
        except Exception:
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
            else:
                raise
    return None, {}


def build_start_url(per_page, administrator=None):
    params = {"pagelen": per_page}
    if administrator is not None:
        params["administrator"] = "true" if administrator else "false"
    return f"{START_URL}?{urllib.parse.urlencode(params)}"


def paginate(state, start_url, max_pages=None):
    url = state.get("next_url") or start_url
    pages = 0
    total = 0

    while url:
        if max_pages is not None and pages >= max_pages:
            break

        payload, _headers = get_json(url)
        if not payload:
            break

        values = payload.get("values", [])
        next_url = payload.get("next")

        for item in values:
            yield item
            total += 1

        state["next_url"] = next_url
        pages += 1
        url = next_url

        print(
            f"Fetched page {pages}, next_url={'set' if next_url else 'end'}, total_emitted={total}",
            file=sys.stderr,
        )

        if not values:
            break


def main():
    parser = argparse.ArgumentParser(
        description="List Bitbucket workspaces accessible to the authenticated user"
    )
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument(
        "--administrator",
        action="store_true",
        help="Only list workspaces where the authenticated user is an administrator",
    )
    parser.add_argument(
        "--non-administrator",
        action="store_true",
        help="Only list workspaces where the authenticated user is not an administrator",
    )
    parser.add_argument(
        "--state-file",
        default=".bitbucket_workspaces_state.json",
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

    if args.administrator and args.non_administrator:
        raise SystemExit("Choose only one of --administrator or --non-administrator.")

    administrator = None
    if args.administrator:
        administrator = True
    elif args.non_administrator:
        administrator = False

    state_path = Path(args.state_file)
    state = {} if args.reset else load_state(state_path)
    start_url = build_start_url(args.per_page, administrator=administrator)
    out = open(args.output, "w", encoding="utf-8") if args.output != "-" else sys.stdout

    emitted = 0
    try:
        for workspace in paginate(state=state, start_url=start_url, max_pages=args.max_pages):
            out.write(json.dumps(workspace) + "\n")
            emitted += 1
    finally:
        if args.output != "-":
            out.close()
        save_state(state_path, state)

    print(f"Done: {emitted} workspace records emitted.", file=sys.stderr)


if __name__ == "__main__":
    main()
