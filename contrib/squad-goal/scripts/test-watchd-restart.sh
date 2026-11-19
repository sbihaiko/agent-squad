#!/bin/bash
# Guard for #1471: a restart after a publish drains the live runs first (ADR-0162 decision 3). `watchd.sh restart`
# pauses dispatch at once, marks its drain, and a detached waiter stops the supervisor only once no run and no gate is
# live; then it starts the supervisor again, so a published copy never lands under a run its stop would kill. The lanes
# keep their beat under the pause, so the takeover bound (ADR-0166) still ends a stuck run, and the restart follows it.
# A plain drain is a stop the owner asked for: a restart that finds it never turns that stop into a restarted loop.
# Offline: a temp state dir, a stub cycle and stub lanes that stay up until the loop stops. No network.
# mirrors: watchd.sh
# usage: bash test-watchd-restart.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; sup=; pids=; gp=
trap 'bash "$T/watchd.sh" stop >/dev/null 2>&1; kill -9 $sup $pids $gp 2>/dev/null; rm -rf "$T"' EXIT
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export SG_STATE="$T/st" SG_PROFILE= HOME="$T/home"; mkdir -p "$SG_STATE" "$HOME" "$SG_STATE/locks/gate"
. "$d/caps.sh"   # SG_PS: the process table read through squad.procs, the reader the shipped script uses (#1508)
cp "$d/watchd.sh" "$T/watchd.sh"; cp "$d/drainpolicy.py" "$T/drainpolicy.py"
python3 - "$T/watchd.sh" "$SG_STATE" "$d" <<'PY'
import pathlib,sys
f,st=sys.argv[1],sys.argv[2]
p=pathlib.Path(f); t=p.read_text()
t=t.replace('. "$(dirname "$0")/env.sh"','SG_STATE="%s"; SG_PROFILE='%st,1)
# The copy reads the process table through the real caps.sh, never the `ps` fallback (#1508): a sandbox refuses the setuid
# table binary, and a copy that fell back to it read its own supervisor as gone.
t=t.replace('"$(dirname "$0")/caps.sh"','"%s/caps.sh"'%sys.argv[3])
t=t.replace('. "$(dirname "$0")/ghcall.sh"   # the one seam the start\'s loop-bug read goes through (ADR-0146)',
            'gh_aggregate() { [ -s "$SG_STATE/issues.json" ] || return 1; cat "$SG_STATE/issues.json"; }')
for lane in ("watch.sh", "fastloop.sh", "cqflush.sh"):
    t=t.replace('"$(dirname "$me")/%s"'%lane,'"%s/%s"'%(f.rsplit("/",1)[0],lane))
p.write_text(t)
PY
# The drain reads a run through squad/run_liveness.py (ADR-0167), found in SG_SQUAD as a live copy finds it.
export SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T" SG_HUB_DIR="$T/hub" SG_SHARED="$T/shared" SG_SQUAD="$(cd "$d/../../.." && pwd)"
printf '#!/bin/bash\nsleep 300\n' > "$T/fastloop.sh"; printf '#!/bin/bash\nsleep 300\n' > "$T/cqflush.sh"
# The cycle lane beats, and it applies the takeover bound (ADR-0166) the way the real lane does: a run whose record has
# not moved for SG_TAKEOVER_SECONDS gets its verdict from the one writer of that verdict, squad.killed, and its engine
# process is ended. The rule itself is guarded by tests/test_janitor_takeover.py; this stub only lets this suite prove
# that the restart's drain keeps the lane running and lets go once that verdict is written.
cat > "$T/watch.sh" <<'STUB'
#!/bin/bash
while :; do
  date +%s > "$SG_STATE/beat"
  if [ -s "$SG_STATE/active-runs.txt" ]; then while IFS= read -r r; do
    [ -n "$r" ] && [ -f "$r/record.jsonl" ] || continue
    tail -n 1 "$r/record.jsonl" | /usr/bin/grep -q '"verdict"' && continue
    m=$(python3 -c 'import os,sys;print(int(os.path.getmtime(sys.argv[1])))' "$r/record.jsonl" 2>/dev/null) || continue
    [ $(( $(date +%s) - m )) -gt "${SG_TAKEOVER_SECONDS:-1800}" ] || continue
    PYTHONPATH="${SG_SQUAD:-}" python3 -m squad.killed "$r" >/dev/null 2>&1
    p=$(/usr/bin/grep -o '"pid": [0-9]*' "$r/record.jsonl" | tail -n 1 | /usr/bin/grep -o '[0-9]*$')
    [ -n "$p" ] && kill -9 "$p" 2>/dev/null
  done < "$SG_STATE/active-runs.txt"; fi
  sleep 0.5
