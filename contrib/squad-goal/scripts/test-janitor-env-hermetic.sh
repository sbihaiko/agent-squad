#!/bin/bash
# seconds: 28.13
# The beside-script janitor suites read no caller state (#393). shell-suites.sh clears every inherited SG_* for the gate, so a
# suite that only worked there went red on a hand run: the loop's own shell exports SG_BOARD_VOCAB=shipped, SG_FORK_DIR and the
# rest, the janitor took the shipped path -- which imports board_columns.py, a module a suite's copy does not make -- and the
# fork rules instead of the fixture's, and the case proved something else. This runs each janitor suite under that shell and
# requires it green, which is what the suite must hold on its own.
# usage: bash test-janitor-env-hermetic.sh
here=$(cd "$(dirname "$0")" && pwd); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
F=$(mktemp -d)   # a fork dir the loop's shell names: not a repository, so a leaked one answers nothing and touches nothing
for s in test-janitor-deadline.sh test-janitor-cleanup.sh test-janitor-disp-expiry.sh; do
  o=$(SG_BOARD_VOCAB=shipped SG_FORK_DIR="$F" SG_FORK_BASE=main SG_FORK_BRANCH='gui/%n' SG_LAND_BRANCH=dev \
      SG_TODO=Todo SG_HUB_DIR="$F/hub" SG_LSOF=/usr/sbin/lsof bash "$here/$s" 2>&1); rc=$?
  if [ "$rc" = 0 ]; then ok "$s passes under the shell the loop exports"; else
    ko "$s exited $rc under the shell the loop exports ($(printf '%s' "$o" | /usr/bin/grep -cE '^FAIL') FAIL)"
    printf '%s\n' "$o" | /usr/bin/grep -E '^FAIL' | cut -c1-140 | head -3
  fi
done
rm -rf "$F"; exit $fail
