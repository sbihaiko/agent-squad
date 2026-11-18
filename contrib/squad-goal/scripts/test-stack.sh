#!/bin/bash
# Regression test for stack.sh's blocker gate: the native blocked-by relation is the fact, and a blocker named only in the
# body's "## Blocked by" section still holds a ticket while it reads open (#1758), with the note naming the missing relation.
# The answer is READY or WAIT: a work branch is the unit of work, so an open blocker whose work has not landed waits (#361).
# usage: bash test-stack.sh [path/to/stack.sh]
src=${1:-$(dirname "$0")/stack.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/bin" "$T/co/runs/.hub"; cp "$src" "$T/s/stack.sh"; cp "$(dirname "$src")"/{ghcall.sh,drainpolicy.py,ghread.py,deadlock.py,revformat.py,board_columns.py,board-definition.json} "$T/s/"   # stack.sh reads the native relation through the GitHub door, the review of a blocker's tip through revformat.py, and the Landing columns from the board definition (#2260)
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
  *"i1807: issue(number: 1807)"*) echo '{"data":{"repository":{"i1807":{"number":1807,"state":"OPEN","body":"Work.\n\n## Blocked by\n\n- #1799","labels":{"nodes":[]},"blockedBy":{"nodes":[]}}}}}';;   # a body-only blocker whose state cannot be read
  *"i1805: issue(number: 1805)"*) echo '{"data":{"repository":{"i1805":{"number":1805,"state":"OPEN","body":"Work.","labels":{"nodes":[]},"blockedBy":{"nodes":[{"number":1799}]}}}}}';;   # no state beside the edge: it is read alone
  *"i1808: issue(number: 1808)"*) echo '{"data":{"repository":{"i1808":{"number":1808,"state":"OPEN","body":"Work.\n\n## Blocked by\n\n- #1791","labels":{"nodes":[]},"blockedBy":{"nodes":[]}}}}}';;
  *"i1791: issue(number: 1791)"*) echo '{"data":{"repository":{"i1791":{"number":1791,"state":"CLOSED"}}}}';;   # a body-only blocker that reads closed (#1808)
  *"i1797: issue(number: 1797)"*) echo '{"data":{"repository":{"i1797":{"number":1797,"state":"OPEN"}}}}';;
  *"i1799: issue(number: 1799)"*) echo 'gh: API rate limit exceeded' >&2; exit 1;;
  *"issues/1799 -q .state"*) echo 'gh: API rate limit exceeded' >&2; exit 1;;
  *"i1806: issue(number: 1806)"*) echo '{"data":{"repository":{"i1806":{"number":1806,"state":"OPEN","body":"Work.","labels":{"nodes":[]},"blockedBy":{"nodes":[{"number":1796,"state":"OPEN"}]}}}}}';;
  *"issues/1796 -q .state"*) echo open;;
  *"issues/1791 -q .state"*) echo closed;;
  *"issues/1797 -q .state"*|*"issues/1798 -q .state"*) echo open;;
  # #2260: a dependent whose blocker sits in Landing with an approved tip starts; the blocker's tip is read by branch.
  *"i1820: issue(number: 1820)"*) echo '{"data":{"repository":{"i1820":{"number":1820,"state":"OPEN","body":"Work.","labels":{"nodes":[]},"blockedBy":{"nodes":[{"number":1821,"state":"OPEN"}]}}}}}';;
  *"i1822: issue(number: 1822)"*) echo '{"data":{"repository":{"i1822":{"number":1822,"state":"OPEN","body":"Work.","labels":{"nodes":[]},"blockedBy":{"nodes":[{"number":1823,"state":"OPEN"}]}}}}}';;
  *"i1824: issue(number: 1824)"*) echo '{"data":{"repository":{"i1824":{"number":1824,"state":"OPEN","body":"Work.","labels":{"nodes":[]},"blockedBy":{"nodes":[{"number":1825,"state":"OPEN"}]}}}}}';;
  *"i1826: issue(number: 1826)"*) echo '{"data":{"repository":{"i1826":{"number":1826,"state":"OPEN","body":"Work.","labels":{"nodes":[]},"blockedBy":{"nodes":[{"number":1827,"state":"OPEN"}]}}}}}';;
  *"i1828: issue(number: 1828)"*) echo '{"data":{"repository":{"i1828":{"number":1828,"state":"OPEN","body":"Work.","labels":{"nodes":[]},"blockedBy":{"nodes":[{"number":1829,"state":"OPEN"}]}}}}}';;
  *"i1830: issue(number: 1830)"*) echo '{"data":{"repository":{"i1830":{"number":1830,"state":"OPEN","body":"Work.","labels":{"nodes":[]},"blockedBy":{"nodes":[{"number":1831,"state":"OPEN"}]}}}}}';;
  *"i1832: issue(number: 1832)"*) echo '{"data":{"repository":{"i1832":{"number":1832,"state":"OPEN","body":"Work.","labels":{"nodes":[]},"blockedBy":{"nodes":[{"number":1833,"state":"OPEN"}]}}}}}';;
  *"commits/gui/1821"*) echo '{"sha":"aaaa1111bbbb2222cccc3333dddd4444eeee5555"}';;
  *"commits/gui/1827"*) echo '{"sha":"dddd4444eeee5555ffff6666aaaa7777bbbb8888"}';;
  *"commits/gui/1829"*) echo '{"sha":"eeee5555ffff6666aaaa7777bbbb8888cccc9999"}';;
  *"commits/gui/1831"*) echo '{"sha":"ffff6666aaaa7777bbbb8888cccc9999dddd0000"}';;
  *"commits/gui/1833"*) printf '{"sha":"%s"}' "$TIP1833";;
  *"issues/1821 -q .state"*|*"issues/1823 -q .state"*|*"issues/1825 -q .state"*|*"issues/1827 -q .state"*|*"issues/1829 -q .state"*|*"issues/1831 -q .state"*|*"issues/1833 -q .state"*) echo open;;
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
case "$r" in *"WAIT blocker #1797 is open"*) ok "#1801: a body-only open blocker holds dispatch";; *) ko "#1801 did not WAIT on its body-only blocker #1797: '$r'";; esac
case "$r" in *"without a native blocked-by relation"*) ok "#1801: the note names the missing native relation";; *) ko "#1801 did not name the missing relation: '$r'";; esac
r=$(run 1807)
case "$r" in *"WAIT blocker #1799"*"not readable"*) ok "#1807: a body-only blocker whose state cannot be read holds dispatch";; *) ko "#1807 did not WAIT on #1799: '$r'";; esac
r=$(run 1808)
case "$r" in *WAIT*) ko "#1808: a body-only blocker that reads closed held dispatch: '$r'";; *READY*) ok "#1808: a body-only blocker that reads closed does not hold dispatch";; *) ko "#1808 did not read READY: '$r'";; esac
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
# #2260: a blocker in Landing holds dispatch no longer once its tip is approved: its work is pushed and reviewed, and only
# the landing gate is left. The answer names the tip the child starts from, so the caller cuts the child's branch there.
mkdir -p "$T/st"
printf '1821\tLanding\n1823\tDoing\n1825\tReview\n1827\tLanding\n1829\tLanding\n1831\tLanding\n1833\tLanding\n' > "$T/board"
printf 'VERDICT: APPROVE\n' > "$T/st/review-gui_1821.md"; printf 'aaaa1111bbbb2222cccc3333dddd4444eeee5555\n' > "$T/st/review-gui_1821.sha"
printf 'VERDICT: APPROVE\n' > "$T/st/review-gui_1831.md"; printf 'ffff6666aaaa7777bbbb8888cccc9999dddd0000\n' > "$T/st/review-gui_1831.sha"
printf 'VERDICT: CHANGES\n\n## Blocking\n\n- a.py:1 — x — y — z\n' > "$T/st/review-gui_1827.md"; printf 'dddd4444eeee5555ffff6666aaaa7777bbbb8888\n' > "$T/st/review-gui_1827.sha"
r=$(SG_STACK_BOARD="$T/board" run 1820)
case "$r" in READY*) ok "#2260: a blocker in Landing with an approved tip does not hold dispatch";; *) ko "#2260 #1820 did not read READY: '$r'";; esac
case "$r" in *"STACK TIP aaaa1111bbbb2222cccc3333dddd4444eeee5555 gui/1821 #1821"*) ok "#2260: the answer names the approved tip and its branch";; *) ko "#2260 #1820 named no stack tip: '$r'";; esac
r=$(SG_STACK_BOARD="$T/board" run 1822)
case "$r" in *WAIT*Doing*) ok "#2260: a blocker in Doing still holds dispatch, named by its column";; *) ko "#2260 #1822 did not WAIT on Doing: '$r'";; esac
r=$(SG_STACK_BOARD="$T/board" run 1824)
case "$r" in *WAIT*Review*) ok "#2260: a blocker in Review still holds dispatch, named by its column";; *) ko "#2260 #1824 did not WAIT on Review: '$r'";; esac
r=$(SG_STACK_BOARD="$T/board" run 1826)
case "$r" in *WAIT*"#1827"*"no approved tip"*) ok "#2260: a Landing blocker whose review asked for changes holds dispatch";; *) ko "#2260 #1826 did not WAIT on the unapproved tip: '$r'";; esac
r=$(SG_STACK_BOARD="$T/board" run 1828)
case "$r" in *WAIT*"#1829"*"no approved tip"*) ok "#2260: a Landing blocker with no review holds dispatch";; *) ko "#2260 #1828 did not WAIT on the unreviewed tip: '$r'";; esac
r=$(SG_STACK_BOARD="$T/missing" run 1830)
case "$r" in *WAIT*"#1831"*) ok "#2260: a blocker whose column is not readable holds dispatch, whatever its review says";; *) ko "#2260 #1830 read an unread column as landing: '$r'";; esac
# The approved tip already in the base needs no stacking: the work is where the child builds.
git -C "$T/co" init -q 2>/dev/null; git -C "$T/co" -c user.email=a@b -c user.name=a commit -q --allow-empty -m "blocker work" 2>/dev/null
TIP1833=$(git -C "$T/co" rev-parse HEAD); git -C "$T/co" update-ref refs/remotes/origin/main "$TIP1833"; export TIP1833
printf 'VERDICT: APPROVE\n' > "$T/st/review-gui_1833.md"; printf '%s\n' "$TIP1833" > "$T/st/review-gui_1833.sha"
r=$(SG_STACK_BOARD="$T/board" run 1832)
case "$r" in READY*) ok "#2260: a Landing blocker whose tip is already in the base is satisfied";; *) ko "#2260 #1832 did not read READY: '$r'";; esac
case "$r" in *"STACK TIP"*) ko "#2260 #1832 named a stacked tip for work already in the base: '$r'";; *) ok "#2260: no stack tip is named for work already in the base";; esac
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
# #1984: the pass's issue list (SG_STACK_GRAPH) decides the dependency component. #2001 names #2002 only as a native edge, not in its body.
mkdir -p "$T/st"; export SG_STACK_GRAPH="$T/g.json"
cat > "$T/g.json" <<'J'
[
{"number":2001,"repo":"x/y","state":"open","labels":[],"body":"Work.","parent":null,"blocked_by_read":true,"blocked_by_complete":true,"blocked_by":[2002],"blocked_by_edges":[{"repo":"x/y","number":2002,"state":"open"}],"blockers":[{"number":2002,"state":"open"}]},
{"number":2002,"repo":"x/y","state":"open","labels":[],"body":"Work.","parent":null,"blocked_by_read":true,"blocked_by_complete":true,"blocked_by":[2001],"blocked_by_edges":[{"repo":"x/y","number":2001,"state":"open"}],"blockers":[{"number":2001,"state":"open"}]},
{"number":2003,"repo":"x/y","state":"open","labels":[],"body":"Work.","parent":null,"blocked_by_read":true,"blocked_by_complete":true,"blocked_by":[],"blocked_by_edges":[],"blockers":[]},
{"number":2004,"repo":"x/y","state":"open","labels":[],"body":"Work.","parent":null,"blocked_by_read":true,"blocked_by_complete":true,"blocked_by":[2099],"blocked_by_edges":[{"repo":"x/y","number":2099,"state":"open"}],"blockers":[{"number":2099,"state":"open"}]},
{"number":2005,"repo":"x/y","state":"open","labels":[],"body":"Work.","parent":null,"blocked_by_read":true,"blocked_by_complete":true,"blocked_by":[2006],"blocked_by_edges":[{"repo":"x/y","number":2006,"state":"open"}],"blockers":[{"number":2006,"state":"open"}]},
{"number":2006,"repo":"x/y","state":"open","labels":[{"name":"frozen:owner"}],"body":"Work.","parent":null,"blocked_by_read":true,"blocked_by_complete":true,"blocked_by":[],"blocked_by_edges":[],"blockers":[]}
]
J
ent() { jq --argjson n "$1" '[.[]|select(.number==$n)][0]' "$T/g.json" > "$T/e$1.json"; }
for n in 2001 2002 2003 2004 2005 2006; do ent "$n"; done
r=$(SG_STACK_ISSUE="$T/e2001.json" run 2001)
case "$r" in WAIT*invalid*) ok "#1984: a card in an invalid component is held";; *) ko "#1984 invalid component not held: '$r'";; esac
r=$(SG_STACK_ISSUE="$T/e2001.json" run 2001)
case "$r" in *"#2002"*) ok "#1984: the held verdict names the native edge's cycle";; *) ko "#1984 verdict omits the cycle: '$r'";; esac
r=$(SG_STACK_ISSUE="$T/e2003.json" run 2003)
case "$r" in READY*) ok "#1984: an unrelated card in the same pass stays eligible";; *) ko "#1984 unrelated card held: '$r'";; esac
r=$(SG_STACK_ISSUE="$T/e2004.json" run 2004)
case "$r" in WAIT*unknown*) ok "#1984: a card in an unknown component is held";; *) ko "#1984 unknown component not held: '$r'";; esac
r=$(SG_STACK_ISSUE="$T/e2005.json" run 2005)
case "$r" in WAIT*owner*) ok "#1984: a frozen blocker holds its dependent and names the owner";; *) ko "#1984 frozen blocker did not name the owner: '$r'";; esac
grep -q "#2005.*owner" "$T/st/alerts.log" 2>/dev/null && ok "#1984: a frozen blocker writes one alert naming the owner" || ko "#1984 no owner alert in alerts.log"
r=$(SG_STACK_ISSUE="$T/e2005.json" run 2005); [ "$(grep -c "#2005.*owner" "$T/st/alerts.log")" = 1 ] && ok "#1984: a repeated hold pass writes one alert" || ko "#1984 repeated hold wrote more than one alert"
printf '%s\n' '[{"number":2007,"repo":"x/y","state":"open","labels":[],"body":"Work.","parent":null,"blocked_by_read":true,"blocked_by_complete":true,"blocked_by":[],"blocked_by_edges":[{"repo":"x/y","number":2006,"state":"open"}],"blockers":[]},{"number":2006,"repo":"x/y","state":"open","labels":[{"name":"frozen:owner"}],"body":"Work.","parent":null,"blocked_by_read":true,"blocked_by_complete":true,"blocked_by":[],"blocked_by_edges":[],"blockers":[]}]' > "$T/g2.json"
printf '%s\n' '{"number":2007,"body":"Work.","blocked_by":[],"blocked_by_read":true,"parent":null,"blockers":[]}' > "$T/e2007.json"
r=$(SG_STACK_GRAPH="$T/g2.json" SG_STACK_ISSUE="$T/e2007.json" run 2007)
case "$r" in *"WAIT dependency component of #2007 is held by owner"*"#2006"*) ok "#1984: an edge found only on the graph holds its component";; *) ko "#1984 graph-only edge did not hold: '$r'";; esac
rm -rf "$T"; exit $fail
