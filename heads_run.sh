#!/usr/bin/env bash
# heads_run.sh – fetch git heads for one host's work slice, 6 parallel workers.
# Usage: ./heads_run.sh <host_number>
#
# Safe to interrupt and resume: each completed repo is tracked via an atomic
# per-repo file in work/done_N/. Leftover tmp files from crashes are cleaned
# up on the next run.

set -euo pipefail

H=${1:?usage: $0 <host_number>}
WORK="work/host${H}.txt"
DONE="work/done_${H}"
OUT="data/heads_${H}.csv"

[ -f "$WORK" ] || { echo "No work file: $WORK"; exit 1; }
mkdir -p "$DONE"

# Clean up temp files left by any previously interrupted run
find "$DONE" -maxdepth 1 -name 'tmp.*' -delete 2>/dev/null || true

fetch() {
    local r=$1
    local key; key=$(printf '%s' "$r" | md5sum | cut -c1-16)
    local done_file="$DONE/$key"
    [ -f "$done_file" ] && return 0          # already done
    local tmp; tmp=$(mktemp "$DONE/tmp.XXXXXX")
    git ls-remote "$r" 2>/dev/null \
      | grep -E 'HEAD|refs/heads' \
      | perl -ane "s/\s+/;/; print \"$r;\$_\"" > "$tmp"
    mv "$tmp" "$done_file"                   # atomic: marks done
}
export -f fetch
export DONE

xargs -P6 -a "$WORK" -I{} bash -c 'fetch "$@"' _ {}

# Collect: done files are named by 16-char hex key
cat "$DONE"/???????????????? > "$OUT" 2>/dev/null || true
echo "[host $H] $(wc -l < "$OUT") head refs → $OUT"
