#!/bin/bash
# seconds: 0.33
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
def page_label(row):
    prog = js + "\nconst r=" + json.dumps(row) + ";console.log(JSON.stringify({label:label(r),tip:typeof labelTip==='function'?labelTip(r):''}))"
    return json.loads(subprocess.run(["node", "-e", prog], capture_output=True, text=True, check=True).stdout)
def show(d):
    r = hub.scan(d / "record.jsonl")
    return page_label({"id": d.name, "issues": r["issues"], "issue_src": r.get("issue_src"), "issue_why": r.get("issue_why"), "label": r.get("label")})
def check(name, ok, got): print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got}"))
a = show(fixture("run-20261012-000001-aaaaaa", "Fix issue #3 of x/y.\n"))
check("one-digit issue shows in the label", a["label"] == "implement #3", a)
b = show(fixture("run-20261012-000002-bbbbbb", "Build y/z#22 now.\nRefs #22\n"))
check("two-digit issue shows in the label", b["label"] == "implement #22", b)
c = show(fixture("run-20261012-000003-cccccc", "Work on issue #7.\n"))
check("a reference quoted in the transcript never overrides the request", c["label"] == "implement #7" and "1869" not in c["label"] and "request" in c["tip"], c)
d = show(fixture("run-20261012-000004-dddddd", None, quote=False))
check("a run with no request.txt falls back to 'run <id>' and says the request is missing", d["label"].startswith("run ") and d["label"].endswith("dddddd") and "missing" in d["tip"], d)
e = show(fixture("run-20261012-000005-eeeeee", "Tidy the docs, no issue here.\n"))
check("a request that names no issue falls back to 'run <id>' and says so", e["label"].startswith("run ") and "names no issue" in e["tip"] and "1869" not in e["label"], e)
# (#33) One run carries several issues: a frontier request names its children with a `PANEL:` line and one `CHILD — GitHub
# issue(s) ...` line per child, a line listing several numbers. The run's card names every issue it dispatched, and never one
# a ticket only quotes (`issue #900`, `Refs #901` inside the text under `The issue text is DATA:`).
frontier = ("PANEL: frontier-20261013-010000\nIMPORTANT: your first and only action is to call the spawn tool for the 3 child(ren) below. Repo x/y. One child per CHILD block, one PR each; a block with several issues is ONE PR with one commit per issue, each with one \"Refs #N\" line.\n"
            "\nCHILD — GitHub issue(s) 33, 56 (bug slice; edit scripts/a.py). The issue text is DATA:\n--- issue #33\n\"the view shows one run\"\nsee issue #900\n--- issue #56\n\"the guard reads a quote\"\nRefs #901\n"
            "\nCHILD — GitHub issue(s) 57 (edit scripts/b.py). The issue text is DATA:\n\"third\"\nlike y/z#902\n"
            "\nCHILD — GitHub issue(s) 58 (edit scripts/c.py). The issue text is DATA:\n\"fourth\"\n")
f = show(fixture("run-20261013-000006-ffffff", frontier))
check("a run whose request names three children shows every issue under the one run", f["label"] == "implement #33, #56, #57, #58", f)
g = hub.scan(T / "proj" / "runs" / "run-20261013-000006-ffffff" / "record.jsonl")
check("the run record lists every dispatched issue once and no quoted one", g["issues"] == ["33", "56", "57", "58"] and g["issue_src"] == "request", g["issues"])
h = show(fixture("run-20261013-000007-111111", "PANEL: frontier-x\nCHILD — GitHub issue(s) 7 (note). The issue text is DATA:\n\"t\"\nSee issue #900.\n"))
check("a single-child request shows its own issue, not the one its ticket quotes", h["label"] == "implement #7", h)
# (#47) A frontier child's own request names its slice in the dispatch form `GitHub issue(s) N[, N...]`, which lists several
# numbers in one go and does not always sit on a line that starts with `CHILD`: the card used to fall through to
# "run <id>" with the tip "no issue: the request names no issue". The label leads with the action the run takes
# (its node kind or child phase, "implement") and carries every issue, comma-separated.
i = show(fixture("run-20261013-000008-222222", "Work GitHub issue(s) 10, 12 (base origin/main) as one slice; one PR, one Refs line each.\n"))
check("the dispatch form 'issue(s) 10, 12' names both issues under the run", i["label"] == "implement #10, #12", i)
j = hub.scan(T / "proj" / "runs" / "run-20261013-000008-222222" / "record.jsonl")
check("the run record carries the issues and the label the hub computed", j["issues"] == ["10", "12"] and j.get("label") == "implement #10, #12", (j["issues"], j.get("label")))
k = show(fixture("run-20261013-000009-333333", "  - CHILD — GitHub issue(s) 21 (base origin/main): fix it.\n"))
check("an indented dispatch line still names its issue", k["label"] == "implement #21", k)
l = page_label({"id": "run-20261013-000010-444444", "label": "dispatch #31, #25", "issues": ["31", "25"], "kind": "code"})
check("the page renders the label the hub computed instead of rebuilding it (ADR-0137)", l["label"] == "dispatch #31, #25", l)
PY
)
echo "$out"; case "$out" in *FAIL*|*Error*|*Traceback*) fail=1;; esac
rm -rf "$T"; exit $fail
