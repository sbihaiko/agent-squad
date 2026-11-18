#!/bin/bash
# The wave's age cap and the conditions around an empty wave (#378, #377, #474, #533, #822, #591, #593, #620, #575):
# a capped wave lands what it holds, a red tip names the child that broke it, and a held gate lock never blocks a pass.
# mirrors: wave.sh wave-fixture.sh
. "$(dirname "$0")/wave-fixture.sh"

# L (#378): a wave has an age cap (SG_WAVE_MAX_AGE_SECONDS, 2 h by default, ADR-0145 rule 13). A wave that reaches it
# lands the children it holds and sends the children still running back to the queue; it takes no new child. The age is
# read from the run's own wave start marker (auto/wave-start-<run>), written once when the run cuts its wave.
W8=wave/run-8; start2 run-8 >/dev/null
child 80 g80 "origin/$W8"; en2 run-8 80 >/dev/null
[ -s "$T/st/auto/wave-start-run-8" ] && ok "L: the run's wave records its start for the age cap" || ko "L: no wave start marker"
# the children of the run: #80 is in the wave, #81 is still running (dispatched, never entered)
printf 'run-8\n' > "$T/st/auto/disp-81"
mkdir -p "$T/co/runs/run-8"
printf '%s\n%s\n' \
  '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #80 work"}}' \
  '{"event": "spawn_accepted", "child": "b", "definition": {"prompt": "GitHub issue #81 work"}}' > "$T/co/runs/run-8/record.jsonl"
bash "$T/s/wave.sh" expired run-8 >/dev/null 2>&1 && ko "L: a wave younger than its cap is expired" || ok "L: a wave younger than its cap is not expired"
printf '%s\n' "$(( $(date +%s) - 7201 ))" > "$T/st/auto/wave-start-run-8"
bash "$T/s/wave.sh" expired run-8 >/dev/null 2>&1 && ok "L: a wave past its cap is expired" || ko "L: a wave past its cap is not expired"
# an expired wave takes no new child: an entry with no approval of its current head sends the child's issue back to the
# queue, and that requeue does not count toward the requeue cap (#594)
before=$($O rev-parse $W8); child 82 g82 "origin/$W8"; REV_BASE=$W8 REV_SHA=0000000000000000000000000000000000000000 rev 82
r=$(cd "$T/c82" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-8 82 2>&1); rc=$?
[ $rc = 1 ] && case "$r" in *"WAVE REQUEUE #82"*"age cap"*) ok "L: an expired wave takes no child with a stale approval, and its issue is back in the queue";; *) ko "L: no cap reason: '$r'";; esac || ko "L: the entry exited $rc: '$r'"
[ "$($O rev-parse $W8)" = "$before" ] && ok "L: the child with a stale approval does not enter" || ko "L: the wave tip moved on an expired wave"
cnt() { SG_STATE=$T/st python3 "$T/s/issue_ledger.py" read "x/y#$1" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["own"]["count"] + d["loop"]["count"])'; }   # the file also holds wave facts (ADR-0149)
[ "$(cnt 82)" = 0 ] && ok "L: a cap requeue does not count toward the requeue cap" || ko "L: the cap requeue was counted: $(cat "$T/st/state/x__y__82.json")"
# an approved child (its review approved its current head, read against the wave) still enters a capped wave (#594): the
# wait was the loop's queue, not its own work
child 83 g83 "origin/$W8"; REV_BASE=$W8 rev 83
r=$(cd "$T/c83" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-8 83 2>&1); rc=$?
[ $rc = 0 ] && $O cat-file -e $W8:g83 2>/dev/null && ok "L: an approved child still enters an expired wave" || ko "L: the approved child did not enter: '$r' (exit $rc)"
: > "$T/gh.log"; : > "$T/board.log"
r=$(land2 run-8); rc=$?
[ $rc = 0 ] && ok "L: the expired wave lands what it holds (exit 0)" || ko "L: exit $rc: '$r'"
$O cat-file -e dev:g80 2>/dev/null && ok "L: the child the wave held is in dev" || ko "L: the held child is not in dev"
/usr/bin/grep -qx 'o 3 Closed 80' "$T/board.log" && ok "L: the card of the held child reads Closed" || ko "L: no Closed card for #80"
/usr/bin/grep -q 'issues/80 -f state=closed' "$T/gh.log" && ok "L: the held child's issue is closed" || ko "L: #80 was not closed"
$O cat-file -e dev:g83 2>/dev/null && ok "L: the approved late child is in dev" || ko "L: #83 is not in dev"
[ ! -e "$T/st/auto/disp-81" ] && /usr/bin/grep -qx 'o 3 Todo 81' "$T/board.log" && ok "L: the child still running is back in the queue" || ko "L: #81 was not requeued"
[ "$(cnt 81)" = 0 ] && ok "L: the landing's cap requeue does not count toward the requeue cap" || ko "L: the landing's cap requeue was counted"
/usr/bin/grep -q 'issues/81 -f state=closed' "$T/gh.log" && ko "L: an issue that never entered the wave was closed" || ok "L: the child back in the queue is not closed"
[ ! -e "$T/st/auto/wave-start-run-8" ] && ok "L: the landed wave clears its start marker" || ko "L: the wave start marker outlives the landing"

