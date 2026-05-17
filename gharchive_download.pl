#!/usr/bin/env perl
# gharchive_download.pl – Download GHArchive hourly .json.gz files to current directory.
# Skips files already on disk. Stops after 3 consecutive failures.
#
# Usage:
#   perl gharchive_download.pl DATE HOUR          # single hour
#   perl gharchive_download.pl START_DATE END_DATE # full date range

use strict;
use warnings;
use HTTP::Tiny;
use Time::Local qw(timegm);

my $BASE = 'https://data.gharchive.org';
my $http = HTTP::Tiny->new(timeout => 60);

@ARGV == 2 or die "Usage: $0 DATE HOUR | $0 START_DATE END_DATE\n";

my @work;
if ($ARGV[1] =~ /^\d{4}-\d{2}-\d{2}$/) {
    my $cur = _to_epoch($ARGV[0]);
    my $end = _to_epoch($ARGV[1]) + 23 * 3600;
    while ($cur <= $end) {
        my @t = gmtime($cur);
        push @work, [sprintf('%04d-%02d-%02d', $t[5]+1900, $t[4]+1, $t[3]), $t[2]];
        $cur += 3600;
    }
} else {
    push @work, [$ARGV[0], $ARGV[1] + 0];
}

for my $item (@work) {
    my ($date, $hour) = @$item;
    my $file = "${date}-${hour}.json.gz";
    if (-f $file) { print STDERR "Skip $file\n"; next }

    my $url = "$BASE/$file";
    my $tmp  = "$file.tmp";
    my $ok   = 0;

    for my $attempt (1 .. 3) {
        print STDERR "GET $url" . ($attempt > 1 ? " (attempt $attempt)" : "") . "\n";
        my $r = $http->get($url);

        if ($r->{success}) {
            open my $fh, '>', $tmp or die "Cannot write $tmp: $!";
            binmode $fh;
            print $fh $r->{content};
            close $fh;
            rename $tmp, $file or die "rename failed: $!";
            print STDERR "Saved $file\n";
            $ok = 1; last;
        }

        my $status = $r->{status};
        if ($status == 404) { warn "404 (no archive): $url\n"; $ok = 1; last }

        warn "Failed $status (attempt $attempt/3)\n";
        last if $attempt == 3;
        sleep($status == 429 ? ($r->{headers}{'retry-after'} // 60) : 2 ** $attempt);
    }

    unless ($ok) { unlink $tmp if -f $tmp; die "Stopped after 3 failures.\n" }
}

sub _to_epoch {
    my ($d) = @_;
    $d =~ /^(\d{4})-(\d{2})-(\d{2})$/ or die "Bad date: $d\n";
    timegm(0, 0, 0, $3, $2-1, $1-1900);
}
