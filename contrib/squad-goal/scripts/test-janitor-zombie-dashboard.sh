#!/bin/bash
# seconds: 3.43
# A pid-less run whose only process in its folder is its dashboard server is a zombie. Rule C reads liveness as the engine
# does (the LIVE predicate of squad/reap.py), and `python3 -m squad.dashboard_server` is not a run's process: a dashboard
# that stayed inside an idle run kept the run "alive" for ever, so the zombie verdict never came (14:53-15:01, #1270).
# usage: bash test-janitor-zombie-dashboard.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/co/runs/run-zombie" "$T/bin"
cp "$src" "$T/s/janitor.py"; here=$(dirname "$src")
cp "$here/issue_state.py" "$here/issue_ledger.py" "$here/board_columns.py" "$here/board-definition.json" "$T/s/"
f="$T/co/runs/run-zombie"; now=$(date +%s)
printf 'CHILD — GitHub issue #71 (bug; one PR):\n' > "$f/request.txt"; touch -t 202001010000 "$f/request.txt"
# idle 1300 s: past rule C's twenty minutes, inside the takeover bound (1800 s), so only rule C can answer
printf '%s\n' "{\"event\": \"run_start\", \"run_id\": \"run-zombie\", \"graph_name\": \"g\", \"graph_hash\": \"h\", \"engine_options\": {}, \"root\": \"main\", \"plan\": [\"main\"], \"pos\": 1, \"ts\": $((now - 1300))}" > "$f/record.jsonl"
touch -t "$(date -r $((now - 1300)) +%Y%m%d%H%M.%S 2>/dev/null || date -d "@$((now - 1300))" +%Y%m%d%H%M.%S)" "$f/record.jsonl"
echo run-zombie > "$T/st/active-runs.txt"; echo run-zombie > "$T/st/auto/disp-71"
# the run's dashboard server, started the way the loop starts it: `python3 -m squad.dashboard_server <folder> --idle 7200`
rp=$(cd "$f" && pwd -P)   # the janitor resolves its checkout, so the loop names the folder by its resolved path (/private/var on macOS)
( cd "$f" && exec python3 -c 'import time; time.sleep(120)' -m squad.dashboard_server "$rp" --idle 7200 ) & dash=$!
sleep 2
SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main python3 -I "$T/s/janitor.py" >"$T/out" 2>&1
sleep 1; o=$(cat "$T/st/cycle.log" 2>/dev/null)
echo "$o" | /usr/bin/grep -q "zombie run run-zombie" && ok "C: a dashboard server alone does not keep an idle pid-less run alive" || ko "C: no zombie verdict for the run: $o $(cat "$T/out")"
echo "$o" | /usr/bin/grep -q "dashboard of zombie run run-zombie" && ok "C: the run's dashboard is ended with it" || ko "C: the dashboard was not ended: $o"
kill -0 "$dash" 2>/dev/null && ko "C: the dashboard server survived the zombie pass" || ok "C: the dashboard server is gone"
[ ! -e "$T/st/auto/disp-71" ] && ok "C: the zombie run releases its dispatch marker" || ko "C: the dispatch marker was kept: $o"
kill "$dash" 2>/dev/null; wait 2>/dev/null
rm -rf "$T"; exit $fail
