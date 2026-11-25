#!/bin/bash
# seconds: 86.83
# Regression test for branchland.sh (#355): the pass that lands a pushed work branch whose check is green in `dev` by a
# fast-forward push. Stub gh (every argv logged), a bare local remote, a stub board, temp state dir. One line per case.
# usage: bash test-branchland.sh [scripts dir]
unset SG_SERIAL_HELD   # a gate inside a gate hands the lock name down; this suite takes its own gate lock, so its wait is proved
unset SG_BOARD_VOCAB   # the loop's shipped vocabulary (Closed, not Done) is ambient in a live shell; this suite expects the default
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/bin" "$T/co/runs"
cp "$d/issue_ledger.py" "$d/gate.sh" "$d/caps.sh" "$d/serial.sh" "$d/fast-gate.sh" "$d/base-battery.sh" "$d/branchland.sh" "$d/revformat.py" "$d/workbranches.sh" "$d/ffland.sh" "$d/landstatus.sh" "$d/address.sh" "$d/ghread.py" "$d/ghthrottle.py" "$d/drainpolicy.py" "$d/ghcall.sh" "$d/basecheck.sh" "$d/wave.sh" "$d/columnlimit.sh" "$d/wavepause.py" "$d/child_issues.py" "$d/autohunt.sh" "$T/s/" 2>/dev/null
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps   # a gate takes gate slots (#567): this repository's squad.caps and a throwaway budget
printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=main SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s SG_LAND_REMOTE=%s/origin.git BOARD=%s/board.sh\n' "$T/st" "$T/co" "$T" "$T" > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
$G init -q -b main "$T/co"; (cd "$T/co" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init && $G branch -q dev)
# The remote the landing pushes to: a bare copy with main and dev, and gui/7-x one commit ahead of dev. mkorigin rebuilds it per case.
mkorigin() { rm -rf "$T/origin.git" "$T/w"; $G clone -q --bare "$T/co" "$T/origin.git"; $G clone -q "$T/origin.git" "$T/w" 2>/dev/null
  (cd "$T/w" && $G checkout -q -b gui/7-x origin/dev && echo y > g && $G add g && $G -c user.email=a@b -c user.name=t commit -q -m "work (Refs #7)" && $G push -q origin gui/7-x 2>/dev/null); }
mkorigin
# mkbranch8: a second open work branch (issue 8), beside gui/7-x, so a case can prove that one branch's review holds only itself.
mkbranch8() { rm -rf "$T/w8"; $G clone -q "$T/origin.git" "$T/w8" 2>/dev/null
  (cd "$T/w8" && $G checkout -q -b gui/8-y origin/dev && echo y8 > h8 && $G add h8 && $G -c user.email=a@b -c user.name=t commit -q -m "work 8 (Refs #8)" && $G push -q origin gui/8-y 2>/dev/null); rm -rf "$T/w8"; }
# The stub: no open PR unless PRS names one. CHECKS names the check-runs answer.
cat > "$T/bin/gh" <<J
#!/bin/bash
echo "\$*" >> "$T/gh.log"
case "\$*" in
  *"pulls?per_page"*) echo "\${PRS:-[]}";;
  *"issues(states: OPEN"*) n=\$(for x in \${OPEN:-7}; do printf '{"number":%s,"title":"t","body":"","state":"OPEN"},' "\$x"; done); [ -n "\${LANDBUG:-}" ] && n="\$n{\"number\":\$LANDBUG,\"title\":\"t\",\"body\":\"\",\"state\":\"OPEN\",\"labels\":{\"nodes\":[{\"name\":\"\${LANDBUG_LABEL:-loop-bug:landing}\"}]}},"; echo "{\"data\":{\"repository\":{\"issues\":{\"pageInfo\":{\"hasNextPage\":false},\"nodes\":[\${n%,}]}}}}";;
  *"/actions/runs/"*"/jobs"*) echo '{"total_count":1,"jobs":[{"name":"check (3.11)","status":"completed","conclusion":"failure","steps":[{"name":"Run tests","number":1,"conclusion":"failure"}]}]}';;
  *"/check-runs"*) d=\$(/usr/bin/git -C $T/origin.git rev-parse -q --verify refs/heads/dev); x=\${CHECKS:-{\"check_runs\":[]\}}; case "\$*" in *"/commits/\$d/"*) x=\${BASECHECKS:-\$x};; esac; echo "\$x";;
  *"issues/8/labels"*) [ -z "\${LABELFAIL:-}" ] || exit 1;;
  *"issues/7/comments"*) cat > "$T/comment.body"; echo '{"id":1}';;
  *"issues/7"*) echo '{"number":7,"state":"closed"}';;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
GREEN='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"success"}]}'
# OLDG: green with its checks finished long ago, so the base read caches its answer (#790).
OLDG='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"success","completed_at":"2001-01-01T00:00:00Z"}]}'
RED='{"check_runs":[{"id":10,"name":"check (3.11)","status":"completed","conclusion":"failure","details_url":"https://github.com/x/y/actions/runs/7701/job/10"}]}'
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

bsha=$($O rev-parse gui/7-x)   # the tip the landing starts from, before the push moves dev (#1836)
r=$(CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ko "dev did not move: '$r'" || ok "green: the branch landed in dev"
# The landing's own commit status (#1836): pending on the tip it started from, success on the tip it landed. GitHub keeps
# created_at/updated_at per status, so the landing's duration sits on the commit; a status left pending is a landing that died.
/usr/bin/grep -q "statuses/$bsha -f state=pending" "$T/gh.log" && ok "the landing is marked pending on the tip it started from" || ko "no pending status on $bsha: $(/usr/bin/grep -c statuses "$T/gh.log") status calls"
/usr/bin/grep -q "statuses/$($O rev-parse dev) -f state=success" "$T/gh.log" && ok "the landing is marked success on the tip it landed" || ko "no success status on $($O rev-parse dev)"
/usr/bin/grep -q 'statuses/.* -f context=squad/landing' "$T/gh.log" && ok "the status names its context" || ko "the status names no context"
$O merge-base --is-ancestor main dev && [ "$($O log --format=%s dev | head -1)" = "work (Refs #7)" ] && ok "dev is a fast-forward of its old head" || ko "dev is not a fast-forward"
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
[ "$(wc -l < "$T/gate.log" | tr -d ' ')" = 1 ] && case "$r2" in
  *"asked for changes"*) ok "gate fail: one gate, and the fix round it opened owns the tip (#1396)";;
  *) ko "gate fail reran: $(cat "$T/gate.log") '$r2'";; esac
