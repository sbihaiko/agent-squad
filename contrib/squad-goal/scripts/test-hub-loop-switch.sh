#!/bin/bash
# seconds: 1.57
# Guard for #923: the loop switch on the hub. Start, drain-stop and stop-now write through one route; each change writes one
# audit line (who, when, from where); a stop-now is refused without its confirm and names what it kills; a tailnet request is
# read-only unless tailnet.loop_write (SQUAD_TAILNET_LOOP_WRITE) is on, and the refusal gives its reason. The state reads
# alive, draining, starting or off from one place. The watchd calls are stubbed: no loop runs. No server.
# usage: bash test-hub-loop-switch.sh [path/to/hub.py]
src=$(cd "$(dirname "${1:-$0}")" && pwd)/$(basename "${1:-hub.py}"); [ -n "$1" ] || src=$(cd "$(dirname "$0")" && pwd)/hub.py; export HUB_SRC=$src
T=$(mktemp -d); fail=0
export SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T" SG_HUB_DIR="$T/hub" SG_STATE="$T/st"
out=$(python3 - <<'PY' 2>&1
import datetime as dt, importlib.util, json, os, pathlib, subprocess, time
spec = importlib.util.spec_from_file_location("hub", os.environ["HUB_SRC"]); hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
STATE = pathlib.Path(os.environ["SG_STATE"]); STATE.mkdir(parents=True, exist_ok=True)
import sys; sys.path.insert(0, str(pathlib.Path(os.environ["HUB_SRC"]).resolve().parents[3])); from squad import record as R
def real_record(d, pid):   # a record as a run writes it: run_start, then call_start with the run process pid
    f = pathlib.Path(d) / "record.jsonl"; f.unlink(missing_ok=True); r = R.Record(f)
    r.append("run_start", run_id="r", graph_name="g", graph_hash="h", engine_options={}, root=".", plan=[])
    r.append("call_start", node="n", entry=1, session="s", pid=pid, limit=None, started_at=time.time())   # the engine names its pid after it started
calls = []
def watchd(*args):   # the marks watchd.sh leaves: a drain pauses dispatch and marks it, a stop clears the mark
    calls.append(list(args))
    if args == ("drain",): (STATE / "auto" / "drain").write_text(""); (STATE / "auto" / "off").write_text("")
    if args in (("stop",), ("stop", "--force")): (STATE / "auto" / "drain").unlink(missing_ok=True)
    return 0
hub.loop_watchd = watchd
tn = {"host": "box.tail1.ts.net", "allow": ["alice@example.com"]}
hub.tailnet_block = lambda checkout=None: dict(tn)
LOOP = {"loopback": hub.Access(True, "", "", False), "tail": hub.Access(True, "", "alice@example.com", True)}
def dead_pid():
    p = subprocess.Popen(["true"]); p.wait(); return p.pid
def supervisor(pid):
    (STATE / "watchd.pid").write_text(f"{pid}\n")
def reset(pid=None):
    for f in ("watchd.pid", "loop-audit.jsonl"):
        (STATE / f).unlink(missing_ok=True)
    (STATE / "auto").mkdir(exist_ok=True)
    for f in ("drain", "off"): (STATE / "auto" / f).unlink(missing_ok=True)
    calls.clear()
    supervisor(os.getpid() if pid is None else pid)
def audit():
    p = STATE / "loop-audit.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.is_file() else []
def check(label, ok, got=""):
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f": {got}"))
live_run = STATE.parent / "runs" / "r1"; live_run.mkdir(parents=True, exist_ok=True)
(live_run / "record.jsonl").write_text('{"event": "start"}\n')
(STATE / "active-runs.txt").write_text(f"{live_run}\n")
(STATE / "locks" / "gate").mkdir(parents=True, exist_ok=True)

reset()
view = hub.loop_view(LOOP["loopback"])
check("the view reads the switch and may write from loopback", view["write"] is True and view["reason"] == "", view)
check("the view names the live run the stop would kill", view["live"]["runs"] == [str(live_run)], view["live"])

reset(dead_pid())
seen = []
def starts(rc, up):   # a watchd start that reads the state while it runs, then brings the supervisor up (or not) and answers rc
    def run(*args):
        calls.append(list(args)); seen.append(hub.loop_view(LOOP["loopback"])["state"])
        if up: supervisor(os.getpid())
        return rc
    return run
