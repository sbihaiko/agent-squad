#!/bin/bash
# seconds: 92.14
# Guard for #923: `watchd.sh drain` pauses dispatch and stops the loop only once no live run and no held gate remain.
# A live run is an active-runs line whose record.jsonl has no verdict; a held gate is a locks/gate/pid naming a live pid.
# Offline: a temp state dir, stub cycle and stub lanes that stay up until the loop stops. No network.
# usage: bash test-watchd-drain.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; gp=; sup=
trap 'bash "$T/watchd.sh" stop >/dev/null 2>&1; kill -9 "$sup" "$gp" 2>/dev/null; rm -rf "$T"' EXIT
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export SG_STATE="$T/st" SG_PROFILE= HOME="$T/home"; mkdir -p "$SG_STATE" "$HOME" "$T/runs/r1" "$SG_STATE/locks/gate"
. "$d/caps.sh"   # SG_PS: the process table read through squad.procs, the reader the shipped script uses (#1508)
cp "$d/watchd.sh" "$d/drainpolicy.py" "$T/"
python3 - "$T/watchd.sh" "$SG_STATE" "$d" <<'PY'
import pathlib,sys
f,st=sys.argv[1],sys.argv[2]
p=pathlib.Path(f); t=p.read_text()
t=t.replace('. "$(dirname "$0")/env.sh"','SG_STATE="%s"; SG_PROFILE='%st,1)
# The copy reads the process table through caps.sh beside the real watchd.sh, never the `ps` fallback (#1508): a sandbox
# refuses the setuid table binary, and a copy that fell back to it read its own supervisor as gone.
t=t.replace('"$(dirname "$0")/caps.sh"','"%s/caps.sh"'%sys.argv[3])
t=t.replace('. "$(dirname "$0")/ghcall.sh"   # the one seam the start\'s loop-bug read goes through (ADR-0146)',
            'gh_aggregate() { [ -s "$SG_STATE/issues.json" ] || return 1; cat "$SG_STATE/issues.json"; }')
