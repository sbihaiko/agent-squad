#!/bin/bash
# Regression test for stack.sh's native blocked-by relation: a body is a person-facing mirror, not a dispatch edge.
# The answer is READY or WAIT: a work branch is the unit of work, so an open blocker whose work has not landed waits (#361).
# usage: bash test-stack.sh [path/to/stack.sh]
src=${1:-$(dirname "$0")/stack.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/bin" "$T/co/runs/.hub"; cp "$src" "$T/s/stack.sh"; cp "$(dirname "$src")"/{ghcall.sh,ghread.py} "$T/s/"   # stack.sh reads the native relation through the GitHub door
printf 'export SG_REPO=x/y SG_CHECKOUT=%s SG_HUB_DIR=%s SG_BASE=main SG_STATE=%s/st\n' "$T/co" "$T/co/runs/.hub" "$T" > "$T/s/env.sh"
echo '{"items":[{"n":1796,"st":"Code Complete","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"   # #1796: a card in Code Complete with no work branch landed
# The body still names #1791, but only the native relation can block dispatch. #1800 has two open native blockers.
cat > "$T/bin/gh" <<'J'
#!/bin/bash
[ -n "${GHLOG:-}" ] && echo "$*" >> "$GHLOG"
case "$*" in
  *"i1792: issue(number: 1792)"*) echo '{"data":{"repository":{"i1792":{"number":1792,"state":"OPEN","body":"Fix the colour map.\n\n## Blocked by\n\n- #1791","labels":{"nodes":[]},"blockedBy":{"nodes":[]}}}}}';;
  *"i1793: issue(number: 1793)"*) echo '{"data":{"repository":{"i1793":{"number":1793,"state":"OPEN","body":"Fix the strip.","labels":{"nodes":[]},"blockedBy":{"nodes":[{"number":1791,"state":"CLOSED"}]}}}}}';;
  *"i1800: issue(number: 1800)"*) echo '{"data":{"repository":{"i1800":{"number":1800,"state":"OPEN","body":"Work.","labels":{"nodes":[]},"blockedBy":{"nodes":[{"number":1797,"state":"OPEN"},{"number":1798,"state":"OPEN"}]}}}}}';;
  *"i1801: issue(number: 1801)"*) echo '{"data":{"repository":{"i1801":{"number":1801,"state":"OPEN","body":"Work.\n\n## Blocked by\n\n- #1797","labels":{"nodes":[]},"blockedBy":{"nodes":[{"number":1791,"state":"CLOSED"}]}}}}}';;
  *"i1802: issue(number: 1802)"*) echo '{"data":{"repository":{"i1802":{"number":1802,"state":"OPEN","body":"Work.\n\n## Blocked by\n\n- #1797\n- #1798","labels":{"nodes":[]},"blockedBy":{"nodes":[{"number":1797,"state":"OPEN"},{"number":1798,"state":"OPEN"}]}}}}}';;
  *"i1803: issue(number: 1803)"*) echo '{"data":{"repository":{"i1803":{"number":1803,"state":"OPEN","body":"Work.","labels":{"nodes":[]}}}}}';;
  *"i1804: issue(number: 1804)"*) echo '{"data":{"repository":{"i1804":null}}}';;
  *"i1805: issue(number: 1805)"*) echo '{"data":{"repository":{"i1805":{"number":1805,"state":"OPEN","body":"Work.","labels":{"nodes":[]},"blockedBy":{"nodes":[{"number":1799}]}}}}}';;   # no state beside the edge: it is read alone
  *"issues/1799 -q .state"*) echo 'gh: API rate limit exceeded' >&2; exit 1;;
  *"i1806: issue(number: 1806)"*) echo '{"data":{"repository":{"i1806":{"number":1806,"state":"OPEN","body":"Work.","labels":{"nodes":[]},"blockedBy":{"nodes":[{"number":1796,"state":"OPEN"}]}}}}}';;
  *"issues/1796 -q .state"*) echo open;;
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
r=$(run 1802)
case "$r" in *"STACK NOTE"*) ko "#1802: a body blocker that is also native was reported as body-only: '$r'";; *) ok "#1802: a body blocker the native relation carries is not reported";; esac
r=$(run 1803)
case "$r" in WAIT*) ok "#1803: an answer with no blocked-by relation is not read as no blockers";; *) ko "#1803 did not WAIT: '$r'";; esac
r=$(run 1804)
case "$r" in WAIT*) ok "#1804: an issue the answer does not carry is not read as no blockers";; *) ko "#1804 did not WAIT: '$r'";; esac
r=$(run 1805)
case "$r" in *WAIT*"#1799"*) ok "#1805: a blocker whose state cannot be read gates dispatch";; *) ko "#1805 did not WAIT on #1799: '$r'";; esac
r=$(run 1806)
case "$r" in *WAIT*"#1796"*) ok "#1806: a Code Complete blocker with no open PR gates dispatch";; *) ko "#1806 did not WAIT on #1796: '$r'";; esac
# #383: the frontier hands over the issue's entry of its open-issue list. The blocker's state beside the edge decides
# (#1797 reads open alone, closed in the entry), and no GitHub request is sent.
printf '{"number":1810,"body":"Work.","blocked_by":[1797],"blocked_by_read":true,"blockers":[{"number":1797,"state":"closed"}]}' > "$T/e1810.json"; printf '[]' > "$T/pulls.json"; : > "$T/gh.log"
r=$(GHLOG=$T/gh.log SG_STACK_ISSUE=$T/e1810.json run 1810)
case "$r" in READY*) ok "#1810: the entry's closed blocker state decides";; *) ko "#1810 did not read READY from its entry: '$r'";; esac
[ ! -s "$T/gh.log" ] && ok "#1810: a handed-over entry cost no GitHub request" || ko "#1810 sent requests: $(cat "$T/gh.log")"
printf '{"number":1811,"body":"Work.","blocked_by":[1797],"blocked_by_read":true,"blockers":[{"number":1797,"state":"open"}]}' > "$T/e1811.json"
r=$(SG_STACK_ISSUE=$T/e1811.json run 1811)
case "$r" in *"WAIT blocker #1797 is open: its work is not in main yet"*) ok "#1811: an open blocker in the entry gates dispatch";; *) ko "#1811 did not WAIT on #1797: '$r'";; esac
printf '{"number":1812,"body":"Work.","blocked_by":[1813],"blocked_by_read":true,"blockers":[{"number":1813,"state":"open"}]}' > "$T/e1812.json"
echo '{"items":[{"n":1813,"st":"Testing","s":"open"}]}' > "$T/co/runs/.hub/hub-board.json"
r=$(SG_STACK_ISSUE=$T/e1812.json run 1812)
case "$r" in *"WAIT blocker #1813 is open"*) ok "#1812: a blocker in no column the board carries gates dispatch";; *) ko "#1812 read the removed column as a satisfied blocker: '$r'";; esac
rm -rf "$T"; exit $fail
