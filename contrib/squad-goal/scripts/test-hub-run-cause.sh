#!/bin/bash
# A run the janitor took over or the wave age cap retired reads as its own hub state, and only when its record names that cause
# and holds no measured red (ADR-0029, ADR-0164). The raw verdict, exit code and reasons stay as recorded, whatever the state.
# usage: bash test-hub-run-cause.sh [path/to/hub.py]
d=$(cd "$(dirname "$0")" && pwd); export HUB_SRC=${1:-$d/hub.py}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
export SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T" SG_HUB_DIR="$T/hub" SG_STATE="$T/st" HOME="$T/home"; mkdir -p "$HOME" "$T/hub" "$T/st"
out=$(python3 - <<'PY' 2>&1
import importlib.util, json, os, pathlib
spec = importlib.util.spec_from_file_location("hub", os.environ["HUB_SRC"]); hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
def check(label, got, want): print(("ok   " if got == want else "FAIL ") + label + ("" if got == want else f": got {got!r}, want {want!r}"))
root = pathlib.Path(os.environ["SG_CHECKOUT"])

def run(name, events):
    folder = root / name; folder.mkdir(parents=True, exist_ok=True)
    (folder / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return hub.scan(folder / "record.jsonl")

def verdict(reasons):
    return {"event": "verdict", "ts": 3, "verdict": "failed", "exit_code": 1, "reasons": reasons, "remains": [], "folded": []}

START = {"event": "run_start", "run_id": "r", "ts": 1}
GREEN = {"event": "gate_result", "ts": 2, "node": "n", "entry": 1, "item": None, "sha": "s", "dirt": False, "suite": "fast", "outcome": "pass", "source": "gate"}
RED = {"event": "gate_result", "ts": 2, "node": "n", "entry": 1, "item": None, "sha": "s", "dirt": False, "suite": "fast", "outcome": "fail", "source": "gate"}
TAKEN = "killed/taken-over"
AGE_CAP = "killed/age-cap-requeue"

taken = run("taken", [START, verdict([TAKEN])])
check("a takeover cause shows the taken over state", hub.state_of(taken, 0), "taken over")
check("the taken over run keeps its raw verdict", taken["verdict"], "failed")
check("the taken over run keeps its exit code", taken["exit_code"], 1)
check("the taken over run keeps its reasons", taken["reasons"], [TAKEN])
check("a passed gate does not stop a takeover state", hub.state_of(run("taken-pass", [START, GREEN, verdict([TAKEN])]), 0), "taken over")

capped = run("capped", [START, verdict([AGE_CAP])])
check("an age cap cause shows the retired state", hub.state_of(capped, 0), "retired (age cap)")
check("the retired run keeps its raw verdict and reasons", (capped["verdict"], capped["exit_code"], capped["reasons"]), ("failed", 1, [AGE_CAP]))

plain = run("plain", [START, verdict(["killed/no-process"])])
check("a run with no recorded cause keeps its recorded verdict", hub.state_of(plain, 0), "failed")

red = run("red-takeover", [START, RED, verdict([TAKEN])])
check("a measured red keeps the failed state after a takeover", hub.state_of(red, 0), "failed")
check("a measured red keeps its reasons after a takeover", red["reasons"], [TAKEN])
red_cap = run("red-capped", [START, RED, verdict([AGE_CAP])])
check("a measured red keeps the failed state after an age cap", hub.state_of(red_cap, 0), "failed")

states = {r["state"]: r for r in hub.RUN_STATES}
check("the taken over state is in the served table", "taken over" in states, True)
check("the retired state is in the served table", "retired (age cap)" in states, True)
check("the taken over badge reads its state", hub.badge_of("taken over")["text"], "taken over")
check("the retired badge reads its state", hub.badge_of("retired (age cap)")["text"], "retired (age cap)")
PY
)
echo "$out"; case "$out" in *FAIL*|*Error*|*Traceback*) fail=1;; esac
exit $fail