# A failed gate reruns only what failed, once (SG_LOCAL_GATE_RERUN_CMD stubs it): a red that machine load caused passes alone and the
# branch lands; a red that is real fails again and the branch waits. The rerun takes the node ids the gate named (#2452), so the
# red these two cases prove is a failed test, as a load flake is.
GATE_KO_IDS="echo 'FAILED tests/test_a.py::test_one - boom'; echo 'FAILED tests/test_b.py::test_two'; exit 1"
RERUN_OK="echo \"rerun \$SG_GATE_FAILED\" >> $T/rerun.log; exit 0"; RERUN_KO="echo rerun >> $T/rerun.log; echo 'still red'; exit 1"
reset; rm -f "$T/gate.log" "$T/rerun.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_KO_IDS" SG_LOCAL_GATE_RERUN_CMD="$RERUN_OK" CHECKS='{"check_runs":[]}' bl)
[ -s "$T/rerun.log" ] && case "$r" in *"BRANCHLAND LANDED gui/7-x"*) ok "a red that passes on its rerun: the branch lands";; *) ko "a load red did not land: '$r'";; esac || ko "a failed gate ran no rerun: '$r'"
reset; rm -f "$T/gate.log" "$T/rerun.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_KO_IDS" SG_LOCAL_GATE_RERUN_CMD="$RERUN_KO" CHECKS='{"check_runs":[]}' bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && case "$r" in *"BRANCHLAND WAIT gui/7-x"*"still red"*) ok "a red that fails its rerun: nothing lands, and the rerun says why";; *) ko "a real red: '$r'";; esac || ko "a real red landed"
# The rerun is handed the node ids that failed (the suite runs with no cache provider, so --lf has nothing to read).
reset; rm -f "$T/gate.log" "$T/rerun.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_KO_IDS" SG_LOCAL_GATE_RERUN_CMD="$RERUN_OK" CHECKS='{"check_runs":[]}' bl)
[ "$(cat "$T/rerun.log" 2>/dev/null)" = "rerun tests/test_a.py::test_one tests/test_b.py::test_two" ] && ok "the rerun gets exactly the failed node ids" || ko "the rerun ids: '$(cat "$T/rerun.log" 2>&1)'"
# (#699) An inconclusive gate (exit 4: fast-gate ran its one retry, and that failed too) never lands and gets no second rerun:
# a rerun that passed would grant the landing the gate held.
GATE_INC="echo gate >> $T/gate.log; echo 'fast-gate: saturation: the battery crashed twice; inconclusive, so the landing holds'; exit 4"
reset; rm -f "$T/gate.log" "$T/rerun.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_INC" SG_LOCAL_GATE_RERUN_CMD="$RERUN_OK" CHECKS='{"check_runs":[]}' bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && [ ! -e "$T/rerun.log" ] && ok "an inconclusive gate: nothing lands and no rerun runs" || ko "inconclusive gate: rerun '$(cat "$T/rerun.log" 2>&1)': '$r'"
case "$r" in *"BRANCHLAND WAIT gui/7-x"*inconclusive*) ok "an inconclusive gate says so";; *) ko "inconclusive gate line: '$r'";; esac
/usr/bin/grep -qh "inconclusive" "$T/st/cq/"x__y#7-status~* 2>/dev/null && ok "the status comment names the inconclusive result" || ko "inconclusive status: $(cat "$T/st/cq/"* 2>&1)"
# (#1684) A gate the machine's full budget kept from running (exit 73) read the tree not at all: nothing lands, no rerun runs,
# and no fix round is opened for a tree no battery saw. One line says the budget was full, and so does the status comment.
GATE_CAP="echo gate >> $T/gate.log; echo 'caps: caps.gates is full (6 slots), held by gate-1: gate-2 skipped, not queued (try)'; exit 73"
reset; rm -f "$T/gate.log" "$T/rerun.log" "$T/st/auto/"branchland-fixpending-*; r=$(SG_LOCAL_GATE_CMD="$GATE_CAP" SG_LOCAL_GATE_RERUN_CMD="$RERUN_OK" CHECKS='{"check_runs":[]}' bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && [ ! -e "$T/rerun.log" ] && ok "a gate with no slot to take: nothing lands and no rerun runs" || ko "capacity gate: rerun '$(cat "$T/rerun.log" 2>&1)': '$r'"
case "$r" in *"BRANCHLAND WAIT gui/7-x"*"budget was full"*) ok "a gate with no slot says the machine's budget was full";; *) ko "capacity line: '$r'";; esac
ls "$T/st/auto/"branchland-fixpending-* >/dev/null 2>&1 && ko "a gate that never ran opened a fix round" || ok "a gate that never ran opens no fix round"
/usr/bin/grep -qh "budget was full" "$T/st/cq/"x__y#7-status~* 2>/dev/null && ok "the status comment names the full budget" || ko "capacity status: $(cat "$T/st/cq/"* 2>&1)"
# A full budget is transient, so it writes no wait marker: the marker is one line per head and base, and the branch would stay
# out of the gate until the base moved. The next pass gates it again.
reset; rm -f "$T/gate.log" "$T/rerun.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_CAP" CHECKS='{"check_runs":[]}' bl); r2=$(SG_LOCAL_GATE_CMD="$GATE_CAP" CHECKS='{"check_runs":[]}' bl)
[ "$(wc -l < "$T/gate.log" | tr -d ' ')" = 2 ] && ok "a full budget writes no wait marker: the next pass gates the branch again" || ko "the branch waited on a marker a full budget wrote: '$(cat "$T/gate.log" 2>&1)'"
# A measured red is never cleared by a retry that never ran: the rerun meets the full budget, and the red keeps its fix round.
RERUN_CAP="echo 'caps: caps.gates is full (6 slots), held by gate-1: gate-2 skipped, not queued (try)'; exit 73"
reset; rm -f "$T/gate.log" "$T/rerun.log" "$T/st/auto/"branchland-fixpending-*; r=$(SG_LOCAL_GATE_CMD="$GATE_KO_IDS" SG_LOCAL_GATE_RERUN_CMD="$RERUN_CAP" CHECKS='{"check_runs":[]}' bl)
ls "$T/st/auto/"branchland-fixpending-* >/dev/null 2>&1 && ok "a red whose rerun met a full budget keeps its fix round" || ko "a rerun that never ran cleared the red: '$r'"
case "$r" in *"BRANCHLAND WAIT gui/7-x"*failed*) ok "the line still names the red, never the budget";; *) ko "the red's line: '$r'";; esac
# (#2330) A landing gate whose slot wait ran out answers 75 with the budget's waited-out line. It read the tree not at all, as a
# full budget: no rerun, no fix round, no wait marker, and one wait line that says the gate did not run. A 75 with no such line
# is the gate's own hold deadline and keeps the failed path.
GATE_WAIT="echo gate >> $T/gate.log; echo 'caps: gate-2 waited 960s for caps.gates, held by child-gate-1 (lease pid 1, live)'; exit 75"
reset; rm -f "$T/gate.log" "$T/rerun.log" "$T/st/auto/"branchland-fixpending-*; r=$(SG_LOCAL_GATE_CMD="$GATE_WAIT" SG_LOCAL_GATE_RERUN_CMD="$RERUN_OK" CHECKS='{"check_runs":[]}' bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && [ ! -e "$T/rerun.log" ] && ok "2330 a waited-out slot wait: nothing lands and no rerun runs" || ko "2330 waited-out gate: rerun '$(cat "$T/rerun.log" 2>&1)': '$r'"
case "$r" in *"BRANCHLAND WAIT gui/7-x: the local gate did not run"*) ok "2330 a waited-out slot wait says the gate did not run";; *) ko "2330 waited-out line: '$r'";; esac
ls "$T/st/auto/"branchland-fixpending-* >/dev/null 2>&1 && ko "2330 a gate that waited out its slots opened a fix round" || ok "2330 a gate that waited out its slots opens no fix round"
/usr/bin/grep -qh "failed at" "$T/st/cq/"x__y#7-status~* 2>/dev/null && ko "2330 the status comment calls the waited-out gate failed: $(cat "$T/st/cq/"* 2>&1)" || ok "2330 the status comment never calls the waited-out gate failed"
reset; rm -f "$T/gate.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_WAIT" CHECKS='{"check_runs":[]}' bl); r2=$(SG_LOCAL_GATE_CMD="$GATE_WAIT" CHECKS='{"check_runs":[]}' bl)
[ "$(wc -l < "$T/gate.log" | tr -d ' ')" = 2 ] && ok "2330 a waited-out slot wait writes no wait marker: the next pass gates again" || ko "2330 the branch waited on a marker: '$(cat "$T/gate.log" 2>&1)'"
GATE_HOLD="echo gate >> $T/gate.log; echo 'serial: holder past its deadline'; exit 75"
reset; rm -f "$T/gate.log" "$T/rerun.log" "$T/st/auto/"branchland-fixpending-*; r=$(SG_LOCAL_GATE_CMD="$GATE_HOLD" SG_LOCAL_GATE_RERUN_CMD="$RERUN_OK" CHECKS='{"check_runs":[]}' bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && [ ! -e "$T/rerun.log" ] && case "$r" in *"BRANCHLAND WAIT gui/7-x"*failed*) ok "2330 a 75 with no waited-out line keeps the failed path, and nothing named is rerun";; *) ko "2330 the bare 75 line: '$r'";; esac || ko "2330 a bare 75 read as a wait: '$r'"
# (#2452) A gate stopped during its slot wait exits 75 with the live wait line and no waited-out line. It proved nothing about
# the tree, so it is a wait: nothing lands, no fix round opens, no wait marker is written, and its review stays the one it had.
GATE_SLOTSTOP="echo gate >> $T/gate.log; echo 'caps: gate-2 is waiting for 3 slot(s): caps.gates is full (3 slots), held by gate-1 (lease pid 1, live, since 10:32:11)'; exit 75"
reset; rm -f "$T/gate.log" "$T/rerun.log" "$T/st/auto/"branchland-fixpending-*; r=$(SG_LOCAL_GATE_CMD="$GATE_SLOTSTOP" SG_LOCAL_GATE_RERUN_CMD="$RERUN_OK" CHECKS='{"check_runs":[]}' bl)
case "$r" in *"BRANCHLAND WAIT gui/7-x: the local gate did not run"*) ok "2452 a gate stopped during its slot wait is a wait, not a red";; *) ko "2452 the stopped slot wait: '$r'";; esac
[ ! -e "$T/rerun.log" ] && ok "2452 a gate stopped during its slot wait runs no rerun" || ko "2452 the stopped gate reran: $(cat "$T/rerun.log" 2>&1)"
ls "$T/st/auto/"branchland-fixpending-* >/dev/null 2>&1 && ko "2452 a stopped slot wait opened a fix round" || ok "2452 a stopped slot wait opens no fix round"
/usr/bin/grep -q '## Blocking' "$T/st/review-gui_7-x.md" 2>/dev/null && ko "2452 the slot wait became a Blocking finding: $(cat "$T/st/review-gui_7-x.md")" || ok "2452 the slot wait writes no Blocking finding"
reset; rm -f "$T/gate.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_SLOTSTOP" CHECKS='{"check_runs":[]}' bl); r2=$(SG_LOCAL_GATE_CMD="$GATE_SLOTSTOP" CHECKS='{"check_runs":[]}' bl)
[ "$(wc -l < "$T/gate.log" | tr -d ' ')" = 2 ] && ok "2452 a stopped slot wait writes no wait marker: the next pass gates again" || ko "2452 a stopped gate wrote a wait marker: '$(cat "$T/gate.log" 2>&1)'"
# (#2452) A rerun that only waited for a slot proves nothing about the tree. The finding keeps the red the first gate
# measured, and no gate slot wait reaches a review.
RERUN_WAIT="echo 'caps: gate-92719 is waiting for 3 slot(s): caps.gates is full (3 slots), held by gate-97970 (lease pid 97718, live)'; echo 'All checks passed!'; exit 1"
reset; rm -f "$T/gate.log" "$T/rerun.log" "$T/st/auto/"branchland-fixpending-*; r=$(SG_LOCAL_GATE_CMD="$GATE_KO" SG_LOCAL_GATE_RERUN_CMD="$RERUN_WAIT" CHECKS='{"check_runs":[]}' bl)
/usr/bin/grep -q 'mypy: 3 errors' "$T/st/review-gui_7-x.md" 2>/dev/null && ok "2452 a red keeps the evidence that named it in the finding" || ko "2452 the finding lost the red: $(cat "$T/st/review-gui_7-x.md" 2>&1)"
/usr/bin/grep -q 'waiting for 3 slot' "$T/st/review-gui_7-x.md" 2>/dev/null && ko "2452 a gate slot wait became a Blocking finding: $(cat "$T/st/review-gui_7-x.md")" || ok "2452 a gate slot wait writes no Blocking finding"
ls "$T/st/auto/"branchland-fixpending-* >/dev/null 2>&1 && ok "2452 the real red still opens its fix round" || ko "2452 the real red lost its fix round: '$r'"
# (#2452) The real budget: another gate holds every caps.gates slot, so the landing gate's own bounded slot wait runs out.
reset; rm -f "$T/gate.log" "$T/rerun.log" "$T/st/auto/"branchland-fixpending-*
PYTHONPATH="$SG_SQUAD" python3 -m squad.caps take gates blocker-2452 --count 99 --pid $$ --target "$T/co" >/dev/null 2>&1
r=$(SG_GATE_SLOT_WAIT_SECONDS=1 SG_LOCAL_GATE_CMD="echo gate >> $T/gate.log" SG_LOCAL_GATE_RERUN_CMD="$RERUN_OK" CHECKS='{"check_runs":[]}' bl)
PYTHONPATH="$SG_SQUAD" python3 -m squad.caps release gates blocker-2452 --target "$T/co" >/dev/null 2>&1
case "$r" in *"BRANCHLAND WAIT gui/7-x: the local gate did not run"*) ok "2452 a full caps.gates: the slot wait is a wait, not a red";; *) ko "2452 the real slot wait: '$r'";; esac
/usr/bin/grep -q '## Blocking' "$T/st/review-gui_7-x.md" 2>/dev/null && ko "2452 the real slot wait became a Blocking finding" || ok "2452 the real slot wait writes no Blocking finding"
# The default gate runs with none of the loop's SG_* variables (a test that reads one would see the live loop state) and at the
# project's own line: the parallel pass lifts no cap, so it records every unmarked call at or over 2 s and only those run
# again, serially (#1726).
printf '#!/bin/bash\necho "SG_STATE=${SG_STATE:-unset} $*" >> %s/py.log\ncase "$*" in *squad.testselect*) echo "all: stub";; esac\n' "$T" > "$T/py"; chmod +x "$T/py"
reset; rm -f "$T/py.log"; r=$(SG_STATE="$T/st" SG_GATE_PY="$T/py" CHECKS='{"check_runs":[]}' bl)
# The gate lock's process read (serial.sh's incarnation check) runs in the loop env and needs SG_STATE (#1508); only the gate
# command inside gate.sh is stripped, so the proof reads every line but that reader's. It is read by shape, not by caller: the
# interpreter is the gate's own (#1086), so the stub logs the reader's call beside the tools'.
/usr/bin/grep -q 'pytest' "$T/py.log" 2>/dev/null && ! /usr/bin/grep -v -- ' -m squad.procs ' "$T/py.log" | /usr/bin/grep -q 'SG_STATE=/' && ok "the default gate runs with no SG_* variable" || ko "the gate saw the loop env: $(cat "$T/py.log" 2>&1)"
/usr/bin/grep 'pytest' "$T/py.log" 2>/dev/null | /usr/bin/grep -q -- '-o heavy_cap_seconds' && ko "the default gate lifts the project line: $(grep pytest "$T/py.log" 2>&1)" || ok "the default gate runs at the project line and lifts no cap (#1726)"
# The gate proves what lands: the tip rebased onto dev, never the bare tip. A red the branch inherited from an old base is fixed
# on dev, so the branch lands once dev carries the fix, and a failed gate runs again when dev moves.
GATE_FIXED="echo \"\$PWD \$(/usr/bin/git rev-parse HEAD)\" >> $T/gate.log; test -f fixed || { echo 'inherited red'; exit 1; }"
reset; rm -f "$T/gate.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_FIXED" CHECKS='{"check_runs":[]}' bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "an inherited red, not fixed on dev: nothing lands" || ko "an inherited red landed: '$r'"
(cd "$T/w" && $G checkout -q dev && $G pull -q origin dev 2>/dev/null && echo z > fixed && $G add fixed && $G -c user.email=a@b -c user.name=t commit -q -m "fix on dev" && $G push -q origin dev 2>/dev/null)
rev APPROVE
# (#1396) The red the first gate named belongs to the tip until its fix round answers, so a base head that moves under it costs
# no second gate. The fixed tip is a new question: it is gated again, and lands on the rebased tree.
r=$(SG_LOCAL_GATE_CMD="$GATE_FIXED" CHECKS='{"check_runs":[]}' bl)
[ "$(wc -l < "$T/gate.log" | tr -d ' ')" = 1 ] && ok "dev moved under a red tip: the gate is not paid again" || ko "the gate reran under the fix round: $(cat "$T/gate.log" 2>&1) '$r'"
(cd "$T/w" && $G checkout -q gui/7-x && echo w2 > w2 && $G add w2 && $G -c user.email=a@b -c user.name=t commit -q -m "work again" && $G push -q origin gui/7-x 2>/dev/null)
rev APPROVE; r=$(SG_LOCAL_GATE_CMD="$GATE_FIXED" CHECKS='{"check_runs":[]}' bl)
[ "$(wc -l < "$T/gate.log" | tr -d ' ')" = 2 ] && ok "the fixed tip is gated again" || ko "a new tip inherited the skip: $(cat "$T/gate.log" 2>&1) '$r'"
case "$r" in *"BRANCHLAND LANDED gui/7-x"*) ok "the fix on dev: the branch lands, gated on the rebased tip";; *) ko "the rebased tip did not land: '$r'";; esac
[ "$($O log --format=%s dev | head -3 | tr '\n' ,)" = "work again,work (Refs #7),fix on dev," ] && ok "dev holds the fix and the work, linear" || ko "dev history: $($O log --format=%s dev | head -3 | tr '\n' ,)"
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
r=$(CHECKS="$GREEN" bl); $O log --format=%s dev | tr '\n' ' ' | /usr/bin/grep -q "^work (Refs #7) other init" && ok "a moved dev: the branch is rebased on top and lands" || ko "no rebase landing: $($O log --format=%s dev | tr '\n' ' ') $r"
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
[ "$(cat "$T/st/auto/kept-fix_8-rebase-hand")" = origin/dev ] && ok "2571 a kept wave branch's marker gets no salvage line" || ko "2571 the kept wave marker changed: '$(cat "$T/st/auto/kept-fix_8-rebase-hand" | tr '\n' '|')'"
# (#2866) The round rebases on a ref that is on the remote. A marker that names a wave branch the loop deleted gets its round on
# origin/dev, the ref the conflict was found with; a stale tracking ref in the checkout does not keep the dead ref alive.
reset; hand8; hs=$($O rev-parse fix/8-rebase-hand); printf 'origin/wave/run-gone\n' > "$T/st/auto/kept-fix_8-rebase-hand"
$G -C "$T/co" update-ref refs/remotes/origin/wave/run-gone "$($G -C "$T/co" rev-parse dev)"
printf 'VERDICT: APPROVE\n' > "$T/st/review-fix_8-rebase-hand.md"; printf '%s\n' "$hs" > "$T/st/review-fix_8-rebase-hand.sha"
r=$(OPEN=8 SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS='{"check_runs":[]}' bl)
/usr/bin/grep -q 'Rebase this branch on origin/dev,' "$T/st/review-fix_8-rebase-hand.md" && ! /usr/bin/grep -q 'wave/run-gone' "$T/st/review-fix_8-rebase-hand.md" \
  && ok "2866 a kept marker whose ref is gone from the remote gets its round on origin/dev" || ko "2866 the round names a ref that is gone: '$(/usr/bin/grep -o 'Rebase this branch on [^,]*' "$T/st/review-fix_8-rebase-hand.md")' '$r'"
