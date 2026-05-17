#!/usr/bin/env perl
# gitlab_to_csv.pl – Convert crawl_gitlab_repos.py NDJSON output to the same
# semicolon-separated format as gharchive_extract.pl.
#
# Output fields (semicolon-separated, one repo per line):
#   repo_id;repo_name;created_at;actor_login;default_branch;description
#
# Embedded semicolons in fields → __SEMICOLON__; embedded newlines → space.
# Only public repos are emitted (visibility == "public").
#
# Usage:
#   perl gitlab_to_csv.pl [FILE ...]          # default: gitlab_repos.ndjson
#   perl gitlab_to_csv.pl --dir DIR           # all *.ndjson files in DIR
#   cat gitlab_repos.ndjson | perl gitlab_to_csv.pl -

use strict;
use warnings;
use open ':std', ':encoding(UTF-8)';
use JSON::PP                qw(decode_json);
use IO::Uncompress::Gunzip  qw(gunzip $GunzipError);

# ── Argument parsing ──────────────────────────────────────────────────────────
my @files;
my @args;
while (@ARGV) {
    my $a = shift @ARGV;
    if ($a eq '--dir') {
        my $dir = shift @ARGV // die "--dir requires a value\n";
        push @files, sort glob("$dir/*.ndjson");
    } else {
        push @args, $a;
    }
}

if (@args == 1 && $args[0] eq '-') {
    push @files, '-';              # read from stdin
} elsif (@args) {
    push @files, @args;
} elsif (!@files) {
    push @files, -f 'gitlab_repos.ndjson.gz' ? 'gitlab_repos.ndjson.gz' : 'gitlab_repos.ndjson';
}

# ── Process ───────────────────────────────────────────────────────────────────
my ($total, $skipped) = (0, 0);

for my $file (@files) {
    my $fh;
    if ($file eq '-') {
        $fh = \*STDIN;
    } else {
        if ($file =~ /\.gz$/) {
            $fh = IO::Uncompress::Gunzip->new($file)
                or do { warn "Cannot gunzip $file: $GunzipError\n"; next };
        } else {
            open $fh, '<', $file or do { warn "Cannot open $file: $!\n"; next };
        }
    }
    print STDERR "Processing $file\n" unless $file eq '-';

    while (my $line = <$fh>) {
        chomp $line;
        next unless length $line;

        my $repo = eval { decode_json($line) };
        if ($@) { warn "Skipping malformed JSON at $file line $.: $@\n"; next }

        # Only public repos
        if (($repo->{visibility} // '') ne 'public') {
            $skipped++;
            next;
        }

        my @fields = (
            $repo->{id}                      // '',
            $repo->{path_with_namespace}     // '',
            $repo->{created_at}              // '',
            $repo->{namespace}{path}         // '',
            $repo->{default_branch}          // '',
            $repo->{description}             // '',
        );

        for my $f (@fields) {
            $f =~ s/[\r\n]+/ /g;
            $f =~ s/^\s+|\s+$//g;
            $f =~ s/;/__SEMICOLON__/g;
        }

        print join(';', @fields), "\n";
        $total++;
    }

    close $fh unless $file eq '-';
}

printf STDERR "Done: %d repos emitted, %d non-public skipped\n", $total, $skipped;
