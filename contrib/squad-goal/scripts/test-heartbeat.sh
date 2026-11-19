#!/bin/bash
# seconds: 0.86
# Regression test for heartbeat.sh's dead-run pass: a run in active-runs.txt with no verdict while no squad process is alive was killed
# (session restart, crash). The pass writes the verdict into the run's own record, so the hub shows the run as ended and stops counting it live.
# Stubbed env.sh and ps pattern; the real squad package writes the verdict. usage: bash test-heartbeat.sh [heartbeat.sh]
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); src=${1:-$d/heartbeat.sh}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export HOME="$T/home"; mkdir -p "$HOME" "$T/s" "$T/st" "$T/co/runs"; cp "$src" "$T/s/heartbeat.sh"; cp "$d/caps.sh" "$T/s/caps.sh"
printf 'export SG_STATE=%s SG_CHECKOUT=%s SG_SQUAD=%s SG_HUB_DIR=%s\n' "$T/st" "$T/co" "$repo" "$T/hub" > "$T/s/env.sh"
export SG_RUN_CMD_RE='[n]osuch-squad-process-marker'   # hermetic: a live loop on this machine runs `squad start`, and it must not count here
hb() { bash "$T/s/heartbeat.sh" 2>&1; }
mkrun() { mkdir -p "$T/co/runs/$1"; printf '%s\n' '{"event": "run_start", "run_id": "r", "graph_name": "g", "graph_hash": "h", "engine_options": {}, "root": "main", "plan": ["main"], "pos": 1, "ts": 1}' > "$T/co/runs/$1/record.jsonl"
  touch -t 202001010000 "$T/co/runs/$1/record.jsonl"; echo "$1" >> "$T/st/active-runs.txt"; }
last() { tail -1 "$T/co/runs/$1/record.jsonl"; }

mkrun run-dead
o=$(hb)
echo "$o" | /usr/bin/grep -q 'DEAD RUN' && ok "a dead run is still reported" || ko "no DEAD RUN line: $o"
last run-dead | /usr/bin/grep -q '"event": "verdict"' && ok "the dead run's record ends in a verdict event" || ko "no verdict written: $(last run-dead)"
last run-dead | /usr/bin/grep -q 'killed/no-process' && ok "the verdict names the reason killed/no-process" || ko "no reason: $(last run-dead)"
python3 -c 'import json,sys;d=json.loads(sys.argv[1]);assert d["verdict"]=="failed" and d["exit_code"]==1 and d["pos"]==2' "$(last run-dead)" && ok "the verdict has the record's own shape (failed, exit 1, pos 2)" || ko "shape: $(last run-dead)"
n=$(/usr/bin/grep -c '"event": "verdict"' "$T/co/runs/run-dead/record.jsonl"); o2=$(hb)
[ "$(/usr/bin/grep -c '"event": "verdict"' "$T/co/runs/run-dead/record.jsonl")" = "$n" ] && ok "a second pass writes no second verdict" || ko "a second verdict was written"
echo "$o2" | /usr/bin/grep -q 'DEAD RUN' && ko "a run that now has a verdict is reported again: $o2" || ok "a run that now has a verdict is not reported again"

# a run that is alive keeps its record untouched
: > "$T/st/active-runs.txt"; mkrun run-live; before=$(cat "$T/co/runs/run-live/record.jsonl")
sleep 30 & sp=$!; export SG_RUN_CMD_RE="[s]leep 30"; o=$(hb); kill "$sp" 2>/dev/null; wait "$sp" 2>/dev/null
[ "$(cat "$T/co/runs/run-live/record.jsonl")" = "$before" ] && ok "a live squad process: the record is not touched" || ko "a live run got a verdict"
echo "$o" | /usr/bin/grep -q 'DEAD RUN' && ko "a live run is reported dead" || ok "a live squad process: no DEAD RUN"
export SG_RUN_CMD_RE='[n]osuch-squad-process-marker'

# a record written a moment ago is a launch window, not a death
: > "$T/st/active-runs.txt"; mkrun run-new; touch "$T/co/runs/run-new/record.jsonl"; before=$(cat "$T/co/runs/run-new/record.jsonl"); rm -f "$T/st/hb/.dead"; hb >/dev/null
[ "$(cat "$T/co/runs/run-new/record.jsonl")" = "$before" ] && ok "a record that moved a moment ago is left alone" || ko "a fresh record got a verdict"
exit $fail