# M (#377): a red check at the wave tip names the child that broke it. Each child's own suite runs again against the wave
# tip, the last entrant first; the first child whose suite fails leaves the wave -- its issue goes back to the queue -- and
# the wave without it lands. The wave's CI is the live check in production (blocked, #439): the local check at the tip is
# what this suite can run, and the child re-run is the same rule.
W9=wave/run-9; start2 run-9 >/dev/null; child 90 g90 "origin/$W9"; en2 run-9 90 >/dev/null; child 91 g91 "origin/$W9"; en2 run-9 91 >/dev/null
# The preconditions of the red-tip case, asserted before the land: both children are in the wave, #91 the last entrant. The
# case below proves the tip check, so a child that never entered it (a clone or an entry that a loaded machine failed, the
# failure swallowed in the setup above) is a setup failure named here -- not left to read as the "no child named" and "#91
# was not requeued" it would otherwise look like, whose real cause is three checks away.
$O merge-base --is-ancestor "$($G -C "$T/c90" rev-parse HEAD 2>/dev/null)" "$W9" 2>/dev/null && [ "$($O rev-parse "$W9")" = "$($G -C "$T/c91" rev-parse HEAD 2>/dev/null)" ] \
  && ok "M: both children are in the wave before the land, #91 the last entrant" \
  || ko "M: a child is not in the wave before the land: tip=$($O rev-parse "$W9" 2>&1) #90=$($G -C "$T/c90" rev-parse HEAD 2>&1) #91=$($G -C "$T/c91" rev-parse HEAD 2>&1)"
printf 'run-9\n' > "$T/st/auto/disp-91"
: > "$T/gh.log"; : > "$T/board.log"
r=$(SG_WAVE_TIP_GATES='! /usr/bin/git log --format=%s origin/dev..HEAD | /usr/bin/grep -q "work 91"' SG_WAVE_CHILD_GATES='[ "$SG_WAVE_CHILD" = 91 ] && exit 1; exit 0' land2 run-9); rc=$?
[ $rc = 0 ] && ok "M: the wave without the child that broke it lands (exit 0)" || ko "M: exit $rc: '$r'"
case "$r" in *"WAVE CHECK RED"*"#91"*) ok "M: the red check names the child that broke it";; *) ko "M: no child named: '$r'";; esac
$O cat-file -e dev:g90 2>/dev/null && ok "M: the rest of the wave is in dev" || ko "M: the rest of the wave is not in dev"
$O cat-file -e dev:g91 2>/dev/null && ko "M: the child that broke the wave landed" || ok "M: the child that broke the wave did not land"
/usr/bin/grep -q 'issues/90 -f state=closed' "$T/gh.log" && ok "M: the issue of the rest is closed" || ko "M: #90 was not closed"
/usr/bin/grep -q 'issues/91 -f state=closed' "$T/gh.log" && ko "M: the issue of the child that broke the wave was closed" || ok "M: the issue of the child that broke the wave is not closed"
[ ! -e "$T/st/auto/disp-91" ] && /usr/bin/grep -qx 'o 3 Todo 91' "$T/board.log" && ok "M: the child that broke the wave is back in the queue" || ko "M: #91 was not requeued"
[ -z "$($O rev-parse -q --verify "refs/heads/$W9")" ] && ok "M: the wave branch goes with its landing" || ko "M: the wave branch survives"

