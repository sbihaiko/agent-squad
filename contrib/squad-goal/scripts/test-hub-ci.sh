#!/bin/bash
# seconds: 0.08
# Regression test: a completed GitHub job with no steps is an account stop, not a failed branch.
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
cat >"$T/probe.py" <<'PY'
import importlib.util
import json
import os
import pathlib

spec = importlib.util.spec_from_file_location("hub", pathlib.Path(os.environ["HUB"]))
hub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hub)
hub.gh_json = lambda path: (
    {"status": "completed", "steps": []} if "/actions/jobs/10" in path else
    {"status": "completed", "conclusion": "failure", "output": {"title": "The job was not started because recent account payments have failed"}} if "/check-runs/10" in path else None
)
row = {"n": 41, "h": "123456789", "d": False, "facts": True, "ms": "clean", "ci": ["failure"], "checks": [{"id": 10, "status": "completed", "conclusion": "failure"}], "r": "x/y"}
print(json.dumps(hub.pr_state(row)))
PY
out=$(SG_REPO=x/y SG_OWNER=x SG_PROJECT=1 SG_CHECKOUT="$T" SG_HUB_DIR="$T/hub" SG_STATE="$T/state" HUB="$d/hub.py" python3 "$T/probe.py")
case "$out" in *'CI cannot run: account'*'account payments'*) ok "the hub names a no-step job as an account stop";; *) ko "the hub reads the account stop as a broken branch: $out";; esac
exit $fail