done
STUB
chmod +x "$T/watch.sh" "$T/fastloop.sh" "$T/cqflush.sh" "$T/watchd.sh"
alive_sup() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }
sq() { p=$(python3 -c "import subprocess;print(subprocess.Popen(['python3','-c','import time;time.sleep(600)','squad','start'],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).pid)"); pids="$pids $p"; echo "$p"; }
old() { python3 -c 'import os,sys,time; t=time.time()-600; os.utime(sys.argv[1],(t,t))' "$1"; }
ev() { printf '{"event": "%s"%s}\n' "$1" "${2:+, \"pid\": $2}"; }
# A live run's record through the one record writer, so the reader and squad.killed read the shape production writes.
mkrec() { mkdir -p "$1"; PYTHONPATH="$SG_SQUAD" python3 - "$1" "$2" <<'PY'
import sys, time
from squad import record
f, pid = sys.argv[1], int(sys.argv[2])
r = record.Record(f + "/record.jsonl")
r.append("run_start", run_id=f.rsplit("/", 1)[-1], graph_name="g", graph_hash="h", engine_options={}, root=".", plan=[])
r.append("call_start", node="n", entry=1, session="s", pid=pid, limit=None, started_at=time.time())
PY
}
# A new supervisor pid, or nothing while the old one still holds: the restart's one observable.
sup_now() { n=$(cat "$SG_STATE/watchd.pid" 2>/dev/null); [ -n "$n" ] && [ "$n" != "$1" ] && kill -0 "$n" 2>/dev/null && echo "$n"; }
# (AC1) A publish while two runs are live restarts the supervisor only after both runs end.
: > "$SG_STATE/active-runs.txt"
bash "$T/watchd.sh" start >/dev/null; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null); sup1=$sup
alive_sup "$sup1" || { ko "the loop did not start"; exit 1; }
pd1=$(sq); pd2=$(sq); mkrec "$T/runs/d1" "$pd1"; mkrec "$T/runs/d2" "$pd2"
printf '%s\n%s\n' "$T/runs/d1" "$T/runs/d2" > "$SG_STATE/active-runs.txt"
out=$(SG_DRAIN_POLL=1 bash "$T/watchd.sh" restart 2>&1)
case "$out" in *restarting*) ok "a restart says it is draining";; *) ko "a restart did not say it is draining: $out";; esac
[ -e "$SG_STATE/auto/off" ] && [ -e "$SG_STATE/auto/drain" ] && [ -e "$SG_STATE/auto/restart" ] \
  && ok "a restart pauses dispatch and marks its drain" || ko "a restart left dispatch on or left no drain mark"
sleep 2
[ "$(cat "$SG_STATE/watchd.pid" 2>/dev/null)" = "$sup1" ] && alive_sup "$sup1" \
  && ok "a restart waits while two runs are live" || ko "the supervisor restarted with two runs live"
kill -0 "$pd1" 2>/dev/null && kill -0 "$pd2" 2>/dev/null \
  && ok "a restart does not kill the live runs" || ko "a restart killed a live run"
ev verdict >> "$T/runs/d1/record.jsonl"; kill -9 "$pd1" 2>/dev/null
sleep 2
[ "$(cat "$SG_STATE/watchd.pid" 2>/dev/null)" = "$sup1" ] && alive_sup "$sup1" \
  && ok "a restart still waits with one run live" || ko "the supervisor restarted with a run still live"
ev verdict >> "$T/runs/d2/record.jsonl"; kill -9 "$pd2" 2>/dev/null
n=; for _ in $(seq 1 40); do n=$(sup_now "$sup1"); [ -n "$n" ] && break; sleep 0.5; done
[ -n "$n" ] && ok "the restart follows once both runs ended" || ko "no restart after both runs ended (pid $(cat "$SG_STATE/watchd.pid" 2>/dev/null), was $sup1)"
sup=${n:-$sup1}
[ ! -e "$SG_STATE/auto/drain" ] && [ ! -e "$SG_STATE/auto/restart" ] \
  && ok "the restart leaves no drain and no restart marker" || ko "the restart left a drain or a restart marker"
[ ! -e "$SG_STATE/auto/off" ] && ok "the restart lifts its own pause" || ko "the restart left dispatch paused"
bash "$T/watchd.sh" stop >/dev/null
# (AC2) A run past the takeover bound is taken over during the drain as today (ADR-0166), and the restart follows it.
rm -f "$SG_STATE/auto/off" "$SG_STATE/auto/drain" "$SG_STATE/auto/drain-pause" "$SG_STATE/auto/restart"
: > "$SG_STATE/active-runs.txt"
DT="$T/runs/t1/record.jsonl"; pt=$(sq); mkrec "$T/runs/t1" "$pt"; echo "$T/runs/t1" > "$SG_STATE/active-runs.txt"
SG_TAKEOVER_SECONDS=3 bash "$T/watchd.sh" start >/dev/null; supt=$(cat "$SG_STATE/watchd.pid" 2>/dev/null); sup=$supt
alive_sup "$supt" || { ko "the loop did not start for the takeover case"; exit 1; }
SG_DRAIN_POLL=1 bash "$T/watchd.sh" restart >/dev/null
sleep 1
[ "$(cat "$SG_STATE/watchd.pid" 2>/dev/null)" = "$supt" ] && alive_sup "$supt" \
  && ok "a restart waits while the run is live" || ko "the restart did not wait for a live run"