hub.loop_watchd = starts(0, True)
code, got = hub.loop_switch(LOOP["loopback"], {"action": "start"}, origin_ok=True)
check("start from loopback runs the watchd start", code == 200 and calls == [["start"]], (code, calls))
check("start clears the drain and pause markers", not (STATE / "auto" / "off").exists() and not (STATE / "auto" / "drain").exists())
check("a start writes exactly one audit line naming who, when and from where",
      [(a["action"], a["who"], a["from"]) for a in audit()] == [("start", "owner", "loopback")] and audit()[0].get("at"), audit())
check("a start in progress reads starting", seen == ["starting"], seen)
check("a start that brought the supervisor up reads on", hub.loop_view(LOOP["loopback"])["state"] == "on", hub.loop_view(LOOP["loopback"]))

reset(dead_pid()); seen.clear(); hub.loop_watchd = starts(1, False)
code, got = hub.loop_switch(LOOP["loopback"], {"action": "start"}, origin_ok=True)
v = hub.loop_view(LOOP["loopback"])
check("a start watchd refuses answers 500", code == 500, (code, got))
check("a failed start reads off, never starting", v["state"] == "off", v)
check("a failed start is shown in the view and the audit line", v.get("failed") and audit() and audit()[-1].get("failed"), (v, audit()))

reset(dead_pid()); seen.clear(); hub.loop_watchd = starts(0, False)
code, got = hub.loop_switch(LOOP["loopback"], {"action": "start"}, origin_ok=True)
v = hub.loop_view(LOOP["loopback"])
check("a start whose supervisor is not alive after it is a failure", code == 500 and v["state"] == "off" and v.get("failed"), (code, v))

reset(dead_pid())
(STATE / "auto" / "starting").write_text("0\n"); os.utime(STATE / "auto" / "starting", (0, 0))
check("a stale start mark (the hub died during a start) reads off", hub.loop_view(LOOP["loopback"])["state"] == "off", hub.loop_view(LOOP["loopback"]))
(STATE / "auto" / "starting").unlink()
hub.loop_watchd = watchd

reset()
code, got = hub.loop_switch(LOOP["loopback"], {"action": "drain"}, origin_ok=True)
check("drain-stop runs the watchd drain, not the immediate stop", code == 200 and calls == [["drain"]], (code, calls))
check("drain writes one audit line", [a["action"] for a in audit()] == ["drain"], audit())
check("a draining loop reads draining", hub.loop_view(LOOP["loopback"])["state"] == "draining", hub.loop_view(LOOP["loopback"]))

reset()
code, got = hub.loop_switch(LOOP["loopback"], {"action": "stop-now"}, origin_ok=True)
check("stop-now without its confirm is refused and kills nothing", code == 400 and calls == [] and audit() == [], (code, calls, got))

reset()
code, got = hub.loop_switch(LOOP["loopback"], {"action": "stop-now", "confirm": True}, origin_ok=True)
check("stop-now runs the watchd hard stop, not the drain", code == 200 and calls == [["stop", "--force"]], (code, calls))
check("stop-now answers what it killed and names a live run it found no process for as survived",
      got.get("killed") == {"runs": [], "gates": []} and got.get("survived", {}).get("runs") == [str(live_run)], got)
check("stop-now writes one audit line, and it claims no kill that did not happen",
      len(audit()) == 1 and audit()[0]["action"] == "stop-now" and audit()[0]["killed"]["runs"] == [], audit())

reset(dead_pid())
code, got = hub.loop_switch(LOOP["loopback"], {"action": "drain"}, origin_ok=True)
check("drain on a stopped loop is refused and writes nothing", code == 409 and calls == [] and audit() == [], (code, calls, got))

reset()
check("a live loop with no drain marker reads on", hub.loop_view(LOOP["loopback"])["state"] == "on", hub.loop_view(LOOP["loopback"]))
(STATE / "auto" / "drain").write_text("")
reset_state = hub.loop_view(LOOP["loopback"])["state"]
check("a live loop with the drain marker reads draining", reset_state == "draining", reset_state)
(STATE / "auto" / "drain").unlink()