t=t.replace('"$(dirname "$me")/watch.sh"','"%s/watch.sh"'%f.rsplit("/",1)[0])
t=t.replace('"$(dirname "$me")/fastloop.sh"','"%s/fastloop.sh"'%f.rsplit("/",1)[0])
t=t.replace('"$(dirname "$me")/cqflush.sh"','"%s/cqflush.sh"'%f.rsplit("/",1)[0])
p.write_text(t)
PY
# The drain reads a run through squad/run_liveness.py (ADR-0167), found in SG_SQUAD as a live copy finds it; the hub part
# below needs the profile a loop exports, as env.sh does.
export SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T" SG_HUB_DIR="$T/hub" SG_SHARED="$T/shared" SG_SQUAD="$(cd "$d/../../.." && pwd)"
printf '#!/bin/bash\nsleep 300\n' > "$T/watch.sh"; printf '#!/bin/bash\nsleep 300\n' > "$T/fastloop.sh"; printf '#!/bin/bash\nsleep 300\n' > "$T/cqflush.sh"
chmod +x "$T/watch.sh" "$T/fastloop.sh" "$T/cqflush.sh" "$T/watchd.sh"
alive_sup() { [ -n "$sup" ] && kill -0 "$sup" 2>/dev/null; }
bash "$T/watchd.sh" start >/dev/null; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
alive_sup && ok "the loop is up for the drain check" || { ko "the loop did not start"; exit 1; }
printf '{"event": "start"}\n' > "$T/runs/r1/record.jsonl"; echo "$T/runs/r1" > "$SG_STATE/active-runs.txt"
sleep 300 & gp=$!; echo "$gp" > "$SG_STATE/locks/gate/pid"
out=$(SG_DRAIN_POLL=1 bash "$T/watchd.sh" drain 2>&1)
case "$out" in *draining*) ok "drain says it is draining";; *) ko "drain did not say draining: $out";; esac
[ -e "$SG_STATE/auto/off" ] && [ -e "$SG_STATE/auto/drain" ] && ok "drain pauses dispatch and marks the drain" || ko "drain left no pause and no drain marker"
sleep 4
alive_sup && ok "a live run and a held gate keep the loop up while draining" || ko "drain stopped the loop with work still live"
printf '{"event": "verdict"}\n' >> "$T/runs/r1/record.jsonl"; sleep 4
alive_sup && ok "the loop stays up while the gate is still held" || ko "drain stopped the loop when only the gate was held"
kill "$gp" 2>/dev/null; gp=
for _ in $(seq 1 20); do ! alive_sup && [ ! -e "$SG_STATE/auto/drain" ] && break; sleep 1; done
if alive_sup; then ko "drain did not stop the loop once the run and the gate were done"; else ok "drain stops the loop once the run and the gate are done"; fi
[ ! -e "$SG_STATE/auto/drain" ] && ok "the drain marker is gone after the stop" || ko "the drain marker outlived the stop"
[ "$(bash "$T/watchd.sh" drain 2>&1)" = "watchd not running" ] && ok "drain on a stopped loop says so" || ko "drain on a stopped loop did not say not running"
# A drain's pause is the drain's own: a start from the terminal after a hub drain dispatches again, so the strip never reads
# "on" while nothing dispatches. A pause the owner set before the drain is the owner's, and a start keeps it.
bash "$T/watchd.sh" start >/dev/null; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
alive_sup && [ ! -e "$SG_STATE/auto/off" ] && ok "a terminal start after a drain lifts the drain's pause" || ko "a terminal start after a drain left auto/off: the loop reads on and dispatches nothing"
SG_DRAIN_POLL=1 bash "$T/watchd.sh" stop >/dev/null
: > "$SG_STATE/auto/off"
bash "$T/watchd.sh" start >/dev/null; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
SG_DRAIN_POLL=1 bash "$T/watchd.sh" drain >/dev/null
for _ in $(seq 1 20); do ! alive_sup && break; sleep 1; done
bash "$T/watchd.sh" start >/dev/null; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
[ -e "$SG_STATE/auto/off" ] && ok "a pause the owner set before the drain survives the next start" || ko "a start removed the owner's own pause"
bash "$T/watchd.sh" stop >/dev/null
# (#1057) A gate lock whose holder died as a zombie holds no drain: the parent has not reaped it, so kill -0 still answers.
sh -c 'sleep 0 & echo $! > "$1"; exec sleep 300' sh "$T/zombie" >/dev/null 2>&1 & zpar=$!
for i in $(seq 1 50); do [ -s "$T/zombie" ] && [ "$($SG_PS -o stat= -p "$(cat "$T/zombie")" 2>/dev/null | cut -c1)" = Z ] && break; sleep 0.1; done
rm -f "$SG_STATE/auto/off" "$SG_STATE/auto/drain"; : > "$SG_STATE/active-runs.txt"; echo "$(cat "$T/zombie")" > "$SG_STATE/locks/gate/pid"
bash "$T/watchd.sh" start >/dev/null; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
SG_DRAIN_POLL=1 bash "$T/watchd.sh" drain >/dev/null 2>&1
for _ in $(seq 1 20); do ! alive_sup && break; sleep 1; done
if alive_sup; then ko "a zombie gate lock held the drain"; else ok "a zombie gate lock does not hold the drain"; fi
kill -9 "$zpar" 2>/dev/null; wait "$zpar" 2>/dev/null; rm -f "$SG_STATE/locks/gate/pid"
bash "$T/watchd.sh" stop >/dev/null
# (#1316) A start during a drain cancels it: the waiter ends, the drain's pause lifts, and the loop stays up once the work ends.
rm -f "$SG_STATE/auto/off" "$SG_STATE/auto/drain" "$SG_STATE/auto/drain-pause"; : > "$SG_STATE/active-runs.txt"
bash "$T/watchd.sh" start >/dev/null; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
sleep 300 & gp=$!; echo "$gp" > "$SG_STATE/locks/gate/pid"
SG_DRAIN_POLL=1 bash "$T/watchd.sh" drain >/dev/null; sleep 2
w=$(cat "$SG_STATE/auto/drain-waiter" 2>/dev/null)
[ -n "$w" ] && kill -0 "$w" 2>/dev/null && ok "a drain with live work runs its waiter" || ko "the drain waiter did not run"
bash "$T/watchd.sh" start >/dev/null
[ -n "$w" ] && ! kill -0 "$w" 2>/dev/null && ok "a start during a drain ends its waiter" || ko "a start during a drain left its waiter running"
[ ! -e "$SG_STATE/auto/off" ] && [ ! -e "$SG_STATE/auto/drain" ] && ok "a start during a drain lifts its pause and marker" || ko "a start during a drain left auto/off or auto/drain"
kill "$gp" 2>/dev/null; gp=; sleep 4
alive_sup && ok "the loop stays up after a cancelled drain's work ends" || ko "a cancelled drain stopped the loop"
bash "$T/watchd.sh" stop >/dev/null
# (#1228) Only the last invocation of a record counts (ADR-0167): the events after its last run_start or resume. A verdict
# there is final, a verdict before it is history, and the run's pid is the last call_start or gate_start of that invocation.
# drain_busy is read out of the real watchd copy and run alone, on one active run at a time.
busy() { ( eval "$(sed -n '/^drain_busy() {/,/^}/p' "$T/watchd.sh")"; me="$T/watchd.sh"; drain_busy ); }
sq() { python3 -c "import subprocess;print(subprocess.Popen(['python3','-c','import time;time.sleep(300)','squad','start'],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).pid)"; }
old() { python3 -c 'import os,sys,time; t=time.time()-600; os.utime(sys.argv[1],(t,t))' "$1"; }
ev() { printf '{"event": "%s"%s}\n' "$1" "${2:+, \"pid\": $2}"; }
q1=$(sq); q2=$(sq); mkdir -p "$T/runs/r2"; R2="$T/runs/r2/record.jsonl"; echo "$T/runs/r2" > "$SG_STATE/active-runs.txt"; rm -f "$SG_STATE/locks/gate/pid"
{ ev run_start; ev call_start 999999; ev verdict; ev resume; ev call_start "$q1"; } > "$R2"; old "$R2"
busy && ok "a run resumed after a verdict holds the drain while its new pid lives" || ko "a verdict before the resume read as final: the drain ends under a live run"
{ ev run_start; ev call_start 999999; ev resume; ev gate_start "$q2"; } > "$R2"; old "$R2"
busy && ok "a gate_start pid of the last invocation holds the drain" || ko "a run waiting on its gate after a resume did not hold the drain"
{ ev run_start; ev call_start "$q1"; ev resume; ev call_start "$q2"; ev verdict; } > "$R2"; old "$R2"
busy && ko "a verdict after the last resume did not read as final" || ok "a verdict after the last resume is final"
{ ev run_start; ev call_start "$q1"; ev resume; } > "$R2"; old "$R2"
busy && ko "a pid from before the resume held the drain" || ok "a pid from before the resume is ignored"
kill -9 "$q1" "$q2" 2>/dev/null
# (#1228 review) A read that fails is unknown, and an unknown holds the drain: never "idle" on a failed read. The drain takes an
# answer only on exit 0 with the exact token on stdout; stderr goes to a file, never into the answer. Each read is bounded, a
# distinct error is one watch.log line, and a streak of failures is one alerts.log line.
mkdir -p "$T/stub/squad"; : > "$T/stub/squad/__init__.py"
cat > "$T/stub/squad/run_liveness.py" <<'STUB'
import os, sys, time
m = os.environ.get("STUB", "")
if m.startswith("noise"): print("Traceback (most recent call last):\nwarning: idle live", file=sys.stderr)
if m == "fail": print("run_liveness: unknown: cannot read the record: [Errno 13] Permission denied", file=sys.stderr); sys.exit(3)
if m == "rc": print("idle"); sys.exit(1)
if m == "junk": print("idle and more")
if m == "hang": time.sleep(30)
if m in ("noise-idle", "idle"): print("idle")
if m == "noise-live": print("live")
STUB
WL="$T/stub.log"; AL="$SG_STATE/alerts.log"; rm -f "$AL" "$SG_STATE/drain-reader.fail"; R3="$T/runs/r3"; mkdir -p "$R3"; echo "$R3" > "$SG_STATE/active-runs.txt"
sb() { ( eval "$(sed -n '/^drain_busy() {/,/^}/p' "$T/watchd.sh")"; me="$T/watchd.sh"; L="$WL"; SG_SQUAD="$T/stub" STUB="$1" SG_DRAIN_READ_S=1 SG_DRAIN_ALERT_AFTER=3 drain_busy ); }
n_wl() { /usr/bin/grep -c "run reader failed" "$WL" 2>/dev/null || true; }
sb noise-idle && ko "stderr noise changed an idle answer: the drain held" || ok "stderr noise does not change an idle answer"
sb noise-live && ok "stderr noise does not change a live answer" || ko "stderr noise changed a live answer: the drain ended"
sb fail && ok "a read failure holds the drain" || ko "a read failure ended the drain"
[ "$(n_wl)" = 1 ] && /usr/bin/grep -q "Permission denied" "$WL" && ok "a read failure is one watch.log line naming its cause" || ko "a read failure logged $(n_wl) lines: $(cat "$WL" 2>/dev/null)"
sb fail; [ "$(n_wl)" = 1 ] && ok "the same failure again writes no second line" || ko "a repeated failure logged $(n_wl) lines"
[ -s "$AL" ] && ko "two failures already raised the alert" || ok "a short failure streak raises no alert"
sb rc && ok "a non-zero exit holds the drain, whatever stdout says" || ko "a non-zero exit with an idle token ended the drain"
[ "$(/usr/bin/grep -c "DRAIN" "$AL" 2>/dev/null)" = 1 ] && ok "a streak of failed reads raises one alerts.log line" || ko "the failure streak raised $(/usr/bin/grep -c DRAIN "$AL" 2>/dev/null) alerts"
sb junk && ok "exit 0 with no exact token holds the drain" || ko "exit 0 with a malformed answer ended the drain"
[ "$(/usr/bin/grep -c "DRAIN" "$AL")" = 1 ] && ok "the alert is raised once per streak" || ko "the alert repeated in one streak"
s=$(date +%s); sb hang && ok "a read that hangs holds the drain" || ko "a hung read ended the drain"
[ $(( $(date +%s) - s )) -lt 10 ] && ok "a hung read is bounded" || ko "a hung read was not bounded"
[ "$(n_wl)" = 4 ] && ok "each distinct failure is one watch.log line" || ko "four distinct failures logged $(n_wl) lines"
sb idle; [ ! -e "$SG_STATE/drain-reader.fail" ] && ok "an answer ends the failure streak" || ko "an answer left the failure streak"
sb fail; [ "$(n_wl)" = 5 ] && ok "a failure after an answer is logged again" || ko "a failure after an answer logged $(n_wl) lines in all"
pz() { ( eval "$(sed -n '/^drain_pause() {/,/^}/p' "$T/watchd.sh")"; SG_DRAIN_POLL=$1 drain_pause ); }
printf '3\nx\n' > "$SG_STATE/drain-reader.fail"; a=$(pz 1); printf '10\nx\n' > "$SG_STATE/drain-reader.fail"; b=$(pz 5); rm -f "$SG_STATE/drain-reader.fail"; c=$(pz 5)
[ "$a $b $c" = "8 60 5" ] && ok "failed reads back the poll off, to a bound, and an answer restores it" || ko "the drain pause read $a $b $c, not 8 60 5"
rm -f "$SG_STATE/drain-reader.fail"; : > "$WL"
( eval "$(sed -n '/^drain_busy() {/,/^}/p' "$T/watchd.sh")"; me="$T/watchd.sh"; L="$WL"; SG_SQUAD="$T/nosquad" drain_busy ) && /usr/bin/grep -q "run reader failed" "$WL" && ok "no reader to run holds the drain and logs it" || ko "a missing run reader let the drain end, or logged nothing"
rm -f "$SG_STATE/drain-reader.fail" "$AL"; : > "$SG_STATE/active-runs.txt"
# The hub's switch over the real watchd (#923 review 2): stop-now with two live runs, a start with a third, then a drain. The
# runs stop-now killed get their verdict at once, so the drain waits on the third run only; a run that then dies with no
# verdict does not hold the drain either. A start that failed in the hub is not shown after a later start from the terminal.
rm -f "$SG_STATE/auto/off"; : > "$SG_STATE/active-runs.txt"
SCRIPTS="$d" WATCHD="$T/watchd.sh" SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T" SG_HUB_DIR="$T/hub" SG_DRAIN_POLL=1 python3 - <<'PY' || fail=1
import importlib.util, json, os, pathlib, subprocess, time
spec = importlib.util.spec_from_file_location("hub", os.environ["SCRIPTS"] + "/hub.py"); hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
hub.WATCHD = pathlib.Path(os.environ["WATCHD"]); hub.tailnet_block = lambda checkout=None: {}
import sys; sys.path.insert(0, str(pathlib.Path(os.environ["SCRIPTS"]).parents[2])); from squad import record as R
ST, T = pathlib.Path(os.environ["SG_STATE"]), pathlib.Path(os.environ["SG_CHECKOUT"])
LB = hub.Access(True, "", "", False); bad = 0
def check(label, ok, got=""):
    global bad; bad |= not ok; print(("ok   " if ok else "FAIL ") + label + ("" if ok else f": {got}"), flush=True)