old "$DT"   # the record stops moving, as a stuck run's does, and the takeover bound passes
for _ in $(seq 1 40); do /usr/bin/grep -q '"verdict"' "$DT" && break; sleep 0.5; done
/usr/bin/grep -q 'killed/no-process' "$DT" && ok "the drain leaves the takeover bound free to end the run" || ko "a draining restart held the takeover bound: $(tail -n 1 "$DT" 2>/dev/null)"
[ -s "$SG_STATE/beat" ] && [ $(( $(date +%s) - $(python3 -c 'import os,sys;print(int(os.path.getmtime(sys.argv[1])))' "$SG_STATE/beat") )) -lt 5 ] \
  && ok "the lanes keep their beat under the drain" || ko "the lanes stopped beating under the drain"
n=; for _ in $(seq 1 40); do n=$(sup_now "$supt"); [ -n "$n" ] && break; sleep 0.5; done
[ -n "$n" ] && ok "the restart follows the takeover" || ko "no restart after the takeover (pid $(cat "$SG_STATE/watchd.pid" 2>/dev/null), was $supt)"
sup=${n:-$supt}; bash "$T/watchd.sh" stop >/dev/null
# A supervisor that survives the stop must not leave the loop paused: the waiter that set the drain's pause is the only one
# that lifts it, so a failed restart ends the waiter and dispatch reads on. A paused-but-live loop with no waiter dispatches
# nothing, and the publish that asked for the restart never reaches the drivers (ADR-0162 decision 3, #1471).
rm -f "$SG_STATE/auto/off" "$SG_STATE/auto/drain" "$SG_STATE/auto/drain-pause" "$SG_STATE/auto/restart" "$SG_STATE/locks/gate/pid"
: > "$SG_STATE/active-runs.txt"
mkdir -p "$T/nostop"
python3 - "$T/watchd.sh" "$T/nostop/watchd.sh" <<'PY'
import pathlib, sys
t = pathlib.Path(sys.argv[1]).read_text()
a = '  halt) hard_stop; rc=$?;'; assert a in t   # the waiter's end is halt (#1662); its failure path stays the real one (#1708)
t = t.replace(a, '  halt) (echo "watchd did not stop" >&2; exit 1); rc=$?;', 1)
pathlib.Path(sys.argv[2]).write_text(t)
PY
chmod +x "$T/nostop/watchd.sh"
bash "$T/watchd.sh" start >/dev/null; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
alive_sup "$sup" || { ko "the loop did not start for the failed-stop case"; exit 1; }
mkdir -p "$SG_STATE/auto"; : > "$SG_STATE/auto/drain-pause"; : > "$SG_STATE/auto/off"; : > "$SG_STATE/auto/drain"; : > "$SG_STATE/auto/restart"; : > "$SG_STATE/auto/owner-stop"
bash "$T/nostop/watchd.sh" drain-wait >/dev/null 2>&1
[ ! -e "$SG_STATE/auto/owner-stop" ] && ok "1708 a stop that fails leaves no owner mark beside the live loop" || ko "1708 a failed halt left the owner mark beside a live loop"
alive_sup "$sup" && ok "a supervisor that survives the stop stays live" || ko "the failed-stop case lost the supervisor"
[ ! -e "$SG_STATE/auto/off" ] && [ ! -e "$SG_STATE/auto/drain-pause" ] \
  && ok "a failed stop lifts the drain's pause, so dispatch never stays paused with no waiter" \
  || ko "a failed stop left the loop paused with no waiter"
[ ! -e "$SG_STATE/auto/drain" ] && [ ! -e "$SG_STATE/auto/restart" ] \
  && ok "a failed stop leaves no drain or restart marker" || ko "a failed stop left a drain or restart marker"
