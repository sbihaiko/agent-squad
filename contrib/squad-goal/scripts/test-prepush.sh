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
# #664: the loop's profile lives beside the live scripts, while a worktree ships only profiles/example.sh. A session
# that carries SG_PROFILES_DIR runs the worktree's own prepush against the loop's profile; without it the same copy
# exits 2 and prints nothing, which is the failure a fix run reported.
wt="$T/wt"; mkdir -p "$wt/profiles" "$T/live"
cp "$d/env.sh" "$d/address.sh" "$d/prepush.sh" "$wt/"; cp "$d/profiles/example.sh" "$wt/profiles/"
printf 'SG_REPO=x/y\nSG_BASE=main\nSG_CHECKOUT=%s\n' "$T/co" > "$T/live/agent-squad.sh"
ppwt() { ( cd "$T/co" && "$@" bash "$wt/prepush.sh" --local /dev/null 2>&1 ); }
r=$(ppwt env SG_PROFILE=agent-squad SG_PROFILES_DIR="$T/live" SG_STATE="$T/st" SG_SQUAD=/dev/null); rc=$?
[ "$rc" = 0 ] && [ -z "$r" ] && ok "the worktree prepush resolves the loop's profile with SG_PROFILES_DIR set" \
  || ko "with SG_PROFILES_DIR: exit $rc '$r'"
r=$(ppwt env -u SG_PROFILES_DIR SG_PROFILE=agent-squad SG_STATE="$T/st" SG_SQUAD=/dev/null); rc=$?
[ "$rc" = 2 ] && ok "without SG_PROFILES_DIR the worktree prepush cannot resolve the profile (exit 2)" \
  || ko "without SG_PROFILES_DIR: exit $rc '$r'"
# The loop's own env.sh resolves the profiles beside it; it must export that directory, so the session it starts carries
# the path and the worktree's prepush resolves the same profile.
lv="$T/livewt"; mkdir -p "$lv/profiles"
cp "$d/env.sh" "$d/address.sh" "$wt/prepush.sh" "$lv/"
printf 'SG_REPO=x/y\nSG_BASE=main\nSG_CHECKOUT=%s\n' "$T/co" > "$lv/profiles/agent-squad.sh"
r=$(cd "$T/co" && env -u SG_PROFILES_DIR SG_PROFILE=agent-squad SG_STATE="$T/st" SG_SQUAD=/dev/null \
  bash -c '. "$1/env.sh" >/dev/null 2>&1; env' _ "$lv" | sed -n 's/^SG_PROFILES_DIR=//p')
[ "$r" = "$lv/profiles" ] && ok "env.sh exports the resolved SG_PROFILES_DIR to the session it starts" || ko "env.sh did not export SG_PROFILES_DIR: '${r:-unset}'"
r=$(cd "$T/co" && env -u SG_PROFILES_DIR SG_PROFILE=agent-squad SG_STATE="$T/st" SG_SQUAD=/dev/null bash "$lv/prepush.sh" --local /dev/null 2>&1); rc=$?
[ "$rc" = 0 ] && [ -z "$r" ] && ok "a prepush beside the loop's profiles resolves with no SG_PROFILES_DIR in the caller" \
  || ko "resolved-default prepush: exit $rc '$r'"
rm -rf "$T"; exit $fail
