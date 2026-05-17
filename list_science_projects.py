#!/usr/bin/env python3
"""
List all projects from the science.ecosyste.ms API as NDJSON.

Pagination follows the RFC 5988 Link header until there is no rel="next".
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


BASE_URL = "https://science.ecosyste.ms/api/v1/projects"
USER_AGENT = "ecosyste-ms-cli/1.0 (research; +https://ecosyste.ms)"


def get_json(url, retries=3, delay=1.0):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                headers = {k.lower(): v for k, v in resp.headers.items()}
                data = json.loads(resp.read())
                return data, headers
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = int(exc.headers.get("retry-after", 60))
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


def parse_next_url(link_header):
    if not link_header:
        return None

    for part in link_header.split(","):
        section = part.strip()
        if 'rel="next"' not in section:
            continue
        start = section.find("<")
        end = section.find(">", start + 1)
        if start != -1 and end != -1:
            return section[start + 1 : end]
    return None


def paginate_projects(per_page=100):
    url = f"{BASE_URL}?page=1&per_page={per_page}"
    while url:
        data, headers = get_json(url)
        if not data:
            return
        yield from data
        url = parse_next_url(headers.get("link"))


def list_projects(out, per_page=100):
    count = 0
    print("Fetching science projects...", file=sys.stderr)
    for project in paginate_projects(per_page=per_page):
        out.write(json.dumps(project) + "\n")
        count += 1
        if count % 1000 == 0:
            print(f"  {count} projects...", file=sys.stderr)
    print(f"Done: {count} projects.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="List all projects from science.ecosyste.ms as NDJSON"
    )
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("-o", "--output", default="-")
    args = parser.parse_args()

    out = open(args.output, "w", encoding="utf-8") if args.output != "-" else sys.stdout
    try:
        list_projects(out, per_page=args.per_page)
    finally:
        if args.output != "-":
            out.close()


if __name__ == "__main__":
    main()
