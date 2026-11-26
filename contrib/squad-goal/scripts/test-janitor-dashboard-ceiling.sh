#!/bin/bash
# seconds: 2.34
# The dashboard term of the freeze of 2026-10-13 (issue #70). Rules B, J and C reap the dashboard of ONE run the pass knows about.
# When any of them stops working for any other reason -- a run dropped from active-runs, a record the pass cannot read -- the
# dashboards of finished runs accumulate with nothing bounding them (41 were alive by 06:47). This suite proves the counted
# ceiling beside them (JANITOR_DASHBOARD_MAX): the oldest dashboards no live run owns go down to the ceiling, the dashboard of a
# live run is never a victim even when it is the oldest, JANITOR_DRY=1 reports without killing, and a pass under the ceiling
# touches nothing. The killed dashboards' runs are NOT in active-runs.txt, so no per-run rule reaches them: the ceiling does.
# Stubbed gh/board, a temp SG_STATE/SG_CHECKOUT; the live loop is never read or written. usage: bash test-janitor-dashboard-ceiling.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); T=$(cd "$T" && pwd -P); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/s/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/s/"
printf '#!/bin/bash\necho "$*" >> %s/board.calls\n' "$T" > "$T/s/board.sh"
printf '#!/bin/bash\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
printf '#!/bin/bash\necho "$*" >> %s/notify.calls\n' "$T" > "$T/notify"; chmod +x "$T/notify"
# The dashboard the loop starts: `<python> -m squad.dashboard_server <run folder> --idle 7200`. A real process whose argv names the
# folder is what the pass reads out of /bin/ps, so the fixture starts one; a stub that never becomes a process would prove nothing.
printf '#!/usr/bin/python3\nimport sys, time\ntime.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 120)\n' > "$T/s/squad.dashboard_server"
chmod +x "$T/s/squad.dashboard_server"
dash() {   # <run folder> -> the pid of a live process whose argv names it
  /usr/bin/python3 "$T/s/squad.dashboard_server" "$1" 120 >/dev/null 2>&1 &
  echo $!
}
alive() { kill -0 "$1" 2>/dev/null; }
died() {   # the pass signals, the process needs a moment to be gone
  local i; for i in 1 2 3 4 5 6 7 8 9 10; do alive "$1" || return 0; sleep 0.2; done; return 1
}
# a run that is still going: in active-runs.txt, a record with no verdict, a fresh request
mkdir -p "$T/co/runs/run-live"; printf 'CHILD — GitHub issue(s) 700 (bug)\n' > "$T/co/runs/run-live/request.txt"
printf '{"event": "run_start", "run_id": "run-live", "ts": %s}\n' "$(date +%s)" > "$T/co/runs/run-live/record.jsonl"
# a finished run the pass still holds: its dashboard is young, so rule B (1 h old) does not reap it either
mkdir -p "$T/co/runs/run-fin"; printf 'CHILD — GitHub issue(s) 701 (bug)\n' > "$T/co/runs/run-fin/request.txt"
printf '{"event": "verdict", "verdict": "failed", "reasons": ["x"]}\n' > "$T/co/runs/run-fin/record.jsonl"
printf 'run-live\nrun-fin\n' > "$T/st/active-runs.txt"
# finished runs the pass cannot see at all: they left active-runs.txt (or it was rebuilt), so no per-run rule owns their dashboards
for n in 1 2 3; do
  mkdir -p "$T/co/runs/run-$n"; printf 'CHILD — GitHub issue(s) 70%s (bug)\n' "$n" > "$T/co/runs/run-$n/request.txt"
  printf '{"event": "verdict", "verdict": "pass", "reasons": []}\n' > "$T/co/runs/run-$n/record.jsonl"
done
pass() {   # $1 = JANITOR_DASHBOARD_MAX, $2 = DRY, rest = extra env
  SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main JANITOR_NOTIFY="$T/notify" \
    JANITOR_DASHBOARD_MAX="$1" JANITOR_DRY="$2" JANITOR_CALL_TIMEOUT=20 python3 -I "$T/s/janitor.py" 2>&1
}
# oldest first: the live run's dashboard is the oldest process of the five, so a ceiling that killed by age alone would take it
p_live=$(dash "$T/co/runs/run-live"); p1=$(dash "$T/co/runs/run-1"); p2=$(dash "$T/co/runs/run-2")
p3=$(dash "$T/co/runs/run-3"); p_fin=$(dash "$T/co/runs/run-fin")
sleep 0.3
# (A) a dry run names the ceiling and its victims, and kills nothing
o=$(pass 2 1)
echo "$o" | /usr/bin/grep -q 'DASHBOARD-CEILING' && ok "A: a dry pass names the ceiling" || ko "A: nothing names the ceiling: $o"
echo "$o" | /usr/bin/grep -q "over the ceiling of 2" && ok "A: the line carries the count it read and the ceiling" || ko "A: the count is not named: $o"
echo "$o" | /usr/bin/grep -q "$p1" && ok "A: the oldest orphan is named as a victim" || ko "A: pid $p1 is not named: $o"
for q in "$p_live" "$p1" "$p2" "$p3" "$p_fin"; do alive "$q" || ko "A: a dry pass killed pid $q"; done
ok "A: a dry pass kills nothing"
# (B) over the ceiling: the oldest dashboards no live run owns go, the live run's own dashboard stays
pass 2 "" >/dev/null
died "$p1" && ok "B: the oldest finished run's dashboard is killed" || ko "B: pid $p1 (run-1) survived"
died "$p2" && ok "B: the next one is killed too" || ko "B: pid $p2 (run-2) survived"
died "$p3" && ok "B: the kill reaches a run the per-run reap cannot see (not in active-runs.txt)" || ko "B: pid $p3 (run-3) survived"
alive "$p_live" && ok "B: the live run's dashboard survives although it is the oldest" || ko "B: the live run's dashboard was killed"
alive "$p_fin" && ok "B: the newest orphan survives: the count is back at the ceiling" || ko "B: pid $p_fin survived the wrong pass"
# (C) the pass log says what it did
/usr/bin/grep -q 'DASHBOARD-CEILING' "$T/st/cycle.log" && ok "C: the action is in the pass log" || ko "C: cycle.log: $(cat "$T/st/cycle.log" 2>/dev/null | tail -3)"
/usr/bin/grep -qE "kill pid=$p1 .*dashboard" "$T/st/cycle.log" && ok "C: the log names the killed dashboard and why" || ko "C: no kill line for $p1: $(cat "$T/st/cycle.log" 2>/dev/null)"
# (D) a pass under the ceiling touches nothing
o=$(pass 99 "")
echo "$o" | /usr/bin/grep -q 'DASHBOARD-CEILING' && ko "D: an under-ceiling pass fired: $o" || ok "D: an under-ceiling pass is silent"
alive "$p_live" && alive "$p_fin" && ok "D: the dashboards under the ceiling survive" || ko "D: an under-ceiling pass killed a dashboard"
# (E) #585: a run keeps its folder in its own worktree (#580) or a fork route's child tree; its dashboard is this loop's too
for w in run/k1 run/k2 child/gui-9; do d="$T/co/worktrees/$w/runs/run-w"; mkdir -p "$d"; printf 'CHILD — GitHub issue(s) 709 (bug)\n' > "$d/request.txt"
  printf '{"event": "verdict", "verdict": "pass", "reasons": []}\n' > "$d/record.jsonl"; done
