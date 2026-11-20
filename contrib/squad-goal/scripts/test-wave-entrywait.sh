#!/bin/bash
# seconds: 11.54
# A wave closes early when its oldest entry waited past a limit (#2316, ADR-0145 rule 16): it lands with the children that
# entered it, the children of the run that did not enter it keep their card and their dispatch, and a later entry cuts the
# run's next wave from the moved base. A wave with no entered child never closes early, and the 7200 s age cap stays the
# second, later close.
# mirrors: wave.sh wave-fixture.sh
. "$(dirname "$0")/wave-fixture.sh"

# EW1 (the setting): the entry wait is a profile setting, 1800 s by default. A wave whose oldest entry is younger than the
# limit does not close early; one past it does; a profile's own SG_WAVE_ENTRY_WAIT_SECONDS decides both.
W20=wave/run-20; start2 run-20 >/dev/null; child 200 g200 "origin/$W20"; en2 run-20 200 >/dev/null
s=$(date +%s)
printf '%s\n' "$(( s - 60 ))" > "$T/st/auto/wave-entry-run-20"
bash "$T/s/wave.sh" entrywait run-20 >/dev/null 2>&1 && ko "EW1: a wave 60 s past its oldest entry closed early under the 1800 s default" || ok "EW1: the 1800 s default holds a wave 60 s past its oldest entry"
SG_WAVE_ENTRY_WAIT_SECONDS=30 bash "$T/s/wave.sh" entrywait run-20 >/dev/null 2>&1 && ok "EW1: a profile's limit of 30 s closes the wave early" || ko "EW1: SG_WAVE_ENTRY_WAIT_SECONDS is not read"
printf '%s\n' "$(( s - 1801 ))" > "$T/st/auto/wave-entry-run-20"
bash "$T/s/wave.sh" entrywait run-20 >/dev/null 2>&1 && ok "EW1: a wave 1801 s past its oldest entry closes early at the default limit" || ko "EW1: the default limit does not close a wave 1801 s past its oldest entry"

# EW2 (the early close and the child left out): #200 entered the wave; #201 was dispatched and never entered. The wave lands
# with #200, one WAVE CLOSE line names the wave, the limit and #201, and #201 keeps its card and its dispatch.
printf 'run-20\n' > "$T/st/auto/disp-201"
mkdir -p "$T/co/runs/run-20"
printf '%s\n%s\n' \
  '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #200 work"}}' \
  '{"event": "spawn_accepted", "child": "b", "definition": {"prompt": "GitHub issue #201 work"}}' > "$T/co/runs/run-20/record.jsonl"
child 201 g201 "origin/$W20"; REV_BASE=$W20 rev 201   # the sibling is ready to enter; it has not entered yet
: > "$T/gh.log"; : > "$T/board.log"
r=$(land2 run-20); rc=$?
[ $rc = 0 ] && ok "EW2: the wave whose oldest entry passed the limit lands (exit 0)" || ko "EW2: exit $rc: '$r'"
$O cat-file -e dev:g200 2>/dev/null && ok "EW2: the child that entered is in dev" || ko "EW2: the entered child is not in dev"
/usr/bin/grep -q 'issues/200 -f state=closed' "$T/gh.log" && ok "EW2: the entered child's issue is closed" || ko "EW2: #200 is not closed"
/usr/bin/grep -q 'issues/201 -f state=closed' "$T/gh.log" && ko "EW2: the child left out was closed" || ok "EW2: the child left out is not closed"
case "$r" in *"WAVE CLOSE $W20"*"1800"*"#201"*) ok "EW2: one WAVE CLOSE line names the wave, the limit and the child left out";; *) ko "EW2: no close line with the wave, the limit and the child: '$r'";; esac
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c 'WAVE CLOSE')" = 1 ] && ok "EW2: the landing writes one close line" || ko "EW2: close lines: $(printf '%s\n' "$r" | /usr/bin/grep -c 'WAVE CLOSE')"
/usr/bin/grep -qx 'o 3 Todo 201' "$T/board.log" && ko "EW2: the child left out lost its column" || ok "EW2: the child left out keeps its card"
[ -e "$T/st/auto/disp-201" ] && ok "EW2: the child left out keeps its dispatch" || ko "EW2: the child left out lost its dispatch marker"
[ -s "$T/st/auto/wave-run-20" ] && [ -s "$T/st/auto/wave-recut-run-20" ] && ok "EW2: the run keeps a wave to land, and the next cut is pending" || ko "EW2: the run's wave marker or the recut marker is missing"
[ ! -e "$T/st/auto/wave-entry-run-20" ] && ok "EW2: the next wave starts its own entry clock" || ko "EW2: the entry clock of the closed wave survives"