# N (#474): a loop run lives under its issue worktree, not under the checkout's runs/, and active-runs.txt records its absolute
# folder. The landing is given that folder, so the age cap reads the run's own record there and requeues the child that never
# entered; before, only the base name was passed, `runs/<run>` was not there, and nothing was requeued, in silence.
W10=wave/run-10; start2 run-10 >/dev/null
child 100 g100 "origin/$W10"; en2 run-10 100 >/dev/null
printf 'run-10\n' > "$T/st/auto/disp-101"
RD="$T/wt/child/gui-100/runs/run-10"; mkdir -p "$RD"
printf '%s\n%s\n' \
  '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #100 work"}}' \
  '{"event": "spawn_accepted", "child": "b", "definition": {"prompt": "GitHub issue #101 work"}}' > "$RD/record.jsonl"
printf '%s\n' "$(( $(date +%s) - 7201 ))" > "$T/st/auto/wave-start-run-10"
: > "$T/gh.log"; : > "$T/board.log"
r=$(cd "$T/co" && bash "$T/s/wave.sh" land run-10 "$RD" 2>&1); rc=$?
[ $rc = 0 ] && ok "N: the capped wave of a run under an issue worktree lands (exit 0)" || ko "N: exit $rc: '$r'"
[ ! -e "$T/st/auto/disp-101" ] && /usr/bin/grep -qx 'o 3 Todo 101' "$T/board.log" && ok "N: the child that never entered, read from the run folder under its worktree, is back in the queue" || ko "N: #101 was not requeued: '$r'"
/usr/bin/grep -q 'issues/101 -f state=closed' "$T/gh.log" && ko "N: an issue that never entered the wave was closed" || ok "N: the child back in the queue is not closed"

# O (#474): a landing whose run record is missing names it in the log, once, and never reads it as a run with no issues.
W11=wave/run-11; start2 run-11 >/dev/null
child 110 g110 "origin/$W11"; en2 run-11 110 >/dev/null
: > "$T/gh.log"; : > "$T/board.log"
r=$(cd "$T/co" && bash "$T/s/wave.sh" land run-11 "$T/wt/child/gui-110/runs/run-11" 2>&1); rc=$?
[ $rc = 0 ] && ok "O: a landing whose run record is missing still lands (exit 0)" || ko "O: exit $rc: '$r'"
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c 'WAVE-NOTE.*run-11.*record')" = 1 ] && ok "O: the missing run record is named in one log line" || ko "O: no single named line: '$r'"

# P (#533): a wave that lands empty because the run's work reached the base outside the wave names the issues the run was
# dispatched for, and closes only those whose non-RED commit (a `Refs #N` line) the base holds. #201 has a fix commit,
# #202 has only a TDD RED commit, #203 has no commit, and #20 must not match the commit of #201 by its prefix.
W12=wave/run-12; start2 run-12 >/dev/null
rm -rf "$T/out"; $G clone -q "$T/origin.git" "$T/out" 2>/dev/null
(cd "$T/out" && $G checkout -q dev && cd . \
  && echo a > o201 && $G add o201 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 201" -m "Refs #201" \
  && mkdir -p tests && echo b > tests/test_202.py && $G add tests/test_202.py && $G -c user.email=a@b -c user.name=t commit -q -m "TDD RED: case for 202" -m "Refs #202" \
  && $G push -q origin HEAD:dev)
