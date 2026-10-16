#!/bin/bash
# Regression test for the hub's killed runs: a run with no verdict whose process is gone must never count as live in the WORKERS header, whatever its age.
# A killed run writes no more events, so its record is idle for ever; without this it read `stalled` and held a footer slot for ever (no network, no server).
# usage: bash test-hub-killed.sh [path/to/hub.py]
d=$(cd "$(dirname "$0")" && pwd); export HUB_SRC=${1:-$d/hub.py}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
export SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T" SG_HUB_DIR="$T/hub" SG_STATE="$T/st" HOME="$T/home"; mkdir -p "$HOME"
out=$(python3 - <<'PY' 2>&1
import importlib.util, os
spec = importlib.util.spec_from_file_location("hub", os.environ["HUB_SRC"]); hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
def check(label, got, want): print(("ok   " if got == want else "FAIL ") + label + ("" if got == want else f": got {got!r}, want {want!r}"))

folder = "/runs/run-20260101-000000-abc123"
base = {"verdict": None, "reasons": [], "land": False, "wts": [folder + "/worktrees/child-a"], "agents": 2}
req = "CHILD - GitHub issue(s) 58 (bug; one PR):"
squad_ps = ["77 uv run --project /s --extra sdk squad start --target /co " + req + " fix it"]
other_ps = ["12 /usr/bin/python3 -m http.server", "13 /bin/zsh -l", "14 python3 -m squad.dashboard_server " + folder]
OLD = 3 * 3600

gone = lambda r=base, age=OLD, ps=other_ps, request=req: hub.run_gone(r, age, ps, folder, request)
check("a stalled run with no process is gone", gone(), True)
check("a run with a verdict is never gone", gone({**base, "verdict": "failed"}), False)
check("a run idle under the stall limit is not judged", gone(age=600), False)
check("its dashboard server alone is not a process of the run", gone(ps=other_ps), True)
check("a squad start process that carries its request keeps it alive", gone(ps=other_ps + squad_ps), False)
check("a squad start process of another request does not", gone(ps=other_ps + ["78 squad start --target /co CHILD - GitHub issue(s) 9 (bug; one PR): x"]), True)
check("a process working in the run's worktree keeps it alive", gone(ps=other_ps + ["90 claude --cwd " + folder + "/worktrees/child-a"]), False)
check("a run with no request.txt falls back to any squad start", gone(ps=other_ps + squad_ps, request=None), False)
check("a run with no request.txt and no squad start is gone", gone(request=None), True)

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