# EW3 (the late sibling's later wave): the child left out enters after the close. Its wave branch is gone, so the entry cuts
# the run's next wave from the moved base, and that wave lands with it.
r=$(cd "$T/c201" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-20 201 2>&1); rc=$?
[ $rc = 0 ] && ok "EW3: the child left out enters the run's later wave (exit 0)" || ko "EW3: exit $rc: '$r'"
$O cat-file -e $W20:g201 2>/dev/null && ok "EW3: the later wave carries the child left out" || ko "EW3: the later wave does not carry #201"
$O merge-base --is-ancestor dev $W20 && ok "EW3: the later wave is cut from the moved base" || ko "EW3: the later wave is not cut from the moved base"
: > "$T/gh.log"; : > "$T/board.log"
r=$(land2 run-20); rc=$?
[ $rc = 0 ] && $O cat-file -e dev:g201 2>/dev/null && ok "EW3: the run's second wave lands too (exit 0)" || ko "EW3: exit $rc: '$r'"
/usr/bin/grep -q 'issues/201 -f state=closed' "$T/gh.log" && ok "EW3: the child left out is delivered by the later wave" || ko "EW3: #201 is not closed"
[ ! -e "$T/st/auto/wave-run-20" ] && ok "EW3: the run's last wave marker goes" || ko "EW3: the run's wave marker survives its last landing"
case "$r" in *"WAVE CLOSE"*) ko "EW3: the later wave closed early: '$r'";; *) ok "EW3: the later wave lands whole, with no early close";; esac

# EW4 (no entered child): a wave that holds no entry never closes early, whatever its age under the cap. Its branch is
# empty, so it lands empty and writes no close line.
W21=wave/run-21; start2 run-21 >/dev/null
printf 'run-21\n' > "$T/st/auto/disp-211"
mkdir -p "$T/co/runs/run-21"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #211 work"}}' > "$T/co/runs/run-21/record.jsonl"
printf '%s\n' "$(( $(date +%s) - 3000 ))" > "$T/st/auto/wave-start-run-21"
bash "$T/s/wave.sh" entrywait run-21 >/dev/null 2>&1 && ko "EW4: a wave with no entry closed early" || ok "EW4: a wave with no entered child does not close early"
r=$(land2 run-21); rc=$?
if [ $rc = 0 ]; then case "$r" in *"WAVE CLOSE"*) ko "EW4: an empty wave wrote a close line: '$r'";; *) ok "EW4: a wave with no entered child lands with no close line";; esac
else ko "EW4: exit $rc: '$r'"; fi

# EW5 (the age cap stays): the cap is still the later close.
W22=wave/run-22; start2 run-22 >/dev/null; child 220 g220 "origin/$W22"; en2 run-22 220 >/dev/null
printf '%s\n' "$(( $(date +%s) - 7201 ))" > "$T/st/auto/wave-start-run-22"
bash "$T/s/wave.sh" expired run-22 >/dev/null 2>&1 && ok "EW5: the 7200 s age cap still applies" || ko "EW5: a wave past its age cap is not expired"

# EW6 (#2538): a child the run already requeued is not left out. #230 entered; #231 has a requeue fact for the run. The early
# close lands #230 and keeps no marker, so the run stops holding #231 as busy.
W23=wave/run-23; start2 run-23 >/dev/null; child 230 g230 "origin/$W23"; en2 run-23 230 >/dev/null
mkdir -p "$T/co/runs/run-23"
printf '%s\n%s\n' \
  '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #230 work"}}' \
  '{"event": "spawn_accepted", "child": "b", "definition": {"prompt": "GitHub issue #231 work"}}' > "$T/co/runs/run-23/record.jsonl"