mkdir -p "$T/co/runs/run-12"
printf '%s\n%s\n%s\n%s\n' \
  '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #201 work"}}' \
  '{"event": "spawn_accepted", "child": "b", "definition": {"prompt": "GitHub issue #202 work"}}' \
  '{"event": "spawn_accepted", "child": "c", "definition": {"prompt": "GitHub issue #203 work"}}' \
  '{"event": "spawn_accepted", "child": "d", "definition": {"prompt": "GitHub issue #20 work"}}' > "$T/co/runs/run-12/record.jsonl"
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/landed.tsv"
r=$(land2 run-12 2>&1); rc=$?
[ $rc = 0 ] && case "$r" in *"landed empty"*) ok "P: the wave lands empty (exit 0)";; *) ko "P: not an empty landing: '$r'";; esac || ko "P: exit $rc: '$r'"
case "$r" in *"dispatched for #20, #201, #202, #203"*) ok "P: the empty wave names the issues its run was dispatched for";; *) ko "P: the dispatched issues are not named: '$r'";; esac
/usr/bin/grep -q 'issues/201 -f state=closed' "$T/gh.log" && ok "P: the issue with a fix commit in the base is closed" || ko "P: #201 is not closed: $(cat "$T/gh.log")"
/usr/bin/grep -q 'issues/202 -f state=closed' "$T/gh.log" && ko "P: an issue with only a TDD RED commit is closed" || ok "P: a TDD RED commit is no delivery"
/usr/bin/grep -qE 'issues/(203|20) -f state=closed' "$T/gh.log" && ko "P: an issue with no commit in the base is closed" || ok "P: an issue with no commit in the base stays open"
[ -n "$(awk -F'\t' '$4=="201"' "$T/st/landed.tsv" 2>/dev/null)" ] && ok "P: the ledger carries the delivered issue" || ko "P: no ledger row for #201"

# P2 (#822): a commit the base held before the wave was cut is no delivery of this run. An issue landed once, then reopened
# and dispatched again, carries its old `Refs #N` commit in the base; the empty wave of the new run read it, closed the issue
# as completed and wrote a ledger row for a fix that never came.
rm -rf "$T/out15"; $G clone -q "$T/origin.git" "$T/out15" 2>/dev/null
(cd "$T/out15" && $G checkout -q dev && echo r > o221 && $G add o221 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 221, first round" -m "Refs #221" && $G push -q origin HEAD:dev)
W15=wave/run-15; start2 run-15 >/dev/null
mkdir -p "$T/co/runs/run-15"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #221 work"}}' > "$T/co/runs/run-15/record.jsonl"
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/landed.tsv"
r=$(land2 run-15 2>&1); rc=$?
[ $rc = 0 ] && case "$r" in *"landed empty"*) ok "P2: the wave of a reopened issue lands empty (exit 0)";; *) ko "P2: not an empty landing: '$r'";; esac || ko "P2: exit $rc: '$r'"
/usr/bin/grep -q 'issues/221 -f state=closed' "$T/gh.log" && ko "P2: a commit the base held before the cut closed the reopened issue" || ok "P2: a commit older than the wave is no delivery"
[ -z "$(awk -F'\t' '$4=="221"' "$T/st/landed.tsv" 2>/dev/null)" ] && ok "P2: no ledger row for an issue the wave did not deliver" || ko "P2: a ledger row for #221: $(cat "$T/st/landed.tsv")"

