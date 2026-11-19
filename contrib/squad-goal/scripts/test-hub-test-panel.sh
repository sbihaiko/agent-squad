#!/bin/bash
# seconds: 0.09
# Regression test for #1406: a live test.sh run's /test/<id> panel shows its issue and log, even before the log file exists (no network). usage: bash test-hub-test-panel.sh [path/to/hub.py]
src=$(cd "$(dirname "${1:-$0}")" && pwd)/$(basename "${1:-hub.py}"); [ -n "$1" ] || src=$(cd "$(dirname "$0")" && pwd)/hub.py; export HUB_SRC=$src; T=$(mktemp -d); fail=0
export SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T" SG_HUB_DIR="$T/hub" SG_STATE="$T/st"
mkdir -p "$T/st/tests"
out=$(python3 - <<'PY' 2>&1
import importlib.util, os, json, threading, urllib.request, pathlib
from http.server import ThreadingHTTPServer
spec = importlib.util.spec_from_file_location("hub", os.environ["HUB_SRC"]); hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
T = pathlib.Path(os.environ["SG_STATE"]) / "tests"
meta = {"issue": 1400, "cmd": "make test", "dir": "/wt", "t0": 1.0, "state": "running"}
(T / "test-1400-a.json").write_text(json.dumps(meta)); (T / "test-1400-a.log").write_text("suite output line")
(T / "test-1400-b.json").write_text(json.dumps(meta))   # running, test.sh has not created the log yet
srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.H); threading.Thread(target=srv.serve_forever, daemon=True).start()
get = lambda n: urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}/test/{n}").read().decode()
def check(label, ok): print(("ok   " if ok else "FAIL ") + label)
a, b, c = get("test-1400-a"), get("test-1400-b"), get("test-nope")
check("running test with a log: issue and log", "test #1400" in a and "suite output line" in a and "#None" not in a)
check("running test before its log exists: issue shown, not 'no such test'", "test #1400" in b and "no such test" not in b and "#None" not in b)
check("unknown test still says no such test", "no such test" in c)
PY
)
echo "$out"; case "$out" in *FAIL*|*Error*|*Traceback*) fail=1;; esac
rm -rf "$T"; exit $fail
