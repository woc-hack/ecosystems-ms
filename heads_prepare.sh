#!/usr/bin/env bash
# heads_prepare.sh – collect all repos with URL prefixes, shuffle, split across N hosts.
# Usage: ./heads_prepare.sh [N]   default N=3
#
# gitconfig needed for SSH short URLs:
#   [url "git@github.com:"]        insteadOf = gh:
#   [url "git@gitlab.com:"]        insteadOf = gl:
#   [url "git@hf.co:"]             insteadOf = hf:
#   [url "git@bitbucket.org:"]     insteadOf = bb:
#   [url "git@gitlab.gnome.org:"]  insteadOf = gl_gnome:
#   [url "git@git.drupalcode.org:"] insteadOf = dr:
#   [url "git@salsa.debian.org:"]  insteadOf = deb:

set -euo pipefail
N=${1:-3}
mkdir -p work

{
    awk -F';' '{print "gh:"$2}' data/github_new_repos.csv 2>/dev/null

    awk -F';' '{print "gl:"$2}' data/gitlab_repos.csv 2>/dev/null

    zcat data/hf_models.ndjson.gz 2>/dev/null \
      | perl -MJSON::PP -ne 'my $r=eval{JSON::PP::decode_json($_)}or next;
            my $id=$r->{modelId}//next; print "hf:$id\n"'

    zcat data/hf_datasets.ndjson.gz 2>/dev/null \
      | perl -MJSON::PP -ne 'my $r=eval{JSON::PP::decode_json($_)}or next;
            my $id=$r->{datasetId}//next; print "hf:datasets/$id\n"'

    zcat data/hf_spaces.ndjson.gz 2>/dev/null \
      | perl -MJSON::PP -ne 'my $r=eval{JSON::PP::decode_json($_)}or next;
            my $id=$r->{id}//next; print "hf:spaces/$id\n"'

    # Additional hosts — CSV format: repo_id;repo_name;...  (repo_name = owner/repo)
    awk -F';' '{print "bb:"$2}'       data/bitbucket_repos.csv  2>/dev/null
    awk -F';' '{print "gl_gnome:"$2}' data/gl_gnome_repos.csv   2>/dev/null
    awk -F';' '{print "dr:"$2}'       data/dr_repos.csv         2>/dev/null
    awk -F';' '{print "deb:"$2}'      data/deb_repos.csv        2>/dev/null

} | shuf | awk -v n="$N" '{print > "work/host" ((NR-1)%n+1) ".txt"}'

echo "Split $(cat work/host*.txt | wc -l) repos across $N hosts:"
wc -l work/host*.txt
