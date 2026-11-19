#!/bin/bash
# seconds: 0.35
# A live but later process with a reused call pid is not the stranded run. The janitor reaps it and frees its issue marker and leases.
# usage: bash test-janitor-pid-reuse.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/co/runs/run-reused" "$T/caps/runs" "$T/caps/children"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/s/"
sleep 60 & reused=$!
printf 'CHILD — GitHub issue #62 (bug; one PR):\n' > "$T/co/runs/run-reused/request.txt"
printf '{"event": "call_start", "pid": %s, "started_at": 1}\n' "$reused" > "$T/co/runs/run-reused/record.jsonl"
touch -t 202001010000 "$T/co/runs/run-reused/record.jsonl"
echo run-reused > "$T/st/active-runs.txt"; echo run-reused > "$T/st/auto/disp-62"
printf '{"holder": "run-reused", "pid": %s, "since": 1}\n' "$reused" > "$T/caps/runs/run-reused.json"
printf '{"holder": "run-reused:route.router.1", "pid": %s, "since": 1}\n' "$reused" > "$T/caps/children/run-reused_route.router.1.json"
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main SQUAD_CAPS_DIR="$T/caps" python3 -I "$T/s/janitor.py" 2>&1)
kill "$reused" 2>/dev/null
wait "$reused" 2>/dev/null
# The reap itself is what this case proves: the reused pid neither keeps the run live nor keeps its leases (#1854 leaves the line to test-janitor-killed.sh).
/usr/bin/grep -q 'zombie run run-reused' "$T/st/cycle.log" 2>/dev/null && ok "C: reused pid does not keep a dead run active" || ko "C dead run not reaped: $o"
[ ! -e "$T/st/auto/disp-62" ] && ok "C: reaped run releases its dispatch marker" || ko "C dispatch marker kept: $o"
[ ! -e "$T/caps/runs/run-reused.json" ] && [ ! -e "$T/caps/children/run-reused_route.router.1.json" ] && ok "C: reaped run releases its leases" || ko "C leases kept: $o"
rm -rf "$T"; exit $fail