# the route applies the state rule it publishes: an action the state does not list is refused and writes nothing
reset(dead_pid())
code, got = hub.loop_switch(LOOP["loopback"], {"action": "stop-now", "confirm": True}, origin_ok=True)
check("stop-now on a stopped loop is refused with 409 and writes nothing", code == 409 and calls == [] and audit() == [], (code, calls, got))
reset(); (STATE / "auto" / "drain").write_text("")
code, got = hub.loop_switch(LOOP["loopback"], {"action": "drain"}, origin_ok=True)
check("a second drain while draining is refused with 409 and starts no second waiter", code == 409 and calls == [] and audit() == [], (code, calls, got))
reset(); (STATE / "auto" / "drain").write_text(""); (STATE / "auto" / "drain-pause").write_text("")
code, got = hub.loop_switch(LOOP["loopback"], {"action": "start"}, origin_ok=True)
check("a start while draining cancels the drain: 200, one watchd start, one audit line", code == 200 and calls == [["start"]] and [a["action"] for a in audit()] == ["start"], (code, calls, got))
reset()
code, got = hub.loop_switch(LOOP["loopback"], {"action": "start"}, origin_ok=True)
check("a start on a running loop is refused with 409", code == 409 and calls == [] and audit() == [], (code, calls, got))
reset(dead_pid()); (STATE / "auto" / "starting").write_text("x\n")
code, got = hub.loop_switch(LOOP["loopback"], {"action": "start"}, origin_ok=True)
check("a start while starting is refused with 409", code == 409 and calls == [] and audit() == [], (code, calls, got))
(STATE / "auto" / "starting").unlink()

# stop-now ends each listed run and the held gate itself: a run is detached from the supervisor's group (start_new_session),
# so watchd's group TERM never reaches it. The audit names only what died.
import time
def detached(code, *argv):   # a process its launcher leaves behind, as autofrontier leaves a run: own session, parent gone
    out = subprocess.run(["python3", "-c", "import subprocess,sys;p=subprocess.Popen(['python3','-c',*sys.argv[1:]],start_new_session=True,"
                          "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);print(p.pid)", code, *argv], capture_output=True, text=True)
    return int(out.stdout)
def gone_now(pid):
    try: os.kill(pid, 0)
    except OSError: return True
    return False
def gone(pid):
    for _ in range(50):
        try: os.kill(pid, 0)
        except OSError: return True
        time.sleep(0.1)
    return False
# The page's run readers share one process table for 2.5 s (RUN_TABLE_SECS): prime that window before the run is born, so the
# cached read never names it. The kill must read the table fresh, or the run survives a stop-now that claims to end it.
hub.run_table()
kid = STATE / "kid.pid"
run_pid = detached(f"import subprocess,time;c=subprocess.Popen(['sleep','300']);open({str(kid)!r},'w').write(str(c.pid));time.sleep(300)", "squad", "start")   # argv as a run has it: squad start
gate_pid = detached("import time;time.sleep(300)")
for _ in range(50):
    if kid.is_file() and kid.read_text(): break
    time.sleep(0.1)
kid_pid = int(kid.read_text())
real_record(live_run, run_pid)
orphan = STATE.parent / "runs" / "r2"; orphan.mkdir(parents=True, exist_ok=True); (orphan / "record.jsonl").write_text('{"event": "start"}\n')
(STATE / "active-runs.txt").write_text(f"{live_run}\n{orphan}\n")
(STATE / "locks" / "gate" / "pid").write_text(f"{gate_pid}\n")
reset()
check("the page's run table does not name a run born inside its window", run_pid not in hub.run_table(), run_pid)
code, got = hub.loop_switch(LOOP["loopback"], {"action": "stop-now", "confirm": True}, origin_ok=True)
check("stop-now kills a detached run, its child and the held gate", gone(run_pid) and gone(kid_pid) and gone(gate_pid), (run_pid, kid_pid, gate_pid))
a = audit()[-1] if audit() else {}
check("the audit names the run and the gate that died", a.get("killed", {}).get("runs") == [str(live_run)] and a.get("killed", {}).get("gates") == [f"gate pid {gate_pid}"], a)
check("a run with no process to end is named as survived, not killed", a.get("survived", {}).get("runs") == [str(orphan)] and got.get("survived") == a.get("survived"), (a, got))
last = json.loads((live_run / "record.jsonl").read_text().splitlines()[-1])
check("stop-now gives the run it killed its verdict at once, through squad.killed", last.get("event") == "verdict" and "killed/stop-now" in json.dumps(last), last)
check("a killed run is no longer live, so the confirm and a later drain do not wait on it", str(live_run) not in hub.loop_live()["runs"], hub.loop_live())
for p in (run_pid, kid_pid, gate_pid):
    try: os.kill(p, 9)
    except OSError: pass

