#!/usr/bin/env perl
# crawl_repos.pl – GitHub repo crawler via GHArchive.
# Uses plain files (STATE_DIR/) to track already-imported hours.
#
# Usage:
#   perl crawl_repos.pl github:gharchive YYYY-MM-DD HOUR
#   perl crawl_repos.pl github:gharchive-range START_DATE END_DATE
#
# Optional env vars:
#   STATE_DIR  – directory for state files (default: .crawl_state)

use strict;
use warnings;
use HTTP::Tiny;
use JSON::PP               qw(decode_json);
use IO::Uncompress::Gunzip qw(gunzip $GunzipError);
use Time::Local            qw(timegm);
use File::Path             qw(make_path);

# ── Configuration ─────────────────────────────────────────────────────────────
my $STATE_DIR      = $ENV{STATE_DIR} // '.crawl_state';
my $GHARCHIVE_BASE = 'https://data.gharchive.org';

make_path($STATE_DIR) unless -d $STATE_DIR;

# ── State helpers ─────────────────────────────────────────────────────────────

sub state_members {
    my ($key) = @_;
    my $file = "$STATE_DIR/$key";
    return () unless -f $file;
    open my $fh, '<', $file or return ();
    return map { chomp; $_ } <$fh>;
}

sub state_add_member {
    my ($key, $member) = @_;
    my %existing = map { $_ => 1 } state_members($key);
    return if $existing{$member};
    open my $fh, '>>', "$STATE_DIR/$key" or die "state_add_member '$key': $!";
    print $fh "$member\n";
    close $fh;
}

# ── HTTP ──────────────────────────────────────────────────────────────────────
my $HTTP = HTTP::Tiny->new(timeout => 30);

# ── ISO 8601 date → Unix epoch (UTC midnight) ─────────────────────────────────
sub date_to_epoch {
    my ($date) = @_;
    return undef unless $date =~ /^(\d{4})-(\d{2})-(\d{2})$/;
    return eval { timegm(0, 0, 0, $3, $2 - 1, $1 - 1900) };
}

# ══════════════════════════════════════════════════════════════════════════════
# GitHub – GHArchive
# ══════════════════════════════════════════════════════════════════════════════

# Downloads data.gharchive.org/YYYY-MM-DD-HH.json.gz, filters PushEvent /
# ReleaseEvent, prints repos needing a tag-download or sync.
# Skips hours already recorded in STATE_DIR/gharchive_imported.
sub github_import_gharchive_hour {
    my ($date, $hour) = @_;
    my $record = "${date}-${hour}";

    if (grep { $_ eq $record } state_members('gharchive_imported')) {
        print "Skipping $record – already imported\n";
        return 1;
    }

    my $url = "$GHARCHIVE_BASE/${date}-${hour}.json.gz";
    print "Downloading $url\n";

    my $resp = $HTTP->get($url);
    unless ($resp->{success}) {
        warn "Download failed ($resp->{status}): $url\n";
        return 0;
    }

    my @events = _parse_gharchive_events($resp->{content});
    _process_gharchive_events(\@events);

    state_add_member('gharchive_imported', $record);
    print "Import complete: $record\n";
    return 1;
}

# Import every hour across a range of dates (inclusive).
sub github_import_gharchive_range {
    my ($start_date, $end_date) = @_;
    my $cur = date_to_epoch($start_date) or die "Bad start date: $start_date\n";
    my $end = date_to_epoch($end_date)   or die "Bad end date: $end_date\n";
    $end += 23 * 3600;
    while ($cur <= $end) {
        my @t    = gmtime($cur);
        my $date = sprintf '%04d-%02d-%02d', $t[5]+1900, $t[4]+1, $t[3];
        github_import_gharchive_hour($date, $t[2]);
        $cur += 3600;
    }
}

sub _parse_gharchive_events {
    my ($gz_bytes) = @_;
    my $raw;
    gunzip(\$gz_bytes => \$raw) or die "Gunzip failed: $GunzipError";

    my @events;
    for my $line (split /\n/, $raw) {
        next unless length $line;
        my $ev = eval { decode_json($line) } or next;
        next unless ($ev->{type} // '') eq 'PushEvent';
        push @events, $ev;
    }
    printf "Parsed %d relevant events\n", scalar @events;
    return @events;
}

sub _process_gharchive_events {
    my ($events) = @_;
    my %repos_seen;

    for my $ev (@$events) {
        my $name = $ev->{repo}{name} // next;
        $repos_seen{$name}++;
    }

    printf "Processing %d repos with pushes\n", scalar keys %repos_seen;

    for my $repo (sort keys %repos_seen) {
        print "$repo\n";
    }
}

# ══════════════════════════════════════════════════════════════════════════════
# CLI dispatcher
# ══════════════════════════════════════════════════════════════════════════════

my $cmd = shift @ARGV // 'help';

if ($cmd eq 'github:gharchive') {
    my $date = shift @ARGV or die "Usage: $0 github:gharchive YYYY-MM-DD HOUR\n";
    my $hour = shift @ARGV // 0;
    github_import_gharchive_hour($date, $hour);

} elsif ($cmd eq 'github:gharchive-range') {
    my $start = shift @ARGV or die "Usage: $0 github:gharchive-range START_DATE END_DATE\n";
    my $end   = shift @ARGV or die "Usage: $0 github:gharchive-range START_DATE END_DATE\n";
    github_import_gharchive_range($start, $end);

} else {
    print <<'END';
Usage: perl crawl_repos.pl <command> [args]

  github:gharchive DATE HOUR           Import one GHArchive hour  (e.g. 2024-01-15 0)
  github:gharchive-range START END     Import all hours in a date range  (e.g. 2024-01-01 2024-01-07)

State files written to STATE_DIR (.crawl_state by default):
  gharchive_imported   – one "YYYY-MM-DD-HH" record per imported hour (skipped on re-run)

Env vars:
  STATE_DIR   State file directory (default: .crawl_state)
END
}