bash "$T/watchd.sh" stop >/dev/null
# A plain drain is a stop the owner asked for. A restart that finds that drain running never upgrades it to a restart:
# it refuses, names the drain, and the loop still stops (ADR-0162 decision 3).
rm -f "$SG_STATE/auto/off" "$SG_STATE/auto/drain" "$SG_STATE/auto/drain-pause" "$SG_STATE/auto/restart"; : > "$SG_STATE/active-runs.txt"
sleep 300 & gp=$!; echo "$gp" > "$SG_STATE/locks/gate/pid"
bash "$T/watchd.sh" start >/dev/null; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
SG_DRAIN_POLL=1 bash "$T/watchd.sh" drain >/dev/null; sleep 1
w=$(cat "$SG_STATE/auto/drain-waiter" 2>/dev/null)
out=$(SG_DRAIN_POLL=1 bash "$T/watchd.sh" restart 2>&1); rc=$?
if [ "$rc" != 0 ] && case "$out" in *drain*) true;; *) false;; esac; then ok "a restart during a drain is refused and names the drain"
else ko "a restart during a drain: rc=$rc $out"; fi
[ ! -e "$SG_STATE/auto/restart" ] && ok "a refused restart leaves the drain a stop" || ko "a restart during a drain turned the stop into a restart"
[ -n "$w" ] && kill -0 "$w" 2>/dev/null && ok "the refused restart leaves the drain's waiter running" || ko "the refused restart ended the drain's waiter"
kill "$gp" 2>/dev/null; gp=
for _ in $(seq 1 20); do [ -z "$(cat "$SG_STATE/watchd.pid" 2>/dev/null)" ] && break; sleep 1; done
alive_sup "$(cat "$SG_STATE/watchd.pid" 2>/dev/null)" && ko "the drain stopped being a stop: the loop is up" || ok "the drain still stops the loop"
rm -f "$SG_STATE/locks/gate/pid" "$SG_STATE/auto/off" "$SG_STATE/auto/drain" "$SG_STATE/auto/drain-pause"
# With the loop down a restart only starts it, and says so: it has no live work to drain.
out=$(bash "$T/watchd.sh" restart 2>&1); sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
case "$out" in *"not running: starting"*) ok "a restart on a stopped loop starts it and says why";; *) ko "a restart on a stopped loop: $out";; esac
alive_sup "$sup" && ok "the loop is up after that start" || ko "the start of a stopped loop did not bring it up"
bash "$T/watchd.sh" stop >/dev/null
# (#1708) A restart in flight when a loop-bug opens: the waiter stops the loop, its start is refused for the loop-bug, and
# the refusal becomes a loop-bug drain, so the loop starts again when the issue closes and never stays off for good.
A="$SG_STATE/auto"; rm -f "$A"/* "$SG_STATE/loop-audit.jsonl" "$SG_STATE/issues.json"; : > "$SG_STATE/active-runs.txt"
bash "$T/watchd.sh" start >/dev/null 2>&1; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
sleep 300 & gp=$!; echo "$gp" > "$SG_STATE/locks/gate/pid"
SG_DRAIN_POLL=1 bash "$T/watchd.sh" restart >/dev/null 2>&1
printf "%s\n" "[{\"number\":77,\"labels\":[{\"name\":\"loop-bug\"}]}]" > "$SG_STATE/issues.json"
SG_DRAIN_POLL=1 bash "$T/watchd.sh" bugdrain 77 >/dev/null 2>&1
[ ! -e "$A/loop-bug-drain" ] && [ -e "$A/restart" ] && ok "1708 a loop-bug drain leaves a restart in flight to its waiter" || ko "1708 bugdrain changed a restart in flight"
kill "$gp" 2>/dev/null; gp=; rm -f "$SG_STATE/locks/gate/pid"
for _ in $(seq 1 30); do [ -e "$A/loop-bug-drain" ] && break; sleep 1; done
alive_sup "$(cat "$SG_STATE/watchd.pid" 2>/dev/null)" && ko "1708 the restart started the loop under an open loop-bug" || ok "1708 the restart is refused while a loop-bug is open"
[ "$(cat "$A/loop-bug-drain" 2>/dev/null)" = 77 ] && [ -e "$A/off" ] && ok "1708 the refused restart becomes a loop-bug drain that names the issue" || ko "1708 the refused restart left no loop-bug mark: $(ls "$A")"
for _ in $(seq 1 20); do [ -s "$SG_STATE/loop-audit.jsonl" ] && break; sleep 1; done   # the waiter writes the mark, then the line
/usr/bin/grep -q "\"issue\": 77" "$SG_STATE/loop-audit.jsonl" 2>/dev/null && ok "1708 that drain is audited" || ko "1708 the refused restart wrote no audit line"
printf "[]\n" > "$SG_STATE/issues.json"
bash "$T/watchd.sh" start --if-bug-drain >/dev/null 2>&1; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
alive_sup "$sup" && [ ! -e "$A/off" ] && [ ! -e "$A/loop-bug-drain" ] && ok "1708 the loop starts once the loop-bug closes" || ko "1708 the loop stayed off after the loop-bug closed"
bash "$T/watchd.sh" stop >/dev/null 2>&1; rm -f "$SG_STATE/issues.json" "$A/owner-stop"
[ "$fail" = 0 ] && echo "watchd restart: all ok" || echo "watchd restart: FAILED"
exit "$fail"
