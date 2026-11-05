#!/bin/bash
# Regression test for branchland.sh (#355): the pass that lands a pushed work branch whose check is green in `dev` by a
# fast-forward push. Stub gh (every argv logged), a bare local remote, a stub board, temp state dir. One line per case.
# usage: bash test-branchland.sh [scripts dir]
unset SG_SERIAL_HELD   # a gate inside a gate hands the lock name down; this suite takes its own gate lock, so its wait is proved
unset SG_BOARD_VOCAB   # the loop's shipped vocabulary (Closed, not Done) is ambient in a live shell; this suite expects the default
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/bin" "$T/co/runs"
cp "$d/issue_ledger.py" "$d/gate.sh" "$d/caps.sh" "$d/serial.sh" "$d/fast-gate.sh" "$d/base-battery.sh" "$d/branchland.sh" "$d/revformat.py" "$d/workbranches.sh" "$d/ffland.sh" "$d/address.sh" "$d/ghread.py" "$d/ghthrottle.py" "$d/ghcall.sh" "$d/basecheck.sh" "$T/s/" 2>/dev/null
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps   # a gate takes gate slots (#567): this repository's squad.caps and a throwaway budget
printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=main SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s SG_LAND_REMOTE=%s/origin.git BOARD=%s/board.sh\n' "$T/st" "$T/co" "$T" "$T" > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
$G init -q -b main "$T/co"; (cd "$T/co" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init && $G branch -q dev)
# The remote the landing pushes to: a bare copy with main and dev, and gui/7-x one commit ahead of dev. mkorigin rebuilds it per case.
mkorigin() { rm -rf "$T/origin.git" "$T/w"; $G clone -q --bare "$T/co" "$T/origin.git"; $G clone -q "$T/origin.git" "$T/w" 2>/dev/null
  (cd "$T/w" && $G checkout -q -b gui/7-x origin/dev && echo y > g && $G add g && $G -c user.email=a@b -c user.name=t commit -q -m "work" && $G push -q origin gui/7-x 2>/dev/null); }
mkorigin
# mkbranch8: a second open work branch (issue 8), beside gui/7-x, so a case can prove that one branch's review holds only itself.
mkbranch8() { rm -rf "$T/w8"; $G clone -q "$T/origin.git" "$T/w8" 2>/dev/null
  (cd "$T/w8" && $G checkout -q -b gui/8-y origin/dev && echo y8 > h8 && $G add h8 && $G -c user.email=a@b -c user.name=t commit -q -m "work 8" && $G push -q origin gui/8-y 2>/dev/null); rm -rf "$T/w8"; }
# The stub: no open PR unless PRS names one. CHECKS names the check-runs answer.
cat > "$T/bin/gh" <<J
#!/bin/bash
echo "\$*" >> "$T/gh.log"
case "\$*" in
  *"pulls?per_page"*) echo "\${PRS:-[]}";;
  *"issues(states: OPEN"*) n=\$(for x in \${OPEN:-7}; do printf '{"number":%s,"title":"t","body":"","state":"OPEN"},' "\$x"; done); [ -n "\${LANDBUG:-}" ] && n="\$n{\"number\":\$LANDBUG,\"title\":\"t\",\"body\":\"\",\"state\":\"OPEN\",\"labels\":{\"nodes\":[{\"name\":\"\${LANDBUG_LABEL:-loop-bug:landing}\"}]}},"; echo "{\"data\":{\"repository\":{\"issues\":{\"pageInfo\":{\"hasNextPage\":false},\"nodes\":[\${n%,}]}}}}";;
  *"/check-runs"*) d=\$(/usr/bin/git -C $T/origin.git rev-parse -q --verify refs/heads/dev); x=\${CHECKS:-{\"check_runs\":[]\}}; case "\$*" in *"/commits/\$d/"*) x=\${BASECHECKS:-\$x};; esac; echo "\$x";;
  *"issues/7/comments"*) cat > "$T/comment.body"; echo '{"id":1}';;
  *"issues/7"*) echo '{"number":7,"state":"closed"}';;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
GREEN='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"success"}]}'
# OLDG: green with its checks finished long ago, so the base read caches its answer (#790).
OLDG='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"success","completed_at":"2001-01-01T00:00:00Z"}]}'
RED='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"failure"}]}'
RUNNING='{"check_runs":[{"id":10,"name":"check (3.11)","status":"in_progress","conclusion":null}]}'
# The review verdict of the branch tip, which the lander waits for beside the check (#357): VERDICT=APPROVE|CHANGES|none, kept
# the way branchreview.sh keeps it (review-<key>.md and review-<key>.sha under the state directory).
rev() { rm -f "$T/st/review-gui_7-x".*; [ "${1:-}" = none ] && return; printf 'VERDICT: %s\n' "${1:-APPROVE}" > "$T/st/review-gui_7-x.md"
  [ "${1:-}" = CHANGES ] && printf '## Blocking\n- a.sh:1 — wrong — breaks — fix\n' >> "$T/st/review-gui_7-x.md"
  [ "${1:-}" = DEBTONLY ] && printf 'VERDICT: CHANGES\n## Blocking\n\n## Debt\n- a.sh:1 — naming — unclear — rename\n' > "$T/st/review-gui_7-x.md"; $O rev-parse gui/7-x > "$T/st/review-gui_7-x.sha"; }
