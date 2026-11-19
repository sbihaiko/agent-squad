#!/bin/bash
# Rule A spares the loop's own pass (#497): the child review (childcc.py -> childreview.sh -> review.sh -> the model) works
# after the run's verdict, in the child's worktree inside the run folder, so to rule A it looked like a leftover of the
# finished run and was killed on every run. A process whose ancestry carries childcc.py is never a leftover; a stray
# process inside the same folder still is. A finished run leaves active-runs on its first pass (FINISHED-RUN, #2138), and no
# later pass reads it, so rule A kills its leftovers in that same pass, before the line goes (#2406).
# usage: bash test-janitor-own-pass.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/co/runs/run-done/ws/target-route.router.1"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/s/"
f="$T/co/runs/run-done"; wt="$f/ws/target-route.router.1"
printf 'CHILD — GitHub issue #70 (bug; one PR):\n' > "$f/request.txt"
printf '%s\n' '{"event": "run_start", "run_id": "r", "graph_name": "g", "graph_hash": "h", "engine_options": {}, "root": "main", "plan": ["main"], "pos": 1, "ts": 1}' \
  '{"event": "verdict", "verdict": "passed", "exit_code": 0, "reasons": []}' > "$f/record.jsonl"
touch -t 202001010000 "$f/record.jsonl"; echo run-done > "$T/st/active-runs.txt"
# the loop's own pass: a stub childcc.py whose child (a stub childreview.sh) sleeps in the child's worktree
printf '#!/bin/bash\ncd "$1"; sleep 60\n' > "$T/s/childreview.sh"
printf 'import subprocess,sys; subprocess.run(["bash", sys.argv[1], sys.argv[2]])\n' > "$T/s/childcc.py"
python3 "$T/s/childcc.py" "$T/s/childreview.sh" "$wt" & own=$!
# a stray process in the same folder: rule A still kills it
( cd "$wt" && exec sleep 60 ) & stray=$!
sleep 2; cr=$(pgrep -P "$own" | head -1)   # the stub childreview.sh, the pass's child
SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main python3 -I "$T/s/janitor.py" >"$T/out" 2>&1
sleep 1; o=$(cat "$T/st/cycle.log" 2>/dev/null)   # the summary line on stdout names a count; the kills are in cycle.log
echo "$o" | /usr/bin/grep -q "pid=$stray .*leftover of finished run run-done" && ok "A: a stray process inside the finished run is a leftover" || ko "A: the stray process was not killed: $o $(cat "$T/out")"
echo "$o" | /usr/bin/grep -q "pid=$cr .*leftover" && ko "A: the loop's own child review was killed as a leftover: $o" || ok "A: the loop's own child review is never a leftover"
kill -0 "$own" 2>/dev/null && ok "A: the pass is still alive after the janitor" || ko "A: the pass died"
! /usr/bin/grep -qx run-done "$T/st/active-runs.txt" && ok "A: the finished run still leaves active-runs in the same pass" || ko "A: the finished run kept its line: $(cat "$T/st/active-runs.txt")"
for k in $(pgrep -P "$own"); do pkill -P "$k" 2>/dev/null; kill "$k" 2>/dev/null; done; kill "$own" "$stray" 2>/dev/null; wait 2>/dev/null
rm -rf "$T"; exit $fail