def until(f, s=20):
    for _ in range(s * 10):
        if f(): return True
        time.sleep(0.1)
    return False
def alive(pid):
    try: os.kill(pid, 0)
    except OSError: return False
    return True
def run(name):   # a detached squad run, as autofrontier leaves one: its record names its pid in call_start
    out = subprocess.run(["python3", "-c", "import subprocess,sys;print(subprocess.Popen(['python3','-c','import time;time.sleep(300)','squad','start'],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).pid)"], capture_output=True, text=True)
    pid = int(out.stdout); f = T / "runs" / name; f.mkdir(parents=True, exist_ok=True)
    r = R.Record(f / "record.jsonl")
    r.append("run_start", run_id=name, graph_name="g", graph_hash="h", engine_options={}, root=".", plan=[])
    r.append("call_start", node="n", entry=1, session="s", pid=pid, limit=None, started_at=time.time())   # the engine names its pid after it started
    with (ST / "active-runs.txt").open("a") as a: a.write(f"{f}\n")
    PIDS.append(pid); return f, pid
PIDS = []
real = hub.loop_watchd
hub.loop_watchd = lambda *a: 1
code, _ = hub.loop_switch(LB, {"action": "start"}, origin_ok=True)
check("a hub start that fails shows its failure", code == 500 and hub.loop_view(LB)["failed"], hub.loop_view(LB))
hub.loop_watchd = real
time.sleep(1.1)
subprocess.run(["bash", os.environ["WATCHD"], "start"], capture_output=True); subprocess.run(["bash", os.environ["WATCHD"], "stop"], capture_output=True)
v = hub.loop_view(LB)
check("a start and stop from the terminal after it drop the old failure", v["state"] == "off" and not v["failed"], v)
code, _ = hub.loop_switch(LB, {"action": "start"}, origin_ok=True)
check("the hub starts the loop", code == 200 and hub.loop_view(LB)["state"] == "on", (code, hub.loop_view(LB)))
(a, pa), (b, pb) = run("ra"), run("rb")
code, got = hub.loop_switch(LB, {"action": "stop-now", "confirm": True}, origin_ok=True)
check("stop-now kills both live runs", code == 200 and until(lambda: not alive(pa) and not alive(pb)) and sorted(got["killed"]["runs"]) == sorted([str(a), str(b)]), got)
check("each run stop-now killed ends in its verdict", all('"verdict"' in (r / "record.jsonl").read_text().splitlines()[-1] for r in (a, b)))
code, _ = hub.loop_switch(LB, {"action": "start"}, origin_ok=True)
c, pc = run("rc")
check("after the restart only the new run is live", hub.loop_live()["runs"] == [str(c)], hub.loop_live())
code, _ = hub.loop_switch(LB, {"action": "drain"}, origin_ok=True)
time.sleep(3)
check("the drain waits on the new live run", code == 200 and hub.loop_view(LB)["state"] == "draining", hub.loop_view(LB))
os.kill(pc, 9)   # the run dies with no verdict: a crash during the drain
check("the drain finishes once the new run is gone, never waiting on the runs stop-now killed", until(lambda: hub.loop_view(LB)["state"] == "off"), hub.loop_view(LB))
for p in PIDS:
    try: os.kill(p, 9)
    except OSError: pass
