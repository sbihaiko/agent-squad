#!/bin/bash
# Regression test for wtclean.sh's run-worktree rule (throwaway repos, no network). usage: bash test-wtclean-run-tree.sh [path/to/wtclean.sh]
# A launch makes worktrees/run/<key> on squad/run/<key> (#580). Once every run folder inside it has ended for good and its wave
# landed, the tree and the branch go; a live, paused or resumable run, a held wave, a dirty tree or unlanded commits keep both.
# #676: a kept tree is named once, with its reason. A tool's own scratch (`.codex/`) is no work and keeps nothing. A failed run
# that still has remains ends for good a day after its verdict: the loop never resumes a run. A tree that holds another
# registered worktree is kept: its removal deletes the inner tree with it. An ended run leaves active-runs.txt once no tree
# holds its folder, and a fresh ~/wt-* tree at the base tip is not a landed one.
src=${1:-$(dirname "$0")/wtclean.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/hm"; cp "$src" "$T/s/wtclean.sh"
printf 'export SG_CHECKOUT=%s SG_STATE=%s SG_FORK_DIR= SG_REPO=x/y SG_OWNER=x SG_BASE=main SG_LAND_BRANCH=main\n' "$T/co" "$T/st" > "$T/s/env.sh"
export SG_LSOF=${SG_LSOF:-$(PATH="$PATH:/usr/sbin:/usr/bin" command -v lsof 2>/dev/null)} HOME="$(cd "$T/hm" && pwd -P)"
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
mk codex '{"event": "run_start", "pos": 1}' "$(v completed)"; mkdir worktrees/run/codex/.codex; echo x > worktrees/run/codex/.codex/config.toml
mk stale '{"event": "run_start", "pos": 1}' "$(v failed '["x"]' '["n1"]')"; touch -t 202001010000 worktrees/run/stale/runs/run-stale/record.jsonl
mk nest '{"event": "run_start", "pos": 1}' "$(v completed)"
$G worktree add -q -b squad/run-nest/c worktrees/run/nest/runs/run-nest/ws/c origin/main; echo work > worktrees/run/nest/runs/run-nest/ws/c/f
# active-runs.txt: an ended run in the checkout's own runs/ (no tree holds it), a run with no verdict, a run whose wave has not
# landed, and an ended run whose child tree is still registered.
line() { mkdir -p "runs/run-$1"; printf '%s\n' "${@:2}" > "runs/run-$1/record.jsonl"; touch -t 202001010000 "runs/run-$1/record.jsonl"; echo "$T/co/runs/run-$1" >> "$T/st/active-runs.txt"; }
line ended '{"event": "run_start", "pos": 1}' "$(v completed)"; line going '{"event": "run_start", "pos": 1}'
line lwave '{"event": "run_start", "pos": 1}' "$(v completed)"; : > "$T/st/auto/wave-run-lwave"
line lws '{"event": "run_start", "pos": 1}' "$(v completed)"; $G worktree add -q -b squad/run-lws/c runs/run-lws/ws/c origin/main; echo w > runs/run-lws/ws/c/f
$G worktree add -q -b gui/cn worktrees/child/cn origin/main; mkdir -p worktrees/child/cn/runs/run-cn; echo "$(v completed)" > worktrees/child/cn/runs/run-cn/record.jsonl
$G worktree add -q -b squad/run-cn/c worktrees/child/cn/runs/run-cn/ws/c origin/main; echo work > worktrees/child/cn/runs/run-cn/ws/c/f
$G worktree add -q -b fresh/1 "$T/hm/wt-fresh" origin/main   # made a moment ago at the base tip: a gate may be starting in it
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
gone codex && ok "a tool's scratch .codex/ keeps no landed run" || ko "codex kept: $out"
gone stale && ok "a failed run with remains, a day past its verdict: removed" || ko "stale kept: $out"
kept nest && [ -f worktrees/run/nest/runs/run-nest/ws/c/f ] && ok "a run tree that holds a registered worktree: kept, the inner tree intact" || ko "nest removed: $out"
[ -f worktrees/child/cn/runs/run-cn/ws/c/f ] && case "$out" in *"WT KEPT child cn:"*) ok "a child tree that holds a registered worktree: kept and named";; *) ko "cn not named: $out";; esac || ko "cn removed with its inner tree: $out"
for k in ahead dirty human nest; do case "$out" in *"WT KEPT run $k:"*) ok "the kept tree $k is named";; *) ko "$k kept in silence: $out";; esac; done
o2=$(bash "$T/s/wtclean.sh" 2>&1); case "$o2" in *"WT KEPT run ahead"*) ko "a kept tree is named again: $o2";; *) ok "a kept tree is named once";; esac
lines=$(cat "$T/st/active-runs.txt")
case "$lines" in *run-ended*) ko "an ended run kept its active-runs line: $lines";; *) ok "an ended run leaves active-runs.txt";; esac
for k in going lwave lws; do case "$lines" in *"run-$k"*) ok "active-runs keeps $k";; *) ko "active-runs dropped $k";; esac; done
[ -d "$T/hm/wt-fresh" ] && ok "a fresh ~/wt-* tree at the base tip: kept" || ko "wt-fresh removed: $out"
rm -rf "$T"; exit $fail
