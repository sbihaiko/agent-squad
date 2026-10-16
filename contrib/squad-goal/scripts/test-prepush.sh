#!/bin/bash
# Regression test for prepush.sh, the run's own pre-push check (#361: the pull-request path is gone, so the only mode is
# `--local <body-file>`: the run, in its worktree, runs it before every push). Throwaway repo, a bare local remote, a temp
# state dir. usage: bash test-prepush.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; mkdir -p "$T/s" "$T/st"
cp "$d/prepush.sh" "$T/s/"
printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=main SG_CHECKOUT=%s\n' "$T/st" "$T/co" > "$T/s/env.sh"
$G init -q --bare "$T/remote.git"; $G init -q -b main "$T/co"; cd "$T/co"; c() { $G -c user.email=a@b -c user.name=t commit -q "$@"; }
mkdir -p UI scripts; echo x > UI/a.cs; echo y > scripts/b.py; $G add .; c -m init; $G remote add origin "$T/remote.git"; $G push -q -u origin main 2>/dev/null
$G checkout -q -b fix/1-x; echo z > UI/c.cs; $G add .; c -m fix; $G push -q origin fix/1-x 2>/dev/null; SHA=$($G rev-parse HEAD)
# b <body>   writes the body file and prints its path: prepush.sh reads the body the run would put in the push
b() { printf '%s\n' "$1" > "$T/body"; printf '%s' "$T/body"; }
pp() { bash "$T/s/prepush.sh" --local "$@" 2>&1; }
r=$(pp "$(b 'Refs #1200')") && ok "a clean head and body: no output, exit 0" || ko "a clean body was flagged: $r"
r=$(pp "$(b 'Closes #1200')"); case "$r" in *"refs: closing keyword"*) ok "Closes #N flagged";; *) ko "Closes not flagged: '$r'";; esac
r=$(pp "$(b 'Adds `UI/c.cs` and tests in UI/missing_tests.cs')"); case "$r" in *"cited: the body cites UI/missing_tests.cs"*) case "$r" in *UI/c.cs*) ko "present file flagged";; *) ok "cited file missing from the head flagged, present one not";; esac;; *) ko "missing cited file not flagged: '$r'";; esac
r=$(pp "$(b 'runner squad/gui_test_supervise.py and probe tmp/x/run.json; the fork-side scripts/zz.py')") && ok "paths outside the repo's dirs and fork lines skipped" || ko "false positive: $r"
c --allow-empty -m local; r=$(pp "$(b 'Refs #1200')"); case "$r" in *"unpushed: 1 commit"*) ok "an unpushed commit on HEAD is flagged";; *) ko "unpushed not flagged: '$r'";; esac
$G reset -q --hard "$SHA"; r=$(pp "$(b 'Refs #1200')") && ok "a pushed head is clean again" || ko "after the reset: '$r'"
# a body file the run never wrote: the reading is empty, and the head check still runs
$G checkout -q main 2>/dev/null || true
r=$(pp /dev/null) && ok "an empty body file is no failure of its own" || ko "empty body: '$r'"
rm -rf "$T"; exit $fail