raise SystemExit(bad)
PY
# (#1708, ADR-0146) Any open loop-bug drains the loop, and the loop starts again when none is open. Two marks say why the
# loop is off: auto/owner-stop (the owner) and auto/loop-bug-drain (the loop, for the issue it names). The owner wins.
A="$SG_STATE/auto"; AU="$SG_STATE/loop-audit.jsonl"; W="$T/watchd.sh"
issues() { printf "%s\n" "$1" > "$SG_STATE/issues.json"; }
clean() { rm -f "$A/off" "$A/drain" "$A/drain-pause" "$A/restart" "$A/owner-stop" "$A/loop-bug-drain" "$AU" "$SG_STATE/locks/gate/pid" "$SG_STATE/issues.json"; : > "$SG_STATE/active-runs.txt"; }
up() { bash "$W" start >/dev/null 2>&1; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null); }
down() { for _ in $(seq 1 20); do alive_sup || return 0; sleep 1; done; return 1; }
hold() { sleep 300 & gp=$!; echo "$gp" > "$SG_STATE/locks/gate/pid"; }
bash "$W" stop --force >/dev/null 2>&1; clean
# the label rule, with the loop down: nothing drains
lb() { printf "%s" "$1" > "$T/i.json"; bash "$W" loopbug "$T/i.json" 2>/dev/null; }
o=$(lb "[{\"number\":42,\"labels\":[{\"name\":\"bug\"},{\"name\":\"loop-bug\"}]}]"); [ "$o $?" = "42 0" ] && ok "1708 a bare loop-bug is a loop defect" || ko "1708 a bare loop-bug is not read"
o=$(lb "[{\"number\":43,\"labels\":[{\"name\":\"loop-bug:infra\"}]}]"); [ "$o $?" = "43 0" ] && ok "1708 a prefixed loop-bug is a loop defect" || ko "1708 a prefixed loop-bug is not read"
lb "[{\"number\":44,\"labels\":[{\"name\":\"loop-buggy\"}]}]" >/dev/null; [ $? = 1 ] && ok "1708 a lookalike label is none" || ko "1708 a lookalike label is not none"
lb "[{\"number\":45,\"pull_request\":{},\"labels\":[{\"name\":\"loop-bug\"}]}]" >/dev/null; [ $? = 1 ] && ok "1708 a pull request is no issue" || ko "1708 a pull request read as a loop defect"
lb "" >/dev/null; [ $? = 2 ] && ok "1708 an empty body is unknown" || ko "1708 an empty body is not unknown"
lb "{}" >/dev/null; [ $? = 2 ] && ok "1708 a body that is no list is unknown" || ko "1708 an object body is not unknown"
[ ! -e "$A/loop-bug-drain" ] && ok "1708 a loop that is down is not drained" || ko "1708 a dead loop took the loop-bug mark"
# the owner stop leaves its own mark, on a live loop and on one that is not running
clean; up; bash "$W" stop >/dev/null 2>&1
[ -e "$A/owner-stop" ] && ! alive_sup && ok "1708 an owner stop on an idle loop leaves the owner mark" || ko "1708 an idle owner stop left no owner mark"
[ ! -e "$A/off" ] && ok "1708 the owner mark is its own: the stop sets no pause" || ko "1708 the owner stop set the pause"
out=$(bash "$W" start --not-owner 2>&1); rc=$?; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
[ "$rc" != 0 ] && ! alive_sup && ok "1708 a start that is not the owner refuses under the owner mark" || ko "1708 a non-owner start ran under the owner mark: $out"
up; alive_sup && [ ! -e "$A/owner-stop" ] && ok "1708 an owner start removes the owner mark" || ko "1708 an owner start kept the owner mark"
bash "$W" stop --force >/dev/null 2>&1
[ -e "$A/owner-stop" ] && ok "1708 stop --force leaves the owner mark" || ko "1708 stop --force left no owner mark"
clean; echo 7 > "$A/loop-bug-drain"; : > "$A/off"; : > "$A/drain-pause"   # the state a finished loop-bug drain leaves
bash "$W" stop >/dev/null 2>&1
[ -e "$A/owner-stop" ] && [ ! -e "$A/loop-bug-drain" ] && ok "1708 an owner stop on a loop that is not running takes the drain over" || ko "1708 an owner stop on a dead loop left the loop-bug mark"
clean; echo 7 > "$A/loop-bug-drain"; : > "$A/off"; : > "$A/drain-pause"
bash "$W" drain >/dev/null 2>&1
[ -e "$A/owner-stop" ] && [ ! -e "$A/loop-bug-drain" ] && ok "1708 an owner drain on a loop that is not running takes the drain over" || ko "1708 an owner drain on a dead loop left the loop-bug mark"
# the loop-bug drain: marked, audited once, and nothing is killed
clean; up; hold
SG_GH_PASS=autobug SG_DRAIN_POLL=1 bash "$W" bugdrain 7 >/dev/null 2>&1
[ "$(cat "$A/loop-bug-drain" 2>/dev/null)" = 7 ] && [ -e "$A/off" ] && [ -e "$A/drain" ] && ok "1708 a loop-bug drain marks the issue and pauses dispatch" || ko "1708 bugdrain left no mark or no pause"
sleep 2; alive_sup && kill -0 "$gp" 2>/dev/null && ok "1708 the drain kills no gate and keeps the loop up under it" || ko "1708 the loop-bug drain stopped under a held gate"
[ "$(/usr/bin/grep -c "\"action\": \"drain\"" "$AU" 2>/dev/null)" = 1 ] && /usr/bin/grep -q "\"issue\": 7" "$AU" && /usr/bin/grep -q "\"from\": \"autobug\"" "$AU" && /usr/bin/grep -q "\"at\": " "$AU" \
  && ok "1708 the drain is one audit line that names the issue and the pass" || ko "1708 the audit: $(cat "$AU" 2>/dev/null)"
