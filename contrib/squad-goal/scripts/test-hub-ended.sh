#!/bin/bash
# seconds: 0.20
# Regression test for #830: hub.py lists the runs that ended on the machine's local day, and no other day's run.
# usage: bash test-hub-ended.sh [path/to/hub.py]
d=$(cd "$(dirname "$0")" && pwd); src=${1:-$d/hub.py}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/home" "$T/co" "$T/st" "$T/runs/run-today" "$T/runs/run-old"
now=$(date +%s); old=$((now - 3 * 86400))
printf '%s\n' "{\"ts\": $now, \"event\": \"run_start\", \"pos\": 1}" "{\"ts\": $now, \"event\": \"verdict\", \"verdict\": \"completed\", \"reasons\": [], \"exit_code\": 0, \"pos\": 2}" >"$T/runs/run-today/record.jsonl"
printf '%s\n' "{\"ts\": $old, \"event\": \"run_start\", \"pos\": 1}" "{\"ts\": $old, \"event\": \"verdict\", \"verdict\": \"completed\", \"reasons\": [], \"exit_code\": 0, \"pos\": 2}" >"$T/runs/run-old/record.jsonl"
cat >"$T/probe.py" <<'PY'
import importlib.util, json, pathlib, sys
spec = importlib.util.spec_from_file_location("hub", sys.argv[1]); hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
hub.project_dirs = lambda: [pathlib.Path(sys.argv[2])]
print(json.dumps(sorted(r["id"] for r in hub.ended_today())))
PY
got=$(env HOME="$T/home" SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T/co" SG_HUB_DIR="$T/co" SG_STATE="$T/st" python3 "$T/probe.py" "$src" "$T/runs" 2>"$T/err")
if [ "$got" = '["run-today"]' ]; then ok "today's ended run is listed and yesterday's is not"; else ko "ended list was $got: $(tail -1 "$T/err")"; fi
exit $fail
