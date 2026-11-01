#!/bin/bash
# Guard for #923: the phone's Status panel holds the loop switch. It shows the state with who changed it and when; start,
# drain-stop and stop-now post to /api/loop; a stop-now asks first and its confirm names the live runs and gates it kills;
# the reason a tailnet request is read-only is shown; the change log is the hub's audit lines. The switch has no Save.
# Static: the markup and the script's contract with the hub route. No browser, no server.
# usage: bash test-hub-mobile-loop.sh [path/to/hub-mobile.html]
d=$(cd "$(dirname "$0")" && pwd); f=${1:-$d/hub-mobile.html}
python3 - "$f" <<'PY'
import re, sys
t = open(sys.argv[1], encoding="utf-8").read()
fail = 0
def check(label, ok):
    global fail
    print(("ok   " if ok else "FAIL ") + label)
    fail |= not ok
ids = ("loop-text", "loop-start", "loop-drain", "loop-now", "loop-reason", "loop-confirm", "loop-confirm-text", "loop-confirm-go", "loop-confirm-no", "loop-log", "loop-log-box")
check("the switch has its state, its three controls, its reason, its confirm and its change log", all(f'id={i}' in t or f'id="{i}"' in t for i in ids))
check("the state pill takes its class from the loop view", '$("loop").className = v.state' in t)
check("status() no longer draws the state from the read-only status line", "s.loop.on" not in t)
check("the switch reads the hub's loop route", 'call("/api/loop")' in t)
check("the switch writes to the hub's loop route with POST and a JSON body", re.search(r'fetch\("/api/loop", \{ method: "POST"', t) is not None and 'Content-Type": "application/json"' in t)
check("start, drain and stop-now post their own action", all(s in t for s in ('loopAct("start")', 'loopAct("drain")', 'loopAct("stop-now", { confirm: true })')))
check("stop-now opens a confirm before any write", re.search(r'\$\("loop-now"\)\.onclick = \(\) => \{[^}]*loop-confirm"\)\.hidden = false', t, re.S) is not None)
check("the confirm names the live runs and gates it kills", 'Stop now kills: ' in t and 'loopLive(loopNow)' in t)
check("the state line names who changed it and when, in the desktop's time format", 'changed by " + v.who + " at " + loopWhen(v.when)' in t)
check("the change log shows the time in the desktop's format, never the raw ISO stamp", 'li.textContent = loopWhen(e.at) + " "' in t and "e.at + \" \"" not in t)
check("a failed start is shown", "v.failed" in t and "e.failed" in t)
import shutil, subprocess
m = re.search(r"^const loopWhen = .*;$", t, re.M)
check("the phone has the desktop's loopWhen", m is not None)
if m and shutil.which("node"):
    r = subprocess.run(["node", "-e", m.group(0) + '\nprocess.stdout.write(loopWhen("2026-10-31T16:02:09-03:00")+"|"+loopWhen(""))'], capture_output=True, text=True, timeout=10)
    check("loopWhen reads 2026-10-31 16:02 from an ISO stamp", r.stdout == "2026-10-31 16:02|")
check("the reason line is drawn by loopReason", '$("loop-reason").textContent = loopReason(v)' in t)
m = re.search(r"^const loopReason = .*;$", t, re.M)
check("the phone has loopReason", m is not None)
if m and shutil.which("node"):
    js = m.group(0) + '\nprocess.stdout.write(loopReason({reason:"read-only from the tailnet",failed:"watchd did not start"})+"|"+loopReason({reason:"read-only from the tailnet",failed:""})+"|"+loopReason({reason:"",failed:"watchd did not start"}))'
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    a, b, c = (r.stdout.split("|") + ["", "", ""])[:3]
    check("a read-only view still shows the failure reason (#923 review 2)", "read-only from the tailnet" in a and "watchd did not start" in a)
    check("the read-only reason alone is shown alone", b == "read-only from the tailnet")
    check("the failure alone is shown on a writable view", "watchd did not start" in c and "read-only" not in c)
check("the change log lists the hub's audit lines", '$("loop-log").replaceChildren(...v.log.map' in t and 'e.from + "): " + e.action' in t)
check("the draining and starting states have their own colour", "#loop.draining #loop-text" in t and "#loop.starting #loop-text" in t)
block = t.split('<div id=loop>', 1)[1].split('</details>', 1)[0] if '<div id=loop>' in t else ""
check("the switch has no Save control", block and not re.search(r"\bsave\b", block, re.I))
check("the switch script has no Save control", not re.search(r'loop[^\n]*save|save[^\n]*loop', t, re.I))
sys.exit(fail)
PY
rc=$?
[ "$rc" = 0 ] && echo "hub mobile loop: all ok" || echo "hub mobile loop: FAILED"
exit "$rc"
