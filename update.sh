#!/usr/bin/env bash
# update.sh – Refresh all repo sources and extract new/updated repos.
#
# Sources:
#   GitHub   – GHArchive hourly .json.gz files (new-repo CreateEvents)
#   GitLab   – gitlab.com API crawl (cursor-resumed)
#   HuggingFace – librarian-bots Parquet snapshots (models/datasets) + API (spaces)
#
# Outputs:
#   data/github_new_repos.csv   repo_id;repo_name;created_at;actor_login;default_branch;description
#   data/gitlab_repos.csv       same semicolon format
#   data/hf_models.ndjson.gz    HuggingFace models metadata
#   data/hf_datasets.ndjson.gz  HuggingFace datasets metadata
#   data/hf_spaces.ndjson.gz    HuggingFace spaces metadata
#   data/github_heads.csv       gh:repo;sha;ref  (git heads via SSH)
#   data/gitlab_heads.csv       gl:repo;sha;ref
#
# Usage:
#   ./update.sh                        # yesterday → today
#   START=2026-01-01 END=2026-05-17 ./update.sh

set -euo pipefail

START="${START:-$(date -u -d 'yesterday' '+%Y-%m-%d')}"
END="${END:-$(date -u '+%Y-%m-%d')}"

DIR="$(cd "$(dirname "$0")" && pwd)"

# ── 1. GHArchive ─────────────────────────────────────────────────────────────
echo "[gharchive] downloading $START → $END"
perl "$DIR/gharchive_download.pl" "$START" "$END"

mkdir -p data

echo "[gharchive] extracting new repos"
zcat ${START}*.json.gz ${END}*.json.gz 2>/dev/null \
  | sort -u \
  | perl "$DIR/gharchive_extract.pl" \
  > data/github_new_repos.csv
echo "  $(wc -l < data/github_new_repos.csv) new GitHub repos"

# ── 2. GitLab ────────────────────────────────────────────────────────────────
echo "[gitlab] crawling new repos"
python3 "$DIR/crawl_gitlab_repos.py"

echo "[gitlab] converting to CSV"
perl "$DIR/gitlab_to_csv.pl" gitlab_repos.ndjson > data/gitlab_repos.csv
echo "  $(wc -l < data/gitlab_repos.csv) new GitLab repos"

# ── 2b. Other GitLab instances ───────────────────────────────────────────────
for host_slug in "gitlab.gnome.org:gl_gnome" "salsa.debian.org:deb" "git.drupalcode.org:dr"; do
    host="${host_slug%%:*}"; prefix="${host_slug##*:}"
    echo "[$prefix] crawling $host"
    python3 "$DIR/crawl_gitlab_repos.py" --host "$host"
    slug="${host//./_}"
    perl "$DIR/gitlab_to_csv.pl" "${slug}_repos.ndjson" > "data/${prefix}_repos.csv"
    echo "  $(wc -l < "data/${prefix}_repos.csv") repos"
done

# ── 3. HuggingFace ───────────────────────────────────────────────────────────
echo "[huggingface] refreshing snapshots"
python3 "$DIR/hf_refresh.py"
echo "  models:   $(zcat data/hf_models.ndjson.gz   | wc -l)"
echo "  datasets: $(zcat data/hf_datasets.ndjson.gz | wc -l)"
echo "  spaces:   $(zcat data/hf_spaces.ndjson.gz   | wc -l)"

# ── 4. Git heads ─────────────────────────────────────────────────────────────
# Distribute across 3 hosts (set HEADS_HOSTS="host1 host2 host3" or defaults to localhost).
# Each host runs 6 parallel git ls-remote workers; recovers from interruption.
echo "[heads] preparing work list"
bash "$DIR/heads_prepare.sh" 3

HEADS_HOSTS="${HEADS_HOSTS:-localhost localhost localhost}"
read -ra _HOSTS <<< "$HEADS_HOSTS"

echo "[heads] running on hosts: ${_HOSTS[*]}"
for i in 1 2 3; do
    host="${_HOSTS[$i-1]}"
    if [ "$host" = "localhost" ]; then
        bash "$DIR/heads_run.sh" "$i" &
    else
        rsync -a work/host${i}.txt "${host}:work/" 2>/dev/null
        ssh "$host" "mkdir -p data work && bash heads_run.sh $i" &
    fi
done
wait

# Collect remote results
for i in 2 3; do
    host="${_HOSTS[$i-1]}"
    [ "$host" != "localhost" ] && rsync "${host}:data/heads_${i}.csv" data/
done

cat data/heads_*.csv > data/all_heads.csv 2>/dev/null || true
echo "  $(wc -l < data/all_heads.csv) total head refs"

echo "Done."
