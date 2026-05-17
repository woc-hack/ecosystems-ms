#!/usr/bin/env python3
"""
Convert HuggingFace hub Parquet snapshots (from librarian-bots) to NDJSON.

Drops the 'card' column (raw model-card text) to keep output lean.
Output fields: modelId/datasetId, author, createdAt, last_modified,
               downloads, likes, tags, pipeline_tag, library_name

Usage:
    python hf_parquet_to_ndjson.py --type models   # hf_models_{0,1,2}.parquet → hf_models.ndjson
    python hf_parquet_to_ndjson.py --type datasets # hf_datasets_{0,1,2}.parquet → hf_datasets.ndjson
    python hf_parquet_to_ndjson.py --type all
    python hf_parquet_to_ndjson.py FILE.parquet [FILE2 ...] --output out.ndjson
"""

import argparse
import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

DROP_COLS = {"card"}


def parquet_to_ndjson(parquet_paths, output_path):
    total = 0
    with open(output_path, "w", encoding="utf-8") as out:
        for path in parquet_paths:
            p = Path(path)
            if not p.exists():
                print(f"Missing: {path}", file=sys.stderr)
                continue
            print(f"Reading {path} …", file=sys.stderr)
            table = pq.read_table(p)
            keep = [c for c in table.schema.names if c not in DROP_COLS]
            table = table.select(keep)
            for batch in table.to_batches(max_chunksize=10_000):
                rows = batch.to_pydict()
                n = len(next(iter(rows.values())))
                for i in range(n):
                    record = {k: v[i] for k, v in rows.items()}
                    # Convert timestamps to ISO strings
                    for k, v in record.items():
                        if hasattr(v, "isoformat"):
                            record[k] = v.isoformat()
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                total += n
            print(f"  {total} records so far", file=sys.stderr)
    print(f"Done: {total} records → {output_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*", help="Explicit parquet files")
    parser.add_argument("--type", choices=["models", "datasets", "all"])
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    if args.files:
        output = args.output or "hf_out.ndjson"
        parquet_to_ndjson(args.files, output)
        return

    types = ["models", "datasets"] if args.type == "all" else [args.type]
    for t in types:
        paths = sorted(Path(".").glob(f"hf_{t}_*.parquet"))
        if not paths:
            print(f"No hf_{t}_*.parquet files found.", file=sys.stderr)
            continue
        output = args.output or f"hf_{t}.ndjson"
        parquet_to_ndjson(paths, output)


if __name__ == "__main__":
    main()