SG_STATE=$T/st python3 "$T/s/issue_ledger.py" requeue "x/y#231" run-23 0123456789abcdef0123456789abcdef01234567 >/dev/null 2>&1
printf '%s\n' "$(( $(date +%s) - 1801 ))" > "$T/st/auto/wave-entry-run-23"
: > "$T/gh.log"
r=$(land2 run-23); rc=$?
[ $rc = 0 ] && /usr/bin/grep -q 'issues/230 -f state=closed' "$T/gh.log" && ok "EW6: the wave lands the child that entered" || ko "EW6: exit $rc: '$r'"
case "$r" in *"left out: none"*) ok "EW6: the close line leaves out no requeued child";; *) ko "EW6: the close line names a requeued child: '$r'";; esac
[ ! -e "$T/st/auto/wave-recut-run-23" ] && ok "EW6: no recut marker stays for a requeued child" || ko "EW6: the recut marker stays for a requeued child"
[ ! -e "$T/st/auto/wave-run-23" ] && ok "EW6: the run's wave marker goes" || ko "EW6: the run's wave marker stays, so the requeued issue stays busy"

# recut <run> <entered> <left out ...>: EW2's setup. The run's wave closes early with <entered> in it, and each other issue is a
# dispatched child of the run that never entered (child id k<issue>). It prints the landing and exits with its code.
recut() { local r=$1 e=$2 n rec; shift 2
  start2 "$r" >/dev/null; child "$e" "g$e" "origin/wave/$r"; en2 "$r" "$e" >/dev/null
  mkdir -p "$T/co/runs/$r"
  rec='{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #'$e' work"}}'
  for n in "$@"; do printf '%s\n' "$r" > "$T/st/auto/disp-$n"
    rec="$rec"$'\n''{"event": "spawn_accepted", "child": "k'$n'", "definition": {"prompt": "GitHub issue #'$n' work"}}'; done
  printf '%s\n' "$rec" > "$T/co/runs/$r/record.jsonl"
  printf '%s\n' "$(( $(date +%s) - 1801 ))" > "$T/st/auto/wave-entry-$r"
  land2 "$r"; }
gone() { local r=$1 m left=""; for m in wave wave-recut wave-start; do [ -e "$T/st/auto/$m-$r" ] && left="$left $m-$r"; done; printf '%s' "$left"; }

# EW7 (#2559, AC1): a left-out child that never enters holds its issue for at most the wave age cap. The early close starts the
# clock of the run's next cut, and the recut marker names the child left out. The run still holds its run slot, so only the
# age cap ends the recut: the landing requeues the child and clears every marker of the run, with no landing failure.
printf '%s\n' "$T/co/runs/run-24" >> "$T/st/active-runs.txt"
r=$(recut run-24 240 241); rc=$?
[ $rc = 0 ] && case "$r" in *"WAVE CLOSE wave/run-24"*"#241"*) true;; *) false;; esac && ok "EW7: the wave closes early and leaves #241 out" || ko "EW7: exit $rc: '$r'"
[ -s "$T/st/auto/wave-start-run-24" ] && ok "EW7: the early close starts the age clock of the run's next cut" || ko "EW7: the early close left no wave-start marker, so the age cap never fires"
[ "$(sed -n 2p "$T/st/auto/wave-recut-run-24" 2>/dev/null)" = 241 ] && ok "EW7: the recut marker names the child left out" || ko "EW7: the recut marker does not name #241: '$(cat "$T/st/auto/wave-recut-run-24" 2>/dev/null)'"
printf '%s\n' "$(( $(date +%s) - 7201 ))" > "$T/st/auto/wave-start-run-24"
bash "$T/s/wave.sh" expired run-24 >/dev/null 2>&1 && ok "EW7: the recut run is expired past the age cap" || ko "EW7: the recut run is not expired past the age cap"
r=$(land2 run-24 2>&1); rc=$?
[ $rc = 0 ] && ok "EW7: the landing of the expired recut exits 0" || ko "EW7: exit $rc: '$r'"
case "$r" in *"WAVE REQUEUE #241"*) ok "EW7: the age cap sends the child left out back to the queue";; *) ko "EW7: #241 is not requeued: '$r'";; esac
case "$r" in *NEEDS-HUMAN*) ko "EW7: the expired recut went to the owner: '$r'";; *) ok "EW7: the expired recut needs no owner";; esac
[ -z "$(gone run-24)" ] && ok "EW7: no wave, recut or start marker stays" || ko "EW7: markers stay:$(gone run-24)"

