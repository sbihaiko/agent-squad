#!/bin/bash
# Regression test for #22: the hub labels a run by the issue its own request names (runs/<run>/request.txt), for any digit count, and says why when it knows none (no network, no server). usage: bash test-hub-run-issue.sh [path/to/hub.py]
src=$(cd "$(dirname "${1:-$0}")" && pwd)/$(basename "${1:-hub.py}"); [ -n "$1" ] || src=$(cd "$(dirname "$0")" && pwd)/hub.py; export HUB_SRC=$src; T=$(mktemp -d); fail=0
export SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T" SG_HUB_DIR="$T/hub" SG_STATE="$T/st" T
out=$(python3 - <<'PY' 2>&1
import importlib.util, os, json, pathlib, re, subprocess
spec = importlib.util.spec_from_file_location("hub", os.environ["HUB_SRC"]); hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
T = pathlib.Path(os.environ["T"])
# the label code the page runs: the JS between `const label=` and `const sub=`
js = re.search(r"const label=.*?(?=\nconst sub=)", pathlib.Path(os.environ["HUB_SRC"]).read_text(), re.S).group(0)
quoted = json.dumps({"ts": 1.0, "event": "prompt", "text": "see the old fix, Refs #1869, issue #1869"})   # a reference quoted inside the transcript
def fixture(name, request, quote=True):
    d = T / "proj" / "runs" / name; d.mkdir(parents=True)
    (d / "record.jsonl").write_text((quoted + "\n" if quote else "") + json.dumps({"ts": 2.0, "event": "run_start"}) + "\n")
    if request is not None: (d / "request.txt").write_text(request)
    return d
def show(d):
    r = hub.scan(d / "record.jsonl")
    row = {"id": d.name, "issues": r["issues"], "issue_src": r.get("issue_src"), "issue_why": r.get("issue_why")}
    prog = js + "\nconst r=" + json.dumps(row) + ";console.log(JSON.stringify({label:label(r),tip:typeof labelTip==='function'?labelTip(r):''}))"
    return json.loads(subprocess.run(["node", "-e", prog], capture_output=True, text=True, check=True).stdout)
def check(name, ok, got): print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got}"))
a = show(fixture("run-20261012-000001-aaaaaa", "Fix issue #3 of x/y.\n"))
check("one-digit issue shows in the label", a["label"] == "build #3", a)
b = show(fixture("run-20261012-000002-bbbbbb", "Build y/z#22 now.\nRefs #22\n"))
check("two-digit issue shows in the label", b["label"] == "build #22", b)
c = show(fixture("run-20261012-000003-cccccc", "Work on issue #7.\n"))
check("a reference quoted in the transcript never overrides the request", c["label"] == "build #7" and "1869" not in c["label"] and "request" in c["tip"], c)
d = show(fixture("run-20261012-000004-dddddd", None, quote=False))
check("a run with no request.txt falls back to 'run <id>' and says the request is missing", d["label"].startswith("run ") and d["label"].endswith("dddddd") and "missing" in d["tip"], d)
e = show(fixture("run-20261012-000005-eeeeee", "Tidy the docs, no issue here.\n"))
check("a request that names no issue falls back to 'run <id>' and says so", e["label"].startswith("run ") and "names no issue" in e["tip"] and "1869" not in e["label"], e)
PY
)
echo "$out"; case "$out" in *FAIL*|*Error*|*Traceback*) fail=1;; esac
rm -rf "$T"; exit $fail
