#!/bin/bash
# seconds: 0.07
# Regression test for the hub's run listing: a failed / unverified / needs-human run whose every issue is closed stops being open business (no network, no server). usage: bash test-hub-demote.sh [path/to/hub.py]
src=$(cd "$(dirname "${1:-$0}")" && pwd)/$(basename "${1:-hub.py}"); [ -n "$1" ] || src=$(cd "$(dirname "$0")" && pwd)/hub.py; export HUB_SRC=$src; T=$(mktemp -d); fail=0
export SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T" SG_HUB_DIR="$T/hub" SG_STATE="$T/st"
out=$(python3 - <<'PY' 2>&1
import importlib.util, os
spec = importlib.util.spec_from_file_location("hub", os.environ["HUB_SRC"]); hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
closed = {5, 6}
cases = [("needs-human closed", "needs-human", {"land": True, "issues": ["5", "6"]}, "verified"),
         ("needs-human one open", "needs-human", {"land": True, "issues": ["5", "7"]}, "needs-human"),
         ("failed closed", "failed", {"land": True, "issues": ["5"]}, "verified"),
         ("needs-human closed, nothing landed", "needs-human", {"land": False, "issues": ["5"]}, "verified")]
for label, st, r, want in cases:
    got = hub.demoted(st, r, closed); print(("ok   " if got == want else "FAIL ") + label + ("" if got == want else f": got {got}, want {want}"))
PY
)
echo "$out"; case "$out" in *FAIL*|*Error*) fail=1;; esac
rm -rf "$T"; exit $fail