# EW8 (#2559, AC2): a requeue of the child left out, written after the early close, drops the recut when no child is left out.
# The requeue reads no ledger fact: runreview.sh writes that fact after the requeue.
r=$(recut run-25 250 251); rc=$?
[ $rc = 0 ] && [ -e "$T/st/auto/wave-recut-run-25" ] || ko "EW8: the setup did not close early: exit $rc: '$r'"
r=$(cd "$T/co" && SG_REQUEUE_RUN=run-25 bash "$T/s/wave.sh" requeue 251 "the review asked for changes" 2>&1)
case "$r" in *"WAVE REQUEUE #251"*) ok "EW8: the child left out is requeued";; *) ko "EW8: no requeue: '$r'";; esac
[ -z "$(gone run-25)" ] && ok "EW8: the last requeue drops the recut, wave and start markers" || ko "EW8: markers stay:$(gone run-25)"
r=$(recut run-26 260 261 262); rc=$?
[ $rc = 0 ] && [ -e "$T/st/auto/wave-recut-run-26" ] || ko "EW8: the setup with two left out did not close early: exit $rc: '$r'"
(cd "$T/co" && SG_REQUEUE_RUN=run-26 bash "$T/s/wave.sh" requeue 261 "the review asked for changes" >/dev/null 2>&1)
[ -e "$T/st/auto/wave-recut-run-26" ] && [ -e "$T/st/auto/wave-run-26" ] && ok "EW8: a requeue that leaves one child out keeps both markers" || ko "EW8: a marker went while #262 is still left out:$(gone run-26)"
[ "$(sed -n 2p "$T/st/auto/wave-recut-run-26" 2>/dev/null)" = 262 ] && ok "EW8: the recut marker names only the child still left out" || ko "EW8: the recut marker reads '$(cat "$T/st/auto/wave-recut-run-26" 2>/dev/null)'"

# EW9 (#2559, E3): a child left out that commits again after the close keeps its tip. Its requeue pushes the head to
# fix/<n>-salvage-<run> and names that branch.
WT=$T/co/runs/run-27/ws/target-k271
mkdir -p "$T/co/runs/run-27/ws"; $G clone -q "$T/origin.git" "$WT" 2>/dev/null
(cd "$WT" && $G checkout -q -b gui/271 origin/dev && echo a > g271 && $G add g271 && $G commit -q -m "work 271 (Refs #271)") || ko "EW9: the child's worktree is not ready"
r=$(recut run-27 270 271); rc=$?
[ $rc = 0 ] && [ -e "$T/st/auto/wave-recut-run-27" ] || ko "EW9: the setup did not close early: exit $rc: '$r'"
(cd "$WT" && echo b > g271b && $G add g271b && $G commit -q -m "more work 271 (Refs #271)") || ko "EW9: the commit after the close failed"
tip=$($G -C "$WT" rev-parse HEAD)
r=$(cd "$T/co" && SG_REQUEUE_RUN=run-27 bash "$T/s/wave.sh" requeue 271 "the review asked for changes" 2>&1)
case "$r" in *"WAVE REQUEUE #271"*"fix/271-salvage-run-27"*) ok "EW9: the requeue names the salvage branch";; *) ko "EW9: the requeue names no salvage branch: '$r'";; esac
[ "$($O rev-parse -q --verify refs/heads/fix/271-salvage-run-27 2>/dev/null)" = "$tip" ] && ok "EW9: the salvage branch holds the tip committed after the close" || ko "EW9: the remote salvage branch is not the child's tip"

# EW10 (#2559): a recut run that has its verdict holds no run slot, so its child left out can never enter. The landing ends
# the recut and never reads the wave branch the early close deleted.
r=$(recut run-28 280 281); rc=$?
[ $rc = 0 ] && [ -e "$T/st/auto/wave-recut-run-28" ] || ko "EW10: the setup did not close early: exit $rc: '$r'"
printf '%s\n' '{"event": "verdict", "verdict": "completed"}' >> "$T/co/runs/run-28/record.jsonl"
printf '%s\n' "$T/co/runs/run-28" >> "$T/st/active-runs.txt"
r=$(land2 run-28 2>&1); rc=$?
case "$r" in *"is not on the remote"*|*"is not on $T"*) ko "EW10: the landing reads the deleted wave branch: '$r'";; *) ok "EW10: the landing does not read the deleted wave branch";; esac
[ $rc = 0 ] && [ -z "$(gone run-28)" ] && ok "EW10: the landing ends the recut (exit 0, no marker)" || ko "EW10: exit $rc, markers:$(gone run-28): '$r'"

rm -rf "$T"; [ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
