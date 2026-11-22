#!/bin/bash
# seconds: 24.51
# The tip check's eviction and the run's end class (#2209, #2216, #2217): a red child is evicted and the trimmed
# wave lands or holds, and a run that ends owner-only or does-not-reproduce freezes its issue once.
# mirrors: wave.sh wave-fixture.sh
. "$(dirname "$0")/wave-fixture.sh"

# The end-class cases read back the labels and comments they wrote (#2217): the richer gh stub is installed here.
gh_store_stub

# W2209 (#2209): the tip check evicts the wave's only child, so nothing is ahead of the base. The landing runs no second tip
# gate on a tip that equals the base: it closes the wave as an empty landing in the same pass, and the remote wave branch,
# which still carries the evicted commit, goes. Before, the pass ended with the wave kept, and every later pass ran the
# check, evicted the child and requeued it again, each at the price of a full gate.
W209=wave/run-209; start2 run-209 >/dev/null; child 22091 g22091 "origin/$W209"; en2 run-209 22091 >/dev/null
mkdir -p "$T/co/runs/run-209"; printf 'CHILD 1: GitHub issue #22091\n' > "$T/co/runs/run-209/request.txt"
printf 'run-209\n' > "$T/st/auto/disp-22091"; : > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/tip2209.log"
TIP2209="echo call >> $T/tip2209.log; false"
r=$(SG_WAVE_TIP_GATES="$TIP2209" SG_WAVE_CHILD_GATES='exit 1' land2 run-209 2>&1); rc=$?
[ $rc = 0 ] && ok "W2209: a wave whose only child the tip check evicted lands empty (exit 0)" || ko "W2209: pass 1: exit $rc: '$r'"
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c "^WAVE $W209 landed empty in dev")" = 1 ] && ok "W2209: the empty landing is said once" || ko "W2209: no single empty-landing line: '$r'"
[ "$(calls "$T/tip2209.log")" = 1 ] && ok "W2209: the tip gate runs once, never on a tip that equals the base" || ko "W2209: the tip gate ran $(calls "$T/tip2209.log") times: '$r'"
[ -z "$($O rev-parse -q --verify "refs/heads/$W209" 2>/dev/null)" ] && ok "W2209: the remote wave branch is deleted" || ko "W2209: the remote wave branch is still there"
$O cat-file -e dev:g22091 2>/dev/null && ko "W2209: the evicted child landed" || ok "W2209: the evicted child is not in dev"
/usr/bin/grep -q 'issues/22091 -f state=closed' "$T/gh.log" && ko "W2209: the evicted child's issue was closed" || ok "W2209: the evicted child's issue is not closed"
[ ! -e "$T/st/auto/wave-run-209" ] && ok "W2209: the run's wave marker is gone" || ko "W2209: the run's wave marker is still there"
# A second pass finds no wave: it runs no check and requeues nothing.
r=$(SG_WAVE_TIP_GATES="$TIP2209" SG_WAVE_CHILD_GATES='exit 1' land2 run-209 2>&1); rc=$?
case "$r" in *"WAVE CHECK RED"*|*"WAVE REQUEUE"*) ko "W2209: the second pass evicted or requeued again: '$r'";; *) ok "W2209: the second pass names no red check and no requeue";; esac
[ "$(calls "$T/tip2209.log")" = 1 ] && ok "W2209: the second pass runs no tip gate" || ko "W2209: the tip gate ran $(calls "$T/tip2209.log") times after two passes"
[ "$(/usr/bin/grep -c '^o 3 Todo 22091$' "$T/board.log")" = 1 ] && ok "W2209: the evicted child's card goes to Todo once" || ko "W2209: Todo rows: '$(cat "$T/board.log")'"
# The empty landing is held (a fix run of the wave has no verdict yet), so the wave stays and the next pass evicts the same
# child again: the commit is dropped and named, and no second refused fact, card move or comment is written for the run.
W211=wave/run-211; start2 run-211 >/dev/null; child 22092 g22092 "origin/$W211"; en2 run-211 22092 >/dev/null
printf 'run-211\n' > "$T/st/auto/disp-22092"; printf '%s\n' "$T/nofix2209" > "$T/st/auto/ccfix-run-211-1"; : > "$T/board.log"; rm -f "$T/tip2209.log" "$T"/st/cq/*22092-wave*
r=$(SG_WAVE_TIP_GATES="$TIP2209" SG_WAVE_CHILD_GATES='exit 1' land2 run-211 2>&1); rc=$?
[ $rc != 0 ] && case "$r" in *"WAVE WAIT $W211"*"does not land empty"*) true;; *) false;; esac && ok "W2209: a fix run in flight holds the empty landing after the eviction" || ko "W2209: held pass: exit $rc: '$r'"
r=$(SG_WAVE_TIP_GATES="$TIP2209" SG_WAVE_CHILD_GATES='exit 1' land2 run-211 2>&1); rc=$?
case "$r" in *"WAVE CHECK RED"*) ko "W2209: the held pass evicted the child again: '$r'";; *) ok "W2209: the held pass evicts no child again";; esac
case "$r" in *"WAVE REQUEUE"*) ko "W2209: the same run requeued the child twice: '$r'";; *) ok "W2209: the same run requeues the child once";; esac
[ "$(/usr/bin/grep -c '^o 3 Todo 22092$' "$T/board.log")" = 1 ] && ok "W2209: the repeated eviction moves no card again" || ko "W2209: Todo rows after two evictions: '$(cat "$T/board.log")'"
[ "$(ls "$T"/st/cq/*22092-wave* 2>/dev/null | /usr/bin/grep -c .)" = 1 ] && ok "W2209: the repeated eviction queues no second comment" || ko "W2209: queued comments: '$(ls "$T"/st/cq/ 2>/dev/null)'"
[ "$(calls "$T/tip2209.log")" = 1 ] && ok "W2209: the held pass runs no second tip gate" || ko "W2209: the tip gate ran $(calls "$T/tip2209.log") times in two held passes"
rm -f "$T/st/auto/ccfix-run-211-1"
# Two children, one evicted: a child is still ahead of the base, so the check runs again and the wave lands with it.
W212=wave/run-212; start2 run-212 >/dev/null; child 22093 g22093 "origin/$W212"; en2 run-212 22093 >/dev/null; child 22094 bad2209 "origin/$W212"; en2 run-212 22094 >/dev/null
printf 'run-212\n' > "$T/st/auto/disp-22094"; : > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/tip2209.log"
r=$(SG_WAVE_TIP_GATES="echo call >> $T/tip2209.log; [ ! -e bad2209 ]" SG_WAVE_CHILD_GATES='/usr/bin/git diff --name-only "$SG_WAVE_CHILD_FROM" "$SG_WAVE_CHILD_TO" | /usr/bin/grep -qx bad2209 && exit 1; exit 0' land2 run-212 2>&1); rc=$?
[ $rc = 0 ] && $O cat-file -e dev:g22093 2>/dev/null && ! $O cat-file -e dev:bad2209 2>/dev/null && ok "W2209: with one of two children evicted, the other still lands" || ko "W2209: two children: exit $rc: '$r'"
[ "$(calls "$T/tip2209.log")" = 2 ] && ok "W2209: the check runs again on the tip that keeps a child" || ko "W2209: the tip gate ran $(calls "$T/tip2209.log") times with a child left"
[ "$(/usr/bin/grep -c '^o 3 Todo 22094$' "$T/board.log")" = 1 ] && /usr/bin/grep -q 'issues/22093 -f state=closed' "$T/gh.log" && ok "W2209: the evicted child is requeued once and the other child's issue closes" || ko "W2209: two children: board '$(cat "$T/board.log")'"

# W2216 (#2216), criterion 2: two children, the newer one red. The pass evicts it and the trimmed tip ends unproven. The next pass
# keeps the trimmed tip: it does not evict the same child again, and its tip check runs once, on the child the run kept.
W213=wave/run-213; start2 run-213 >/dev/null; child 22101 g22101 "origin/$W213"; en2 run-213 22101 >/dev/null
child 22102 bad2216 "origin/$W213"; en2 run-213 22102 >/dev/null
printf 'run-213\n' > "$T/st/auto/disp-22101"; printf 'run-213\n' > "$T/st/auto/disp-22102"
: > "$T/board.log"; rm -f "$T/tip2216.log"
TIP2216="echo call >> $T/tip2216.log; [ ! -e bad2216 ] || exit 1; exit 4"
C213='/usr/bin/git diff --name-only "$SG_WAVE_CHILD_FROM" "$SG_WAVE_CHILD_TO" | /usr/bin/grep -qx bad2216 && exit 1; exit 0'
r=$(SG_WAVE_TIP_GATES="$TIP2216" SG_WAVE_CHILD_GATES="$C213" land2 run-213 2>&1); rc=$?
case "$r" in *"WAVE WAIT $W213"*unproven*) ok "W2216: pass 1 holds the trimmed tip as unproven after the eviction";; *) ko "W2216: pass 1: exit $rc: '$r'";; esac
[ "$(calls "$T/tip2216.log")" = 2 ] && ok "W2216: pass 1 runs the tip gate on the full tip and on the trimmed tip" || ko "W2216: pass 1 ran the tip gate $(calls "$T/tip2216.log") times"
r=$(SG_WAVE_TIP_GATES="$TIP2216" SG_WAVE_CHILD_GATES="$C213" land2 run-213 2>&1); rc=$?
case "$r" in *"WAVE CHECK RED"*) ko "W2216: pass 2 evicted the child again: '$r'";; *) ok "W2216: pass 2 evicts no child again";; esac
[ "$(calls "$T/tip2216.log")" = 3 ] && ok "W2216: pass 2 runs the tip gate once, on the trimmed tip" || ko "W2216: the tip gate ran $(calls "$T/tip2216.log") times after two passes"
[ "$(/usr/bin/grep -c '^o 3 Todo 22102$' "$T/board.log")" = 1 ] && ok "W2216: the evicted child's card goes to Todo once" || ko "W2216: Todo rows: '$(cat "$T/board.log")'"

# W2216 (#2216), criterion 3: the run predates a rearm, so its ledger answers floor to the requeue fact. The eviction writes its
# card move and comment once, and a held pass repeats neither.
W214=wave/run-214; start2 run-214 >/dev/null; child 22103 bad2216f "origin/$W214"; en2 run-214 22103 >/dev/null
SG_STATE=$T/st python3 "$T/s/issue_ledger.py" rearm "x/y#22103" >/dev/null 2>&1
printf 'run-214\n' > "$T/st/auto/disp-22103"; printf '%s\n' "$T/nofix2216" > "$T/st/auto/ccfix-run-214-1"
: > "$T/board.log"; rm -f "$T"/st/cq/*22103-wave*
TIP2216F="[ ! -e bad2216f ] || exit 1; exit 0"
F214='/usr/bin/git diff --name-only "$SG_WAVE_CHILD_FROM" "$SG_WAVE_CHILD_TO" | /usr/bin/grep -qx bad2216f && exit 1; exit 0'
r=$(SG_WAVE_TIP_GATES="$TIP2216F" SG_WAVE_CHILD_GATES="$F214" land2 run-214 2>&1); rc=$?
case "$r" in *"WAVE WAIT $W214"*"does not land empty"*) ok "W2216: pass 1 holds the empty landing after the eviction";; *) ko "W2216: pass 1: exit $rc: '$r'";; esac
r=$(SG_WAVE_TIP_GATES="$TIP2216F" SG_WAVE_CHILD_GATES="$F214" land2 run-214 2>&1); rc=$?
case "$r" in *"WAVE WAIT $W214"*"does not land empty"*) ok "W2216: pass 2 holds the empty landing";; *) ko "W2216: pass 2: exit $rc: '$r'";; esac
[ "$(/usr/bin/grep -c '^o 3 Todo 22103$' "$T/board.log")" = 1 ] && ok "W2216: a held pass under floor repeats no card move" || ko "W2216: Todo rows under floor: '$(cat "$T/board.log")'"
[ "$(ls "$T"/st/cq/*22103-wave* 2>/dev/null | /usr/bin/grep -c .)" = 1 ] && ok "W2216: a held pass under floor repeats no comment" || ko "W2216: queued comments under floor: '$(ls "$T"/st/cq/ 2>/dev/null)'"

# W2216 (#2216), criterion 4: a child evicted at one commit re-enters with a fix that is red too, reads stuck, and is evicted
# again. The second eviction writes its requeue on the new commit.
W215=wave/run-215; start2 run-215 >/dev/null; child 22105 bad2216g "origin/$W215"; en2 run-215 22105 >/dev/null
printf 'run-215\n' > "$T/st/auto/disp-22105"; : > "$T/board.log"
TIP2216G="[ ! -e bad2216g ] || exit 1; exit 0"
G215='/usr/bin/git diff --name-only "$SG_WAVE_CHILD_FROM" "$SG_WAVE_CHILD_TO" | /usr/bin/grep -qx bad2216g && exit 1; exit 0'
r=$(SG_WAVE_TIP_GATES="$TIP2216G" SG_WAVE_CHILD_GATES="$G215" land2 run-215 2>&1); rc=$?
[ "$rc" = 0 ] && ok "W2216: the first eviction closes its wave empty" || ko "W2216: first eviction: exit $rc: '$r'"
start2 run-215 >/dev/null; child 22105 bad2216g "origin/$W215"; en2 run-215 22105 >/dev/null
SG_STATE=$T/st python3 "$T/s/issue_ledger.py" wave "x/y#22105" run-215 stuck "the check at the tip of $W215 is red" >/dev/null 2>&1
case "$(wfact 22105 run-215)" in stuck*) ok "W2216: the re-entered child reads stuck";; *) ko "W2216: the re-entered child reads '$(wfact 22105 run-215)'";; esac
B2=$($O rev-parse -q --verify "refs/heads/$W215")
r=$(SG_WAVE_TIP_GATES="$TIP2216G" SG_WAVE_CHILD_GATES="$G215" land2 run-215 2>&1); rc=$?
[ "$(/usr/bin/grep -c '^o 3 Todo 22105$' "$T/board.log")" = 2 ] && ok "W2216: the eviction of the re-entered child moves its card to Todo" || ko "W2216: Todo rows: '$(cat "$T/board.log")' ($r)"
case "$(SG_STATE=$T/st python3 "$T/s/issue_ledger.py" read "x/y#22105" 2>/dev/null | python3 -c 'import json,sys; print((json.load(sys.stdin).get("requeued") or {}).get("run-215", {}).get("head", ""))')" in
  "$B2") ok "W2216: the eviction writes its requeue on the re-entered commit";; *) ko "W2216: the requeue head is not the re-entered commit $B2: '$r'";; esac

# W2757 (#2757): a child of two commits, where only the newer one is red. The eviction drops every commit of that child, so
# no unreviewed part of it reaches dev. Before, only the red commit left, and the older one landed.
# A sibling child entered before it and one entered after it stay in the tip and land.
W216=wave/run-216; start2 run-216 >/dev/null
child 27570 g27570 "origin/$W216"; en2 run-216 27570 >/dev/null
child 27571 g27571 "origin/$W216"
(cd "$T/c27571" && echo bad > bad2757 && $G add bad2757 && $G -c user.email=a@b -c user.name=t commit -q -m "more work 27571 (Refs #27571)")
en2 run-216 27571 >/dev/null
child 27572 g27572 "origin/$W216"; en2 run-216 27572 >/dev/null
printf 'run-216\n' > "$T/st/auto/disp-27571"; : > "$T/board.log"
TIP2757="[ ! -e bad2757 ] || exit 1; exit 0"
C2757='/usr/bin/git diff --name-only "$SG_WAVE_CHILD_FROM" "$SG_WAVE_CHILD_TO" | /usr/bin/grep -qx bad2757 && exit 1; exit 0'
r=$(SG_WAVE_TIP_GATES="$TIP2757" SG_WAVE_CHILD_GATES="$C2757" land2 run-216 2>&1); rc=$?
$O cat-file -e dev:bad2757 2>/dev/null && ko "W2757: the red commit of the evicted child landed: '$r'" || ok "W2757: the red commit of the evicted child is not in dev"
$O cat-file -e dev:g27571 2>/dev/null && ko "W2757: the older commit of the evicted child landed: '$r'" || ok "W2757: the older commit of the evicted child is not in dev"
[ $rc = 0 ] && $O cat-file -e dev:g27570 2>/dev/null && $O cat-file -e dev:g27572 2>/dev/null \
  && ok "W2757: the sibling children entered before and after the evicted child land" || ko "W2757: a sibling did not land: exit $rc: '$r'"
[ "$(/usr/bin/grep -c '^o 3 Todo 27571$' "$T/board.log")" = 1 ] && ok "W2757: the evicted child's card goes to Todo once" || ko "W2757: Todo rows: '$(cat "$T/board.log")'"
# A drop of the older commit conflicts: a sibling commit after it edits the file that commit adds. The eviction takes the
# "cannot leave it" path, and the wave does not land.
W217=wave/run-217; start2 run-217 >/dev/null
child 27573 g27573 "origin/$W217"
(cd "$T/c27573" && echo bad > bad2757c && $G add bad2757c && $G -c user.email=a@b -c user.name=t commit -q -m "more work 27573 (Refs #27573)")
en2 run-217 27573 >/dev/null
child 27574 g27574 "origin/$W217"; en2 run-217 27574 >/dev/null
# The entry refuses a sibling that shares a file (#1434), so the sibling's edit of g27573 goes on the remote wave by hand.
(cd "$T/c27574" && $G fetch -q origin && $G reset -q --hard "origin/$W217" && echo edit > g27573 && $G add g27573 \
  && $G -c user.email=a@b -c user.name=t commit -q -m "edit 27574 (Refs #27574)" && $G push -q origin "HEAD:$W217")
printf 'run-217\n' > "$T/st/auto/disp-27573"; D217=$($O rev-parse dev)
C2757C='/usr/bin/git diff --name-only "$SG_WAVE_CHILD_FROM" "$SG_WAVE_CHILD_TO" | /usr/bin/grep -qx bad2757c && exit 1; exit 0'
r=$(SG_WAVE_TIP_GATES="[ ! -e bad2757c ] || exit 1; exit 0" SG_WAVE_CHILD_GATES="$C2757C" land2 run-217 2>&1); rc=$?
[ $rc != 0 ] && case "$r" in *"WAVE CHECK RED $W217: #27573 broke the wave tip and cannot leave it (a rebase conflict)"*) true;; *) false;; esac \
  && ok "W2757: a conflict in the drop of the child's older commit takes the cannot-leave-it path" || ko "W2757: conflict: exit $rc: '$r'"
[ "$($O rev-parse dev)" = "$D217" ] && ok "W2757: the conflicted eviction lands nothing in dev" || ko "W2757: dev moved after the conflicted eviction: '$r'"
# N (#2217): a run that ends owner-only freezes its issue once, and a run that ends does-not-reproduce freezes it at this build.
# A failed run with no class (a gate reason) is counted as before and freezes nothing. The stub keeps the labels and the
# comments written, so the freeze reads back what stands.

export LABEL_STORE=1; rm -f "$T"/lab-* "$T"/com-*
r=$((cd "$T/co" && bash "$T/s/wave.sh" fail 2217 run-owner217 "owner-only: criterion (1) needs a credential" own owner-only) 2>&1)
endfiles() { ls "$T/st/cq/"*#"$1"-endclass* 2>/dev/null; }
e1=$(endfiles 2217)
if /usr/bin/grep -q 'issues/2217/labels.*labels\[\]=frozen:owner.*labels\[\]=needs-ratification' "$T/gh.log" \
   && [ "$(printf '%s\n' "$e1" | /usr/bin/grep -c .)" = 1 ] && /usr/bin/grep -qF 'Reason: owner-only: criterion (1) needs a credential' "$e1"; then
  ok "N1: an owner-only end freezes its issue with one label write and one queued comment that quotes the reason"
else ko "N1: owner-only end did not freeze: '$r'"; fi
[ "$(freezes 2217)" = 1 ] && ok "N1: the two labels go in one request (debt 2)" || ko "N1: freeze requests: '$(/usr/bin/grep 'issues/2217/labels' "$T/gh.log")'"
cp "$e1" "$T/com-2217"   # the flush posted the queued comment, so the freeze reads back as standing
w2=$(posts)
r=$((cd "$T/co" && bash "$T/s/wave.sh" fail 2217 run-owner218 "owner-only: criterion (1) needs a credential" own owner-only) 2>&1)
[ "$(posts)" = "$w2" ] && ok "N2: a second run id of the same class writes no second freeze (debt 3)" || ko "N2: a second run id froze the issue again: '$r'"
case "$r" in *"already stands"*) ok "N2: the standing freeze is named as standing";; *) ko "N2: the standing freeze is not named: '$r'";; esac
[ "$(endfiles 2217 | /usr/bin/grep -c .)" = 1 ] && ok "N2: and queues no second status comment" || ko "N2: a second status comment was queued"
rm -f "$T"/st/cq/*#2217-endclass* "$T/com-2217"   # the labels stand, but the comment never reached the issue
r=$((cd "$T/co" && bash "$T/s/wave.sh" fail 2217 run-owner219 "owner-only: criterion (1) needs a credential" own owner-only) 2>&1)
[ "$(endfiles 2217 | /usr/bin/grep -c .)" = 1 ] && ok "N2b: a freeze whose comment is missing queues the comment again (debt 3)" || ko "N2b: the missing comment was not queued again: '$r'"
case "$r" in *"already stands"*) ko "N2b: a freeze with no comment is named as standing: '$r'";; *) ok "N2b: a freeze with no comment is not named as standing";; esac
nrb=$($G -C "$T/co" rev-parse --short=12 HEAD)
r=$((cd "$T/co" && bash "$T/s/wave.sh" fail 2201 run-nr201 "the defect does not reproduce at the tip" own does-not-reproduce) 2>&1)
e3=$(ls "$T/st/cq/"*#2201-endclass* 2>/dev/null)
if /usr/bin/grep -q 'issues/2201/labels.*labels\[\]=frozen:not-startable' "$T/gh.log" \
   && [ "$(printf '%s\n' "$e3" | /usr/bin/grep -c .)" = 1 ] && /usr/bin/grep -qF "SQUAD-NOT-STARTABLE: build=$nrb" "$e3"; then
  ok "N3: a does-not-reproduce end freezes its issue at this build"
else ko "N3: does-not-reproduce end did not freeze: '$r'"; fi
printf 'SQUAD-NOT-STARTABLE: build=%s run run-nr201 ended failed: the defect does not reproduce at the base tip.\n\nReason: the defect does not reproduce at the tip\n' "$nrb" > "$T/com-2201"
w3=$(posts)
r=$((cd "$T/co" && bash "$T/s/wave.sh" fail 2201 run-nr202 "the defect does not reproduce at the tip" own does-not-reproduce) 2>&1)
[ "$(posts)" = "$w3" ] && ok "N3: the same build keeps its freeze and writes nothing again" || ko "N3: the same build froze the issue again: '$r'"
(cd "$T/co" && echo z > z && $G add z && $G -c user.email=a@b -c user.name=t commit -q -m "a later build")
r=$((cd "$T/co" && bash "$T/s/wave.sh" fail 2201 run-nr203 "the defect does not reproduce at the tip" own does-not-reproduce) 2>&1)
[ "$(freezes 2201)" = 2 ] && ok "N3: a later build writes the freeze again" || ko "N3: a later build wrote $(freezes 2201) freeze request(s): '$r'"
r=$((cd "$T/co" && bash "$T/s/wave.sh" fail 2202 run-gate202 "ac-verify:no_ac_commands" own) 2>&1)
case "$r" in *"WAVE FAILED #2202"*) ok "N4: a gate reason is counted as before";; *) ko "N4: gate reason not counted: '$r'";; esac
/usr/bin/grep -q 'issues/2202/labels.*labels\[\]=frozen' "$T/gh.log" && ko "N4: a gate reason froze its issue" || ok "N4: a gate reason freezes nothing"
export LABEL_REFUSE=2203
r=$((cd "$T/co" && bash "$T/s/wave.sh" fail 2203 run-own203 "owner-only: a credential" own owner-only) 2>&1); rc=$?
unset LABEL_REFUSE
[ "$rc" != 0 ] && ok "N5: a refused freeze exits non-zero (debt 2)" || ko "N5: the refused freeze exited 0: '$r'"
case "$r" in *"WAVE FROZEN"*) ko "N5: a refused freeze claims the issue is frozen";; *) ok "N5: a refused freeze prints no WAVE FROZEN";; esac
case "$r" in *"WAVE FREEZE PARTIAL #2203"*) ok "N5: the partial freeze is named";; *) ko "N5: the partial freeze is silent: '$r'";; esac
[ -z "$(ls "$T/st/cq/"*#2203-endclass* 2>/dev/null)" ] && ok "N5: no comment is queued for a refused freeze" || ko "N5: a comment was queued for a refused freeze"

rm -rf "$T"; [ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