$G -C "$T/co" update-ref -d refs/remotes/origin/wave/run-gone
reset; hand8; hs=$($O rev-parse fix/8-rebase-hand); printf 'origin/wave/run-live\n' > "$T/st/auto/kept-fix_8-rebase-hand"; $O branch -q wave/run-live dev
printf 'VERDICT: APPROVE\n' > "$T/st/review-fix_8-rebase-hand.md"; printf '%s\n' "$hs" > "$T/st/review-fix_8-rebase-hand.sha"
r=$(OPEN=8 SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS='{"check_runs":[]}' bl)
/usr/bin/grep -q 'Rebase this branch on origin/wave/run-live,' "$T/st/review-fix_8-rebase-hand.md" && ok "2866 a kept marker whose ref is on the remote keeps its round on that ref" || ko "2866 a live kept ref was replaced: '$(/usr/bin/grep -o 'Rebase this branch on [^,]*' "$T/st/review-fix_8-rebase-hand.md")' '$r'"
# (#2571) A salvage branch gets a rebase round when its pusher marked it (auto/salvaged-<key>), never by its name. Its kept marker
# keeps origin/dev on line 1 and one `salvage <tip> <base>` line per round, never a `round` line. One round per tip; a new tip on
# the base of the last round, or a third conflict, freezes the card, calls no wave.sh, and keeps the branch and the marker.
SB=fix/8-salvage-run-s; SK=fix_8-salvage-run-s; SKM="$T/st/auto/kept-$SK"
# salv8: dev changes g, and the salvage branch, cut from main, changes g too, so its rebase on dev conflicts. The review approves it.
salv8() { reset; rm -f "$T/st/auto/"*"-$SK" "$T/st/review-$SK".* "$T/st/landround-$SK.head" "$T/lc.called"
  (cd "$T/w" && $G checkout -q dev && echo other > g && $G add g && $G -c user.email=a@b -c user.name=t commit -q -m "dev g" && $G push -q origin dev 2>/dev/null \
  && $G checkout -q -b "$SB" origin/main && echo mine > g && $G add g && $G -c user.email=a@b -c user.name=t commit -q -m "salvage (Refs #8)" && $G push -q origin "$SB" 2>/dev/null)
  sapprove; }
