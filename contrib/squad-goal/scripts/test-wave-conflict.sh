#!/bin/bash
# seconds: 66.38
# The landing conflict and the entry gate's reuse (#1500, #1718, #1798, #1800): one rebase round on a kept branch,
# the round rule for a conflict met on it, and the entry gate's memo and mode.
# mirrors: wave.sh wave-fixture.sh
. "$(dirname "$0")/wave-fixture.sh"

# The Z9 cases take the machine-wide gate lock, as the entry cases do; the holder helper is beside them,
# never in the fixture: a script the loop could run must not take the lock (tests/test_squad_full_gate_machine_lock.py).
hold() { SG_STATE=$T/st bash "$T/s/serial.sh" gate sh -c "sleep 10; echo holder >> $T/gl" & holder=$!
  for _ in $(seq 1 600); do
    [ "$(cat "$T/st/locks/gate/pid" 2>/dev/null)" = "$holder" ] && return 0
    kill -0 "$holder" 2>/dev/null || return 1   # the holder is gone: it never came to hold the lock
    sleep 0.1; done
  return 1; }

# L (#1500): a landing conflict keeps the wave head on fix/<n>-land-<run> for one rebase round, never a requeue and a rebuild. The
# marker's line 1 is the bare base ref (every #1344 reader stays unchanged); `issue <n>` lines are the close set; `round <k> <sha>`
# lines are the rounds that started. The repeat rule reads the base sha: the same sha after a round is a loop bug, a moved base
# starts the next round, and a conflict after round two requeues exactly the issue lines with rework and removes the branches.
lwave() { local run=$1 spec n f; shift; start2 "$run" >/dev/null; mkdir -p "$T/co/runs/$run"; : > "$T/co/runs/$run/record.jsonl"
  for spec in "$@"; do n=${spec%%:*}; f=${spec#*:}
    [ "$f" = - ] || { child "$n" "$f" "origin/wave/$run"; en2 "$run" "$n" >/dev/null; }
    printf '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #%s work"}}\n' "$n" >> "$T/co/runs/$run/record.jsonl"; done; }
ldev() { rm -rf "$T/wl"; $G clone -q "$T/origin.git" "$T/wl" 2>/dev/null
  (cd "$T/wl" && $G checkout -q dev && echo "dev $RANDOM$RANDOM" > "$1" && $G add "$1" && $G -c user.email=a@b -c user.name=t commit -q -m "dev moves $1" && $G push -q origin HEAD:dev); }
lmk() { printf '%s/st/auto/kept-fix_%s-land-%s' "$T" "$1" "$2"; }
lbr() { $O rev-parse -q --verify "refs/heads/$1" 2>/dev/null; }
lreset() { : > "$T/gh.log"; : > "$T/board.log"; rm -f "$T"/st/cq/*150[0-9]-* "$T"/st/cq/*151[0-9]-*; }

# L1: the first landing conflict keeps the wave head; nothing is requeued and no rework label is added.
lwave run-l1 1501:lf1; ldev lf1; lreset; wh=$($O rev-parse wave/run-l1); d1=$($O rev-parse dev)
r=$(land2 run-l1); rc=$?
[ "$(lbr fix/1501-land-run-l1)" = "$wh" ] && ok "L1: the wave head is kept on fix/1501-land-run-l1" || ko "L1: no kept branch at the wave head: '$r'"
m=$(lmk 1501 run-l1)
[ "$(sed -n 1p "$m" 2>/dev/null)" = origin/dev ] && [ "$(sed -n 2p "$m" 2>/dev/null)" = "issue 1501" ] && [ "$(sed -n 3p "$m" 2>/dev/null)" = "round 1 $d1" ] && [ "$(wc -l < "$m" 2>/dev/null | tr -d ' ')" = 3 ] && ok "L1: the marker is the bare base ref, one issue line and round 1 with the base sha" || ko "L1: marker is '$(cat "$m" 2>&1 | tr '\n' '|')'"
head -1 "$T/st/review-fix_1501-land-run-l1.md" 2>/dev/null | /usr/bin/grep -q 'VERDICT: CHANGES' && /usr/bin/grep -q 'Rebase this branch on origin/dev' "$T/st/review-fix_1501-land-run-l1.md" && [ "$(cat "$T/st/review-fix_1501-land-run-l1.sha" 2>/dev/null)" = "$wh" ] && ok "L1: a CHANGES review at the kept head asks a rebase on origin/dev" || ko "L1: no round review"
case "$r" in *"WAVE REQUEUE"*) ko "L1: the conflict requeued: '$r'";; *) ok "L1: no requeue";; esac
/usr/bin/grep -q ' 1501$' "$T/board.log" && ko "L1: the card moved: '$(cat "$T/board.log")'" || ok "L1: the card does not move"
/usr/bin/grep -q 'labels\[\]=rework' "$T/gh.log" && ko "L1: a rework label was added" || ok "L1: no rework label"
[ $rc = 1 ] && [ ! -e "$T/st/auto/wave-run-l1" ] && ok "L1: exit 1 and the wave marker goes" || ko "L1: exit $rc, wave marker '$(ls "$T/st/auto" | /usr/bin/grep wave-run-l1)'"
[ -n "$(lbr wave/run-l1)" ] && ok "L1: wave/run-l1 stays on the remote while the round runs" || ko "L1: wave/run-l1 is gone"

# L2: a two-issue wave keeps one branch named for the lowest issue; both issues are in the marker.
lwave run-l2 1503:lf2b 1502:lf2a; ldev lf2a; lreset; r=$(land2 run-l2)
m=$(lmk 1502 run-l2)
[ "$(sed -n '2,3p' "$m" 2>/dev/null | tr '\n' ,)" = "issue 1502,issue 1503," ] && [ -z "$(lbr fix/1503-land-run-l2)" ] && ok "L2: one branch, both issues in the marker" || ko "L2: '$(cat "$m" 2>&1 | tr '\n' '|')'"

# L10: an issue the run dispatched that is not on the wave head is not listed.
lwave run-l10 1512:lf10 1513:-; ldev lf10; lreset; r=$(land2 run-l10)
case "$r" in *"WAVE REQUEUE #1513"*) ok "L10: a dispatched issue the wave head does not carry is requeued, not stranded";; *) ko "L10: #1513 not requeued: '$r'";; esac
/usr/bin/grep -qx "o 3 Todo 1513" "$T/board.log" && ! /usr/bin/grep -qx "o 3 Todo 1512" "$T/board.log" && ok "L10: the card of #1513 goes to Todo and the kept #1512 stays" || ko "L10: board: '$(cat "$T/board.log")'"
[ "$(grep -c '^issue ' "$(lmk 1512 run-l10)" 2>/dev/null)" = 1 ] && ! /usr/bin/grep -q 'issue 1513' "$(lmk 1512 run-l10)" && ok "L10: a dropped issue is not in the marker" || ko "L10: '$(cat "$(lmk 1512 run-l10)" 2>&1 | tr '\n' '|')'"

# L4: the same paths with a moved base start round two, and no bug is filed.
lwave run-l4 1504:lf4; ldev lf4; r=$(land2 run-l4); ldev lf4; d2=$($O rev-parse dev); lreset; r=$(land2 run-l4)
m=$(lmk 1504 run-l4)
[ "$(sed -n 4p "$m" 2>/dev/null)" = "round 2 $d2" ] && ok "L4: round two starts at the moved base" || ko "L4: marker is '$(cat "$m" 2>&1 | tr '\n' '|')'"
/usr/bin/grep -q 'labels\[\]=bug' "$T/gh.log" && ko "L4: a bug was filed" || ok "L4: no bug is filed"
case "$r" in *"WAVE REQUEUE"*) ko "L4: requeued: '$r'";; *) ok "L4: not requeued";; esac
/usr/bin/grep -q 'Rebase this branch on origin/dev' "$T/st/review-fix_1504-land-run-l4.md" 2>/dev/null && ok "L4: the round review is written again" || ko "L4: no round review"

# L5: the same base sha after a round did not resolve the conflict: the loop bug, whatever the round count.
lwave run-l5 1505:lf5; ldev lf5; land2 run-l5 >/dev/null; lreset; r=$(land2 run-l5)
[ "$(/usr/bin/grep -c 'labels\[\]=bug' "$T/gh.log")" = 1 ] && case "$r" in *"WAVE-BUG #1505"*) true;; *) false;; esac && ok "L5: the same base sha files the loop bug" || ko "L5: no bug: '$r'"
case "$r" in *"WAVE REQUEUE"*) ko "L5: requeued: '$r'";; *) ok "L5: not requeued";; esac

# L6: a third conflict requeues exactly the marker's issue lines with rework, removes the marker and both branches, files no bug.
lwave run-l6 1506:lf6a 1511:lf6b; ldev lf6a; land2 run-l6 >/dev/null; ldev lf6a; land2 run-l6 >/dev/null; ldev lf6a; lreset; r=$(land2 run-l6)
for n in 1506 1511; do
  /usr/bin/grep -qx "o 3 Todo $n" "$T/board.log" && /usr/bin/grep -q "issues/$n/labels.*labels\[\]=rework" "$T/gh.log" && ok "L6: #$n is requeued with rework" || ko "L6: #$n not requeued: '$r'"
done
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c 'WAVE REQUEUE')" = 2 ] && ok "L6: the requeued set is the marker's issue lines" || ko "L6: requeues: '$r'"
[ ! -e "$(lmk 1506 run-l6)" ] && [ -z "$(lbr fix/1506-land-run-l6)" ] && [ -z "$(lbr wave/run-l6)" ] && ok "L6: the marker, the kept branch and wave/run-l6 are gone" || ko "L6: marker/branches remain"
/usr/bin/grep -q 'labels\[\]=bug' "$T/gh.log" && ko "L6: a bug was filed" || ok "L6: no bug is filed"
# a later run of the same issue starts at round one
lwave run-l6b 1506:lf6c; ldev lf6c; lreset; r=$(land2 run-l6b)
[ "$(sed -n '3p' "$(lmk 1506 run-l6b)" 2>/dev/null | cut -d' ' -f1,2)" = "round 1" ] && ! /usr/bin/grep -q 'labels\[\]=bug' "$T/gh.log" && ok "L6: a later run of the issue starts at round one" || ko "L6: '$(cat "$(lmk 1506 run-l6b)" 2>&1 | tr '\n' '|')' '$r'"

# L11: the same sha after round two files the loop bug and does not requeue.
lwave run-l11 1514:lf11; ldev lf11; land2 run-l11 >/dev/null; ldev lf11; land2 run-l11 >/dev/null; lreset; r=$(land2 run-l11)
[ "$(/usr/bin/grep -c 'labels\[\]=bug' "$T/gh.log")" = 1 ] && ok "L11: the same sha after round two files the loop bug" || ko "L11: no bug: '$r'"
case "$r" in *"WAVE REQUEUE"*) ko "L11: requeued: '$r'";; *) ok "L11: not requeued";; esac

# L7: a #1344 entry-kept branch of the issue does not collide: the landing conflict starts round one on its own -land- marker.
printf 'origin/wave/run-l7\n' > "$T/st/auto/kept-fix_1507-rebase-run-l7"
lwave run-l7 1507:lf7; ldev lf7; lreset; r=$(land2 run-l7)
[ "$(sed -n 3p "$(lmk 1507 run-l7)" 2>/dev/null | cut -d' ' -f1,2)" = "round 1" ] && [ "$(cat "$T/st/auto/kept-fix_1507-rebase-run-l7")" = origin/wave/run-l7 ] && ok "L7: round one on the -land- marker; the entry marker is untouched" || ko "L7: '$(cat "$(lmk 1507 run-l7)" 2>&1 | tr '\n' '|')'"

# L12: wave.sh landconflict is the repeat rule for a conflict the lander meets on a kept landing branch (branchland.sh calls it).
lwave run-l12 1515:lf12; ldev lf12; land2 run-l12 >/dev/null; ldev lf12; nd=$($O rev-parse dev); lreset
r=$(cd "$T/co" && bash "$T/s/wave.sh" landconflict fix/1515-land-run-l12 "$nd" "$($O rev-parse fix/1515-land-run-l12)" "lf12")
[ "$(sed -n 4p "$(lmk 1515 run-l12)" 2>/dev/null)" = "round 2 $nd" ] && ok "L12: landconflict starts round two at the moved base" || ko "L12: '$(cat "$(lmk 1515 run-l12)" 2>&1 | tr '\n' '|')' '$r'"

# Z9 (#1718, ADR-0168): the child's final gate is its last step, so its green pass is the wave entry gate's own key. The entry
# asks the memo before it takes the machine gate lock: a pass already holding the tree it enters with admits the child with no
# second battery and with no wait behind a held lock, and the entry record names the reuse. A child that commits after its gate
# made a new tree: the memo answers nothing, so the entry still meets the lock, holds out of a held one, and runs the battery
# once the lock is free. A red or a doubt always runs the suite.
W23=wave/run-23; start2 run-23 >/dev/null
printf '#!/bin/bash\necho battery >> "%s/z9-battery.log"\n' "$T" > "$T/z9-batt.sh"
BL=$T/z9-battery.log
z9_childgate() { (cd "$1" && SG_STATE=$T/st SG_FAST_CMD="bash $T/z9-batt.sh" SG_FAST_RETRY_CMD=false bash "$T/s/fast-gate.sh" 2>&1); }
z9_entry() { (cd "$1" && SG_STATE=$T/st SG_WAVE_GATES="bash $T/s/fast-gate.sh" SG_FAST_CMD="bash $T/z9-batt.sh" SG_FAST_RETRY_CMD=false bash "../s/wave.sh" enter run-23 "$2" 2>&1); }
# Z9b (#1798): the entry's mode is on its gate-history line, in the seventeenth field: `reused` when a kept green pass
# answered the entry and `gated` when the entry ran the gate itself. It is read with the caller, so a row of the child's own
# gate never stands in for the entry's.
lmode() { awk -F'\t' -v s="$1" '$14 == "entry" && $4 == s { m = $14 " " $17 } END { print m }' "$T/st/gate-history" 2>/dev/null; }
child 960 g960 "origin/$W23"; rm -f "$BL"; o=$(z9_childgate "$T/c960")
[ -s "$BL" ] && ok "Z9: the child's final gate runs the battery and keeps its pass" || ko "Z9: the child's gate ran no battery: '$o'"
# A child that commits after its gate holds a tree the pass never read. Its setup runs before any hold, so the hold covers only
# the entry it is meant to prove.
child 961 g961 "origin/$W23"; rm -f "$BL"; o=$(z9_childgate "$T/c961")
echo more >> "$T/c961/g961"; $G -C "$T/c961" commit -qam "a change after the gate (Refs #961)"
if hold; then
  rm -f "$BL"; REV_BASE=$W23 rev 960
  o=$(z9_entry "$T/c960" 960); rc=$?
  case "$o" in *"entered $W23"*) ok "Z9: a green final gate frees the child of the held gate lock: it enters";; *) ko "Z9: the entry did not enter under the held lock: rc $rc '$o'";; esac
  [ -s "$BL" ] && ko "Z9: the entry ran a second battery: $(cat "$BL")" || ok "Z9: the entry runs no second battery for the tree the child proved"
  case "$(wfact 960 run-23)" in *reuse*) ok "Z9: the entry record names the reuse";; *) ko "Z9: the record does not name the reuse: '$(wfact 960 run-23)'";; esac
  s960=$($G -C "$T/c960" rev-parse HEAD)
  [ "$(lmode "$s960")" = "entry reused" ] && ok "Z9: the entry's reuse is a history row whose mode is reused" \
    || ko "Z9: the reuse row is '$(lmode "$s960")': '$(tail -n 1 "$T/st/gate-history" 2>/dev/null)'"
  printf '1\tgate\tpass\t%s\t1\t\t\tpass\t\t0\t0\t0\tno\tchild\t0\t0\tskipped\t-\t-\t0\t-\tno\tlocal\t-\n' "$s960" >> "$T/st/gate-history"
  [ "$(lmode "$s960")" = "entry reused" ] && ok "Z9: a child row after the entry row leaves the entry's reuse row" \
    || ko "Z9: a child row after the entry row shadows the reuse row: '$(lmode "$s960")'"
  wait
  if hold; then
    rm -f "$BL"; REV_BASE=$W23 rev 961
    o=$(z9_entry "$T/c961" 961); rc=$?
    case "$o" in *"WAVE HOLD #961"*) ok "Z9: a tree the pass never read still meets the held gate lock";; *) ko "Z9: the changed tree entered beside the lock or ran a battery: rc $rc '$o'";; esac
    [ -s "$BL" ] && ko "Z9: the changed tree ran a battery beside the lock: $(cat "$BL")" || ok "Z9: nothing ran beside the held lock"
    wait
    REV_BASE=$W23 rev 961   # the held pass rebased the child on the wave tip before it met the lock: the review reads its new head
    rm -f "$BL"; r=$(z9_entry "$T/c961" 961); rc=$?
    [ $rc = 0 ] && [ -s "$BL" ] && ok "Z9: with the lock free, the changed tree runs its own battery and enters" || ko "Z9: the changed tree did not run its battery: rc $rc '$(cat "$BL" 2>/dev/null)' '$r'"
    case "$(wfact 961 run-23)" in *reuse*) ko "Z9: a fresh battery recorded a reuse it never made: '$(wfact 961 run-23)'";; *) ok "Z9: a fresh battery records no reuse";; esac
    s961=$($G -C "$T/c961" rev-parse HEAD)
    [ "$(lmode "$s961")" = "entry gated" ] && ok "Z9: the entry that ran its own gate is a history row whose mode is gated" \
      || ko "Z9: the gated row is '$(lmode "$s961")': '$(tail -n 1 "$T/st/gate-history" 2>/dev/null)'"
  else
    wait 2>/dev/null
    ko "Z9: the second holder never held the gate lock, so the changed tree's hold could not be proved"
  fi
else
  wait 2>/dev/null
  ko "Z9: the holder never came to hold the gate lock, so the entry without a lock could not be proved"
fi

# Z10 (#1800, ADR-0166): SG_WAVE_ENTRY_GATE names whether the wave entry runs its own battery. With `off`, a child whose entry
# finds no reusable green verdict enters after the clean rebase with no battery, and the entry is recorded as skipped. A reuse
# hit enters as today and is recorded as reused, and with `on` the entry is unchanged: a miss runs the battery.
W24=wave/run-24; start2 run-24 >/dev/null
printf '#!/bin/bash\necho battery >> "%s/z10-battery.log"\n' "$T" > "$T/z10-batt.sh"
BL10=$T/z10-battery.log
z10_childgate() { (cd "$1" && SG_STATE=$T/st SG_FAST_CMD="bash $T/z10-batt.sh" SG_FAST_RETRY_CMD=false bash "$T/s/fast-gate.sh" 2>&1); }
z10_entry() { (cd "$1" && SG_STATE=$T/st SG_WAVE_ENTRY_GATE=$2 SG_WAVE_GATES="bash $T/s/fast-gate.sh" SG_FAST_CMD="bash $T/z10-batt.sh" SG_FAST_RETRY_CMD=false bash "../s/wave.sh" enter run-24 "$3" 2>&1); }
# (1) The gate off, and no reusable verdict: the child enters with no battery, and the entry records skipped.
child 970 g970 "origin/$W24"; REV_BASE=$W24 rev 970; rm -f "$BL10"
o=$(z10_entry "$T/c970" off 970); rc=$?
case "$o" in *"entered $W24"*) [ $rc = 0 ] && ok "Z10: the gate off enters a child with no reusable verdict";; *) ko "Z10: the off entry refused the child: rc $rc '$o'";; esac
[ -s "$BL10" ] && ko "Z10: the off entry ran a battery on a miss: $(cat "$BL10")" || ok "Z10: the off entry runs no battery on a miss"
case "$(wfact 970 run-24)" in "entered "*skipped*) ok "Z10: the off entry records skipped";; *) ko "Z10: the entry record is '$(wfact 970 run-24)'";; esac
s970=$($G -C "$T/c970" rev-parse HEAD)
[ "$(lmode "$s970")" = "entry skipped" ] && ok "Z10: the skipped entry is a history row whose mode is skipped" \
  || ko "Z10: the skipped row is '$(lmode "$s970")': '$(tail -n 1 "$T/st/gate-history" 2>/dev/null)'"
# (2) The gate off, and a reusable green verdict: the child enters as today, and the entry records reused.
child 971 g971 "origin/$W24"; rm -f "$BL10"; z10_childgate "$T/c971" >/dev/null; rm -f "$BL10"; REV_BASE=$W24 rev 971
o=$(z10_entry "$T/c971" off 971); rc=$?
case "$o" in *"entered $W24"*) ok "Z10: the gate off enters a child whose final gate is a reuse hit";; *) ko "Z10: the reuse hit did not enter: rc $rc '$o'";; esac
[ -s "$BL10" ] && ko "Z10: the reuse hit ran a second battery: $(cat "$BL10")" || ok "Z10: the reuse hit runs no battery"
s971=$($G -C "$T/c971" rev-parse HEAD)
[ "$(lmode "$s971")" = "entry reused" ] && ok "Z10: the reuse hit is a history row whose mode is reused" \
  || ko "Z10: the reuse row is '$(lmode "$s971")': '$(tail -n 1 "$T/st/gate-history" 2>/dev/null)'"
# (3) The gate on (the default): a miss runs the battery as before, and the entry records no skip.
child 972 g972 "origin/$W24"; REV_BASE=$W24 rev 972; rm -f "$BL10"
o=$(z10_entry "$T/c972" on 972); rc=$?
case "$o" in *"entered $W24"*) ok "Z10: the gate on enters a child whose verdict is a miss";; *) ko "Z10: the on entry did not enter: rc $rc '$o'";; esac
[ -s "$BL10" ] && ok "Z10: the gate on runs the battery on a miss" || ko "Z10: the gate on ran no battery on a miss: '$o'"
case "$(wfact 972 run-24)" in *skipped*) ko "Z10: the gate on recorded a skip it never made: '$(wfact 972 run-24)'";; *) ok "Z10: the gate on records no skip";; esac

# Z11 (#1800, ADR-0166): the wave's landing gate is unchanged when the entry gate is off. It still proves every child, and a
# red tip still takes out the child that broke it and lands the rest.
W25=wave/run-25; start2 run-25 >/dev/null
en2off() { local r=$1 n=$2 o; REV_BASE=wave/$r rev "$n"; o=$((cd "$T/c$n" && SG_WAVE_ENTRY_GATE=off SG_WAVE_GATES=true bash "$T/s/wave.sh" enter "$r" "$n") 2>&1) \
  || { ko "en2off $r #$n: the child did not enter its wave: $o"; return 1; }; printf '%s\n' "$o"; }
child 980 g980 "origin/$W25"; en2off run-25 980 >/dev/null
child 981 g981 "origin/$W25"; en2off run-25 981 >/dev/null
child 982 g982 "origin/$W25"; en2off run-25 982 >/dev/null
printf 'run-25\n' > "$T/st/auto/disp-981"
: > "$T/gh.log"; : > "$T/board.log"
r=$(SG_WAVE_TIP_GATES='! /usr/bin/git log --format=%s origin/dev..HEAD | /usr/bin/grep -q "work 981"' SG_WAVE_CHILD_GATES='[ "$SG_WAVE_CHILD" = 981 ] && exit 1; exit 0' land2 run-25); rc=$?
[ $rc = 0 ] && ok "Z11: with the entry gate off, the wave without the child that broke it lands (exit 0)" || ko "Z11: exit $rc: '$r'"
case "$r" in *"WAVE CHECK RED"*"#981"*) ok "Z11: the landing gate names the child that broke the wave";; *) ko "Z11: no child named: '$r'";; esac
$O cat-file -e dev:g980 2>/dev/null && $O cat-file -e dev:g982 2>/dev/null && ok "Z11: the other children land" || ko "Z11: the other children are not in dev"
$O cat-file -e dev:g981 2>/dev/null && ko "Z11: the child that broke the wave landed" || ok "Z11: the culprit is not in dev"
/usr/bin/grep -q 'issues/980 -f state=closed' "$T/gh.log" && /usr/bin/grep -q 'issues/982 -f state=closed' "$T/gh.log" && ok "Z11: the issues of the other children close" || ko "Z11: the other children's issues were not closed"
/usr/bin/grep -q 'issues/981 -f state=closed' "$T/gh.log" && ko "Z11: the culprit's issue was closed" || ok "Z11: the culprit's issue is not closed"
[ ! -e "$T/st/auto/disp-981" ] && /usr/bin/grep -qx 'o 3 Todo 981' "$T/board.log" && ok "Z11: the culprit is back in the queue" || ko "Z11: the culprit was not requeued"

# Z12 (#1800, criterion 4): a wave of two children, both entered with the entry gate off. The bad child's tree carries the file
# z12bad, and the landing battery fails on any tree that holds it: the tip check reads the tip's tree, and each child's own
# check reads the files its commits change. The landing gate takes the bad child out and lands the good one.
W26=wave/run-26; start2 run-26 >/dev/null
child 990 g990 "origin/$W26"; en2off run-26 990 >/dev/null
child 991 z12bad "origin/$W26"; en2off run-26 991 >/dev/null
case "$(wfact 990 run-26)" in "entered "*skipped*) ok "Z12: the good child entered with no entry battery";; *) ko "Z12: #990 entry is '$(wfact 990 run-26)'";; esac
case "$(wfact 991 run-26)" in "entered "*skipped*) ok "Z12: the bad child entered with no entry battery";; *) ko "Z12: #991 entry is '$(wfact 991 run-26)'";; esac
printf 'run-26\n' > "$T/st/auto/disp-991"
: > "$T/gh.log"; : > "$T/board.log"
r=$(SG_WAVE_TIP_GATES='[ ! -e z12bad ]' SG_WAVE_CHILD_GATES='/usr/bin/git diff --name-only "$SG_WAVE_CHILD_FROM" "$SG_WAVE_CHILD_TO" | /usr/bin/grep -qx z12bad && exit 1; exit 0' land2 run-26); rc=$?
[ $rc = 0 ] && ok "Z12: with the entry gate off, the wave without the bad child lands (exit 0)" || ko "Z12: exit $rc: '$r'"
case "$r" in *"WAVE CHECK RED"*"#991"*) ok "Z12: the landing gate names the bad child";; *) ko "Z12: no child named: '$r'";; esac
$O cat-file -e dev:g990 2>/dev/null && ok "Z12: the good child lands" || ko "Z12: the good child is not in dev"
$O cat-file -e dev:z12bad 2>/dev/null && ko "Z12: the bad child landed" || ok "Z12: the bad child is not in dev"
/usr/bin/grep -q 'issues/990 -f state=closed' "$T/gh.log" && ok "Z12: the good child's issue closes" || ko "Z12: #990 was not closed"
/usr/bin/grep -q 'issues/991 -f state=closed' "$T/gh.log" && ko "Z12: the bad child's issue was closed" || ok "Z12: the bad child's issue is not closed"
[ ! -e "$T/st/auto/disp-991" ] && /usr/bin/grep -qx 'o 3 Todo 991' "$T/board.log" && ok "Z12: the bad child is back in the queue" || ko "Z12: the bad child was not requeued"

rm -rf "$T"; [ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
