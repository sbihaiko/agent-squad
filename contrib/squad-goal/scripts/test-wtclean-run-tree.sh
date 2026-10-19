#!/bin/bash
# Regression test for wtclean.sh's run-worktree rule (throwaway repos, no network). usage: bash test-wtclean-run-tree.sh [path/to/wtclean.sh]
# A launch makes worktrees/run/<key> on squad/run/<key> (#580). Once every run folder inside it has ended for good and its wave
# landed, the tree and the branch go; a live, paused or resumable run, a held wave, a dirty tree or unlanded commits keep both.
src=${1:-$(dirname "$0")/wtclean.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/hm"; cp "$src" "$T/s/wtclean.sh"
printf 'export SG_CHECKOUT=%s SG_STATE=%s SG_FORK_DIR= SG_REPO=x/y SG_OWNER=x SG_BASE=main\n' "$T/co" "$T/st" > "$T/s/env.sh"
export SG_LSOF=${SG_LSOF:-$(PATH="$PATH:/usr/sbin:/usr/bin" command -v lsof 2>/dev/null)} HOME="$T/hm"
G=/usr/bin/git; $G init -q --bare "$T/remote.git"; $G init -q -b main "$T/co"; cd "$T/co"
$G -c user.email=a@b -c user.name=t commit -q --allow-empty -m init; $G remote add origin "$T/remote.git"; $G push -q -u origin main 2>/dev/null
printf 'worktrees/\nruns/\n' >> .git/info/exclude
# mk <key> <record line>...: one run tree with one run folder whose record holds the lines
mk() { local k=$1; shift; $G worktree add -q -b "squad/run/$k" "worktrees/run/$k" origin/main; mkdir -p "worktrees/run/$k/runs/run-$k"
  printf '%s\n' "$@" > "worktrees/run/$k/runs/run-$k/record.jsonl"; }
V='{"event": "verdict", "verdict": "%s", "reasons": %s, "remains": %s}'; v() { printf "$V" "$1" "${2:-[]}" "${3:-[]}"; }
mk done '{"event": "run_start", "pos": 1}' "$(v completed)"
mk nofin '{"event": "run_start", "pos": 1}'
mk wave '{"event": "run_start", "pos": 1}' "$(v completed)"; : > "$T/st/auto/wave-run-wave"
mk human '{"event": "run_start", "pos": 1}' "$(v needs-human)"
mk paused '{"event": "run_start", "pos": 1}' "$(v stopped '["paused:owner"]')"
mk remains '{"event": "run_start", "pos": 1}' "$(v failed '["x"]' '["n1"]')"
mk dead '{"event": "run_start", "pos": 1}' "$(v failed '["x"]')"
mk resumed '{"event": "run_start", "pos": 1}' "$(v stopped)" '{"event": "resume", "pos": 3}'
mk dirty '{"event": "run_start", "pos": 1}' "$(v completed)"; echo x > worktrees/run/dirty/f
mk ahead '{"event": "run_start", "pos": 1}' "$(v completed)"; $G -C worktrees/run/ahead -c user.email=a@b -c user.name=t commit -q --allow-empty -m mine
out=$(bash "$T/s/wtclean.sh" 2>&1)
gone() { [ ! -d "worktrees/run/$1" ] && ! $G rev-parse -q --verify "refs/heads/squad/run/$1" >/dev/null; }
kept() { [ -d "worktrees/run/$1" ] && $G rev-parse -q --verify "refs/heads/squad/run/$1" >/dev/null; }
gone done && ok "a completed, landed run: tree and branch removed" || ko "done kept: $out"
gone dead && ok "a failed run with nothing left to resume: removed" || ko "dead kept: $out"
kept nofin && ok "a run with no verdict: kept" || ko "nofin removed"
kept wave && ok "a run whose wave has not landed: kept" || ko "wave removed"
kept human && ok "a needs-human run: kept" || ko "human removed"
kept paused && ok "a paused run: kept" || ko "paused removed"
kept remains && ok "a failed run with remains: kept" || ko "remains removed"
kept resumed && ok "a run resumed after a verdict: kept" || ko "resumed removed"
kept dirty && ok "a dirty tree: kept" || ko "dirty removed"
kept ahead && ok "a tree with commits outside the base: kept" || ko "ahead removed"
case "$out" in *"WT CLEAN run done"*) ok "the pass names the run it freed";; *) ko "no WT CLEAN line: $out";; esac
rm -rf "$T"; exit $fail