sapprove() { printf 'VERDICT: APPROVE\n' > "$T/st/review-$SK.md"; $O rev-parse "$SB" > "$T/st/review-$SK.sha"; }
devmv() { (cd "$T/w" && $G checkout -q dev && date +%s%N >> "d$1" && $G add "d$1" && $G -c user.email=a@b -c user.name=t commit -q -m "dev $1" && $G push -q origin dev 2>/dev/null); }
newtip() { (cd "$T/w" && $G checkout -q "$SB" && echo "$1" >> g && $G add g && $G -c user.email=a@b -c user.name=t commit -q -m "salvage $1 (Refs #8)" && $G push -q origin "$SB" 2>/dev/null); sapprove; }
sbl() { OPEN=8 SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS='{"check_runs":[]}' bl; }
# stubwave <cmd>: run <cmd> with wave.sh replaced by a stub that logs each call
stubwave() { mv "$T/s/wave.sh" "$T/s/wave.real"; printf '#!/bin/bash\necho "$*" >> "%s/lc.called"\n' "$T" > "$T/s/wave.sh"; chmod +x "$T/s/wave.sh"; "$@"; mv "$T/s/wave.real" "$T/s/wave.sh"; }
# an unmarked salvage-named branch keeps its review and gets no round
salv8; r=$(sbl)
head -1 "$T/st/review-$SK.md" | /usr/bin/grep -q APPROVE && [ ! -e "$SKM" ] && ok "2571 an unmarked salvage-named branch keeps its review and gets no round" || ko "2571 an unmarked salvage branch got a round: '$(cat "$SKM" 2>&1)' '$r'"
# a marked salvage branch: one CHANGES round review at its tip, and the marker
salv8; touch "$T/st/auto/salvaged-$SK"; st1=$($O rev-parse "$SB"); dv1=$($O rev-parse dev); r=$(sbl)
[ "$(python3 "$T/s/revformat.py" --classify "$T/st/review-$SK.md" "$T/st/review-$SK.sha" "$st1")" = changes ] && ok "2571 a marked salvage conflict gets a CHANGES round review at its tip" || ko "2571 no round review: '$(cat "$T/st/review-$SK.md" 2>&1)' '$r'"
[ "$(cat "$SKM" 2>/dev/null)" = "origin/dev
salvage $st1 $dv1" ] && ok "2571 the kept marker names origin/dev and one salvage line, no round line" || ko "2571 the marker: '$(cat "$SKM" 2>&1 | tr '\n' '|')'"
[ "$($O rev-parse "$SB")" = "$st1" ] && ok "2571 the round leaves the branch at its tip" || ko "2571 the branch moved"
# the same tip on a new base writes no second round
devmv 2; sapprove; r=$(sbl)
[ "$(/usr/bin/grep -c '^salvage ' "$SKM")" = 1 ] && head -1 "$T/st/review-$SK.md" | /usr/bin/grep -q APPROVE && ok "2571 the same tip on a new base writes no second round" || ko "2571 a second round at the same tip: '$(cat "$SKM" | tr '\n' '|')' '$r'"
# a new tip that conflicts on a new base is round two
newtip t2; devmv 3; st2=$($O rev-parse "$SB"); dv3=$($O rev-parse dev); r=$(sbl)
[ "$(sed -n 3p "$SKM")" = "salvage $st2 $dv3" ] && [ "$(python3 "$T/s/revformat.py" --classify "$T/st/review-$SK.md" "$T/st/review-$SK.sha" "$st2")" = changes ] \
  && ok "2571 a new tip that conflicts on a new base gets round two" || ko "2571 round two: '$(cat "$SKM" | tr '\n' '|')' '$r'"
# a third conflict freezes the card: no review, no wave.sh, the branch and the marker stay; said once per tip
newtip t3; devmv 4; st3=$($O rev-parse "$SB"); dv4=$($O rev-parse dev); rm -f "$T/gh.log" "$T/st/cq"/*; r=$(stubwave sbl)
/usr/bin/grep -q 'issues/8/labels.*frozen:owner' "$T/gh.log" 2>/dev/null && ok "2571 a third conflict freezes the card" || ko "2571 no freeze on the third conflict: '$r'"
head -1 "$T/st/review-$SK.md" | /usr/bin/grep -q APPROVE && [ "$(/usr/bin/grep -c '^salvage ' "$SKM")" = 2 ] && ok "2571 a third conflict writes no review and no round line" || ko "2571 the third conflict wrote: '$(cat "$SKM" | tr '\n' '|')' '$(head -1 "$T/st/review-$SK.md")'"
[ ! -e "$T/lc.called" ] && [ "$($O rev-parse -q --verify "refs/heads/$SB")" = "$st3" ] && ok "2571 a third conflict calls no wave.sh and keeps the branch" || ko "2571 wave.sh '$(cat "$T/lc.called" 2>&1)' or the branch is gone"
s=$(cat "$T/st/cq/"x__y#8-status~* 2>/dev/null)
case "$s" in *frozen:owner*g*"${dv4:0:7}"*"${dv3:0:7}"*) ok "2571 one status line names the stop, the files and both bases";; *) ko "2571 the stop status: '$s'";; esac
devmv 5; sapprove; r=$(stubwave sbl)
[ "$(/usr/bin/grep -c 'issues/8/labels.*frozen:owner' "$T/gh.log")" = 1 ] && ok "2571 the stop is said once per tip" || ko "2571 the stop repeated: $(/usr/bin/grep -c 'frozen:owner' "$T/gh.log")"
# a status is one comment per issue, edited in place (#328): a stopped tip queues no conflict status over its stop reason
/usr/bin/grep -qh 'BRANCHLAND CONFLICT' "$T/st/cq/"x__y#8-status~* 2>/dev/null && ko "2571 a stopped tip queued a conflict status: $(cat "$T/st/cq/"x__y#8-status~* | tr '\n' '|')" || ok "2571 a stopped tip queues no conflict status over its stop"
# a new tip that conflicts on the base of the last round freezes the card
salv8; touch "$T/st/auto/salvaged-$SK"; r=$(sbl); newtip t2; rm -f "$T/gh.log"; r=$(stubwave sbl)
/usr/bin/grep -q 'issues/8/labels.*frozen:owner' "$T/gh.log" 2>/dev/null && [ "$(/usr/bin/grep -c '^salvage ' "$SKM")" = 1 ] && ok "2571 a new tip on the same base freezes the card and adds no round" || ko "2571 same base: '$(cat "$SKM" | tr '\n' '|')' '$r'"
[ ! -e "$T/lc.called" ] && [ -n "$($O rev-parse -q --verify "refs/heads/$SB")" ] && [ -s "$SKM" ] && ok "2571 the same-base stop calls no wave.sh and keeps the branch and the marker" || ko "2571 same base: wave.sh '$(cat "$T/lc.called" 2>&1)' or residue gone"
# a stop whose label write failed is tried again on the next pass, on the same base
salv8; touch "$T/st/auto/salvaged-$SK"; r=$(sbl); newtip t2; rm -f "$T/gh.log"; r=$(LABELFAIL=1 stubwave sbl)
case "$r" in *"label write failed"*) ok "2571 a failed label write is named";; *) ko "2571 the failed label write is silent: '$r'";; esac
rm -f "$T/gh.log"; r=$(stubwave sbl)
/usr/bin/grep -q 'issues/8/labels.*frozen:owner' "$T/gh.log" 2>/dev/null && [ -n "$(ls "$T/st/auto/"branchland-salvagestop-"$SK"-* 2>/dev/null)" ] && ok "2571 the next pass on the same base retries the stop" || ko "2571 the stop was not retried: '$r'"
# a stopped tip stays stopped when dev moves: no round line, no round review (the stop is keyed to the tip, not to the base)
salv8; touch "$T/st/auto/salvaged-$SK"; r=$(sbl); newtip t2; r=$(stubwave sbl); devmv 2; sapprove; r=$(stubwave sbl)
[ "$(/usr/bin/grep -c '^salvage ' "$SKM")" = 1 ] && head -1 "$T/st/review-$SK.md" | /usr/bin/grep -q APPROVE && ok "2571 a stopped tip gets no round after dev moves" || ko "2571 a stopped tip got a round after dev moved: '$(cat "$SKM" | tr '\n' '|')' '$r'"
# a stop whose label write failed stays a stop when dev moves, and the third failed write stops the retries with one status line
salv8; touch "$T/st/auto/salvaged-$SK"; r=$(sbl); newtip t2; r=$(LABELFAIL=1 stubwave sbl); devmv 2; sapprove; rm -f "$T/st/cq"/*; r=$(LABELFAIL=1 stubwave sbl)
[ "$(/usr/bin/grep -c '^salvage ' "$SKM")" = 1 ] && head -1 "$T/st/review-$SK.md" | /usr/bin/grep -q APPROVE && ok "2571 a failed stop gets no round after dev moves" || ko "2571 a failed stop got a round after dev moved: '$(cat "$SKM" | tr '\n' '|')' '$r'"
r=$(LABELFAIL=1 stubwave sbl); s=$(cat "$T/st/cq/"x__y#8-status~* 2>/dev/null)
case "$r" in *"BRANCHLAND ALERT"*) case "$s" in *"label write failed 3 times"*) /usr/bin/grep -q "BRANCHLAND ALERT $SB" "$T/st/alerts.log" 2>/dev/null && ok "2571 the third failed label write alerts once with a status line and an alerts.log line" || ko "2571 no alerts.log line";; *) ko "2571 no status after three failed writes: '$s'";; esac;; *) ko "2571 no alert after three failed writes: '$r'";; esac
rm -f "$T/gh.log"; r=$(LABELFAIL=1 stubwave sbl)
/usr/bin/grep -q 'issues/8/labels' "$T/gh.log" 2>/dev/null && ko "2571 a write past the try cap was sent again: '$r'" || ok "2571 a stop past the try cap sends no more label writes"
# a salvage branch that ended with no landing (deleted by hand) leaves no marker; a kept wave marker is not the sweep's
salv8; touch "$T/st/auto/salvaged-$SK"; r=$(sbl); : > "$T/st/auto/branchland-salvagestop-$SK-0123456789"; printf 'origin/dev\n' > "$T/st/auto/kept-fix_9-rebase-gone"
$O update-ref -d "refs/heads/$SB"; python3 -c 'import os,sys,time; [os.utime(f,(time.time()-120,)*2) for f in sys.argv[1:]]' "$T/st/auto/"*"$SK"*; r=$(sbl)
[ ! -e "$SKM" ] && [ ! -e "$T/st/auto/salvaged-$SK" ] && [ -z "$(ls "$T/st/auto/"branchland-salvagestop-"$SK"-* 2>/dev/null)" ] && ok "2571 a gone salvage branch's three markers are swept" || ko "2571 markers of a gone branch remain: $(ls "$T/st/auto" | /usr/bin/grep "$SK" | tr '\n' ' ')"
[ -s "$T/st/auto/kept-fix_9-rebase-gone" ] && ok "2571 the sweep leaves a kept marker with no salvage marker" || ko "2571 the sweep removed a wave kept marker"; rm -f "$T/st/auto/kept-fix_9-rebase-gone"
# a marker written after the pass read the branch list is kept: its pusher may have pushed after the read (the list predates it)
LK=fix_8-salvage-run-late; touch "$T/st/auto/salvaged-$LK"; python3 -c 'import os,sys,time; os.utime(sys.argv[1],(time.time()+60,)*2)' "$T/st/auto/salvaged-$LK"; r=$(sbl)
[ -e "$T/st/auto/salvaged-$LK" ] && ok "2571 a marker newer than the branch list read is not swept" || ko "2571 the sweep removed a marker written after the list read: '$r'"; rm -f "$T/st/auto/salvaged-$LK"
# a landed salvage branch drops all three markers
salv8; (cd "$T/w" && $G checkout -q -B "$SB" origin/dev && echo s8 > s8 && $G add s8 && $G -c user.email=a@b -c user.name=t commit -q -m "salvage (Refs #8)" && $G push -q -f origin "$SB" 2>/dev/null); sapprove
touch "$T/st/auto/salvaged-$SK"; printf 'origin/dev\nsalvage %s x\n' "$($O rev-parse "$SB")" > "$SKM"; : > "$T/st/auto/branchland-salvagestop-$SK-0123456789"; r=$(sbl)
case "$r" in *"BRANCHLAND LANDED $SB"*) [ ! -e "$SKM" ] && [ ! -e "$T/st/auto/salvaged-$SK" ] && [ -z "$(ls "$T/st/auto/"branchland-salvagestop-"$SK"-* 2>/dev/null)" ] && ok "2571 a landed salvage branch drops its three markers" || ko "2571 a landed salvage branch left: $(ls "$T/st/auto" | /usr/bin/grep "$SK" | tr '\n' ' ')";; *) ko "2571 the clean salvage branch did not land: '$r'";; esac

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
# A plain `loop-bug` is a full drain (#1980): it stops the landing too. This replaces the earlier rule that let a green branch
# land beside it; the contradiction is named for #2041.
reset; r=$(SG_GH_AGGREGATE_WINDOW=0 LANDBUG=641 LANDBUG_LABEL=loop-bug CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "a plain loop-bug stops the landing (#1980)" || ko "a branch landed under a plain loop-bug: '$r'"
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
/usr/bin/grep -qx -- "-m pytest -p no:deepeval -n 3 -m not heavy" "$T/selpy.log" 2>/dev/null && ok "the landing check runs the full battery, never the selection" || ko "landing check argv: '$(cat "$T/selpy.log" 2>&1)' $r"
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
# (#1398) The fix round must be told WHY the gate is red: the review carries the assertion python printed, not only the id.
printf '%s\n' \
  '_______________________________ test_one ________________________________' \
  'tests/test_a.py:12: in test_one' \
  '    assert 1 == 2' \
  'E   AssertionError: 1 != 2' \
  'FAILED tests/test_a.py::test_one - AssertionError: 1 != 2' > "$T/ko.txt"
GATE_KO_LOG="echo gate >> $T/gate.log; cat $T/ko.txt; exit 1"
moved=0; movebase() { moved=$((moved+1)); (cd "$T/w" && $G checkout -q -B dev origin/dev && echo "b$moved" > "b$moved" \
  && $G add "b$moved" && $G -c user.email=a@b -c user.name=t commit -q -m "base $moved" && $G push -q origin dev) >/dev/null 2>&1; }
reset; rm -f "$T/gate.log"; r=$(SG_LOCAL_GATE_CMD="$GATE_KO_LOG" CHECKS='{"check_runs":[]}' bl)
case "$(head -1 "$T/st/review-gui_7-x.md" 2>/dev/null)" in
  "VERDICT: CHANGES"*) ok "a branch-owned red writes the CHANGES review the fix pass reads";;
  *) ko "no CHANGES review after a red gate: '$(head -2 "$T/st/review-gui_7-x.md" 2>&1)'";; esac
/usr/bin/grep -q "tests/test_a.py::test_one" "$T/st/review-gui_7-x.md" 2>/dev/null \
  && ok "the review names the failing id" || ko "the review names no failing id: $(cat "$T/st/review-gui_7-x.md" 2>&1)"
# (#1398) The evidence the fix run needs: what python printed, and that the gate ran the tip rebased onto the base.
/usr/bin/grep -q "AssertionError: 1 != 2" "$T/st/review-gui_7-x.md" 2>/dev/null \
  && ok "the review carries the gate's assertion line" || ko "the review carries no assertion line: $(cat "$T/st/review-gui_7-x.md" 2>&1)"
/usr/bin/grep -q "rebased onto dev" "$T/st/review-gui_7-x.md" 2>/dev/null \
  && ok "the review says the gate ran the tip rebased onto the base" || ko "the review does not say it read a rebased tip"
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
# (#1398) A gate that names no id (a shell suite) still carries several of its error lines, never only the last one.
printf '%s\n' \
  'not ok 12 the promotion gate names the proven sha' \
  'bash: line 3: ffland.sh: No such file or directory' \
  'FAIL shell suite: the gate died' > "$T/ko2.txt"
GATE_KO_SHELL="echo gate >> $T/gate.log; cat $T/ko2.txt; exit 1"
(cd "$T/w" && $G checkout -q gui/7-x && echo yy > yy && $G add yy && $G -c user.email=a@b -c user.name=t commit -q -m "another tip" \
  && $G push -q origin gui/7-x) >/dev/null 2>&1; rev APPROVE
r2=$(SG_LOCAL_GATE_CMD="$GATE_KO_SHELL" CHECKS='{"check_runs":[]}' bl)
n=$(/usr/bin/grep -o ' | ' "$T/st/review-gui_7-x.md" 2>/dev/null | wc -l | tr -d ' ')
[ "${n:-0}" -ge 2 ] \
  && ok "a whole-change red carries several of the gate's own lines" || ko "a whole-change red carries only its tail ($n lines): $(cat "$T/st/review-gui_7-x.md" 2>&1)"
# DEBT (#1500): a kept landing branch fix/<n>-land-<run> closes every `issue` line of its marker, moves each card forward, deletes
# wave/run-<run> from the remote, and removes the marker; the branch-name issue is closed only through its own line. A marker with
# no `issue` line (the #1344 form) closes the branch's own issue alone.
land7() { reset; $O update-ref -d refs/heads/gui/7-x; (cd "$T/w" && $G checkout -q -b "wave/run-z" origin/dev && echo wz > wz && $G add wz && $G -c user.email=a@b -c user.name=t commit -q -m "wave work (Refs #7)" \
  && echo wz9 > wz9 && $G add wz9 && $G -c user.email=a@b -c user.name=t commit -q -m "wave work nine (Refs #9)" && $G push -q origin wave/run-z 2>/dev/null \
  && $G checkout -q -b fix/7-land-run-z wave/run-z && $G push -q origin fix/7-land-run-z 2>/dev/null)
  rm -f "$T/st/review-fix_7-land-run-z".*; printf 'VERDICT: APPROVE\n' > "$T/st/review-fix_7-land-run-z.md"; $O rev-parse fix/7-land-run-z > "$T/st/review-fix_7-land-run-z.sha"; }
land7; printf 'origin/dev\nissue 7\nissue 9\nround 1 abc\n' > "$T/st/auto/kept-fix_7-land-run-z"
r=$(OPEN="7 9" CHECKS="$GREEN" bl)
case "$r" in *"BRANCHLAND LANDED fix/7-land-run-z"*) ok "a kept landing branch lands through ffland";; *) ko "the kept landing branch did not land: '$r'";; esac
/usr/bin/grep -q 'issues/7 -f state=closed' "$T/gh.log" && /usr/bin/grep -q 'issues/9 -f state=closed' "$T/gh.log" && ok "every issue line of the marker is closed" || ko "closes: '$(/usr/bin/grep 'state=closed' "$T/gh.log")'"
/usr/bin/grep -qx 'o 3 Done 7' "$T/board.log" && /usr/bin/grep -qx 'o 3 Done 9' "$T/board.log" && ok "both cards move forward" || ko "board: '$(cat "$T/board.log")'"
[ -z "$($O rev-parse -q --verify refs/heads/wave/run-z)" ] && [ ! -e "$T/st/auto/kept-fix_7-land-run-z" ] && ok "wave/run-z is deleted and the marker is removed" || ko "wave/run-z or the marker remains"
[ "$(awk -F'\t' '$2=="fix/7-land-run-z"{print $4}' "$T/st/landed.tsv" | sort | tr '\n' ' ')" = "7 9 " ] && ok "one landed row for each closed issue" || ko "landed.tsv: '$(cat "$T/st/landed.tsv")'"
land7; printf 'origin/dev\nissue 9\n' > "$T/st/auto/kept-fix_7-land-run-z"
r=$(OPEN="7 9" CHECKS="$GREEN" bl)
/usr/bin/grep -q 'issues/9 -f state=closed' "$T/gh.log" && ! /usr/bin/grep -q 'issues/7 -f state=closed' "$T/gh.log" && ok "with an issue line, the branch-name issue is not closed on its own" || ko "closes: '$(/usr/bin/grep 'state=closed' "$T/gh.log")'"
land7; printf 'origin/dev\n' > "$T/st/auto/kept-fix_7-land-run-z"
r=$(OPEN="7 9" CHECKS="$GREEN" bl)
/usr/bin/grep -q 'issues/7 -f state=closed' "$T/gh.log" && ! /usr/bin/grep -q 'issues/9 -f state=closed' "$T/gh.log" && [ ! -e "$T/st/auto/kept-fix_7-land-run-z" ] && ok "an old-form one-line marker closes the branch's own issue alone" || ko "closes: '$(/usr/bin/grep 'state=closed' "$T/gh.log")'"
# (#2184) A close proves that a commit the base carries names the issue with a `Refs #N` line. An `issue` line of the kept marker
# that no landed commit names closes nothing, queues one note, and leaves the issue open with its card in Todo.
land7; printf 'origin/dev\nissue 7\nissue 9\nissue 10\nround 1 abc\n' > "$T/st/auto/kept-fix_7-land-run-z"
r=$(OPEN="7 9 10" CHECKS="$GREEN" bl)
/usr/bin/grep -q 'issues/7 -f state=closed' "$T/gh.log" && /usr/bin/grep -q 'issues/9 -f state=closed' "$T/gh.log" && ! /usr/bin/grep -q 'issues/10 -f state=closed' "$T/gh.log" \
  && ok "a marker line no landed commit names closes nothing, and the named ones close" || ko "closes: '$(/usr/bin/grep 'state=closed' "$T/gh.log")'"
[ "$(ls "$T/st/cq" | /usr/bin/grep -c '^x__y#10-status~')" = 1 ] && ok "the unproven marker line queues one note on the issue" || ko "no note on #10: $(ls "$T/st/cq" 2>/dev/null)"
! /usr/bin/grep -qx 'o 3 Done 10' "$T/board.log" && ok "the unproven marker line's card stays in Todo" || ko "the card of #10 moved: $(cat "$T/board.log")"
[ -z "$(awk -F'\t' '$2=="fix/7-land-run-z" && $4=="10"' "$T/st/landed.tsv")" ] && ok "no landed row for the unproven marker line" || ko "landed.tsv: '$(cat "$T/st/landed.tsv")'"
# (#1826) A kept landing branch whose tip the base already carries has landed: the pass writes its books, deletes the branch, its wave
# branch and the marker, and spends no check, no review and no gate. Its tip is a dev commit, so a review of it reads an empty diff
# and answers BLOCKED for ever, and each round moves the tip to a fresh try count.
land7; $O update-ref refs/heads/dev "$($O rev-parse refs/heads/fix/7-land-run-z)"
printf 'origin/dev\nissue 7\nissue 9\nround 1 abc\n' > "$T/st/auto/kept-fix_7-land-run-z"
before=$($O rev-parse dev)
r=$(OPEN="7 9" CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$before" ] && ok "an already-landed kept branch pushes nothing" || ko "the pass pushed a branch the base already carried"
case "$r" in *"BRANCHLAND LANDED fix/7-land-run-z"*) ok "an already-landed kept branch writes its books";; *) ko "no landed line: '$r'";; esac
/usr/bin/grep -q 'issues/7 -f state=closed' "$T/gh.log" && /usr/bin/grep -q 'issues/9 -f state=closed' "$T/gh.log" && ok "an already-landed kept branch closes every issue line of its marker" || ko "closes: '$(/usr/bin/grep 'state=closed' "$T/gh.log")'"
case "$r" in *"BRANCHLAND WAIT"*) ko "an already-landed kept branch waited: '$r'";; *) ok "an already-landed kept branch waits for no review and no gate";; esac
[ -z "$($O rev-parse -q --verify refs/heads/fix/7-land-run-z)" ] && [ -z "$($O rev-parse -q --verify refs/heads/wave/run-z)" ] && [ ! -e "$T/st/auto/kept-fix_7-land-run-z" ] \
  && ok "an already-landed kept branch goes, with its wave branch and its marker" || ko "the branch, its wave branch or its marker remains"
# Only a kept branch: an ordinary work branch at the base tip carries no commit yet, and the pass waits for its review as before.
reset; (cd "$T/w" && $G checkout -q -b fix/8-z origin/dev && $G push -q origin fix/8-z 2>/dev/null) >/dev/null 2>&1
r=$(OPEN="8" CHECKS="$GREEN" bl)
! /usr/bin/grep -q 'state=closed' "$T/gh.log" && ok "an ordinary branch at the base tip is not read as landed" || ko "an ordinary branch at the base tip closed an issue: $(/usr/bin/grep 'state=closed' "$T/gh.log")"

# DEBT (#1500): the lander's one route to the repeat rule. A kept landing branch whose round pushed a head (landround-*.head differs from
# the tip) and still conflicts with dev goes to `wave.sh landconflict`; one case for each outcome, on the real route.
landc() { reset; $O update-ref -d refs/heads/gui/7-x; rm -f "$T/st/landround-fix_7-land-run-z.head" "$T/st/auto/kept-fix_7-land-run-z"
  (cd "$T/w" && $G checkout -q -b "wave/run-z" origin/dev && echo mine > g && $G add g && $G -c user.email=a@b -c user.name=t commit -q -m "wave work (Refs #7)" && $G push -q origin wave/run-z 2>/dev/null \
  && $G checkout -q -b fix/7-land-run-z wave/run-z && $G push -q origin fix/7-land-run-z 2>/dev/null \
  && $G checkout -q -b devmove origin/dev && echo other > g && $G add g && $G -c user.email=a@b -c user.name=t commit -q -m "dev g" && $G push -q origin HEAD:dev 2>/dev/null)
  printf 'VERDICT: APPROVE\n' > "$T/st/review-fix_7-land-run-z.md"; $O rev-parse fix/7-land-run-z > "$T/st/review-fix_7-land-run-z.sha"
  printf 'pushedround\n' > "$T/st/landround-fix_7-land-run-z.head"; }
LKM="$T/st/auto/kept-fix_7-land-run-z"
# moved base: round two starts, no bug, no requeue
landc; printf 'origin/dev\nissue 7\nround 1 aaaaaaaa\n' > "$LKM"; dv=$($O rev-parse dev)
r=$(OPEN=7 SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS='{"check_runs":[]}' bl)
[ "$(sed -n 3,4p "$LKM" | tr '\n' ,)" = "round 1 aaaaaaaa,round 2 $dv," ] && ok "a conflict after a pushed round on a moved base starts round two (landconflict)" || ko "moved base: '$(cat "$LKM" 2>&1 | tr '\n' '|')' '$r'"
! /usr/bin/grep -q 'labels\[\]=bug' "$T/gh.log" && ! /usr/bin/grep -q 'labels\[\]=rework' "$T/gh.log" && ok "a moved base files no bug and requeues nothing" || ko "moved base wrote: $(cat "$T/gh.log")"
# same sha: the loop bug and the stop of every issue, three issues (a pipe to head would end wave.sh before the third)
landc; printf 'origin/dev\nissue 7\nissue 9\nissue 10\nround 1 %s\n' "$($O rev-parse dev)" > "$LKM"
r=$(OPEN="7 9 10" SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS='{"check_runs":[]}' bl)
[ "$(/usr/bin/grep -c 'labels\[\]=bug' "$T/gh.log")" = 3 ] && for n in 7 9 10; do /usr/bin/grep -q "issues/$n/labels.*frozen:owner" "$T/gh.log" || { ko "same sha: #$n not stopped: $(cat "$T/gh.log")"; break; }; done && ok "the same base sha files the loop bug and stops all three issues" || ko "same sha: bugs $(/usr/bin/grep -c 'labels\[\]=bug' "$T/gh.log")"
! /usr/bin/grep -q 'labels\[\]=rework' "$T/gh.log" && ok "the same base sha requeues nothing" || ko "same sha requeued"
# after round two: the issues are requeued with rework, five or more of them; the marker and both branches go
landc; printf 'origin/dev\nissue 7\nissue 9\nissue 10\nissue 11\nissue 12\nissue 13\nround 1 aaaaaaaa\nround 2 bbbbbbbb\n' > "$LKM"
r=$(OPEN="7 9 10 11 12 13" SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS='{"check_runs":[]}' bl)
[ "$(/usr/bin/grep -c 'labels\[\]=rework' "$T/gh.log")" = 6 ] && [ "$(/usr/bin/grep -c ' Todo ' "$T/board.log")" = 6 ] && ok "a conflict after round two requeues all six issues with rework" || ko "round two: rework $(/usr/bin/grep -c 'labels\[\]=rework' "$T/gh.log"), todo $(/usr/bin/grep -c ' Todo ' "$T/board.log")"
[ ! -e "$LKM" ] && [ -z "$($O rev-parse -q --verify refs/heads/fix/7-land-run-z)" ] && [ -z "$($O rev-parse -q --verify refs/heads/wave/run-z)" ] && ! /usr/bin/grep -q 'labels\[\]=bug' "$T/gh.log" && ok "the marker, the kept branch and wave/run-z are gone, and no bug is filed" || ko "round two residue: $(ls "$T/st/auto")"
# a clean pushed round lands through ffland and never meets the repeat rule
landc; (cd "$T/w" && $G checkout -q -b okround origin/dev && echo wz > wz && $G add wz && $G -c user.email=a@b -c user.name=t commit -q -m "round (Refs #7)" && $G push -q -f origin HEAD:fix/7-land-run-z 2>/dev/null)
$O rev-parse fix/7-land-run-z > "$T/st/review-fix_7-land-run-z.sha"; printf 'origin/dev\nissue 7\nround 1 aaaaaaaa\n' > "$LKM"; rm -f "$T/lc.called"
mv "$T/s/wave.sh" "$T/s/wave.real"; printf '#!/bin/bash\necho "$*" >> "%s/lc.called"\n' "$T" > "$T/s/wave.sh"; chmod +x "$T/s/wave.sh"
r=$(OPEN=7 SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS='{"check_runs":[]}' bl); mv "$T/s/wave.real" "$T/s/wave.sh"
case "$r" in *"BRANCHLAND LANDED fix/7-land-run-z"*) [ ! -e "$T/lc.called" ] && ok "a clean pushed round lands and landconflict is not called" || ko "landconflict ran: $(cat "$T/lc.called")";; *) ko "the clean round did not land: '$r'";; esac

# (#1960) The no-push bound. A kept landing round whose fix run ended (its push marker is gone) with the branch still at the head
# the round started from pushed nothing: no conflict will ever come back to land_round, so the lander requeues its issues (Todo,
# rework, the salvage ref named) and removes the marker. A round still running, or one never dispatched, is left alone.
nopush() { landc; tip=$($O rev-parse fix/7-land-run-z); printf 'origin/dev\nissue 7\nissue 9\nround 1 aaaaaaaa\n' > "$LKM"
  python3 "$T/s/revformat.py" --round-review dev origin/dev g > "$T/st/review-fix_7-land-run-z.md"; printf '%s\n' "$tip" > "$T/st/review-fix_7-land-run-z.sha"
  printf '%s\n' "$tip" > "$T/st/landround-fix_7-land-run-z.head"; rm -f "$T/st/auto/branchfix-"*
  [ "${1:-}" = undispatched ] || printf 'run-q\n' > "$T/st/auto/branchfix-fix_7-land-run-z-${tip:0:9}"
  [ "${1:-}" = running ] && printf 'fix/7-land-run-z %s\n' "$tip" > "$T/st/auto/branchfix-push-run-q"; : ; }
nopush; r=$(OPEN="7 9" SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS='{"check_runs":[]}' bl)
[ "$(/usr/bin/grep -c 'labels\[\]=rework' "$T/gh.log")" = 2 ] && [ "$(/usr/bin/grep -c ' Todo ' "$T/board.log")" = 2 ] && ok "1960 a kept round that pushed nothing requeues its issues with rework" || ko "1960 no-push: rework $(/usr/bin/grep -c 'labels\[\]=rework' "$T/gh.log"), todo $(/usr/bin/grep -c ' Todo ' "$T/board.log"): '$r'"
[ ! -e "$LKM" ] && ok "1960 the kept marker is removed" || ko "1960 the marker remains: '$(cat "$LKM" 2>&1 | tr '\n' '|')'"
case "$r" in *"wave/run-z"*) [ -n "$($O rev-parse -q --verify refs/heads/wave/run-z)" ] && ok "1960 the salvage ref is named and stays on the remote" || ko "1960 wave/run-z is gone";; *) ko "1960 no salvage ref named: '$r'";; esac
for k in running undispatched; do
  nopush "$k"; r=$(OPEN="7 9" SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS='{"check_runs":[]}' bl)
  ! /usr/bin/grep -q 'labels\[\]=rework' "$T/gh.log" && [ -s "$LKM" ] && ok "1960 a round that is $k is not requeued" || ko "1960 a round that is $k was requeued: '$r'"
done
# The round review the lander writes for a kept #1344 branch is the one writer's shape: it reads as changes, never malformed.
reset; hand8; hs=$($O rev-parse fix/8-rebase-hand); printf 'origin/dev\n' > "$T/st/auto/kept-fix_8-rebase-hand"
printf 'VERDICT: APPROVE\n' > "$T/st/review-fix_8-rebase-hand.md"; printf '%s\n' "$hs" > "$T/st/review-fix_8-rebase-hand.sha"
r=$(OPEN=8 SG_LOCAL_GATE_CMD="$GATE_OK" CHECKS='{"check_runs":[]}' bl)
[ "$(python3 "$T/s/revformat.py" --classify "$T/st/review-fix_8-rebase-hand.md" "$T/st/review-fix_8-rebase-hand.sha" "$hs")" = changes ] && ok "1960 the lander's round review classifies as changes" || ko "1960 the lander's round review: $(cat "$T/st/review-fix_8-rebase-hand.md")"

# (#2323) A connect that drops is transient: the branch list read is tried again inside the same pass, so one dropped connect no
# longer ends the whole cycle. A read that keeps failing names itself once, lands nothing and starts no fix round.
cat > "$T/bin/flakygit" <<J
#!/bin/bash
echo "\$*" >> "$T/git.log"
[ "\$1" = ls-remote ] && [ ! -e "$T/connected" ] && { touch "$T/connected"; echo "fatal: unable to access 'https://github.com/x/y.git/': Failed to connect to github.com port 443 after 75000 ms: Couldn't connect to server" >&2; exit 128; }
exec /usr/bin/git "\$@"
J
chmod +x "$T/bin/flakygit"
reset; rm -f "$T/connected" "$T/git.log" "$T/st/auto/branchland-fixpending-"*
r=$(SG_GIT="$T/bin/flakygit" SG_BRANCH_LIST_PAUSE=0 CHECKS="$GREEN" bl)
[ "$(/usr/bin/grep -c 'ls-remote --heads' "$T/git.log" 2>/dev/null)" = 2 ] && ok "2323 a dropped connect: the branch list is read again inside the same pass" || ko "2323 the dropped connect was not retried: '$(cat "$T/git.log" 2>&1)' '$r'"
case "$r" in *"BRANCHLAND LANDED gui/7-x"*) ok "2323 the retried read lands the branch in the same pass";; *) ko "2323 one dropped connect ended the pass: '$r'";; esac
cat > "$T/bin/deadgit" <<J
#!/bin/bash
[ "\$1" = ls-remote ] && { echo "fatal: unable to access 'https://github.com/x/y.git/': Failed to connect to github.com port 443: Connection timed out" >&2; exit 128; }
exec /usr/bin/git "\$@"
J
chmod +x "$T/bin/deadgit"
reset; rm -f "$T/st/auto/branchland-fixpending-"*
r=$(SG_GIT="$T/bin/deadgit" SG_BRANCH_LIST_PAUSE=0 CHECKS="$GREEN" bl)
[ "$($O rev-parse dev)" = "$($O rev-parse main)" ] && ok "2323 a read that keeps failing lands nothing" || ko "2323 a dead read landed: '$r'"
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c 'BRANCHLAND: the remote branch list is not readable')" = 1 ] && ok "2323 a read that keeps failing names itself once" || ko "2323 the dead read's lines: '$r'"

rm -rf "$T"; exit $fail