SG_DRAIN_POLL=1 bash "$W" bugdrain 8 >/dev/null 2>&1
[ "$(cat "$A/loop-bug-drain")" = 7 ] && [ "$(wc -l < "$AU" | tr -d " ")" = 1 ] && ok "1708 a second request is a no-op" || ko "1708 a second bugdrain wrote again"
out=$(bash "$W" restart 2>&1); case "$out" in *refused*"#7"*) ok "1708 a restart under the drain names the issue";; *) ko "1708 a restart under the drain said: $out";; esac
issues "[{\"number\":7,\"labels\":[{\"name\":\"loop-bug\"}]}]"
out=$(bash "$W" start 2>&1); rc=$?
[ "$rc" != 0 ] && [ -e "$A/drain" ] && [ -e "$A/loop-bug-drain" ] && case "$out" in *"#7"*) true;; *) false;; esac && ok "1708 a start under the drain refuses while the loop-bug is open, and names it" || ko "1708 a start cancelled the loop-bug drain: rc=$rc $out"
rm -f "$SG_STATE/issues.json"; out=$(bash "$W" start 2>&1); rc=$?
[ "$rc" != 0 ] && [ -e "$A/loop-bug-drain" ] && [ -e "$A/drain" ] && ok "1708 an unknown read holds the drain" || ko "1708 a failed read cancelled the loop-bug drain: rc=$rc $out"
issues "[]"; out=$(bash "$W" start --if-bug-drain 2>&1); rc=$?
[ "$rc" = 0 ] && alive_sup && [ ! -e "$A/loop-bug-drain" ] && [ ! -e "$A/off" ] && [ ! -e "$A/drain" ] && ok "1708 with no loop-bug open the watcher start cancels the drain and removes the mark" || ko "1708 the watcher start: rc=$rc $out"
out=$(bash "$W" start --if-bug-drain 2>&1); [ $? != 0 ] && ok "1708 the watcher start refuses a loop that carries no loop-bug mark" || ko "1708 the watcher start ran with no mark: $out"
# the owner wins
SG_DRAIN_POLL=1 bash "$W" drain >/dev/null 2>&1
[ -e "$A/owner-stop" ] && ok "1708 an owner drain leaves the owner mark" || ko "1708 an owner drain left no owner mark"
aun=$(wc -l < "$AU" | tr -d " "); SG_DRAIN_POLL=1 bash "$W" bugdrain 9 >/dev/null 2>&1
[ ! -e "$A/loop-bug-drain" ] && [ "$(wc -l < "$AU" | tr -d " ")" = "$aun" ] && ok "1708 a loop-bug drain under an owner drain writes nothing" || ko "1708 bugdrain overwrote the owner drain"
bash "$W" start >/dev/null 2>&1
[ ! -e "$A/owner-stop" ] && alive_sup && ok "1708 a start that cancels the owner drain removes the owner mark" || ko "1708 the owner mark stayed beside a live loop"
SG_DRAIN_POLL=1 bash "$W" bugdrain 9 >/dev/null 2>&1
[ "$(cat "$A/loop-bug-drain" 2>/dev/null)" = 9 ] && ok "1708 the loop-bug drain works again after that start" || ko "1708 a stale owner mark disabled the loop-bug drain"
SG_DRAIN_POLL=1 bash "$W" stop >/dev/null 2>&1
[ -e "$A/owner-stop" ] && [ ! -e "$A/loop-bug-drain" ] && ok "1708 an owner stop under a loop-bug drain takes it over" || ko "1708 an owner stop kept the loop-bug mark"
kill "$gp" 2>/dev/null; gp=; down && ok "1708 the loop stops once the gate ends" || ko "1708 the drained loop did not stop"
# a mark with no pause beside it is stale
clean; up; echo 5 > "$A/loop-bug-drain"; hold
SG_DRAIN_POLL=1 bash "$W" bugdrain 6 >/dev/null 2>&1
[ "$(cat "$A/loop-bug-drain")" = 6 ] && [ -e "$A/drain" ] && ok "1708 a stale loop-bug mark is written again and the loop drains" || ko "1708 a stale mark made the drain a no-op"
kill "$gp" 2>/dev/null; gp=; down
# a loop drained for a loop-bug: an unknown read holds it, and no open loop-bug starts it
[ -e "$A/loop-bug-drain" ] && [ -e "$A/off" ] && ! alive_sup && ok "1708 a finished loop-bug drain leaves the loop off with its mark" || ko "1708 the finished drain left: $(ls "$A")"
rm -f "$SG_STATE/issues.json"; out=$(bash "$W" start 2>&1); rc=$?; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
[ "$rc" != 0 ] && ! alive_sup && [ -e "$A/loop-bug-drain" ] && ok "1708 an unknown read does not start a loop drained for a loop-bug" || ko "1708 a failed read started the drained loop: $out"
issues "[]"; out=$(bash "$W" start --if-bug-drain 2>&1); rc=$?; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
[ "$rc" = 0 ] && alive_sup && [ ! -e "$A/loop-bug-drain" ] && [ ! -e "$A/off" ] && ok "1708 with no loop-bug open the drained loop starts, and the mark and the pause go" || ko "1708 the drained loop did not start: rc=$rc $out"
# a stop that fails leaves the loop on, and so leaves no owner mark
mkdir -p "$T/nostop1708"
python3 - "$W" "$T/nostop1708/watchd.sh" <<"PY"
import pathlib, sys
t = pathlib.Path(sys.argv[1]).read_text()
t = t.replace("\ncase \"${1:-status}\" in", "\nhard_stop() { echo \"watchd did not stop\" >&2; return 1; }\ncase \"${1:-status}\" in", 1)
pathlib.Path(sys.argv[2]).write_text(t)
PY
bash "$T/nostop1708/watchd.sh" stop --force >/dev/null 2>&1; rc=$?
[ "$rc" != 0 ] && alive_sup && [ ! -e "$A/owner-stop" ] && ok "1708 a hard stop that fails leaves no owner mark" || ko "1708 a failed stop left the owner mark beside a live loop"
: > "$A/owner-stop"; bash "$T/nostop1708/watchd.sh" halt >/dev/null 2>&1
[ ! -e "$A/owner-stop" ] && ok "1708 a halt that fails removes the owner mark" || ko "1708 a failed halt left the owner mark beside a live loop"
# (#1708) the pass verb on a live loop: `loopbug` reads the list, prints the number and writes the drain mark itself.
rm -f "$A/owner-stop" "$A/loop-bug-drain" "$A/off" "$A/drain" "$SG_STATE/loop-audit.jsonl"
printf '[{"number":88,"labels":[{"name":"loop-bug"}]}]' > "$T/i.json"
out=$(bash "$W" loopbug "$T/i.json" 2>/dev/null); rc=$?
[ "$rc" = 0 ] && [ "$out" = 88 ] && [ "$(cat "$A/loop-bug-drain" 2>/dev/null)" = 88 ] && alive_sup \
  && [ "$(wc -l < "$SG_STATE/loop-audit.jsonl" 2>/dev/null | tr -d " ")" = 1 ] \
  && ok "1708 loopbug on a live loop prints the issue, writes the mark and one audit line, and kills nothing" \
  || ko "1708 loopbug on a live loop: rc=$rc out=$out mark=$(cat "$A/loop-bug-drain" 2>/dev/null)"