pw1=$(dash "$T/co/worktrees/run/k1/runs/run-w"); pw2=$(dash "$T/co/worktrees/run/k2/runs/run-w"); pw3=$(dash "$T/co/worktrees/child/gui-9/runs/run-w")
sleep 0.3
o=$(pass 3 1)
echo "$o" | /usr/bin/grep -q "5 dashboards of this loop over the ceiling of 3" && ok "E: dashboards of runs in run and child worktrees are counted" || ko "E: worktree dashboards not counted: $o"
pass 3 "" >/dev/null
died "$pw1" && ok "E: the oldest worktree run's dashboard goes down to the ceiling" || ko "E: pid $pw1 (worktree run) survived"
alive "$p_live" && ok "E: the live run's dashboard survives" || ko "E: the live run's dashboard was killed"
# (F) #3004: rule B, the one-hour bound. A run whose verdict is over an hour old left active-runs.txt at 300 s, so the per-run
# pass never read it at 3600 s; the bound reads every dashboard of this loop instead, under the ceiling and with it turned off.
for n in old young open; do mkdir -p "$T/co/runs/run-$n"; printf 'CHILD — GitHub issue(s) 710 (bug)\n' > "$T/co/runs/run-$n/request.txt"; done
printf '{"event": "verdict", "verdict": "pass", "reasons": []}\n' > "$T/co/runs/run-old/record.jsonl"; touch -t 202001010000 "$T/co/runs/run-old/record.jsonl"
printf '{"event": "verdict", "verdict": "pass", "reasons": []}\n' > "$T/co/runs/run-young/record.jsonl"; touch -t "$(date -v-10M +%Y%m%d%H%M)" "$T/co/runs/run-young/record.jsonl"
printf '{"event": "run_start", "run_id": "run-open", "ts": 1}\n' > "$T/co/runs/run-open/record.jsonl"; touch -t 202001010000 "$T/co/runs/run-open/record.jsonl"
mkdir -p "$T/elsewhere/runs/run-x"; printf '{"event": "verdict", "verdict": "pass", "reasons": []}\n' > "$T/elsewhere/runs/run-x/record.jsonl"; touch -t 202001010000 "$T/elsewhere/runs/run-x/record.jsonl"
po=$(dash "$T/co/runs/run-old"); py=$(dash "$T/co/runs/run-young"); pn=$(dash "$T/co/runs/run-open"); px=$(dash "$T/elsewhere/runs/run-x")
sleep 0.3
for cap in 99 0; do
  pass "$cap" "" >/dev/null
  [ "$cap" = 99 ] && { died "$po" && ok "F: a finished run's dashboard past one hour is killed under the ceiling" || ko "F: pid $po (run-old) survived"; }
  alive "$py" && ok "F($cap): a verdict ten minutes old keeps its dashboard" || ko "F($cap): the young run's dashboard was killed"
  alive "$pn" && ok "F($cap): a run with no verdict keeps its dashboard" || ko "F($cap): the open run's dashboard was killed"
  alive "$px" && ok "F($cap): a dashboard outside this checkout is not this pass's" || ko "F($cap): a foreign dashboard was killed"
done
/usr/bin/grep -qE "kill pid=$po .*dashboard of finished run run-old" "$T/st/cycle.log" && ok "F: the log names the finished run" || ko "F: no kill line for $po: $(tail -3 "$T/st/cycle.log")"
po2=$(dash "$T/co/runs/run-old"); sleep 0.3; pass 0 "" >/dev/null
died "$po2" && ok "F: the bound holds with the ceiling turned off" || ko "F: pid $po2 survived with JANITOR_DASHBOARD_MAX=0"
kill "$py" "$pn" "$px" 2>/dev/null
kill "$p_live" "$p_fin" "$pw1" "$pw2" "$pw3" 2>/dev/null
rm -rf "$T"; exit $fail