bl() { bash "$T/s/branchland.sh" 2>&1; }
O="$G -C $T/origin.git"
reset() { rm -f "$T/st/cq"/* "$T/gh.log" "$T/board.log" "$T/st/auto"/branchland-*; mkorigin; rev APPROVE; }
rev APPROVE

r=$(CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ko "dev did not move: '$r'" || ok "green: the branch landed in dev"
$O merge-base --is-ancestor main dev && [ "$($O log --format=%s dev | head -1)" = work ] && ok "dev is a fast-forward of its old head" || ko "dev is not a fast-forward"
$O rev-list --merges dev | /usr/bin/grep -q . && ko "a merge commit landed" || ok "history stays linear"
[ "$($O rev-parse main)" = "$($G -C "$T/co" rev-parse main)" ] && ok "main did not move" || ko "main moved"
$O rev-parse -q --verify refs/heads/gui/7-x >/dev/null && ko "branch not deleted" || ok "the landed branch is deleted by push"
case "$r" in *"BRANCHLAND LANDED gui/7-x as $($O rev-parse dev)"*"closed #7"*) ok "the line names the branch, the sha and the issue";; *) ko "no landed line: '$r'";; esac
# the landing ledger the hub's last-landing fact reads (#358): epoch, branch, landed sha, issue; the branch itself is gone from the remote
[ "$(awk -F'\t' '$2=="gui/7-x"{print $3 "/" $4}' "$T/st/landed.tsv" 2>/dev/null)" = "$($O rev-parse dev)/7" ] && ok "the landing is kept as a ledger row: branch, landed sha, issue" || ko "no ledger row: '$(cat "$T/st/landed.tsv" 2>/dev/null)'"
# The landing is the issue's `landed` fact (ADR-0149, #695): rule R reads it, never a run's land heads.
[ "$(SG_STATE=$T/st python3 "$T/s/issue_ledger.py" read 'x/y#7' landed | python3 -c 'import json,sys; f=json.load(sys.stdin).get("gui/7-x") or {}; print(f.get("kind",""), f.get("sha",""))')" = "branch $($O rev-parse dev)" ] && ok "the landing is a landed fact of the branch, with its sha" || ko "no landed fact: '$(SG_STATE=$T/st python3 "$T/s/issue_ledger.py" read 'x/y#7' landed)'"
/usr/bin/grep -q "issues/7 -f state=closed" "$T/gh.log" && ok "the issue is closed by the API" || ko "no close call: $(cat "$T/gh.log")"
ls "$T/st/cq/"x__y#7-delivery~* >/dev/null 2>&1 && ok "the one delivery comment is queued" || ko "no delivery file: $(ls "$T/st/cq" 2>&1)"
/usr/bin/grep -q "issues/7/comments" "$T/gh.log" && ko "the pass posted the comment itself" || ok "the pass posts no comment itself (the flush does)"
/usr/bin/grep -q "o 3 Done 7" "$T/board.log" 2>/dev/null && ok "the card moves to Done" || ko "no Done move: $(cat "$T/board.log" 2>&1)"
/usr/bin/grep -qE "pulls/[0-9]+/merge|pr merge" "$T/gh.log" && ko "a merge API call was made" || ok "no merge API call"
# A branch whose issue is closed is old work (merged under a pull request, or dropped): it never lands again (#367).
reset; r=$(OPEN=8 CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "a branch whose issue is closed does not land" || ko "a closed issue's branch landed: '$r'"
! /usr/bin/grep -q "issues/7 -f state=closed" "$T/gh.log" && [ ! -e "$T/board.log" ] && ok "a closed issue's branch writes nothing" || ko "a closed issue's branch wrote"

# a red check waits and says why, once per head
reset; r=$(CHECKS="$RED" bl); r2=$(CHECKS="$RED" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "red: nothing landed" || ko "red landed"
case "$r" in *"BRANCHLAND WAIT gui/7-x"*"check"*"failure"*) ok "red: says why";; *) ko "red silent: '$r'";; esac
[ -z "$r2" ] && ok "red: says it once per head" || ko "red repeated: '$r2'"
# a running check waits the same way
reset; r=$(CHECKS="$RUNNING" bl); r2=$(CHECKS="$RUNNING" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && case "$r" in *"BRANCHLAND WAIT gui/7-x"*running*) ok "running: waits and says why";; *) ko "running silent: '$r'";; esac || ko "running landed"
[ -z "$r2" ] && ok "running: says it once per head" || ko "running repeated: '$r2'"
# no check yet, with the local gate off: it waits as before
reset; rm -f "$T/gate.log"; r=$(SG_LOCAL_GATE=no CHECKS='{"check_runs":[]}' bl); case "$r" in *"BRANCHLAND WAIT gui/7-x"*"no check run"*) ok "no check run, gate off: waits and says why";; *) ko "no check silent: '$r'";; esac
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && [ ! -e "$T/gate.log" ] && ok "gate off: no gate runs, nothing lands" || ko "gate off: the gate ran or the branch landed"
# Zero check runs while CI is off (#385): the local gate runs in a clean checkout of the tip. SG_LOCAL_GATE_CMD stubs it and logs
# the directory and the sha it ran at.
GATE_OK="echo \"\$PWD \$(/usr/bin/git rev-parse HEAD)\" >> $T/gate.log; exit 0"
GATE_KO="echo \"\$PWD \$(/usr/bin/git rev-parse HEAD)\" >> $T/gate.log; echo 'mypy: 3 errors'; exit 1"
reset; rm -f "$T/gate.log"; tip=$($O rev-parse gui/7-x); r=$(SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS='{"check_runs":[]}' bl)
[ "$(awk '{print $2}' "$T/gate.log" 2>/dev/null)" = "$tip" ] && ok "zero checks: the gate runs at the branch tip" || ko "zero checks: no gate at $tip: '$(cat "$T/gate.log" 2>&1)' '$r'"
case "$(awk '{print $1}' "$T/gate.log" 2>/dev/null)" in ""|"$T/co"|"$T/w") ko "the gate did not run in a clean checkout";; *) ok "the gate runs in a clean checkout";; esac
[ "$($O rev-parse dev)" = "$tip" ] && ok "zero checks + gate pass: the branch lands in dev by fast-forward" || ko "gate pass did not land: '$r'"
case "$r" in *"BRANCHLAND LANDED gui/7-x"*"closed #7"*) ok "gate pass: the landed line";; *) ko "gate pass: no landed line: '$r'";; esac
/usr/bin/grep -qh "local gate, CI off" "$T/st/cq/"x__y#7-status~* 2>/dev/null && /usr/bin/grep -qh "${tip:0:9}" "$T/st/cq/"x__y#7-status~* && ok "gate pass: the status comment names the local gate and the sha" || ko "gate pass: no status comment: $(cat "$T/st/cq/"* 2>&1)"
/usr/bin/grep -q "o 3 Done 7" "$T/board.log" 2>/dev/null && /usr/bin/grep -q "issues/7 -f state=closed" "$T/gh.log" && ok "gate pass: the issue closes and the card moves to Done" || ko "gate pass: no close or no Done"
IFS=$'\t' read -r _ kind res at < "$T/st/gate-status" 2>/dev/null
[ "$kind $res $at" = "fast pass $tip" ] && ok "gate pass: one fast status line carries the result and the sha it ran at" || ko "gate pass: status line: '$(cat "$T/st/gate-status" 2>&1)'"
/usr/bin/grep -q 'fast-gate\.sh' "$d/branchland.sh" && ok "the default local gate is the fast gate: the shell suites of the changed scripts run in it" || ko "the default local gate does not run fast-gate.sh"
[ "$($G -C "$T/co" worktree list | wc -l | tr -d ' ')" = 1 ] && ok "the gate checkout is removed" || ko "a gate worktree is left: $($G -C "$T/co" worktree list)"
reset; rm -f "$T/gate.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_KO" CHECKS='{"check_runs":[]}' bl); r2=$(SG_LOCAL_GATE_CMD="$GATE_KO" CHECKS='{"check_runs":[]}' bl)
[ -s "$T/gate.log" ] && [ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "zero checks + gate fail: nothing lands" || ko "gate fail landed or no gate ran: '$r'"
case "$r" in *"BRANCHLAND WAIT gui/7-x"*"local gate"*) ok "gate fail: says why";; *) ko "gate fail silent: '$r'";; esac
/usr/bin/grep -qh "local gate, CI off" "$T/st/cq/"x__y#7-status~* 2>/dev/null && /usr/bin/grep -qh "mypy: 3 errors" "$T/st/cq/"x__y#7-status~* && ok "gate fail: the failure is recorded on the issue" || ko "gate fail: no status comment: $(cat "$T/st/cq/"* 2>&1)"
! /usr/bin/grep -q "issues/7 -f state=closed" "$T/gh.log" && [ ! -e "$T/board.log" ] && ok "gate fail: closes nothing, moves no card" || ko "gate fail wrote"
[ "$(wc -l < "$T/gate.log" | tr -d ' ')" = 1 ] && [ -z "$r2" ] && ok "gate fail: runs once per head" || ko "gate fail reran: $(cat "$T/gate.log") '$r2'"
# A failed gate reruns only what failed, once (SG_LOCAL_GATE_RERUN_CMD stubs it): a red that machine load caused passes alone and the
# branch lands; a red that is real fails again and the branch waits.
RERUN_OK="echo \"rerun \$SG_GATE_FAILED\" >> $T/rerun.log; exit 0"; RERUN_KO="echo rerun >> $T/rerun.log; echo 'still red'; exit 1"
reset; rm -f "$T/gate.log" "$T/rerun.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_KO" SG_LOCAL_GATE_RERUN_CMD="$RERUN_OK" CHECKS='{"check_runs":[]}' bl)
[ -s "$T/rerun.log" ] && case "$r" in *"BRANCHLAND LANDED gui/7-x"*) ok "a red that passes on its rerun: the branch lands";; *) ko "a load red did not land: '$r'";; esac || ko "a failed gate ran no rerun: '$r'"
reset; rm -f "$T/gate.log" "$T/rerun.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_KO" SG_LOCAL_GATE_RERUN_CMD="$RERUN_KO" CHECKS='{"check_runs":[]}' bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && case "$r" in *"BRANCHLAND WAIT gui/7-x"*"still red"*) ok "a red that fails its rerun: nothing lands, and the rerun says why";; *) ko "a real red: '$r'";; esac || ko "a real red landed"
# The rerun is handed the node ids that failed (the suite runs with no cache provider, so --lf has nothing to read).
GATE_KO_IDS="echo 'FAILED tests/test_a.py::test_one - boom'; echo 'FAILED tests/test_b.py::test_two'; exit 1"
reset; rm -f "$T/gate.log" "$T/rerun.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_KO_IDS" SG_LOCAL_GATE_RERUN_CMD="$RERUN_OK" CHECKS='{"check_runs":[]}' bl)
[ "$(cat "$T/rerun.log" 2>/dev/null)" = "rerun tests/test_a.py::test_one tests/test_b.py::test_two" ] && ok "the rerun gets exactly the failed node ids" || ko "the rerun ids: '$(cat "$T/rerun.log" 2>&1)'"
# (#699) An inconclusive gate (exit 4: fast-gate ran its one retry, and that failed too) never lands and gets no second rerun:
# a rerun that passed would grant the landing the gate held.
GATE_INC="echo gate >> $T/gate.log; echo 'fast-gate: saturation: the battery crashed twice; inconclusive, so the landing holds'; exit 4"
reset; rm -f "$T/gate.log" "$T/rerun.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_INC" SG_LOCAL_GATE_RERUN_CMD="$RERUN_OK" CHECKS='{"check_runs":[]}' bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && [ ! -e "$T/rerun.log" ] && ok "an inconclusive gate: nothing lands and no rerun runs" || ko "inconclusive gate: rerun '$(cat "$T/rerun.log" 2>&1)': '$r'"
case "$r" in *"BRANCHLAND WAIT gui/7-x"*inconclusive*) ok "an inconclusive gate says so";; *) ko "inconclusive gate line: '$r'";; esac
/usr/bin/grep -qh "inconclusive" "$T/st/cq/"x__y#7-status~* 2>/dev/null && ok "the status comment names the inconclusive result" || ko "inconclusive status: $(cat "$T/st/cq/"* 2>&1)"
# The default gate runs with none of the loop's SG_* variables (a test that reads one would see the live loop state) and with the
# 2 s line off: that line is the CI speed rule, and this machine's load is not a defect of the branch; the 20 s cap stays.
printf '#!/bin/bash\necho "SG_STATE=${SG_STATE:-unset} $*" >> %s/py.log\ncase "$*" in *squad.testselect*) echo "all: stub";; esac\n' "$T" > "$T/py"; chmod +x "$T/py"
reset; rm -f "$T/py.log"; r=$(SG_STATE="$T/st" SG_GATE_PY="$T/py" CHECKS='{"check_runs":[]}' bl)
/usr/bin/grep -q 'pytest' "$T/py.log" 2>/dev/null && ! /usr/bin/grep -q 'SG_STATE=/' "$T/py.log" && ok "the default gate runs with no SG_* variable" || ko "the gate saw the loop env: $(cat "$T/py.log" 2>&1)"
/usr/bin/grep 'pytest' "$T/py.log" 2>/dev/null | /usr/bin/grep -q -- '-o heavy_cap_seconds=20' && ok "the default gate lifts the 2 s line to the 20 s cap" || ko "the gate keeps the 2 s line: $(grep pytest "$T/py.log" 2>&1)"
# The gate proves what lands: the tip rebased onto dev, never the bare tip. A red the branch inherited from an old base is fixed
# on dev, so the branch lands once dev carries the fix, and a failed gate runs again when dev moves.
GATE_FIXED="echo \"\$PWD \$(/usr/bin/git rev-parse HEAD)\" >> $T/gate.log; test -f fixed || { echo 'inherited red'; exit 1; }"
reset; rm -f "$T/gate.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_FIXED" CHECKS='{"check_runs":[]}' bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "an inherited red, not fixed on dev: nothing lands" || ko "an inherited red landed: '$r'"
(cd "$T/w" && $G checkout -q dev && $G pull -q origin dev 2>/dev/null && echo z > fixed && $G add fixed && $G -c user.email=a@b -c user.name=t commit -q -m "fix on dev" && $G push -q origin dev 2>/dev/null)
rev APPROVE; r=$(SG_LOCAL_GATE_CMD="$GATE_FIXED" CHECKS='{"check_runs":[]}' bl)
[ "$(wc -l < "$T/gate.log" | tr -d ' ')" = 2 ] && ok "dev moved: the failed gate runs again" || ko "dev moved, the gate did not rerun: $(cat "$T/gate.log" 2>&1) '$r'"
case "$r" in *"BRANCHLAND LANDED gui/7-x"*) ok "the fix on dev: the branch lands, gated on the rebased tip";; *) ko "the rebased tip did not land: '$r'";; esac
[ "$($O log --format=%s dev | head -2 | tr '\n' ,)" = "work,fix on dev," ] && ok "dev holds the fix and the work, linear" || ko "dev history: $($O log --format=%s dev | head -3 | tr '\n' ,)"
[ "$($G -C "$T/co" worktree list | wc -l | tr -d ' ')" = 1 ] && ok "the rebased gate checkout is removed" || ko "a gate worktree is left: $($G -C "$T/co" worktree list)"
reset; rm -f "$T/gate.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS="$RED" bl)
[ ! -e "$T/gate.log" ] && [ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "a red check runs no gate and lands nothing" || ko "a red check ran the gate or landed"
reset; rm -f "$T/gate.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS="$RUNNING" bl)
[ ! -e "$T/gate.log" ] && [ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "a running check runs no gate and lands nothing" || ko "a running check ran the gate or landed"
# a green check is not enough: the review must have approved the tip (#357)
# A CHANGES with no Blocking finding (debt only) is an approval (#392): the lander reads the findings, as autopilot does.
reset; rev DEBTONLY; r=$(CHECKS="$GREEN" bl)
case "$r" in *"BRANCHLAND LANDED gui/7-x"*) ok "a debt-only CHANGES lands: no Blocking finding";; *) ko "a debt-only CHANGES did not land: '$r'";; esac
reset; rev CHANGES; r=$(CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "a review that asked for changes: nothing lands on a green check" || ko "landed over a CHANGES verdict"
case "$r" in *"BRANCHLAND WAIT gui/7-x"*"changes"*) ok "CHANGES: waits and says why";; *) ko "CHANGES silent: '$r'";; esac
! /usr/bin/grep -q "issues/7" "$T/gh.log" && [ ! -e "$T/board.log" ] && ok "a branch under CHANGES closes nothing" || ko "CHANGES branch wrote"
reset; rev none; r=$(CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && case "$r" in *"BRANCHLAND WAIT gui/7-x"*"review"*) ok "no review yet: waits and says why";; *) ko "no-review silent: '$r'";; esac || ko "landed with no review"
reset; echo 0000000000000000000000000000000000000000 > "$T/st/review-gui_7-x.sha"; r=$(CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "a review of an older tip does not approve the new one" || ko "landed on a stale review"
# a red check never closes the issue or moves the card
! /usr/bin/grep -q "issues/7" "$T/gh.log" && [ ! -e "$T/board.log" ] && ok "a waiting branch closes nothing and moves no card" || ko "a waiting branch wrote"

# (#1376) The landing pass asks ONE parser for a four-way classification -- stale, malformed, changes or approves --
# instead of reading a first-line substring and a --summary count. A prose review has no recognised finding, so the old
# predicate read blocking=0 and RELEASED the branch; the classification calls it malformed and holds it. These cases drive
# the real parser (revformat.py) and the real pass (branchland.sh). --summary keeps its exact line and exit code, which
# the other callers read, so it is guarded here too.
printf 'I looked at the diff and it seems mostly fine, though the naming is odd.\nNo blocking issues as far as I can tell.\n' > "$T/prose.md"
out=$(python3 "$T/s/revformat.py" --summary "$T/prose.md"); rc=$?
[ "$rc" = 0 ] && [ "$out" = "blocking=0 debt=0" ] && ok "the --summary line and exit 0 on a prose review are unchanged (other callers read them)" || ko "--summary on prose changed: rc=$rc '$out'"
printf 'VERDICT: CHANGES\n## Blocking\n- a.sh:1 — wrong — breaks — fix\n## Debt\n- b.sh:2 — naming — unclear — rename\n' > "$T/valid.md"
out=$(python3 "$T/s/revformat.py" --summary "$T/valid.md"); rc=$?
[ "$rc" = 0 ] && [ "$out" = "blocking=1 debt=1" ] && ok "the --summary counts of a valid review are unchanged" || ko "--summary on a valid review changed: rc=$rc '$out'"
reset; cp "$T/prose.md" "$T/st/review-gui_7-x.md"; $O rev-parse gui/7-x > "$T/st/review-gui_7-x.sha"
tip=$($O rev-parse gui/7-x)
[ "$(python3 "$T/s/revformat.py" --classify "$T/st/review-gui_7-x.md" "$T/st/review-gui_7-x.sha" "$tip")" = malformed ] && ok "the parser classifies a prose review as malformed" || ko "the prose review was not classified malformed"
r=$(CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "a prose review lands nothing" || ko "a prose review released the branch: '$r'"
case "$r" in *"BRANCHLAND WAIT gui/7-x"*malformed*"## Blocking"*"## Debt"*) ok "a malformed review holds its branch and names the shape";; *) ko "no malformed hold naming the shape: '$r'";; esac
# a corrected file at the SAME commit: the next pass classifies it again and lands it, so the hold is not keyed on the tip
printf 'VERDICT: APPROVE\n' > "$T/st/review-gui_7-x.md"
r=$(CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$tip" ] && ok "a corrected file at the same commit lands on the next pass" || ko "the corrected file did not land: '$r'"
# a valid CHANGES with one Blocking finding: 'changes', and it holds
reset; rev CHANGES
[ "$(python3 "$T/s/revformat.py" --classify "$T/st/review-gui_7-x.md" "$T/st/review-gui_7-x.sha" "$($O rev-parse gui/7-x)")" = changes ] && ok "a valid CHANGES with a Blocking finding reads changes" || ko "the Blocking review was not classified changes"
r=$(CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "a valid CHANGES with a Blocking finding still holds" || ko "a Blocking review landed"
# a CHANGES whose sections are both empty: the shape check passes it, and the counts say blocking=0, so the old predicate
# released the branch. The reviewer asked for a change and said nowhere what -- malformed, and it holds.
reset; printf 'VERDICT: CHANGES\n## Blocking\n' > "$T/st/review-gui_7-x.md"; $O rev-parse gui/7-x > "$T/st/review-gui_7-x.sha"
cls=$(python3 "$T/s/revformat.py" --classify "$T/st/review-gui_7-x.md" "$T/st/review-gui_7-x.sha" "$($O rev-parse gui/7-x)")
r=$(CHECKS="$GREEN" bl)
[ "$cls" = malformed ] && [ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "a CHANGES with no Blocking and no Debt is malformed, and holds" || ko "an empty CHANGES released or misclassified ('$cls'): '$r'"
# review.sh's legacy line 'VERDICT: APPROVE (reason)' is the bare verdict, not prose: the branch lands, never held forever
reset; printf 'VERDICT: APPROVE (small change; no blocking findings)\n## Blocking\n(none)\n' > "$T/st/review-gui_7-x.md"; $O rev-parse gui/7-x > "$T/st/review-gui_7-x.sha"
r=$(CHECKS="$GREEN" bl)
case "$r" in *"BRANCHLAND LANDED gui/7-x"*) ok "a kept review with review.sh's legacy VERDICT line lands";; *) ko "the legacy APPROVE line did not land: '$r'";; esac
# a valid debt-only CHANGES keeps its approval behaviour: 'approves', and it lands
reset; rev DEBTONLY
[ "$(python3 "$T/s/revformat.py" --classify "$T/st/review-gui_7-x.md" "$T/st/review-gui_7-x.sha" "$($O rev-parse gui/7-x)")" = approves ] && ok "a valid debt-only CHANGES reads approves" || ko "the debt-only review was not an approval"
r=$(CHECKS="$GREEN" bl)
case "$r" in *"BRANCHLAND LANDED gui/7-x"*) ok "a valid debt-only CHANGES still lands";; *) ko "the debt-only CHANGES did not land: '$r'";; esac
# a saved review is the full-review shape or a fix round's, and the file names no mode: '## Resolved' is malformed in the
# full shape and valid in the fix-round shape, so the pass asks the second and lands it
reset; printf 'VERDICT: APPROVE\n## Resolved\n- a.sh:1 — x — fixed: y — check\n' > "$T/st/review-gui_7-x.md"; $O rev-parse gui/7-x > "$T/st/review-gui_7-x.sha"
[ "$(python3 "$T/s/revformat.py" --classify "$T/st/review-gui_7-x.md" "$T/st/review-gui_7-x.sha" "$($O rev-parse gui/7-x)")" = malformed ] && ok "a full review with '## Resolved' is malformed" || ko "'## Resolved' is accepted outside a fix round"
r=$(CHECKS="$GREEN" bl)
case "$r" in *"BRANCHLAND LANDED gui/7-x"*) ok "a fix-round review with '## Resolved' lands (the file names no mode)";; *) ko "the fix-round review did not land: '$r'";; esac
# a review whose recorded sha is not the tip is stale, and holds
reset; rev APPROVE; printf '0%.0s' $(seq 1 40) > "$T/st/review-gui_7-x.sha"
cls=$(python3 "$T/s/revformat.py" --classify "$T/st/review-gui_7-x.md" "$T/st/review-gui_7-x.sha" "$($O rev-parse gui/7-x)")
r=$(CHECKS="$GREEN" bl)
[ "$cls" = stale ] && [ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "a review of another tip is stale, and holds" || ko "a stale review released or misclassified ('$cls'): '$r'"
# a malformed review of one branch stops no other branch: gui/7-x is held while gui/8-y lands
reset; mkbranch8
cp "$T/prose.md" "$T/st/review-gui_7-x.md"; $O rev-parse gui/7-x > "$T/st/review-gui_7-x.sha"
printf 'VERDICT: APPROVE\n' > "$T/st/review-gui_8-y.md"; $O rev-parse gui/8-y > "$T/st/review-gui_8-y.sha"
r=$(OPEN="7 8" CHECKS="$GREEN" bl)
$O rev-parse -q --verify refs/heads/gui/8-y >/dev/null && ko "the other branch did not land: '$r'" || ok "a malformed review of one branch lands the other branch"
tip7=$($O rev-parse -q --verify refs/heads/gui/7-x 2>/dev/null)
[ -n "$tip7" ] && ! $O merge-base --is-ancestor "$tip7" dev 2>/dev/null && ok "the malformed branch stays held while the other lands" || ko "the malformed branch was released"
case "$r" in *"BRANCHLAND WAIT gui/7-x"*malformed*) true;; *) ko "no malformed line for the held branch: '$r'";; esac
case "$r" in *"BRANCHLAND LANDED gui/8-y"*) ok "the pass lands the other branch and names it";; *) ko "no landed line for the other branch: '$r'";; esac

# dev moved after the branch was cut: the branch is rebased on top, then lands
reset; (cd "$T/w" && $G checkout -q dev && echo z > h && $G add h && $G -c user.email=a@b -c user.name=t commit -q -m "other" && $G push -q origin dev 2>/dev/null)
r=$(CHECKS="$GREEN" bl); $O log --format=%s dev | tr '\n' ' ' | /usr/bin/grep -q "^work other init" && ok "a moved dev: the branch is rebased on top and lands" || ko "no rebase landing: $($O log --format=%s dev | tr '\n' ' ') $r"
$O rev-list --merges dev | /usr/bin/grep -q . && ko "a merge commit landed" || ok "a moved dev: history stays linear"

# a refused push says so and lands nothing
reset; printf '#!/bin/sh\nwhile read o n ref; do [ "$ref" = refs/heads/dev ] && { echo "GH013: Repository rule violations found" >&2; exit 1; }; done\nexit 0\n' > "$T/origin.git/hooks/pre-receive"; chmod +x "$T/origin.git/hooks/pre-receive"
before=$($O rev-parse dev); r=$(CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$before" ] && ok "a refused push lands nothing" || ko "dev moved under a refusal"
case "$r" in *"BRANCHLAND REFUSE gui/7-x"*ruleset*) ok "a refused push says so";; *) ko "refusal silent: '$r'";; esac
! /usr/bin/grep -q "issues/7" "$T/gh.log" && [ ! -e "$T/board.log" ] && ! ls "$T/st/cq/"* >/dev/null 2>&1 && ok "a refused push closes nothing, queues nothing, moves no card" || ko "a refusal wrote"
r2=$(CHECKS="$GREEN" bl); [ -z "$r2" ] && ok "a refusal is said once per head" || ko "refusal repeated: '$r2'"

# (#361) a pull request naming the branch changes nothing: the loop reads no pull request, so the branch lands
reset; r=$(PRS='[{"number":41,"head":{"ref":"gui/7-x"}}]' CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" != "$($O rev-parse main)" ] && ok "a branch a pull request names lands like any other" || ko "a pull-request branch was not landed: '$r'"
# a branch that names no issue is not a work branch
reset; (cd "$T/w" && $G push -q origin gui/7-x:refs/heads/scratch 2>/dev/null; $G push -q origin :gui/7-x 2>/dev/null)
r=$(CHECKS="$GREEN" bl); [ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "a branch naming no issue is ignored" || ko "landed scratch: '$r'"

# (#392) A holder of the machine-wide `gate` lock; the runner's gate then waits for it, and runs after it, never beside it. The hold
# outlasts the runner's own startup -- the fetch and the clean-checkout work before the gate -- so the wait is proved on a busy machine.
hold() { (SG_STATE=$T/st bash "$T/s/serial.sh" gate sh -c "sleep 10; echo holder >> $T/gl") & for _ in $(seq 1 50); do [ -s "$T/st/locks/gate/pid" ] && break; sleep 0.1; done; }
# (#392) The local gate takes the machine-wide gate lock: with a holder it waits, says so on one line, runs after it, and lands.
reset; rm -f "$T/gl"; hold
r=$(SG_LOCAL_GATE_CMD="echo gate >> $T/gl" CHECKS='{"check_runs":[]}' bl 2>&1); wait
[ "$(tr '\n' ' ' < "$T/gl")" = "holder gate " ] && ok "the local gate waits for the holder of the gate lock, then runs" || ko "gate order '$(tr '\n' ' ' < "$T/gl")': '$r'"
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c '^serial: waiting for lock gate held by pid')" = 1 ] && ok "the local gate's wait shows on one line" || ko "wait lines: '$r'"
case "$r" in *"BRANCHLAND LANDED gui/7-x"*) ok "the waiting local gate is not dropped: the branch lands";; *) ko "the waiting gate did not land: '$r'";; esac
# (#392) A gate command that starts a gate of its own (promote-stg.sh does) runs inside the lock its parent holds: SG_SERIAL_HELD
# survives the drop of the loop's SG_* variables, or the nested gate waits for its own parent. The nested gate reads the loop's
# state directory, which the gate command's env does not carry, so the test names it there.
reset; rm -f "$T/nest.log"; r=$(SG_SERIAL_HELD=other SG_LOCAL_GATE_CMD="SG_STATE=$T/st SERIAL_WAIT_SECONDS=4 bash $T/s/gate.sh echo nested >> $T/nest.log" CHECKS='{"check_runs":[]}' bl)
[ "$(cat "$T/nest.log" 2>/dev/null)" = nested ] && ok "a gate nested in the local gate runs at once, under its parent's lock" || ko "the nested gate waited for its own parent: '$(cat "$T/nest.log" 2>&1)' '$r'"
# (#392) The wait line is said once. The gate's output carries it, and it is passed on to stderr, so the failure record that quotes
# the output must not carry it a second time.
reset; rm -f "$T/gl"; hold
r=$(SG_LOCAL_GATE_CMD="echo 'mypy: 3 errors'; exit 1" SG_LOCAL_GATE_RERUN_CMD="exit 1" CHECKS='{"check_runs":[]}' bl); wait
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c '^serial: waiting for lock gate held by pid')" = 1 ] && ok "a failed gate that waited: the wait shows once on stderr" || ko "wait lines: '$r'"
/usr/bin/grep -qh "mypy: 3 errors" "$T/st/cq/"x__y#7-status~* && ! /usr/bin/grep -qh "serial: waiting for lock" "$T/st/cq/"x__y#7-status~* && ok "the failure record does not repeat the wait line" || ko "the failure record: $(cat "$T/st/cq/"x__y#7-status~* 2>&1)"
# A trial rebase that conflicts is aborted clean; nothing is resolved. One CONFLICT line names the files and the dev sha, once.
reset; rm -f "$T/gate.log"; (cd "$T/w" && $G checkout -q dev && echo other > g && $G add g && $G -c user.email=a@b -c user.name=t commit -q -m "dev g" && $G push -q origin dev 2>/dev/null)
devsha=$($O rev-parse dev); tip=$($O rev-parse gui/7-x); rev APPROVE
r=$(SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS='{"check_runs":[]}' bl); r2=$(SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS='{"check_runs":[]}' bl)
case "$r" in *"BRANCHLAND CONFLICT gui/7-x"*g*"${devsha:0:7}"*) ok "a conflict: one line names the files and the dev sha";; *) ko "conflict line: '$r'";; esac
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c 'BRANCHLAND CONFLICT')" = 1 ] && [ -z "$r2" ] && ok "a conflict is said once per head and dev head" || ko "conflict repeated: '$r' '$r2'"
[ ! -e "$T/gate.log" ] && [ "$($O rev-parse dev)" = "$devsha" ] && [ "$($O rev-parse gui/7-x)" = "$tip" ] && ok "a conflict: no gate runs, nothing lands, the branch is untouched" || ko "a conflict ran the gate or moved a ref"
/usr/bin/grep -qh "BRANCHLAND CONFLICT gui/7-x" "$T/st/cq/"x__y#7-status~* 2>/dev/null && /usr/bin/grep -qh "${devsha:0:7}" "$T/st/cq/"x__y#7-status~* && ok "a conflict: the one status line is on the issue" || ko "conflict: no status line: $(cat "$T/st/cq/"* 2>&1)"
[ "$($G -C "$T/co" worktree list | wc -l | tr -d ' ')" = 1 ] && [ -z "$($G -C "$T/co" status --porcelain)" ] && ok "a conflict leaves no worktree and no residue" || ko "conflict residue: $($G -C "$T/co" worktree list) $($G -C "$T/co" status --porcelain)"
# DEBT (#1344): a rebase round comes from the kept marker the wave writes, never from a branch name. A hand-made fix/*-rebase-* branch
# that conflicts with dev keeps its own APPROVE; a branch with the kept marker gets the one CHANGES round.
hand8() { (cd "$T/w" && $G checkout -q dev && echo other > g && $G add g && $G -c user.email=a@b -c user.name=t commit -q -m "dev g" && $G push -q origin dev 2>/dev/null \
  && $G checkout -q -b fix/8-rebase-hand origin/main && echo mine > g && $G add g && $G -c user.email=a@b -c user.name=t commit -q -m "hand (Refs #8)" && $G push -q origin fix/8-rebase-hand 2>/dev/null); }
reset; hand8; hs=$($O rev-parse fix/8-rebase-hand); rm -f "$T/st/auto/kept-fix_8-rebase-hand"
printf 'VERDICT: APPROVE\n' > "$T/st/review-fix_8-rebase-hand.md"; printf '%s\n' "$hs" > "$T/st/review-fix_8-rebase-hand.sha"
r=$(OPEN=8 SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS='{"check_runs":[]}' bl)
case "$r" in *"BRANCHLAND CONFLICT fix/8-rebase-hand"*) ok "a hand-made rebase-named branch conflicts with dev";; *) ko "no conflict line for the hand-made branch: '$r'";; esac
head -1 "$T/st/review-fix_8-rebase-hand.md" | /usr/bin/grep -q APPROVE && ok "a hand-made rebase-named branch keeps its own review" || ko "a CHANGES review was fabricated: '$(head -1 "$T/st/review-fix_8-rebase-hand.md")'"
reset; hand8; hs=$($O rev-parse fix/8-rebase-hand); printf 'origin/dev\n' > "$T/st/auto/kept-fix_8-rebase-hand"
printf 'VERDICT: APPROVE\n' > "$T/st/review-fix_8-rebase-hand.md"; printf '%s\n' "$hs" > "$T/st/review-fix_8-rebase-hand.sha"
r=$(OPEN=8 SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS='{"check_runs":[]}' bl)
head -1 "$T/st/review-fix_8-rebase-hand.md" | /usr/bin/grep -q CHANGES && ok "a kept rebase branch gets its one CHANGES round" || ko "a kept rebase branch got no round: '$(head -1 "$T/st/review-fix_8-rebase-hand.md")' '$r'"

# A failed gate leaves no worktree and no staged residue, even when the gate staged files.
reset; r=$(SG_LOCAL_GATE_CMD="echo a > staged1; /usr/bin/git add staged1; echo 'mypy: 1 error'; exit 1" SG_LOCAL_GATE_RERUN_CMD="exit 1" CHECKS='{"check_runs":[]}' bl)
[ "$($G -C "$T/co" worktree list | wc -l | tr -d ' ')" = 1 ] && [ -z "$($G -C "$T/co" status --porcelain)" ] && [ -z "$($G -C "$T/co" diff --cached --name-only)" ] && ok "a failed gate leaves no worktree and nothing staged" || ko "gate residue: $($G -C "$T/co" worktree list)"
# A gate that fails only on a fixable ruff finding: ruff --fix runs in the trial, its commit is made, the gate reruns and passes,
# and the fix lands with the branch. SG_RUFF_FIX_CMD stubs the fix.
RUFF_RED="echo \"\$PWD\" >> $T/gate.log; test -f lintfix || { echo 'Found 1 error.'; echo '[*] 1 fixable with the \`--fix\` option.'; exit 1; }"
reset; rm -f "$T/gate.log" "$T/fix.log"; r=$(SG_LOCAL_GATE_CMD="$RUFF_RED" SG_LOCAL_GATE_RERUN_CMD="exit 1" SG_RUFF_FIX_CMD="echo \$PWD >> $T/fix.log; echo f > lintfix" CHECKS='{"check_runs":[]}' bl)
case "$r" in *"BRANCHLAND LANDED gui/7-x"*) ok "a fixable ruff red: the fix is made and the branch lands";; *) ko "ruff fix did not land: '$r'";; esac
[ "$($O log --format=%s dev | head -1)" = "Apply ruff --fix at the landing" ] && $O show dev:lintfix >/dev/null 2>&1 && ok "the fix is a commit on the trial and lands with the branch" || ko "dev history: $($O log --format=%s dev | head -3 | tr '\n' ,)"
[ "$(wc -l < "$T/gate.log" | tr -d ' ')" -ge 2 ] && ok "the gate reruns after the fix" || ko "no rerun: $(cat "$T/gate.log" 2>&1)"
# A red that is not fixable by ruff never runs the fix; a fix that does not cure the gate lands nothing.
reset; rm -f "$T/fix.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_KO" SG_LOCAL_GATE_RERUN_CMD="exit 1" SG_RUFF_FIX_CMD="echo x >> $T/fix.log" CHECKS='{"check_runs":[]}' bl)
[ ! -e "$T/fix.log" ] && ok "a red with no fixable ruff finding runs no fix" || ko "the fix ran on a non-ruff red"
reset; rm -f "$T/fix.log"; r=$(SG_LOCAL_GATE_CMD="echo '[*] 1 fixable with the --fix option.'; echo 'mypy: 2 errors'; exit 1" SG_LOCAL_GATE_RERUN_CMD="exit 1" SG_RUFF_FIX_CMD="echo f > lintfix" CHECKS='{"check_runs":[]}' bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && case "$r" in *"BRANCHLAND WAIT gui/7-x"*) ok "a fix that does not cure the gate: nothing lands, reported as a failure";; *) ko "silent: '$r'";; esac || ko "landed after a failed fix"
/usr/bin/grep -q -- '--unsafe-fixes' "$d/branchland.sh" && ko "branchland uses --unsafe-fixes" || ok "branchland never uses --unsafe-fixes"
# (#566) The landing check is the loop's one base battery -- ruff, mypy and the fast pytest -- read against the branch the
# tip lands in, in the fast gate, with a bounded worker count instead of `auto`. branchland.sh keeps no copy of it. The count
# is set here, so this suite reads no profile's default and depends on no worker count.
. "$d/base-battery.sh" 2>/dev/null
lcmd=$(SG_GATE_WORKERS=3 sg_base_gate "refs/sg-gate/main" /usr/bin/true 2>/dev/null)
lbat=$(SG_GATE_WORKERS=3 sg_base_battery /usr/bin/true "refs/sg-gate/main" 2>/dev/null)
case "$lcmd" in *"SG_FAST_BASE=refs/sg-gate/main "*"fast-gate.sh"*) ok "the landing check reads the changed scripts against refs/sg-gate/\$land";; *) ko "the shared base gate is not the fast gate at refs/sg-gate/\$land: '$lcmd'";; esac
case "$lbat" in *"ruff check squad tests"*"mypy squad"*'-n "${SQUAD_GATE_WORKERS:-$workers}"'*"not heavy"*"sg_battery_run /usr/bin/true refs/sg-gate/main 3 yes"*) ok "the landing check runs ruff, mypy and the fast pytest with SG_GATE_WORKERS";; *) ko "the landing check command: '$lbat'";; esac
case "$lbat$lcmd" in *"-n auto"*) ko "the landing check runs pytest -n auto";; *) ok "the landing check never runs pytest -n auto";; esac
/usr/bin/grep -q 'sg_base_gate "refs/sg-gate/\$land"' "$d/branchland.sh" && ok "branchland builds its landing check from the shared definition" || ko "branchland does not use the shared base gate"
# The gate command keeps no copy; the serial rerun below keeps its own lint and types leg, and the names that failed.
! /usr/bin/grep -v '^[[:space:]]*#' "$d/branchland.sh" | /usr/bin/grep -v '^rerun=' | /usr/bin/grep -q 'ruff check squad tests' && ok "branchland keeps no copy of the base battery" || ko "branchland still carries a copy of the base battery"
# A loop defect in the landing path (#641, ADR-0146 decision 3): while an issue labelled `loop-bug:landing` is open, the pass
# lands nothing and names the issue. A plain `loop-bug` stops intake only: a green, approved branch still lands.
reset; r=$(SG_GH_AGGREGATE_WINDOW=0 LANDBUG=641 CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "a loop-bug:landing issue stops the landing" || ko "a branch landed under a loop-bug:landing issue: '$r'"
case "$r" in *"LOOP STOP: #641 is a loop defect in the landing path; no wave lands until it closes"*) ok "the pass names the landing stop";; *) ko "no landing stop line: '$r'";; esac
reset; r=$(SG_GH_AGGREGATE_WINDOW=0 LANDBUG=641 LANDBUG_LABEL=loop-bug CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ko "a plain loop-bug stopped the landing: '$r'" || ok "a plain loop-bug lets an approved branch land"
# The rerun of what failed stays serial, and runs only the names the gate failed: the id list, never the battery.
rerunline=$(/usr/bin/grep -E '^rerun=' "$d/branchland.sh")
case "$rerunline" in *'$SG_GATE_FAILED'*) ok "the rerun runs only the failed names";; *) ko "the rerun does not take the failed names: '$rerunline'";; esac
case "${rerunline#*pytest}" in *' -n '*) ko "the serial rerun takes workers: '$rerunline'";; *) ok "the serial rerun stays serial";; esac
# (#646, the scope rule) The landing check runs the full battery whatever the delta would select: it asks the selector at the
# landing stage, and that answer is `all`, so pytest gets no file list. A stub python logs each argv and answers the stage.
printf '#!/bin/bash\necho "$*" >> "%s/selpy.log"\ncase "$*" in *squad.testselect*) case "$*" in *"--stage landing"*) echo "all: landing runs the full battery";; *) cat "%s/select.out" 2>/dev/null;; esac;; esac\nexit 0\n' "$T" "$T" > "$T/selpy"; chmod +x "$T/selpy"
printf 'tests/test_a.py\n' > "$T/select.out"
reset; rm -f "$T/selpy.log"; r=$(SG_STATE="$T/st" SG_GATE_PY="$T/selpy" SG_GATE_WORKERS=3 CHECKS='{"check_runs":[]}' bl)
/usr/bin/grep -q -- "squad.testselect --stage landing --base refs/sg-gate/dev" "$T/selpy.log" 2>/dev/null && ok "the landing check asks the selector at the landing stage" || ko "landing stage: '$(cat "$T/selpy.log" 2>&1)' $r"
/usr/bin/grep -qx -- "-m pytest -p no:deepeval -o heavy_cap_seconds=20 -n 3 -m not heavy" "$T/selpy.log" 2>/dev/null && ok "the landing check runs the full battery, never the selection" || ko "landing check argv: '$(cat "$T/selpy.log" 2>&1)' $r"
! /usr/bin/grep -q "tests/test_a.py" "$T/selpy.log" && ok "the landing check narrows nothing a delta would have selected" || ko "landing check selected: '$(cat "$T/selpy.log" 2>&1)'"
IFS=$'\t' read -r _ kind res _ sel scope < "$T/st/gate-status" 2>/dev/null
[ "$kind $res $sel $scope" = "fast pass all: landing runs the full battery full" ] && ok "the landing check's status line states the full scope and its reason" || ko "landing status: '$(cat "$T/st/gate-status" 2>&1)'"
# (#786) A red base holds the landing, and the base is read before the gate: the branch tip is green, the base head (dev) is red.
# The pass names the base, lands nothing, pushes nothing to dev, and says it once on alerts.log; the issue landed at the dev head
# gets one queued comment. BASECHECKS answers the check runs of the base head; CHECKS answers the branch tip.
# (#809) The red head is a commit that dev gained after the branch was cut: the branch does not contain it. rebase_on_red moves the
# branch on top of that head, as a fix branch is.
basehold() { reset; rm -f "$T/gate.log" "$T/st/auto/basecheck-"*; $G clone -q "$T/origin.git" "$T/hb" 2>/dev/null
  (cd "$T/hb" && $G checkout -q dev && echo z > h && $G add h && $G -c user.email=a@b -c user.name=t commit -q -m "red head" && $G push -q origin dev) >/dev/null 2>&1; rm -rf "$T/hb"
  before=$($O rev-parse dev); printf '1700000000\tgui/6-y\t%s\t6\n' "$before" >> "$T/st/landed.tsv"; }
basehold; r=$(BASECHECKS="$RED" SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS="$GREEN" bl)
case "$r" in "BRANCHLAND WAIT gui/7-x: base dev is red at "*": check (3.11) ended failure"*) ok "a red base: the pass names the base and waits";; *) ko "a red base: no wait line: '$r'";; esac
[ "$($O rev-parse dev)" = "$before" ] && ok "a red base: dev does not move" || ko "a red base: dev moved"
[ "$(/usr/bin/grep -c 'BASE CI' "$T/st/alerts.log" 2>/dev/null)" = 1 ] && ok "a red base: one alerts.log line" || ko "a red base: alerts $(cat "$T/st/alerts.log" 2>/dev/null)"
[ "$(ls "$T/st/cq" | /usr/bin/grep -c '#6-basecheck-')" = 1 ] && ok "a red base: one queued comment on the issue landed at that head" || ko "a red base: cq $(ls "$T/st/cq" 2>/dev/null)"
r2=$(BASECHECKS="$RED" CHECKS="$GREEN" bl)
[ -z "$r2" ] && [ "$(/usr/bin/grep -c 'BASE CI' "$T/st/alerts.log")" = 1 ] && [ "$(ls "$T/st/cq" | /usr/bin/grep -c '#6-basecheck-')" = 1 ] && ok "a red base: the second pass says and queues nothing more" || ko "a red base repeat: '$r2' alerts $(cat "$T/st/alerts.log") cq $(ls "$T/st/cq")"
# The base is read before the local gate: a zero-check tip on a red base runs no gate.
basehold; r=$(BASECHECKS="$RED" SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS='{"check_runs":[]}' bl)
case "$r" in *"BRANCHLAND WAIT gui/7-x: base dev is red at"*) ok "a red base with no tip check: the pass waits on the base";; *) ko "a red base, zero checks: '$r'";; esac
[ ! -e "$T/gate.log" ] && ok "a red base: the local gate does not run" || ko "a red base ran the local gate: $(cat "$T/gate.log")"
[ "$($O rev-parse dev)" = "$before" ] && ok "a red base with no tip check: dev does not move" || ko "a red base with no tip check: dev moved"
# The hold ends with the base: once dev is green, the same branch lands.
# (#809) A fix branch on top of the red head lands past the red base when its own evidence is green at that tip.
rebase_on_red() { rm -rf "$T/rb"; $G clone -q "$T/origin.git" "$T/rb" 2>/dev/null
  (cd "$T/rb" && $G checkout -q gui/7-x && $G -c user.email=a@b -c user.name=t rebase -q origin/dev && $G push -q -f origin gui/7-x) >/dev/null 2>&1; rm -rf "$T/rb"; rev APPROVE; }
basehold; rebase_on_red; tip=$($O rev-parse gui/7-x)
r=$(BASECHECKS="$RED" CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$tip" ] && ok "red base, fix on top of the red head: the branch lands" || ko "red base, fix on top of the red head: dev not moved to $tip: '$r'"
case "$r" in *"BRANCHLAND PAST RED BASE gui/7-x"*"dev is red at"*"contains"*) ok "red base, fix on top of the red head: one line names why it landed past the red base";; *) ko "no past-red line: '$r'";; esac
# its own check is red at that tip: it waits, past no red base
basehold; rebase_on_red; r=$(BASECHECKS="$RED" CHECKS="$RED" bl)
[ "$($O rev-parse dev)" = "$before" ] && ok "red base, fix on top of the red head, red own check: dev does not move" || ko "red own check landed past the red base: '$r'"
# no check run at that tip, local gate passes: the gate is the own evidence, and the branch lands past the red base
basehold; rebase_on_red; tip=$($O rev-parse gui/7-x); r=$(BASECHECKS="$RED" SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS='{"check_runs":[]}' bl)
[ "$($O rev-parse dev)" = "$tip" ] && case "$r" in *"BRANCHLAND PAST RED BASE gui/7-x"*) ok "red base, fix on top of the red head, zero checks, gate passes: lands past the red base";; *) ko "zero checks, gate passes: no past-red line: '$r'";; esac || ko "zero checks, gate passes: dev not moved: '$r'"
# a branch that does not contain the red head waits, even with its own check green
basehold; r=$(BASECHECKS="$RED" CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$before" ] && case "$r" in *"BRANCHLAND WAIT gui/7-x: base dev is red at"*) ok "red base, branch without the red head: waits";; *) ko "branch without the red head: '$r'";; esac || ko "branch without the red head landed"
rm -f "$T/st/auto/basecheck-"* "$T/st/auto/branchland-"*; basehold; r=$(CHECKS="$OLDG" bl)
[ "$($O rev-parse dev)" != "$before" ] && ok "a green base: the held branch lands" || ko "a green base: nothing landed: '$r'"
# (#790) The base is read again right after the landing push: the head it lands on has its own answer, written after the push.
new=$($O rev-parse dev); [ -e "$T/st/auto/basecheck-$new" ] && ok "a landing reads the base head it pushed" || ko "no read of the pushed head $new: $(ls "$T/st/auto")"
rm -f "$T/st/auto/basecheck-"*; /usr/bin/grep -v 'gui/6-y' "$T/st/landed.tsv" > "$T/st/landed.tmp" && mv "$T/st/landed.tmp" "$T/st/landed.tsv"
# (#837) The ruff fix stages only the files it changed. A generated file the gate rewrote in the trial (graphify-out/graph.json,
# as the post-checkout hook does) is not in the landing commit; the commit carries the one file ruff fixed.
reset; $G clone -q "$T/origin.git" "$T/lb" 2>/dev/null
(cd "$T/lb" && $G checkout -q gui/7-x && mkdir -p graphify-out && echo '{}' > graphify-out/graph.json && echo broken > lint.py && $G add graphify-out lint.py && $G -c user.email=a@b -c user.name=t commit -q -m "lint and graph" && $G push -q origin gui/7-x) >/dev/null 2>&1; rm -rf "$T/lb"; rev APPROVE
RUFF_PY="echo changed > graphify-out/graph.json; grep -qx fixed lint.py || { echo 'Found 1 error.'; echo '[*] 1 fixable with the \`--fix\` option.'; exit 1; }"
r=$(SG_LOCAL_GATE_CMD="$RUFF_PY" SG_LOCAL_GATE_RERUN_CMD="exit 1" SG_RUFF_FIX_CMD="echo fixed > lint.py" CHECKS='{"check_runs":[]}' bl)
case "$r" in *"BRANCHLAND LANDED gui/7-x"*) ok "a ruff fix beside a generated tracked file: the branch lands";; *) ko "no landing with a generated file dirty: '$r'";; esac
fixc=$($O log --format=%H -n1 --grep='^Apply ruff --fix at the landing' dev)
[ -n "$fixc" ] && [ "$($O show --name-only --format= "$fixc")" = "lint.py" ] && ok "the ruff fix commit carries only the .py file" || ko "the ruff fix commit carries: '$($O show --name-only --format= "$fixc" 2>&1 | tr '\n' ' ')'"
[ "$($O show dev:graphify-out/graph.json 2>/dev/null)" = "{}" ] && ok "the generated file on dev is the branch's, not the trial's" || ko "dev carries the generated file the gate rewrote"
# (#1396) A red the branch owns at an approved tip opens ONE fix round: the landing pass writes the CHANGES review the fix pass
# reads, the way its rebase-conflict path already does. The round then owns the tip, so a base head that moves under it costs no
# second gate. An inconclusive gate (exit 4) is the machine's, never the branch's: it opens no round and leaves the approval alone.
GATE_KO_LOG="echo gate >> $T/gate.log; echo 'FAILED tests/test_a.py::test_one - boom'; exit 1"
moved=0; movebase() { moved=$((moved+1)); (cd "$T/w" && $G checkout -q -B dev origin/dev && echo "b$moved" > "b$moved" \
  && $G add "b$moved" && $G -c user.email=a@b -c user.name=t commit -q -m "base $moved" && $G push -q origin dev) >/dev/null 2>&1; }
reset; rm -f "$T/gate.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_KO_LOG" CHECKS='{"check_runs":[]}' bl)
case "$(head -1 "$T/st/review-gui_7-x.md" 2>/dev/null)" in
  "VERDICT: CHANGES"*) ok "a branch-owned red writes the CHANGES review the fix pass reads";;
  *) ko "no CHANGES review after a red gate: '$(head -2 "$T/st/review-gui_7-x.md" 2>&1)'";; esac
/usr/bin/grep -q "tests/test_a.py::test_one" "$T/st/review-gui_7-x.md" 2>/dev/null \
  && ok "the review names the failing id" || ko "the review names no failing id: $(cat "$T/st/review-gui_7-x.md" 2>&1)"
[ -s "$T/st/review-gui_7-x.sha" ] && [ "$(cat "$T/st/review-gui_7-x.sha")" = "$($O rev-parse gui/7-x)" ] \
  && ok "the review is kept at the tip it read" || ko "the review sha is not the tip: '$(cat "$T/st/review-gui_7-x.sha" 2>&1)'"
ls "$T/st/auto"/branchland-fixpending-gui_7-x-* >/dev/null 2>&1 \
  && ok "a fix-pending marker names the tip" || ko "no fix-pending marker: $(ls "$T/st/auto" 2>&1)"
movebase; r2=$(SG_LOCAL_GATE_CMD="$GATE_KO_LOG" CHECKS='{"check_runs":[]}' bl)
[ "$(wc -l < "$T/gate.log" | tr -d ' ')" = 1 ] \
  && ok "a base head that moves under the fix round pays no second gate" || ko "the gate ran again under a fix round: $(cat "$T/gate.log")"
reset; rm -f "$T/gate.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_INC" CHECKS='{"check_runs":[]}' bl)
case "$(head -1 "$T/st/review-gui_7-x.md" 2>/dev/null)" in
  "VERDICT: APPROVE"*) ok "an inconclusive gate leaves the approval alone";;
  *) ko "an inconclusive gate replaced the approval: '$(head -1 "$T/st/review-gui_7-x.md" 2>&1)'";; esac
ls "$T/st/auto"/branchland-fixpending-gui_7-x-* >/dev/null 2>&1 \
  && ko "an inconclusive gate opened a fix round" || ok "an inconclusive gate opens no fix round"
# A new tip is a new question: the skip names the tip it answered for, so the fixed tip is gated again.
reset; rm -f "$T/gate.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_KO_LOG" CHECKS='{"check_runs":[]}' bl)
(cd "$T/w" && $G checkout -q gui/7-x && echo zz > zz && $G add zz && $G -c user.email=a@b -c user.name=t commit -q -m "fix the gate" \
  && $G push -q origin gui/7-x) >/dev/null 2>&1; rev APPROVE
r2=$(SG_LOCAL_GATE_CMD="$GATE_KO_LOG" CHECKS='{"check_runs":[]}' bl)
[ "$(wc -l < "$T/gate.log" | tr -d ' ')" = 2 ] \
  && ok "a new tip is gated again" || ko "a new tip inherited the skip of the old one: $(cat "$T/gate.log")"
rm -rf "$T"; exit $fail