# S (#591): an empty wave past its age cap requeues the issues of its run that `dev` does not hold, as a non-empty capped
# wave does. Before, the empty branch returned before the age-cap requeue, so a capped run whose children never entered left
# their issues in Doing with their dispatch markers, unless `dev` already held them.
W13=wave/run-13; start2 run-13 >/dev/null
rm -rf "$T/out13"; $G clone -q "$T/origin.git" "$T/out13" 2>/dev/null
(cd "$T/out13" && $G checkout -q dev && echo h > o211 && $G add o211 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 211" -m "Refs #211" && $G push -q origin HEAD:dev)
printf 'run-13\n' > "$T/st/auto/disp-211"; printf 'run-13\n' > "$T/st/auto/disp-212"
mkdir -p "$T/co/runs/run-13"
printf '%s\n%s\n' \
  '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #211 work"}}' \
  '{"event": "spawn_accepted", "child": "b", "definition": {"prompt": "GitHub issue #212 work"}}' > "$T/co/runs/run-13/record.jsonl"
printf '%s\n' "$(( $(date +%s) - 7201 ))" > "$T/st/auto/wave-start-run-13"
: > "$T/gh.log"; : > "$T/board.log"
r=$(land2 run-13); rc=$?
[ $rc = 0 ] && case "$r" in *"landed empty"*) ok "S: an empty capped wave lands empty (exit 0)";; *) ko "S: not an empty landing: '$r'";; esac || ko "S: exit $rc: '$r'"
[ ! -e "$T/st/auto/disp-212" ] && /usr/bin/grep -qx 'o 3 Todo 212' "$T/board.log" && ok "S: the issue dev does not hold is back in the queue" || ko "S: #212 was not requeued: '$r'"
case "$r" in *"WAVE REQUEUE #212"*"age cap"*) ok "S: the requeue names the age cap";; *) ko "S: no cap reason for #212: '$r'";; esac
case "$r" in *"WAVE REQUEUE #211"*) ko "S: the issue dev holds was requeued: '$r'";; *) ok "S: the issue dev holds is not requeued";; esac
[ -e "$T/st/auto/disp-211" ] && ok "S: the issue dev holds keeps its dispatch marker" || ko "S: #211 lost its dispatch marker"
/usr/bin/grep -q 'issues/211 -f state=closed' "$T/gh.log" && ok "S: the issue dev holds is delivered, not requeued" || ko "S: #211 is not closed: $(cat "$T/gh.log")"

# V (#2310): a child whose head carries no commit the base does not already hold by patch has no work of its own to salvage.
# The cap pushed that head to fix/<n>-salvage-<run>, the review approved the branch, and the frontier skipped the issue while
# the empty branch stayed open. The cap now pushes no branch, and the requeue says why, so the issue returns to the queue.
rm -rf "$T/out16"; $G clone -q "$T/origin.git" "$T/out16" 2>/dev/null
(cd "$T/out16" && $G checkout -q dev && echo v > o231 && $G add o231 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 231" -m "Refs #231" && $G push -q origin HEAD:dev)
W16=wave/run-16; start2 run-16 >/dev/null
# the child's own worktree holds the same patch at a sha of its own, under another message, and nothing else
$G clone -q "$T/origin.git" "$T/co/runs/run-16/ws/target-b" 2>/dev/null
(cd "$T/co/runs/run-16/ws/target-b" && $G checkout -q -b gui/231 "origin/dev~1" && echo v > o231 && $G add o231 \
  && $G -c user.email=a@b -c user.name=t commit -q -m "Refs #231 the child's work")
printf 'run-16\n' > "$T/st/auto/disp-231"
printf '%s\n' '{"event": "spawn_accepted", "child": "b", "definition": {"prompt": "GitHub issue #231 work"}}' > "$T/co/runs/run-16/record.jsonl"
printf '%s\n' "$(( $(date +%s) - 7201 ))" > "$T/st/auto/wave-start-run-16"
: > "$T/gh.log"; : > "$T/board.log"; : > "$T/pushes.log"
r=$(land2 run-16); rc=$?
[ $rc = 0 ] && case "$r" in *"landed empty"*) ok "V: the empty capped wave lands empty (exit 0)";; *) ko "V: not an empty landing: '$r'";; esac || ko "V: exit $rc: '$r'"
[ -z "$($O rev-parse -q --verify refs/heads/fix/231-salvage-run-16 2>/dev/null)" ] && ok "V: a by-patch head opens no salvage branch (#2310)" || ko "V: an empty salvage branch was pushed: $(pushes)"
case "$r" in *"WAVE REQUEUE #231"*"not on dev by patch"*) ok "V: the requeue names why no branch carries the child's work (#2310)";; *) ko "V: the requeue names no reason: '$r'";; esac

