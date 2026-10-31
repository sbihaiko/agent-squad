#!/bin/bash
# Guard for #923: the loop switch on the hub. Start, drain-stop and stop-now write through one route; each change writes one
# audit line (who, when, from where); a stop-now is refused without its confirm and names what it kills; a tailnet request is
# read-only unless tailnet.loop_write (SQUAD_TAILNET_LOOP_WRITE) is on, and the refusal gives its reason. The state reads
# alive, draining, starting or off from one place. The watchd calls are stubbed: no loop runs. No server.
# usage: bash test-hub-loop-switch.sh [path/to/hub.py]
src=$(cd "$(dirname "${1:-$0}")" && pwd)/$(basename "${1:-hub.py}"); [ -n "$1" ] || src=$(cd "$(dirname "$0")" && pwd)/hub.py; export HUB_SRC=$src
T=$(mktemp -d); fail=0
export SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T" SG_HUB_DIR="$T/hub" SG_STATE="$T/st"
out=$(python3 - <<'PY' 2>&1
import datetime as dt, importlib.util, json, os, pathlib, subprocess
spec = importlib.util.spec_from_file_location("hub", os.environ["HUB_SRC"]); hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
STATE = pathlib.Path(os.environ["SG_STATE"]); STATE.mkdir(parents=True, exist_ok=True)
calls = []
def watchd(*args):   # the marks watchd.sh leaves: a drain pauses dispatch and marks it, a stop clears the mark
    calls.append(list(args))
    if args == ("drain",): (STATE / "auto" / "drain").write_text(""); (STATE / "auto" / "off").write_text("")
    if args == ("stop",): (STATE / "auto" / "drain").unlink(missing_ok=True)
    return 0
hub.loop_watchd = watchd
tn = {"host": "box.tail1.ts.net", "allow": ["alice@example.com"]}
hub.tailnet_block = lambda: dict(tn)
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
code, got = hub.loop_switch(LOOP["loopback"], {"action": "start"}, origin_ok=True)
check("start from loopback runs the watchd start", code == 200 and calls == [["start"]], (code, calls))
check("start clears the drain and pause markers", not (STATE / "auto" / "off").exists() and not (STATE / "auto" / "drain").exists())
check("a start writes exactly one audit line naming who, when and from where",
      [(a["action"], a["who"], a["from"]) for a in audit()] == [("start", "owner", "loopback")] and audit()[0].get("at"), audit())
check("a start that just began reads starting", hub.loop_view(LOOP["loopback"])["state"] == "starting", hub.loop_view(LOOP["loopback"]))

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
check("stop-now runs the watchd stop", code == 200 and calls == [["stop"]], (code, calls))
check("stop-now answers the live runs and gates it killed", got.get("killed", {}).get("runs") == [str(live_run)] and "gates" in got.get("killed", {}), got)
check("stop-now writes one audit line that names what it killed",
      len(audit()) == 1 and audit()[0]["action"] == "stop-now" and audit()[0]["killed"]["runs"] == [str(live_run)], audit())

reset(dead_pid())
code, got = hub.loop_switch(LOOP["loopback"], {"action": "drain"}, origin_ok=True)
check("drain on a stopped loop is refused and writes nothing", code == 409 and calls == [] and audit() == [], (code, calls, got))

reset()
check("a live loop with no drain marker reads on", hub.loop_view(LOOP["loopback"])["state"] == "on", hub.loop_view(LOOP["loopback"]))
(STATE / "auto" / "drain").write_text("")
reset_state = hub.loop_view(LOOP["loopback"])["state"]
check("a live loop with the drain marker reads draining", reset_state == "draining", reset_state)
(STATE / "auto" / "drain").unlink()

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
