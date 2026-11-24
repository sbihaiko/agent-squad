#!/bin/bash
# seconds: 1.80
# Regression test for #135: hub.py reads its state directory tolerantly -- $SG_STATE when the runner exports it, else the
# state directory the seam (address.sh) names under the runner's home -- so a runner that exports no SG_STATE imports the hub
# instead of dying with KeyError: 'SG_STATE' (no network, no server). usage: bash test-hub-state-default.sh [path/to/hub.py]
d=$(cd "$(dirname "$0")" && pwd); src=${1:-$d/hub.py}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/home" "$T/co" "$T/st" "$T/base"
cat >"$T/probe.py" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("hub", sys.argv[1]); hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
print(hub.STATE)
PY

# probe [SG_STATE] [SG_STATE_BASE] : load the hub in a runner's environment (no SG_STATE unless given), print its STATE.
probe() { ( cd "$T" && env -u SG_PROFILE -u SG_SHARED -u SG_STATE -u SG_STATE_BASE ${1:+SG_STATE="$1"} ${2:+SG_STATE_BASE="$2"} \
    SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT="$T/co" SG_HUB_DIR="$T/hub" HOME="$T/home" \
    python3 "$T/probe.py" "$src" ) 2>"$T/err"; }

out=$(probe "" ""); rc=$?
[ -n "$out" ] && ok "a runner without SG_STATE imports the hub and prints its STATE" \
  || ko "the import died without SG_STATE: $(tr '\n' ' ' <"$T/err" | cut -c1-160)"
case "$out" in "$T/home/"*) ok "the state directory falls under the runner's home";;
  *) ko "STATE: got '$out', wanted a path under $T/home";; esac
[ "$out" != "$T" ] && ok "the state directory is never the cwd the hub was started in" || ko "STATE fell back to the cwd"
[ "$rc" = 0 ] && ok "the import exits 0" || ko "exit code $rc"

out=$(probe "$T/st" ""); rc=$?
[ "$out" = "$T/st" ] && ok "an exported SG_STATE is used as it stands" || ko "SG_STATE set: got '$out'"
[ "$rc" = 0 ] && ok "an exported SG_STATE exits 0" || ko "exit code $rc"

out=$(probe "" "$T/base")
# #1226: with no profile the hub reads a directory under the seam's base, never the base itself, where the legacy
# active-runs.txt of a pre-profile loop still sits.
case "$out" in "$T/base/"?*) ok "with no SG_STATE the seam's own input (SG_STATE_BASE) names the parent directory";;
  *) ko "SG_STATE_BASE: got '$out', wanted a directory under $T/base";; esac
[ "$out" != "$T/base" ] && ok "with no profile the hub never reads the legacy base itself" || ko "STATE is the legacy base"
exit $fail
