#!/bin/bash
# Regression test for stack.sh's native blocked-by relation: a body is a person-facing mirror, not a dispatch edge.
# usage: bash test-stack.sh [path/to/stack.sh]
src=${1:-$(dirname "$0")/stack.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/bin" "$T/co/runs/.hub"; cp "$src" "$T/s/stack.sh"; cp "$(dirname "$src")"/{startability.sh,ghcall.sh,ghread.py} "$T/s/"   # stack.sh reads the native relation through the GitHub door
printf 'export SG_REPO=x/y SG_CHECKOUT=%s SG_HUB_DIR=%s SG_BASE=main SG_STATE=%s/st\n' "$T/co" "$T/co/runs/.hub" "$T" > "$T/s/env.sh"
echo '{"items":[]}' > "$T/co/runs/.hub/hub-board.json"
# The body still names #1791, but only the native relation can block dispatch. #1800 has two open native blockers.
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *"i1792: issue(number: 1792)"*) echo '{"data":{"repository":{"i1792":{"number":1792,"state":"OPEN","body":"Fix the colour map.\n\n## Blocked by\n\n- #1791","labels":{"nodes":[]},"blockedBy":{"nodes":[]}}}}}';;
  *"i1793: issue(number: 1793)"*) echo '{"data":{"repository":{"i1793":{"number":1793,"state":"OPEN","body":"Fix the strip.","labels":{"nodes":[]},"blockedBy":{"nodes":[{"number":1791,"state":"CLOSED"}]}}}}}';;
  *"i1800: issue(number: 1800)"*) echo '{"data":{"repository":{"i1800":{"number":1800,"state":"OPEN","body":"Work.","labels":{"nodes":[]},"blockedBy":{"nodes":[{"number":1797,"state":"OPEN"},{"number":1798,"state":"OPEN"}]}}}}}';;
  *"i1801: issue(number: 1801)"*) echo '{"data":{"repository":{"i1801":{"number":1801,"state":"OPEN","body":"Work.\n\n## Blocked by\n\n- #1797","labels":{"nodes":[]},"blockedBy":{"nodes":[{"number":1791,"state":"CLOSED"}]}}}}}';;
  *"issues/1791 -q .state"*) echo closed;;
  *"issues/1797 -q .state"*|*"issues/1798 -q .state"*) echo open;;
  *"pulls?state=open"*) echo '[]';;
  *"check-runs"*) echo 'false';;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { bash "$T/s/stack.sh" "$1" 2>&1; }
r=$(run 1792)
case "$r" in *READY*) ok "#1792: a body-only blocker does not gate dispatch";; *) ko "#1792 did not read READY: '$r'";; esac
case "$r" in *"#1791"*) ok "#1792: the body-only blocker is reported by name";; *) ko "#1792 did not report #1791: '$r'";; esac
r=$(run 1793)
case "$r" in READY*) ok "#1793: every native blocker closed is ready";; *) ko "#1793 did not read READY: '$r'";; esac
r=$(run 1800)
case "$r" in *"2 open blockers: #1797 #1798"*) ok "#1800: every open native blocker gates dispatch";; *) ko "#1800 did not read its native blockers: '$r'";; esac
r=$(run 1801)
case "$r" in *READY*) ok "#1801: a body-only blocker does not override the native relation";; *) ko "#1801 did not read READY: '$r'";; esac
rm -rf "$T"; exit $fail
