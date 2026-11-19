#!/bin/bash
# seconds: 4.24
# A fix run is started by childfix.sh with its target inside the folder of the run it fixes (#1270). When that run is
# finished, rule A killed every process whose cwd sits in the folder, and the fix run's engine died there while it still
# waited at the run cap -- a state that names no pid, so the reap could not prove it dead and its run slot stayed counted
# for 35 minutes. A run's own session is never a leftover of another run; a stray process in the same folder still is.
# usage: bash test-janitor-fix-run.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/co/runs/run-done/ws/worktrees_child-route.router.1" "$T/bin" "$T/.venv/bin"
# the engine of a fix run, shaped as the squad entry point is: a python3 script that runs as `squad start <run>` (#1270)
printf '#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n' > "$T/.venv/bin/squad"; chmod +x "$T/.venv/bin/squad"
cp "$src" "$T/s/janitor.py"; here=$(dirname "$src")
cp "$here/issue_state.py" "$here/issue_ledger.py" "$here/board_columns.py" "$here/board-definition.json" "$T/s/"
f="$T/co/runs/run-done"; wt="$f/ws/worktrees_child-route.router.1"
printf 'CHILD — GitHub issue #70 (bug; one PR):\n' > "$f/request.txt"
printf '%s\n' '{"event": "run_start", "run_id": "r", "graph_name": "g", "graph_hash": "h", "engine_options": {}, "root": "main", "plan": ["main"], "pos": 1, "ts": 1}' \
  '{"event": "verdict", "verdict": "passed", "exit_code": 0, "reasons": []}' > "$f/record.jsonl"
touch -t 202001010000 "$f/record.jsonl"; echo run-done > "$T/st/active-runs.txt"
# the live fix run's engine: `squad start <run>` (the .venv entry point), working inside the finished run's folder
( cd "$wt" && exec python3 "$T/.venv/bin/squad" start run-fix ) & fix=$!
# a stray process in the same folder: rule A still kills it
( cd "$wt" && exec sleep 60 ) & stray=$!
sleep 2
SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main python3 -I "$T/s/janitor.py" >"$T/out" 2>&1
sleep 1; o=$(cat "$T/st/cycle.log" 2>/dev/null)   # the summary line on stdout names a count; the kills are in cycle.log
echo "$o" | /usr/bin/grep -q "pid=$stray .*leftover of finished run run-done" && ok "A: a stray process inside the finished run is a leftover" || ko "A: the stray process was not killed: $o $(cat "$T/out")"
echo "$o" | /usr/bin/grep -q "pid=$fix .*leftover" && ko "A: the live fix run's session was killed as a leftover: $o" || ok "A: a live run's session is never a leftover of another run"
kill -0 "$fix" 2>/dev/null && ok "A: the fix run's session survives the pass" || ko "A: the fix run's session is dead"
kill "$fix" "$stray" 2>/dev/null; wait 2>/dev/null
rm -rf "$T"; exit $fail