bash "$W" stop --force >/dev/null 2>&1; clean
# (#1711) A drain waits for a landing and for a child review in flight, not only for runs and gates.
rm -f "$SG_STATE/auto/off" "$SG_STATE/auto/drain" "$SG_STATE/auto/drain-stop" "$SG_STATE/auto/drain-named"
: > "$SG_STATE/active-runs.txt"; rm -f "$SG_STATE/locks/gate/pid"
export SQUAD_CAPS_DIR="$T/caps"
mkdir -p "$SG_STATE/locks/single-wland"
sleep 300 & lp=$!; echo "$lp" > "$SG_STATE/locks/single-wland/pid"
bash "$T/watchd.sh" start >/dev/null; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
SG_DRAIN_POLL=1 bash "$T/watchd.sh" drain >/dev/null 2>&1
for _ in $(seq 1 10); do alive_sup || break; sleep 1; done
alive_sup && ok "a landing in flight holds the drain" || ko "the drain stopped the loop under a live landing"
grep -q "DRAIN the drain waits for landing:$lp" "$SG_STATE/alerts.log" 2>/dev/null && ok "the drain names the landing it waits for" \
  || ko "the drain did not name the landing it waits for"
kill "$lp" 2>/dev/null; wait "$lp" 2>/dev/null
for _ in $(seq 1 25); do ! alive_sup && break; sleep 1; done
alive_sup && ko "the loop stayed up after the landing ended" || ok "the drain stops once the landing is done"
bash "$T/watchd.sh" stop >/dev/null
mkdir -p "$T/caps/reviews"
printf '{"pid":%d,"holder":"runreview-r-c","since":%d}\n' "$$" "$(date +%s)" > "$T/caps/reviews/r.json"
bash "$T/watchd.sh" start >/dev/null; sup=$(cat "$SG_STATE/watchd.pid" 2>/dev/null)
SG_DRAIN_POLL=1 bash "$T/watchd.sh" drain >/dev/null 2>&1
for _ in $(seq 1 10); do alive_sup || break; sleep 1; done
alive_sup && ok "a child review in flight holds the drain" || ko "the drain stopped the loop under a live review"
grep -q "DRAIN the drain waits for runreview-r-c" "$SG_STATE/alerts.log" 2>/dev/null && ok "the drain names the review it waits for" \
  || ko "the drain did not name the review it waits for"
rm -f "$T/caps/reviews/r.json"
for _ in $(seq 1 25); do ! alive_sup && break; sleep 1; done
alive_sup && ko "the loop stayed up after the review ended" || ok "the drain stops once the review is done"
# A landing does not start under a committed stop: that is the gap between the last busy read and the stop.
rm -f "$SG_STATE/auto/off" "$SG_STATE/auto/drain"
: > "$SG_STATE/auto/drain-stop"
bash "$d/single.sh" wland true >/dev/null 2>&1; [ "$?" = 76 ] && ok "no landing starts under a committed stop" || ko "a landing started under a committed stop"
rm -f "$SG_STATE/auto/drain-stop"
bash "$d/single.sh" wland true >/dev/null 2>&1; [ "$?" = 0 ] && ok "a landing starts once the stop is lifted" || ko "a landing did not start after the stop was lifted"
[ "$fail" = 0 ] && echo "watchd drain: all ok" || echo "watchd drain: FAILED"
exit "$fail"