# A run whose process died with no verdict (a crash, or a kill the record never heard of) is not live: liveness is the last
# call_start pid alive as a squad run, never the missing verdict alone.
crashed = STATE.parent / "runs" / "r3"; crashed.mkdir(parents=True, exist_ok=True)
real_record(crashed, dead_pid())
(STATE / "active-runs.txt").write_text(f"{crashed}\n")
check("a run whose call_start pid is dead is not live", hub.loop_live()["runs"] == [], hub.loop_live())

# A call_start pid that names a process that is not a squad run (a reused pid: the hub itself, or any other python) is never
# ended: stop-now kills only a process whose command matches SG_RUN_CMD_RE.
other = detached("import time;time.sleep(300)")
mine, theirs = STATE.parent / "runs" / "r4", STATE.parent / "runs" / "r5"
for r, pid in ((mine, os.getpid()), (theirs, other)):
    r.mkdir(parents=True, exist_ok=True)
    real_record(r, pid)
check("the hub's own pid is never a run's pid", hub.loop_run_pid(str(mine)) is None, hub.loop_run_pid(str(mine)))
check("a python process that is not a squad run is never a run's pid", hub.loop_run_pid(str(theirs)) is None, hub.loop_run_pid(str(theirs)))
killed, survived = hub.loop_stop_now({"runs": [str(mine), str(theirs)], "gates": []})
check("stop-now ends neither the hub nor an unrelated python process", not gone_now(other) and killed["runs"] == [] and survived["runs"] == [str(mine), str(theirs)], (killed, survived))
try: os.kill(other, 9)
except OSError: pass
(live_run / "record.jsonl").write_text('{"event": "start"}\n'); (STATE / "active-runs.txt").write_text(f"{live_run}\n"); (STATE / "locks" / "gate" / "pid").unlink()

view = hub.loop_view(LOOP["tail"])
check("the tailnet view is read-only and gives the reason", view["write"] is False and "SQUAD_TAILNET_LOOP_WRITE" in view["reason"], view)
reset()
code, got = hub.loop_switch(LOOP["tail"], {"action": "start"}, origin_ok=True)
check("a tailnet write is refused while tailnet.loop_write is off, with its reason",
      code == 403 and "SQUAD_TAILNET_LOOP_WRITE" in got.get("reason", "") and calls == [] and audit() == [], (code, got, calls))
check("the refusal rule names the tailnet read-only reason",
      hub.loop_refusal(LOOP["tail"], origin_ok=True, tn={}) == hub.LOOP_READ_ONLY, hub.loop_refusal(LOOP["tail"], origin_ok=True, tn={}))
tn["loop_write"] = True
reset()
code, got = hub.loop_switch(LOOP["tail"], {"action": "drain"}, origin_ok=True)
check("a tailnet write runs once tailnet.loop_write is on", code == 200 and calls == [["drain"]], (code, got, calls))
check("a tailnet change names its login and that it came from the tailnet",
      audit()[0]["who"] == "alice@example.com" and audit()[0]["from"] == "tailnet", audit())
check("a tailnet write with a foreign origin is forbidden", hub.loop_refusal(LOOP["tail"], origin_ok=False, tn=tn) == "forbidden")
check("a loopback write that is not from our own origin or not json is forbidden", hub.loop_refusal(LOOP["loopback"], origin_ok=False, tn={}) == "forbidden")
check("a tailnet login the profile does not allow is refused by the seam", hub.loop_refusal(hub.Access(False, "login not allowed", "", True), origin_ok=True, tn=tn) == "login not allowed")
print("done")
PY
)
echo "$out"
echo "$out" | grep -q "^FAIL" && fail=1
echo "$out" | grep -q "^done$" || { echo "FAIL the suite did not finish: the hub may not load"; fail=1; }
rm -rf "$T"
[ "$fail" = 0 ] && echo "hub loop switch: all ok" || echo "hub loop switch: FAILED"
exit "$fail"
