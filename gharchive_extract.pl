#!/usr/bin/env perl
# gharchive_extract.pl – Extract new-repo creation events from GHArchive JSON lines on stdin.
#
# Filter: CreateEvent where payload.ref == payload.master_branch
#         (default-branch init; GitHub dropped ref_type=="repository" around 2025)
#
# Output: repo_id;repo_name;created_at;actor_login;default_branch;description
#
# Usage:  zcat 2026-05-16-*.json.gz | perl gharchive_extract.pl

use strict;
use warnings;
use open ':std', ':encoding(UTF-8)';
use JSON::PP qw(decode_json);

while (my $line = <STDIN>) {
    chomp $line;
    next unless length $line;
    my $ev = eval { decode_json($line) } or next;
    next unless ($ev->{type} // '') eq 'CreateEvent';
    my $p = $ev->{payload} // {};
    next unless defined $p->{ref} && defined $p->{master_branch};
    next unless $p->{ref} eq $p->{master_branch};

    my @fields = (
        $ev->{repo}{id}       // '',
        $ev->{repo}{name}     // '',
        $ev->{created_at}     // '',
        $ev->{actor}{login}   // '',
        $p->{master_branch}   // '',
        $p->{description}     // '',
    );

    for my $f (@fields) {
        $f =~ s/\n/ /g;
        $f =~ s/^\s+|\s+$//g;
        $f =~ s/;/__SEMICOLON__/g;
    }

    print join(';', @fields), "\n";
}
