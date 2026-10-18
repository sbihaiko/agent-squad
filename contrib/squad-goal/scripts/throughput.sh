#!/bin/bash
# usage: throughput.sh [--days N]   the loop's throughput over a window, from local records only (#564)
# Read-only: no GitHub call, no REST, no network. The commits come from the local clone's base branch, the runs from its
# runs/ folder, and the child reviews from $SG_STATE/watch.log. It does not show the time from Todo to landed: no local
# record stamps the move of a card to Todo (throughput.py holds the report and its help).
# It reads the environment the loop exports (SG_CHECKOUT, SG_BASE, SG_STATE) and needs no profile: a hand run in a checkout
# reads that checkout and the base dev, and says that it read no watch.log.
exec python3 "$(dirname "$0")/throughput.py" "$@"