# T (#593): a refused Code Complete board write on entry is named, in the shape the landing names its refused write.
# Before, the entry discarded the write's output, so a refused card move was never named and the card looked moved.
W14=wave/run-14; start2 run-14 >/dev/null; child 310 g310 "origin/$W14"
# env.sh names $T/board.sh, so the refusing board replaces it for this case and the working one comes back after it.
printf '#!/bin/bash\necho "$*" >> "%s/board-refuse.log"\necho "board refused: the project is locked" >&2\nexit 1\n' "$T" > "$T/board.sh"
chmod +x "$T/board.sh"; REV_BASE=$W14 rev 310; : > "$T/board-refuse.log"
r=$(cd "$T/c310" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-14 310 2>&1); rc=$?
[ $rc = 0 ] && $O merge-base --is-ancestor "$($G -C "$T/c310" rev-parse HEAD)" "$W14" && ok "T: a refused card write does not keep the child out of the wave" || ko "T: exit $rc: '$r'"
case "$r" in *"WAVE-BOARD #310 Landing refused:"*) ok "T: the refused entry card write is named";; *) ko "T: the refused write is silent: '$r'";; esac
case "$r" in *"board refused: the project is locked"*) ok "T: the refusal names the board's own error";; *) ko "T: the board error is not named: '$r'";; esac
/usr/bin/grep -qx 'o 3 Landing 310' "$T/board-refuse.log" && ok "T: the entry still attempted the Landing write" || ko "T: no Landing write was attempted"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\necho "${SG_MOVE_REASON:-none} $*" >> "%s/board-reason.log"\necho "issue_state.py: items read 3; project total: none on the REST items route" >&2\n' "$T" "$T" > "$T/board.sh"
chmod +x "$T/board.sh"
# A write that worked names no refusal: the seam's read note is not an error.
r=$(cd "$T/c310" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-14 310 2>&1); rc=$?
[ $rc = 0 ] || ko "T: the second entry exited $rc: '$r'"
case "$r" in *refused*) ko "T: a working board write is named refused: '$r'";; *) ok "T: a working board write names no refusal";; esac

# U (#620): a child that ENTERED its wave (its ledger's entered fact) whose commit reached dev by another route makes the wave land
# empty, and dev holds no `Refs #N` line for it (a squash rewrote the message). The entry is the proof: the issue is
# delivered, never requeued, even past the age cap. The cap decides first (#1833): a capped wave keeps its own landing line.
W15=wave/run-15; start2 run-15 >/dev/null
printf 'run-15\n' > "$T/st/auto/disp-231"; SG_STATE=$T/st python3 "$T/s/issue_ledger.py" wave 'x/y#231' run-15 entered 'entered wave/run-15' >/dev/null
mkdir -p "$T/co/runs/run-15"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #231 work"}}' > "$T/co/runs/run-15/record.jsonl"
printf '%s\n' "$(( $(date +%s) - 7201 ))" > "$T/st/auto/wave-start-run-15"
: > "$T/gh.log"; : > "$T/board.log"
r=$(land2 run-15); rc=$?
[ $rc = 0 ] && case "$r" in *"landed empty"*) ok "U: the wave of an entered child lands empty (exit 0)";; *) ko "U: not an empty landing: '$r'";; esac || ko "U: exit $rc: '$r'"
case "$r" in *"WAVE REQUEUE #231"*) ko "U: an entered child is requeued: '$r'";; *) ok "U: an entered child is not requeued";; esac
/usr/bin/grep -q 'issues/231 -f state=closed' "$T/gh.log" && ok "U: an entered child of an empty wave is delivered" || ko "U: #231 is not closed: $(cat "$T/gh.log")"

