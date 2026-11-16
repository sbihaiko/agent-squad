#!/bin/bash
# Regression test for wtclean.sh's run-worktree rule (throwaway repos, no network). usage: bash test-wtclean-run-tree.sh [path/to/wtclean.sh]
# A launch makes worktrees/run/<key> on squad/run/<key> (#580). Once every run folder inside it has ended for good and its wave
# landed, the tree and the branch go; a live, paused or resumable run, a held wave, a dirty tree or unlanded commits keep both.
# #676: a kept tree is named once, with its reason. A tool's own scratch (`.codex/`) is no work and keeps nothing. A failed run
# that still has remains ends for good a day after its verdict: the loop never resumes a run. A tree that holds another
# registered worktree is kept: its removal deletes the inner tree with it. An ended run with no open wave leaves active-runs.txt
# an hour after its record last moved, whatever keeps its tree (#708), and a fresh ~/wt-* tree at the base tip is not a landed one.
# A line whose run folder is gone leaves active-runs.txt on the next pass and is named in the log (#1433).
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
# #693: a failed run with remains, an hour past its verdict, whose wave is over (landed or stuck: waved- stays, wave- went) leaves
# active-runs.txt, so rule O reads it no more; one with remains and no wave over keeps its line for a resume.
hour2() { python3 -c 'import os, sys, time; t = time.time() - 7200; os.utime(sys.argv[1], (t, t))' "runs/run-$1/record.jsonl"; }
line lover '{"event": "run_start", "pos": 1}' "$(v failed '["x"]' '["n1"]')"; hour2 lover; : > "$T/st/auto/waved-run-lover"
line lrem '{"event": "run_start", "pos": 1}' "$(v failed '["x"]' '["n1"]')"; hour2 lrem
# #708: an ended run (a verdict after its last start or resume) whose wave is not open (no wave-<run>) and whose record is an
# hour idle leaves active-runs.txt, whatever keeps its tree: a dirty child tree, a child tree with commits on no remote (that one
# is salvaged into refs/salvage/wt/<tree path> and removed, #2020), a needs-human or paused verdict. A run resumed after its
# verdict, or one whose record moved this hour, keeps its line.
line lunp '{"event": "run_start", "pos": 1}' "$(v completed)"; $G worktree add -q -b squad/run-lunp/c runs/run-lunp/ws/c origin/main
$G -C runs/run-lunp/ws/c -c user.email=a@b -c user.name=t commit -q --allow-empty -m unpushed
lunpsha=$($G -C runs/run-lunp/ws/c rev-parse HEAD)   # read before the pass: the salvaged tree is gone after it
line lhuman '{"event": "run_start", "pos": 1}' "$(v needs-human)"
line lpaused '{"event": "run_start", "pos": 1}' "$(v stopped '["paused:owner"]')"
line lresumed '{"event": "run_start", "pos": 1}' "$(v stopped)" '{"event": "resume", "pos": 3}'
line lnew '{"event": "run_start", "pos": 1}' "$(v completed)"; touch "runs/run-lnew/record.jsonl"
# #1433: a line whose run folder is gone names no run at all (ADR-0167: a listed folder that is gone holds no slot), so rule A
# drops it on the next pass and says which. A run that finished inside SG_RUN_LINE_SECONDS keeps its line (lnew above): the
# janitor's per-run rules read it for that window, and a run that just started keeps it whatever its record holds.
line lgone '{"event": "run_start", "pos": 1}' "$(v completed)"; rm -rf runs/run-lgone
mkdir -p runs/run-lnever; echo "work" > runs/run-lnever/request.txt   # a launch with no record yet: its folder stands, so its line stays
echo "$T/co/runs/run-lnever" >> "$T/st/active-runs.txt"
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
case "$lines" in *run-lover*) ko "a run whose wave is over kept its active-runs line: $lines";; *) ok "a failed run whose wave is over leaves active-runs.txt";; esac
for k in going lwave lresumed lnew; do case "$lines" in *"run-$k"*) ok "active-runs keeps $k";; *) ko "active-runs dropped $k";; esac; done
for k in lws lrem lunp lhuman lpaused; do case "$lines" in *"run-$k"*) ko "an ended run with no open wave kept its line ($k): $lines";; *) ok "an ended run leaves active-runs.txt whatever keeps its tree ($k)";; esac; done
# #1433: a line whose run folder is gone is dropped on the next pass and named in the log, whatever its verdict; a finished run
# inside the lease window keeps its line, and a run that never got a record keeps its own too (the janitor's launch window).
case "$lines" in *run-lgone*) ko "#1433: a line whose run folder is gone kept its active-runs line: $lines";; *) ok "#1433: a line whose run folder is gone leaves active-runs.txt";; esac
case "$out" in *"RUN GONE run-lgone"*) ok "#1433: the pass names the run whose folder is gone";; *) ko "#1433: the dropped line is silent: $out";; esac
case "$lines" in *run-lnever*) ok "#1433: a launch whose folder stands and has no record yet keeps its line";; *) ko "#1433: a launch with no record lost its line: $lines";; esac
case "$lines" in *run-lnew*) ok "#1433: a run that finished inside the lease window keeps its line";; *) ko "#1433: a fresh finished run lost its line: $lines";; esac
[ -f runs/run-lws/ws/c/f ] && ok "the dropped run with uncommitted work keeps its tree" || ko "a dropped run lost its dirty tree: $out"
[ ! -d runs/run-lunp/ws/c ] && [ "$($G rev-parse -q --verify refs/salvage/wt/runs_run-lunp_ws_c 2>/dev/null)" = "$lunpsha" ] && ok "an unpushed child tree of an ended run is salvaged into a ref and removed" || ko "run-lunp's tree was kept or lost: $out"
[ -d "$T/hm/wt-fresh" ] && ok "a fresh ~/wt-* tree at the base tip: kept" || ko "wt-fresh removed: $out"
rm -rf "$T"; exit $fail
