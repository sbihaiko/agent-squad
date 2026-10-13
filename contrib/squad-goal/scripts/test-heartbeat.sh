#!/bin/bash
# Regression test for heartbeat.sh's dead-run pass: a run in active-runs.txt with no verdict while no squad process is alive was killed
# (session restart, crash). The pass writes the verdict into the run's own record, so the hub shows the run as ended and stops counting it live.
# Stubbed env.sh and ps pattern; the real squad package writes the verdict. usage: bash test-heartbeat.sh [heartbeat.sh]
d=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$d/../../.." && pwd); src=${1:-$d/heartbeat.sh}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export HOME="$T/home"; mkdir -p "$HOME" "$T/s" "$T/st" "$T/co/runs"; cp "$src" "$T/s/heartbeat.sh"
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

# The run-level heartbeat watchdog (#72): a live run whose call process tree has not moved a CPU second for HB_FLAT_MIN
# minutes AND whose record has taken no new event for HB_RECORD_MIN minutes is wedged, not idle, and is killed. Each case
# below is a stub run: a real process holds the pid its record names, and the record's last event is ten minutes old.
# The windows are minutes, so zero means a second pass is already too late for the run; SG_RUN_CMD_RE matches the stub
# process, so the dead-run pass above (which keys on a live squad process) leaves these runs to the watchdog.
stub() { PYTHONPATH="$repo" python3 - "$T/co/runs/$1" "$2" "$3" "$4" <<'PY'
import os, sys, time
from squad import record
folder, pid, age, node = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
ts = time.time() - age
r = record.Record(os.path.join(folder, "record.jsonl"))
r.append("run_start", run_id=os.path.basename(folder), graph_name="g", graph_hash="h", engine_options={}, root="main", plan=["main"], ts=ts - 1)
r.append("call_start", node=node, entry=1, item=None, session="s1", pid=pid, limit=1200.0, started_at=ts, ts=ts)
PY
  echo "$1" >> "$T/st/active-runs.txt"; }
export HB_FLAT_MIN=0 HB_RECORD_MIN=0

# 1. a flat process tree and a frozen record: killed, and the record names the reason
: > "$T/st/active-runs.txt"; rm -rf "$T/co/runs" "$T/st/hbwatch"; export SG_RUN_CMD_RE='[s]leep 300'
sleep 300 >/dev/null 2>&1 & p1=$!; stub run-stale "$p1" 600 router
hb >/dev/null   # the first pass stamps the flat CPU; only a later pass may kill
o=$(hb)
echo "$o" | /usr/bin/grep -q '^STALE HEART run=run-stale' && ok "1: a flat tree with a frozen record is killed" || ko "1: no kill line: $o"
echo "$o" | /usr/bin/grep -q 'killed: heartbeat_stale' && ok "1: the kill line names the reason" || ko "1: no reason in the line: $o"
echo "$o" | /usr/bin/grep -q 'last node router' && ok "1: the kill line names the last node" || ko "1: no last node: $o"
echo "$o" | /usr/bin/grep -q 'cpu=0s' && ok "1: the kill line names the flat CPU time" || ko "1: no CPU time: $o"
kill -0 "$p1" 2>/dev/null && { ko "1: the wedged run's process survived"; kill "$p1" 2>/dev/null; } || ok "1: the wedged run's process is gone"
last run-stale | /usr/bin/grep -q 'killed: heartbeat_stale' && ok "1: the record names the reason" || ko "1: no reason in the record: $(last run-stale)"
python3 -c 'import json,sys;d=json.loads(sys.argv[1]);assert d["event"]=="verdict" and d["verdict"]=="failed" and d["exit_code"]==1 and d["reasons"]==["killed: heartbeat_stale"]' "$(last run-stale)" && ok "1: the record ends in the failed verdict the reason names" || ko "1: shape: $(last run-stale)"
[ "$(echo "$o" | /usr/bin/grep -c 'killed: heartbeat_stale')" = 1 ] && ok "1: one line for the one kill" || ko "1: line count: $o"

# 2 and 3. one long Bash call (record frozen, CPU moving) and a record frozen under a moving process: both untouched
: > "$T/st/active-runs.txt"; rm -rf "$T/co/runs" "$T/st/hbwatch"; export SG_RUN_CMD_RE='[w]hile :'
bash -c 'while :; do :; done' >/dev/null 2>&1 & p2=$!
stub run-bash "$p2" 600 bash_call; before=$(cat "$T/co/runs/run-bash/record.jsonl")
hb >/dev/null; sleep 3; o=$(hb)
kill -0 "$p2" 2>/dev/null && ok "2: the process of the long Bash call is still alive" || ko "2: the working process was killed"
[ -z "$o" ] && ok "2: a run inside one long Bash call is not touched" || ko "2: it was touched: $o"
[ "$(cat "$T/co/runs/run-bash/record.jsonl")" = "$before" ] && ok "3: a frozen record under a moving process keeps its record" || ko "3: the record moved"
last run-bash | /usr/bin/grep -q '"event": "call_start"' && ok "3: no verdict was written for it" || ko "3: a verdict was written: $(last run-bash)"

# 4. one line per kill, and the killed run's branch and worktree survive the pass
: > "$T/st/active-runs.txt"; rm -rf "$T/co/runs" "$T/st/hbwatch"; kill "$p2" 2>/dev/null; wait "$p2" 2>/dev/null
export SG_RUN_CMD_RE='[s]leep 300'; sleep 300 >/dev/null 2>&1 & p3=$!; sleep 300 >/dev/null 2>&1 & p4=$!
stub run-kill-a "$p3" 600 a; stub run-kill-b "$p4" 600 b
git -C "$T/co" init -q; git -C "$T/co" -c user.email=t@t -c user.name=t commit -q --allow-empty -m base
git -C "$T/co" branch gui/72-stub; git -C "$T/co" worktree add -q "$T/wt72" gui/72-stub
hb >/dev/null; o=$(hb)
[ "$(printf '%s\n' "$o" | /usr/bin/grep -c 'killed: heartbeat_stale')" = 2 ] && ok "4: one line per kill" || ko "4: line count: $o"
printf '%s\n' "$o" | /usr/bin/grep -q 'run=run-kill-a' && printf '%s\n' "$o" | /usr/bin/grep -q 'run=run-kill-b' && ok "4: each line names its own run" || ko "4: the lines do not name both runs: $o"
git -C "$T/co" show-ref --verify -q refs/heads/gui/72-stub && ok "4: the killed run's branch survives" || ko "4: the branch is gone"
git -C "$T/co" worktree list | /usr/bin/grep -q "$T/wt72" && [ -e "$T/wt72/.git" ] && ok "4: the worktree survives" || ko "4: the worktree is gone"
kill "$p3" "$p4" 2>/dev/null   # the pass kills them; a failure above must not leave a stray holding this suite's output
unset HB_FLAT_MIN HB_RECORD_MIN
exit $fail
