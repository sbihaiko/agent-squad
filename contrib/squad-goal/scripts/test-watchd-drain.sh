#!/bin/bash
# Guard for #923: `watchd.sh drain` pauses dispatch and stops the loop only once no live run and no held gate remain.
# A live run is an active-runs line whose record.jsonl has no verdict; a held gate is a locks/gate/pid naming a live pid.
# Offline: a temp state dir, stub cycle and stub lanes that stay up until the loop stops. No network.
# usage: bash test-watchd-drain.sh
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; gp=; sup=
trap 'bash "$T/watchd.sh" stop >/dev/null 2>&1; kill -9 "$sup" "$gp" 2>/dev/null; rm -rf "$T"' EXIT
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset "${!SG_@}"; export SG_STATE="$T/st" SG_PROFILE= HOME="$T/home"; mkdir -p "$SG_STATE" "$HOME" "$T/runs/r1" "$SG_STATE/locks/gate"
cp "$d/watchd.sh" "$T/watchd.sh"
python3 - "$T/watchd.sh" "$SG_STATE" <<'PY'
import pathlib,sys
f,st=sys.argv[1],sys.argv[2]
p=pathlib.Path(f); t=p.read_text()
t=t.replace('. "$(dirname "$0")/env.sh"','SG_STATE="%s"; SG_PROFILE='%st,1)
t=t.replace('"$(dirname "$me")/watch.sh"','"%s/watch.sh"'%f.rsplit("/",1)[0])
t=t.replace('"$(dirname "$me")/fastloop.sh"','"%s/fastloop.sh"'%f.rsplit("/",1)[0])
t=t.replace('"$(dirname "$me")/cqflush.sh"','"%s/cqflush.sh"'%f.rsplit("/",1)[0])
p.write_text(t)
PY
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
# The hub's switch over the real watchd (#923 review 2): stop-now with two live runs, a start with a third, then a drain. The
# runs stop-now killed get their verdict at once, so the drain waits on the third run only; a run that then dies with no
# verdict does not hold the drain either. A start that failed in the hub is not shown after a later start from the terminal.
rm -f "$SG_STATE/auto/off"; : > "$SG_STATE/active-runs.txt"
SCRIPTS="$d" WATCHD="$T/watchd.sh" SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T" SG_HUB_DIR="$T/hub" SG_DRAIN_POLL=1 python3 - <<'PY' || fail=1
import importlib.util, json, os, pathlib, subprocess, time
spec = importlib.util.spec_from_file_location("hub", os.environ["SCRIPTS"] + "/hub.py"); hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
hub.WATCHD = pathlib.Path(os.environ["WATCHD"]); hub.tailnet_block = lambda: {}
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
    r.append("call_start", node="n", entry=1, session="s", pid=pid, limit=None, started_at=0)
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
[ "$fail" = 0 ] && echo "watchd drain: all ok" || echo "watchd drain: FAILED"
exit "$fail"
