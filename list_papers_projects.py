#!/usr/bin/env python3
"""
List all projects, papers, and mentions from the papers.ecosyste.ms API.
Uses pagination via the Link header (total-pages header).
"""

import sys
import json
import time
import argparse
import urllib.request
import urllib.error

BASE = "https://papers.ecosyste.ms/api/v1"


def get_json(url, retries=3, delay=1.0):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/json",
                "User-Agent": "ecosyste-ms-cli/1.0 (research; +https://ecosyste.ms)",
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                headers = {k.lower(): v for k, v in resp.headers.items()}
                data = json.loads(resp.read())
                return data, headers
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("retry-after", 60))
                print(f"  Rate limited, sleeping {wait}s...", file=sys.stderr)
                time.sleep(wait)
            elif attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
            else:
                raise
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
            else:
                raise
    return None, {}


def paginate(endpoint, per_page=100, params=""):
    """Yield all items from a paginated endpoint."""
    page = 1
    sep = "&" if "?" in endpoint else "?"
    first_url = f"{BASE}{endpoint}{sep}per_page={per_page}{('&' + params) if params else ''}"
    data, headers = get_json(first_url)
    if data is None:
        return
    total_pages = int(headers.get("total-pages", 1))
    yield from data

    for page in range(2, total_pages + 1):
        url = f"{BASE}{endpoint}{sep}per_page={per_page}&page={page}{('&' + params) if params else ''}"
        data, _ = get_json(url)
        if data:
            yield from data


def list_projects(out, per_page=100, with_mentions=False):
    """Write all projects (optionally with their mentions) as NDJSON."""
    print("Fetching projects...", file=sys.stderr)
    count = 0
    for project in paginate("/projects", per_page=per_page):
        if with_mentions:
            eco = project["ecosystem"]
            name = project["name"]
            endpoint = f"/projects/{eco}/{urllib.request.quote(name, safe='')}/mentions"
            mentions = list(paginate(endpoint, per_page=per_page))
            project["_mentions"] = mentions
        out.write(json.dumps(project) + "\n")
        count += 1
        if count % 500 == 0:
            print(f"  {count} projects...", file=sys.stderr)
    print(f"Done: {count} projects.", file=sys.stderr)


def list_papers(out, per_page=100, with_mentions=False):
    """Write all papers (optionally with their mentions) as NDJSON."""
    print("Fetching papers...", file=sys.stderr)
    count = 0
    for paper in paginate("/papers", per_page=per_page):
        if with_mentions and paper.get("mentions_count", 0) > 0:
            doi_enc = urllib.request.quote(paper["doi"], safe="")
            endpoint = f"/papers/{doi_enc}/mentions"
            mentions = list(paginate(endpoint, per_page=per_page))
            paper["_mentions"] = mentions
        out.write(json.dumps(paper) + "\n")
        count += 1
        if count % 1000 == 0:
            print(f"  {count} papers...", file=sys.stderr)
    print(f"Done: {count} papers.", file=sys.stderr)


def list_mentions_for_project(ecosystem, name, out, per_page=100):
    name_enc = urllib.request.quote(name, safe="")
    endpoint = f"/projects/{ecosystem}/{name_enc}/mentions"
    count = 0
    for mention in paginate(endpoint, per_page=per_page):
        out.write(json.dumps(mention) + "\n")
        count += 1
    print(f"Done: {count} mentions for {ecosystem}/{name}.", file=sys.stderr)


def list_mentions_for_paper(doi, out, per_page=100):
    doi_enc = urllib.request.quote(doi, safe="")
    endpoint = f"/papers/{doi_enc}/mentions"
    count = 0
    for mention in paginate(endpoint, per_page=per_page):
        out.write(json.dumps(mention) + "\n")
        count += 1
    print(f"Done: {count} mentions for paper {doi}.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="List projects, papers, and mentions from papers.ecosyste.ms API"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_proj = sub.add_parser("projects", help="List all projects")
    p_proj.add_argument("--with-mentions", action="store_true",
                        help="Embed mentions in each project record")
    p_proj.add_argument("--per-page", type=int, default=100)
    p_proj.add_argument("-o", "--output", default="-")

    p_papers = sub.add_parser("papers", help="List all papers")
    p_papers.add_argument("--with-mentions", action="store_true",
                          help="Embed mentions in each paper record (slow)")
    p_papers.add_argument("--per-page", type=int, default=100)
    p_papers.add_argument("-o", "--output", default="-")

    p_pm = sub.add_parser("project-mentions",
                           help="List mentions for a specific project")
    p_pm.add_argument("ecosystem", help="e.g. pypi")
    p_pm.add_argument("name", help="e.g. numpy")
    p_pm.add_argument("--per-page", type=int, default=100)
    p_pm.add_argument("-o", "--output", default="-")

    p_dm = sub.add_parser("paper-mentions",
                           help="List mentions for a specific paper by DOI")
    p_dm.add_argument("doi", help="e.g. 10.1038/s41586-020-2649-2")
    p_dm.add_argument("--per-page", type=int, default=100)
    p_dm.add_argument("-o", "--output", default="-")

    args = parser.parse_args()

    out = open(args.output, "w") if args.output != "-" else sys.stdout

    try:
        if args.cmd == "projects":
            list_projects(out, per_page=args.per_page,
                          with_mentions=args.with_mentions)
        elif args.cmd == "papers":
            list_papers(out, per_page=args.per_page,
                        with_mentions=args.with_mentions)
        elif args.cmd == "project-mentions":
            list_mentions_for_project(args.ecosystem, args.name, out,
                                      per_page=args.per_page)
        elif args.cmd == "paper-mentions":
            list_mentions_for_paper(args.doi, out, per_page=args.per_page)
    finally:
        if args.output != "-":
            out.close()


if __name__ == "__main__":
    main()