# Q (#575): a gate lock held by another pass never makes an entry or a landing wait for it. The entry and the landing run
# inline in the fast lane and the watch cycle, so a wait there stops every dispatch and every cycle (a promotion held the
# lock for 30 min). Both use try mode: the child enters, and the wave lands, on a later pass, with no requeue and no try spent.
W30=wave/run-30; start2 run-30 >/dev/null; child 301 q1 "origin/$W30"
sleep 60 & hp=$!; mkdir -p "$T/st/locks/gate"; echo "$hp" > "$T/st/locks/gate/pid"; : > "$T/board.log"
t0=$(date +%s); r=$(REV_BASE=$W30 rev 301; cd "$T/c301" && SERIAL_WAIT_SECONDS=8 SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-30 301 2>&1); rc=$?; t1=$(date +%s)
[ $rc != 0 ] && [ $((t1 - t0)) -lt 6 ] && ok "Q: an entry under a held gate lock returns at once" || ko "Q: entry exit $rc after $((t1 - t0)) s: '$r'"
case "$r" in *"WAVE HOLD #301"*"gate lock"*) ok "Q: the entry names the held gate lock";; *) ko "Q: no hold line: '$r'";; esac
case "$r" in *"WAVE REQUEUE"*) ko "Q: a held gate lock requeued the child: '$r'";; *) ok "Q: a held gate lock requeues nothing";; esac
/usr/bin/grep -q " 301$" "$T/board.log" && ko "Q: the card moved: $(cat "$T/board.log")" || ok "Q: the card does not move"
# Q2 (#567): a full gate-slot budget (caps.gates) holds like a held lock: the entry holds the child out, with no requeue and no card move.
# The budget the entry reads is the one its own tree resolves (gate.sh takes slots with --target "$PWD"), so the case fills
# the leaves from the same tree the entry runs in. A take from the caller's directory resolves this repository's project file,
# and the entry's tree resolves another ceiling: the slots it fills are never the slots the entry counts (#2030 follow-up).
rm -rf "$T/st/locks/gate"; sleep 60 & hs=$!; PYTHONPATH="$SG_SQUAD" python3 -m squad.caps take gates other --count 12 --pid "$hs" --target "$T/c301" >/dev/null; : > "$T/board.log"
t0=$(date +%s); r=$(REV_BASE=$W30 rev 301; cd "$T/c301" && SERIAL_WAIT_SECONDS=8 SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-30 301 2>&1); rc=$?; t1=$(date +%s)
[ $rc != 0 ] && [ $((t1 - t0)) -lt 6 ] && ok "Q2: an entry under full gate slots returns at once" || ko "Q2: entry exit $rc after $((t1 - t0)) s: '$r'"
case "$r" in *"WAVE HOLD #301"*"gate slots"*"other"*) ok "Q2: the entry names the full gate slots and their holder";; *) ko "Q2: no hold line: '$r'";; esac
case "$r" in *"WAVE REQUEUE"*) ko "Q2: full gate slots requeued the child: '$r'";; *) ok "Q2: full gate slots requeue nothing";; esac
PYTHONPATH="$SG_SQUAD" python3 -m squad.caps release gates other; kill $hs 2>/dev/null
rm -rf "$T/st/locks/gate"; en2 run-30 301 >/dev/null
mkdir -p "$T/st/locks/gate"; echo "$hp" > "$T/st/locks/gate/pid"; before=$($O rev-parse dev)
# Q (#955): the landing waits for a held gate lock a bounded time (SERIAL_WAIT_SECONDS), then holds: no red tip, no try spent.
t0=$(date +%s); r=$(SERIAL_WAIT_SECONDS=2 SG_WAVE_TIP_GATES="bash '$T/s/gate.sh' true" land2 run-30 2>&1); rc=$?; t1=$(date +%s)
[ $rc != 0 ] && [ $((t1 - t0)) -ge 2 ] && [ $((t1 - t0)) -lt 20 ] && ok "Q: a landing under a held gate lock waits its bound, then holds" || ko "Q: land exit $rc after $((t1 - t0)) s: '$r'"
case "$r" in *"WAVE WAIT $W30"*"gate lock"*) ok "Q: the landing names the held gate lock";; *) ko "Q: no wait line: '$r'";; esac
case "$r" in *"WAVE CHECK RED"*) ko "Q: a held gate lock read as a red tip: '$r'";; *) ok "Q: a held gate lock is no red tip";; esac
[ -z "$(tries 301 run-30)" ] && ok "Q: a held gate lock spends no landing try" || ko "Q: a try was counted"
[ "$($O rev-parse dev)" = "$before" ] && ok "Q: dev does not move while the lock is held" || ko "Q: dev moved"
rm -rf "$T/st/locks/gate"; kill $hp 2>/dev/null
# Q3 (#955): a gate lock that frees inside the landing's bound does not cost the cycle its landing: the wave lands on that pass.
sleep 2 & hq=$!; mkdir -p "$T/st/locks/gate"; echo "$hq" > "$T/st/locks/gate/pid"; before=$($O rev-parse dev)
r=$(SERIAL_WAIT_SECONDS=30 SG_WAVE_TIP_GATES="bash '$T/s/gate.sh' true" land2 run-30 2>&1); rc=$?
[ $rc = 0 ] && [ "$($O rev-parse dev)" != "$before" ] && ok "Q3: the wave lands on the pass that finds the lock free, inside the bound" || ko "Q3: exit $rc: '$r'"

