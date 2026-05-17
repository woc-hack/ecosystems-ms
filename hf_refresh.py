#!/usr/bin/env python3
"""
Refresh HuggingFace hub snapshots in the current directory.

Models + datasets: downloaded as Parquet from librarian-bots (updated daily),
                   converted to NDJSON (card text dropped).
Spaces:            crawled via HF API (no bulk snapshot available).

Usage:  python hf_refresh.py [--delay SECS]   # default delay: 0.3s between API pages
        HF_TOKEN=hf_xxx python hf_refresh.py  # optional token for higher rate limits
"""

import gzip, json, os, re, sys, time, urllib.error, urllib.request
from pathlib import Path

PARQUET_BASE = "https://huggingface.co/api"
USER_AGENT   = "ecosyste-ms-cli/1.0 (research; +https://ecosyste.ms)"
TOKEN        = os.environ.get("HF_TOKEN")

PARQUET_SOURCES = {
    "models":   "librarian-bots/model_cards_with_metadata",
    "datasets": "librarian-bots/dataset_cards_with_metadata",
}
DROP_COLS = {"card"}


# ── helpers ──────────────────────────────────────────────────────────────────

def hf_get(url, stream=False):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(1, 4):
        try:
            resp = urllib.request.urlopen(req, timeout=60)
            return resp.read(), resp.headers.get("Link", "")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", 60))
                print(f"  rate limited, sleeping {wait}s …", file=sys.stderr)
                time.sleep(wait)
                continue
            raise SystemExit(f"HTTP {e.code}: {url}")
        except Exception as e:
            if attempt == 3: raise SystemExit(f"Error: {e}")
            time.sleep(2 ** attempt)
    raise SystemExit("retry loop exhausted")


def next_link(link_header):
    m = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
    return m.group(1) if m else None


# ── parquet → ndjson ─────────────────────────────────────────────────────────

def fetch_parquet_urls(dataset_id):
    url = f"https://datasets-server.huggingface.co/parquet?dataset={urllib.parse.quote(dataset_id, safe='')}"
    data, _ = hf_get(url)
    return [f["url"] for f in json.loads(data)["parquet_files"]]


def download_parquet(url, path):
    print(f"  downloading {Path(path).name} …", file=sys.stderr)
    data, _ = hf_get(url)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def parquet_to_ndjson(parquet_paths, out_path):
    import pyarrow.parquet as pq
    total = 0
    with gzip.open(out_path, "wt", encoding="utf-8") as out:
        for path in parquet_paths:
            table = pq.read_table(path)
            keep  = [c for c in table.schema.names if c not in DROP_COLS]
            for batch in table.select(keep).to_batches(max_chunksize=10_000):
                rows = batch.to_pydict()
                n    = len(next(iter(rows.values())))
                for i in range(n):
                    rec = {k: v[i] for k, v in rows.items()}
                    for k, v in rec.items():
                        if hasattr(v, "isoformat"):
                            rec[k] = v.isoformat()
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                total += n
    print(f"  {total} records → {out_path}", file=sys.stderr)
    return total


def refresh_parquet(kind, dataset_id):
    print(f"[{kind}] fetching parquet URLs …", file=sys.stderr)
    urls = fetch_parquet_urls(dataset_id)
    paths = []
    for i, url in enumerate(urls):
        path = f"hf_{kind}_{i}.parquet"
        download_parquet(url, path)
        paths.append(path)
    parquet_to_ndjson(paths, f"data/hf_{kind}.ndjson.gz")


# ── spaces API crawl ──────────────────────────────────────────────────────────

def refresh_spaces(delay):
    import urllib.parse
    url   = f"https://huggingface.co/api/spaces?limit=1000&sort=lastModified&direction=-1"
    out   = "data/hf_spaces.ndjson.gz"
    total = 0
    print(f"[spaces] crawling API …", file=sys.stderr)
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        while url:
            data, link_hdr = hf_get(url)
            items  = json.loads(data)
            if not items:
                break
            public = [r for r in items if not r.get("private", False)]
            for r in public:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            total += len(public)
            print(f"  {total} spaces …", file=sys.stderr, end="\r")
            url = next_link(link_hdr)
            if url:
                time.sleep(delay)
    print(f"\n[spaces] {total} records → {out}", file=sys.stderr)


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, urllib.parse
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=0.3)
    args = ap.parse_args()

    for kind, dataset_id in PARQUET_SOURCES.items():
        refresh_parquet(kind, dataset_id)

    refresh_spaces(args.delay)
