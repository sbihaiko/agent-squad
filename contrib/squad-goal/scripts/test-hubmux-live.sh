#!/bin/bash
# seconds: 0.12
# Regression test for the hub index: it counts and lists the runs the hub sees working (no verdict, a dashboard that answers), never the finished runs the live-run list keeps on purpose (no network, no server). usage: bash test-hubmux-live.sh
here=$(cd "$(dirname "$0")" && pwd); export HUBMUX_SRC=$here/hubmux.py; T=$(mktemp -d); fail=0
mkdir -p "$T/profiles" "$T/state/alpha" "$T/co"
echo ': "${SG_REPO:=x/alpha}"; : "${SG_OWNER:=x}"; : "${SG_PROJECT:=3}"; : "${SG_CHECKOUT:='"$T/co"'}"' > "$T/profiles/alpha.sh"
for i in run-a run-b run-c run-done-1 run-done-2; do echo "$T/co/runs/$i"; done > "$T/state/alpha/active-runs.txt"
export SG_PROFILES_DIR="$T/profiles" SG_STATE_BASE="$T/state"
out=$(python3 - <<'PY' 2>&1
import importlib.util, os
spec = importlib.util.spec_from_file_location("hubmux", os.environ["HUBMUX_SRC"]); mux = importlib.util.module_from_spec(spec); spec.loader.exec_module(mux)
apps = mux.load_apps()
row = lambda i, st, kind="code": {"id": i, "state": st, "kind": kind, "live": True, "age": 5}
rows = [row("run-a", "running"), row("run-b", "stalled"), row("run-c", "starting"), row("run-done-1", "verified"), row("test-1", "running", "test")]
apps["alpha"]["mod"].runs = lambda: rows
got = mux.live(apps["alpha"]); ok = got == ["run-a", "run-b", "run-c"]
print(("ok   " if ok else "FAIL ") + f"live ids: {got}")
page = mux.root_page(apps, "t").decode(); ok = 'class="badge on">3 live</span>' in page and '<div class="runs">run-a run-b run-c</div>' in page and "run-done" not in page
print(("ok   " if ok else "FAIL ") + "index line: " + page.split("</a>", 1)[1].split("</li>")[0])
PY
)
echo "$out"; case "$out" in *FAIL*|*Error*) fail=1;; esac
rm -rf "$T"; exit $fail
