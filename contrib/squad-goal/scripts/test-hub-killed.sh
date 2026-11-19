#!/bin/bash
# seconds: 0.06
# Regression test for the hub's killed runs: a run with no verdict whose own process is gone must never count as live in the WORKERS header, whatever its age.
# A killed run writes no more events, so its record is idle for ever; without this it read `stalled` and held a footer slot for ever (no network, no server).
# The run's own process is the pid of its last call (#1226): a process that only names the run folder never keeps it live.
# usage: bash test-hub-killed.sh [path/to/hub.py]
d=$(cd "$(dirname "$0")" && pwd); export HUB_SRC=${1:-$d/hub.py}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
export SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T" SG_HUB_DIR="$T/hub" SG_STATE="$T/st" HOME="$T/home"; mkdir -p "$HOME"
out=$(python3 - <<'PY' 2>&1
import importlib.util, os
spec = importlib.util.spec_from_file_location("hub", os.environ["HUB_SRC"]); hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
def check(label, got, want): print(("ok   " if got == want else "FAIL ") + label + ("" if got == want else f": got {got!r}, want {want!r}"))

CALL = 1000.0   # the run's last call started here; its engine is pid 77
base = {"verdict": None, "reasons": [], "pid": 77, "pid_at": CALL, "agents": 2}
others = {12: 10.0, 13: 10.0, 14: 10.0}   # an http server, a shell, the run's own dashboard server
OLD = 3 * 3600

gone = lambda r=base, age=OLD, procs=others: hub.run_gone(r, age, procs)
check("a stalled run with no process is gone", gone(), True)
check("a run with a verdict is never gone", gone({**base, "verdict": "failed"}), False)
check("a young run with no process is gone too: no grace for a dead run", gone(age=60), True)
check("its dashboard server alone is not a process of the run", gone(procs=others), True)
check("its own process keeps it alive, whatever its age", gone(procs={**others, 77: CALL - 5}), False)
check("a reused pid that started after the call does not", gone(procs={**others, 77: CALL + 600}), True)
check("a run with no call yet is a launch inside its window", gone({**base, "pid": None, "pid_at": None}, age=60), False)
check("a run with no call past its window is gone", gone({**base, "pid": None, "pid_at": None}, age=hub.RUN_LAUNCH_GRACE + 1), True)

rows = [{"id": "live", "state": "running", "live": True, "age": 5, "agents": 1},
        {"id": "quiet", "state": "stalled", "live": True, "age": 2000, "agents": 3},
        {"id": "killed", "state": "stalled", "live": False, "age": OLD, "agents": 2}]
check("the killed run is not in the running group", [hub.group_of(r) for r in rows], ["running", "running", "4h"])
b = hub.gone_badge(hub.badge_of("stalled"))
check("the badge says why it is not live", b["text"], "stalled · no process")
PY
)
echo "$out"; case "$out" in *FAIL*|*Error*) fail=1;; esac
exit $fail