# R (#566): the wave tip's check is the loop's one base battery -- ruff, mypy and the fast pytest -- read against
# origin/$SG_BASE, in the fast gate, with a bounded worker count instead of `auto`. wave.sh keeps no copy of it. The count
# is set here, so this suite reads no profile's default and depends on no worker count.
. "$d/base-battery.sh" 2>/dev/null
wcmd=$(SG_GATE_WORKERS=3 sg_base_gate "origin/dev" /usr/bin/true 2>/dev/null)
wbat=$(SG_GATE_WORKERS=3 sg_base_battery /usr/bin/true origin/dev 2>/dev/null)
case "$wcmd" in *"SG_FAST_BASE=origin/dev "*"fast-gate.sh"*) ok "R: the tip check reads the changed scripts against origin/\$SG_BASE";; *) ko "R: the shared base gate is not the fast gate at origin/\$SG_BASE: '$wcmd'";; esac
case "$wbat" in *"ruff check squad tests"*"mypy squad"*'-n "${SQUAD_GATE_WORKERS:-$workers}"'*"not heavy"*"sg_battery_run /usr/bin/true origin/dev 3 yes"*) ok "R: the base gate runs ruff, mypy and the fast pytest with SG_GATE_WORKERS";; *) ko "R: the base gate command: '$wbat'";; esac
case "$wbat$wcmd" in *"-n auto"*) ko "R: the base gate runs pytest -n auto";; *) ok "R: the base gate never runs pytest -n auto";; esac
/usr/bin/grep -q 'sg_base_gate "origin/\$base"' "$T/s/wave.sh" && ok "R: wave.sh builds its tip check from the shared definition" || ko "R: wave.sh does not use the shared base gate"
! /usr/bin/grep -v '^[[:space:]]*#' "$T/s/wave.sh" | /usr/bin/grep -q 'ruff check squad tests' && ok "R: wave.sh keeps no copy of the base battery" || ko "R: wave.sh still carries a copy of the base battery"

rm -rf "$T"; [ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
