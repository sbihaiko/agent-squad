#!/bin/bash
# The wave's entry (#374, #376, #392): the run cuts its wave branch, a green child enters it by a fast-forward,
# and a child held out says why: failed gates, a conflict, a review that did not approve it, or a held gate lock.
# mirrors: wave.sh wave-fixture.sh
. "$(dirname "$0")/wave-fixture.sh"

# A: the run cuts one wave branch, named for the run, from dev.
r=$(start)
[ "$($O rev-parse -q --verify $W)" = "$($O rev-parse dev)" ] && ok "A: the run cuts its wave branch from dev" || ko "A: no wave branch: '$r'"
case "$r" in "WAVE $W "*) ok "A: start names the wave branch and its sha";; *) ko "A: start said nothing: '$r'";; esac
# The run's completion finds its wave through this marker, and `land` clears it (#375): a run lands its wave once, and a
# run that cut none is never sent to `land`.
[ "$(cat "$T/st/auto/wave-run-1" 2>/dev/null)" = "$W" ] && ok "A: start records the run's wave for its completion" || ko "A: no wave marker: '$(cat "$T/st/auto/wave-run-1" 2>/dev/null)'"
[ -e "$T/st/auto/waved-run-1" ] && ok "A: start also leaves the durable waved-<run> record" || ko "A: no waved-run-1 record"
r2=$(start)
[ "$($O rev-parse $W)" = "$($O rev-parse dev)" ] && ok "A: a wave branch that already exists is left where it is" || ko "A: the second start moved the wave: '$r2'"

# B: two green children enter, and the wave carries both with no merge commit.
child 7 g7; r=$(en 7)
case "$r" in "WAVE gui/7 "*) ok "B: a green child enters the wave";; *) ko "B: #7 did not enter: '$r'";; esac
child 8 g8; r=$(en 8)
[ "$($O rev-parse $W)" = "$($G -C "$T/c8" rev-parse HEAD)" ] && ok "B: the wave tip is the last entrant" || ko "B: the tip is not the entrant's head: '$r'"
[ "$($O log --format=%s $W | tr '\n' ' ')" = "work 8 (Refs #8) work 7 (Refs #7) init " ] && ok "B: the wave branch carries both children, in entry order" || ko "B: the wave log is '$($O log --format=%s $W | tr '\n' ' ')'"
$O rev-list --merges $W | /usr/bin/grep -q . && ko "B: a merge commit entered the wave" || ok "B: the wave history has no merge commit"
$O merge-base --is-ancestor dev $W && ok "B: the wave branch stays a fast-forward of dev" || ko "B: the wave is not a fast-forward of dev"
/usr/bin/grep -qx 'o 3 Landing 7' "$T/board.log" && /usr/bin/grep -qx 'o 3 Landing 8' "$T/board.log" && ok "B: the card of a child that entered reads Landing" || ko "B: no Landing card: $(cat "$T/board.log" 2>/dev/null)"
# The entry leaves a durable record per issue (#467): the janitor reads Code Complete from it, never from the run's own land.
[ "$(SG_STATE=$T/st python3 "$T/s/issue_ledger.py" entered x/y | sort | tr '\t\n' ': ')" = "run-1:7 run-1:8 " ] && ok "B: each entry is an entered fact in its issue's ledger" || ko "B: entered facts: '$(SG_STATE=$T/st python3 "$T/s/issue_ledger.py" entered x/y)'"
[ -z "$(/bin/ls "$T/st/auto" | /usr/bin/grep '^entered-')" ] && ok "B: an entry writes no entered- marker (ADR-0149)" || ko "B: marker: $(/bin/ls "$T/st/auto")"
case "$(wfact 7 run-1)" in "entered "*) ok "B: the entry is a fact in the issue's ledger";; *) ko "B: ledger fact of #7: '$(wfact 7 run-1)'";; esac
IFS=$'\t' read -r _ kind res at < "$T/st/gate-status" 2>/dev/null
[ "$kind $res $at" = "fast pass $($G -C "$T/c8" rev-parse HEAD)" ] && ok "B: the entry gate leaves one fast status line: its result and the sha it ran at" || ko "B: status line: '$(cat "$T/st/gate-status" 2>&1)'"

# C: a child whose gates fail never enters, and its issue is back in the queue.
printf 'run-1\n' > "$T/st/auto/disp-9"; child 9 g9; before=$($O rev-parse $W); r=$(GATES=false en 9)
[ "$($O rev-parse $W)" = "$before" ] && [ "$(entered 9)" = no ] && ok "C: a child whose gates fail never enters" || ko "C: #9 entered anyway: '$r'"
case "$r" in *"WAVE REQUEUE #9"*gates*) ok "C: the failed gates are said";; *) ko "C: the failure is silent: '$r'";; esac
[ ! -e "$T/st/auto/disp-9" ] && ok "C: its dispatch marker is dropped, so the frontier builds it again" || ko "C: the marker is kept"
/usr/bin/grep -qx 'o 3 Todo 9' "$T/board.log" && ok "C: its card is back in the queue" || ko "C: no Todo card for #9"
SG_STATE=$T/st python3 "$T/s/issue_ledger.py" entered x/y | /usr/bin/grep -q $'\t9$' && ko "C: #9 has an entered fact" || ok "C: a child that never entered leaves no entered fact"
case "$(wfact 9 run-1)" in "refused the local gates failed"*) ok "C: the refusal and its cause are a fact in the issue's ledger";; *) ko "C: ledger fact of #9: '$(wfact 9 run-1)'";; esac

# The gates run in the child's own worktree, before the entry: a gate that only passes on the child's file proves it.
child 10 g10; r=$(GATES='test -f g10' en 10)
[ "$(entered 10)" = yes ] && ok "C: the gates run in the child's own worktree" || ko "C: #10 did not enter with a gate over its own tree: '$r'"
# The gates read the child rebased on the wave tip, never the stale base it was cut from: a child cut from an old dev failed
# a gate the wave tip already passes, and every child went back to the queue (#514).
child 14 g14 origin/dev; r=$(GATES='test -f g7 && test -f g14' en 14)
[ "$(entered 14)" = yes ] && ok "C: the gates run on the child rebased on the wave tip" || ko "C: #14, cut from an old base, was gated on it: '$r'"
# A rebase that stops with no unmerged file (a tracked change in the child's worktree) names git's own last line, never an empty tail (#1062).
child 15 g15 origin/dev; echo dirty >> "$T/c15/g15"; r=$(en 15)
case "$r" in *"WAVE REQUEUE #15: the rebase on $W conflicts: git said: "?*) ok "C: a rebase with no unmerged file names git's own last line";; *) ko "C: the refusal ends at the colon: '$r'";; esac
# No gate command set: the entry is refused whole, so no child ever enters a wave ungated.
before=$($O rev-parse $W); printf 'run-1\n' > "$T/st/auto/disp-13"; child 13 g13; r=$(GATES= en 13)
[ "$($O rev-parse $W)" = "$before" ] && [ "$(entered 13)" = no ] && ok "C: no gates set: a child never enters a wave ungated" || ko "C: #13 entered ungated: '$r'"
case "$r" in *"WAVE-ERROR"*gates*) ok "C: the missing gates are said";; *) ko "C: the missing gate is silent: '$r'";; esac
[ -e "$T/st/auto/disp-13" ] && ok "C: a bad call touches nothing, so the marker stays" || ko "C: the marker was dropped over a bad call"

# D: a child that conflicts with the wave tip never enters, and says why.
tip0=$($O rev-parse $W)                       # the tip before #11 entered
child 11 g11; en 11 >/dev/null                # #11 adds g11 and enters
child 12 g11 "$tip0"                          # #12 adds the same file from the older tip: the rebase conflicts
printf 'run-1\n' > "$T/st/auto/disp-12"; before=$($O rev-parse $W); mine=$($G -C "$T/c12" rev-parse HEAD); r=$(en 12)
[ "$($O rev-parse $W)" = "$before" ] && [ "$(entered 12)" = no ] && ok "D: a child that conflicts with the wave tip never enters" || ko "D: #12 entered: '$r'"
case "$r" in *"WAVE HOLD #12"*conflict*g11*) ok "D: the conflict is said, and names the file";; *) ko "D: no reason for the conflict: '$r'";; esac
# A conflict keeps the child's own commits (#1344): pushed to fix/12-rebase-run-1, the child's HEAD never reset, one fix round
# queued on that branch by a CHANGES verdict, and no requeue (no fresh build, the card stays Doing).
[ "$($O rev-parse -q --verify refs/heads/fix/12-rebase-run-1)" = "$mine" ] && ok "D: the conflicted commits are kept on fix/12-rebase-run-1 on the remote" || ko "D: the commits are not kept on fix/12-rebase-run-1"
[ "$($G -C "$T/c12" rev-parse HEAD)" = "$mine" ] && ok "D: the child's HEAD is not reset to the base" || ko "D: the child's HEAD was reset"
head -1 "$T/st/review-fix_12-rebase-run-1.md" 2>/dev/null | /usr/bin/grep -q CHANGES && ok "D: a CHANGES verdict asks one rebase round on fix/12-rebase-run-1" || ko "D: no CHANGES verdict for fix/12-rebase-run-1"
[ ! -e "$($G -C "$T/c12" rev-parse --git-path rebase-merge)" ] && ok "D: the failed rebase is aborted, so the worktree carries no half-rebase" || ko "D: the child is left mid-rebase"
[ -e "$T/st/auto/disp-12" ] && ! /usr/bin/grep -q 'Todo 12' "$T/board.log" && ok "D: a conflicting child is not requeued to Todo (no fresh build)" || ko "D: #12 was requeued"

# The round rebases on the wave tip the child conflicted with, never on dev (#1344): the review names that ref, and the kept
# branch carries a marker that names it too, so the work-branch passes read the kept state from the marker, not the branch name.
/usr/bin/grep -q "Rebase this branch on origin/$W," "$T/st/review-fix_12-rebase-run-1.md" 2>/dev/null && ok "D: the rebase round is ordered on origin/$W, the tip it conflicted with" || ko "D: the round is not ordered on origin/$W"
[ "$(cat "$T/st/auto/kept-fix_12-rebase-run-1" 2>/dev/null)" = "origin/$W" ] && ok "D: the kept branch is marked with the ref its round rebases on" || ko "D: no kept marker for fix/12-rebase-run-1"

# D2 (#1344, the retry path): the wave tip moves between the first push and its repeat, and the repeat's rebase conflicts. The
# first rebase rewrites the child's head, so the head the repeat keeps is not the one the child had at the entry. The review
# must name the pushed head, the remote tip the fix pass compares with.
w20saved=$($O rev-parse $W); child 20 g20 "$($O rev-parse $W)"
rm -rf "$T/sib20"; $G clone -q "$T/origin.git" "$T/sib20" 2>/dev/null
(cd "$T/sib20" && $G checkout -q -b s20 "origin/$W" && echo s > h20 && $G add h20 && $G -c user.email=a@b -c user.name=t commit -q -m "a sibling (Refs #21)" && $G push -q origin HEAD:refs/heads/$W)
(cd "$T/sib20" && echo other > g20 && $G add g20 && $G -c user.email=a@b -c user.name=t commit -q -m "a conflicting sibling (Refs #22)" && $G push -q origin HEAD:refs/heads/sib20)
SIB20=$($G -C "$T/sib20" rev-parse HEAD)
# The D2 hook replaces the suite's own pre-receive: it is saved here and put back at the end of the block, so a later
# scene never pushes through a hook that was written for this one.
cp "$T/origin.git/hooks/pre-receive" "$T/pre-receive.d2saved" 2>/dev/null || : > "$T/pre-receive.d2saved"
cat > "$T/origin.git/hooks/pre-receive" <<H
#!/bin/bash
reject=
while read -r old new ref; do printf '%s\t%s\t%s\n' "\$old" "\$new" "\$ref" >> "$T/pushes.log"; [ "\$ref" = refs/heads/$W ] && [ -e "$T/hook20" ] && reject=1; done
[ -n "\$reject" ] || exit 0
rm -f "$T/hook20"; mkdir -p "$T/origin.git/refs/heads/wave"; printf '%s\n' "$SIB20" > "$T/origin.git/refs/heads/$W"; exit 1
H
chmod +x "$T/origin.git/hooks/pre-receive"
: > "$T/hook20"; mine0=$($G -C "$T/c20" rev-parse HEAD); printf 'run-1\n' > "$T/st/auto/disp-20"
r=$(en 20); rm -f "$T/hook20"
kb=$($O rev-parse -q --verify refs/heads/fix/20-rebase-run-1); head20=$($G -C "$T/c20" rev-parse HEAD)
[ -n "$kb" ] && [ "$kb" = "$head20" ] && [ "$kb" != "$mine0" ] && ok "D2: the repeat keeps the head it pushed, the one the first rebase wrote" || ko "D2: remote $kb, child $head20, first $mine0: '$r'"
[ "$(cat "$T/st/review-fix_20-rebase-run-1.sha" 2>/dev/null)" = "$kb" ] && ok "D2: the review names the pushed remote tip, so the fix pass reads it" || ko "D2: review sha '$(cat "$T/st/review-fix_20-rebase-run-1.sha" 2>/dev/null)', remote tip $kb"
case "$r" in *"WAVE HOLD #20"*"kept on fix/20-rebase-run-1"*) ok "D2: the repeat holds the child on the kept branch";; *) ko "D2: no kept hold line: '$r'";; esac
[ "$(cat "$T/st/auto/kept-fix_20-rebase-run-1" 2>/dev/null)" = "origin/$W" ] && ok "D2: the retry path keeps the marker" || ko "D2: no marker on the retry path"
# DEBT (#1344): a repeat on a kept branch that already holds a later commit of its round is the kept state, not a failed push:
# no push is made, the branch keeps the round's commit, and the hold does not say the push failed.
rm -rf "$T/rnd20"; $G clone -q "$T/origin.git" "$T/rnd20" 2>/dev/null
(cd "$T/rnd20" && $G checkout -q fix/20-rebase-run-1 && echo round > r20 && $G add r20 && $G -c user.email=a@b -c user.name=t commit -q -m "the round (Refs #20)" && $G push -q origin HEAD:refs/heads/fix/20-rebase-run-1)
R20=$($O rev-parse refs/heads/fix/20-rebase-run-1)
r=$(en 20)
[ "$($O rev-parse refs/heads/fix/20-rebase-run-1)" = "$R20" ] && ok "a repeat on a branch that holds its round's commit leaves the branch as it is" || ko "the round's commit was overwritten: '$r'"
case "$r" in *"the push failed"*) ko "a kept branch reads as a failed push: '$r'";; *"kept on fix/20-rebase-run-1"*) ok "a repeat on a kept branch reads as kept";; *) ko "no kept line on the repeat: '$r'";; esac
# A repeat while the round's branch has moved past the first keep reads the kept state and rewrites nothing: the round owns the
# branch and its review (#1344).
(cd "$T/rnd20" && echo round2 > r20b && $G add r20b && $G -c user.email=a@b -c user.name=t commit -q -m "the round, a commit later (Refs #20)" && $G push -q origin HEAD:refs/heads/fix/20-rebase-run-1)
rev 20; rs20=$(cat "$T/st/review-fix_20-rebase-run-1.sha" 2>/dev/null)
r=$(cd "$T/c20" && SG_WAVE_GATES=${GATES-true} bash "$T/s/wave.sh" enter run-1 20)
[ -n "$rs20" ] && [ "$(cat "$T/st/review-fix_20-rebase-run-1.sha" 2>/dev/null)" = "$rs20" ] && ok "a repeat while the round is open never rewrites the round's review" || ko "the repeat rewrote the review: '$r'"
case "$r" in *"not requeued"*) ok "a repeat while the round is open holds on the kept wording";; *) ko "the repeat's hold: '$r'";; esac
cp "$T/pre-receive.d2saved" "$T/origin.git/hooks/pre-receive"; chmod +x "$T/origin.git/hooks/pre-receive"
# The scene moved the wave branch and wrote its own state: both are put back so no later scene reads a wave tip it did not build.
$O update-ref "refs/heads/$W" "$w20saved"
rm -f "$T/st/auto/kept-"* "$T/st/auto/disp-20" "$T/st/review-fix_20-rebase-run-1."* "$T/st/review-fix_20-rebase-run-1".md \
      "$T/st/auto/disp-12" "$T/st/review-fix_12-rebase-run-1."* "$T/st/review-fix_12-rebase-run-1".md
# P (#1348): a push that fails for a reason git does not name as a rejection holds the child: no requeue, no marker move, and
# the hold names git's last stderr line. A push git rejects as non-fast-forward still refuses, with git's line in the reason.
# The child's push URL is a path that is no repository: the push fails in transport, and git names no rejection.
printf 'run-1\n' > "$T/st/auto/disp-1348"; child 1348 g1348; before=$($O rev-parse $W)
$G -C "$T/c1348" config remote.origin.pushurl "$T/nowhere.git"
r=$(en 1348); $G -C "$T/c1348" config --unset remote.origin.pushurl
[ "$($O rev-parse $W)" = "$before" ] && [ "$(entered 1348)" = no ] && ok "P: a push that fails on the network never enters the wave" || ko "P: #1348 entered or moved the wave: '$r'"
case "$r" in *"WAVE HOLD #1348: the push to $W failed (git: "?*")"*) ok "P: a failed push holds the child and names git's last line";; *) ko "P: no hold for a failed push: '$r'";; esac
case "$r" in *"WAVE REQUEUE #1348"*) ko "P: a failed push requeued the child: '$r'";; *) ok "P: a failed push requeues nothing";; esac
[ -e "$T/st/auto/disp-1348" ] && ok "P: a held child keeps its dispatch marker" || ko "P: the marker of a held child was dropped"
case "$(wfact 1348 run-1)" in "held the push to $W failed"*) ok "P: the hold and its cause are a fact in the issue's ledger";; *) ko "P: ledger fact of #1348: '$(wfact 1348 run-1)'";; esac

# A non-fast-forward rejection: the remote's wave tip moves on every gate run, so each push is behind it. Git rejects both
# pushes (fetch first), the entry refuses, and the reason quotes git's own last stderr line.
cat > "$T/advance.sh" <<'EOF'
#!/bin/bash
# Moves the wave branch on the remote by one commit with a new file, so the child's push is behind its tip.
export GIT_INDEX_FILE="$ADV_ROOT/adv.idx"; rm -f "$GIT_INDEX_FILE"
b=$(echo "$RANDOM$RANDOM$$" | /usr/bin/git -C "$ADV_ROOT/origin.git" hash-object -w --stdin) || exit 1
/usr/bin/git -C "$ADV_ROOT/origin.git" read-tree "$ADV_REF" || exit 1
/usr/bin/git -C "$ADV_ROOT/origin.git" update-index --add --cacheinfo "100644,$b,adv-$b.txt" || exit 1
t=$(/usr/bin/git -C "$ADV_ROOT/origin.git" write-tree) || exit 1
c=$(/usr/bin/git -C "$ADV_ROOT/origin.git" commit-tree "$t" -p "$(/usr/bin/git -C "$ADV_ROOT/origin.git" rev-parse "$ADV_REF")" -m adv) || exit 1
/usr/bin/git -C "$ADV_ROOT/origin.git" update-ref "$ADV_REF" "$c"
EOF
printf 'run-1\n' > "$T/st/auto/disp-1349"; child 1349 g31; before=$($O rev-parse $W)
r=$(GATES="ADV_ROOT=$T ADV_REF=refs/heads/$W bash $T/advance.sh" en 1349)
[ "$($O rev-parse $W)" != "$before" ] && [ "$(entered 1349)" = no ] && ok "P: a non-fast-forward push does not enter the child" || ko "P: #1349 entered: '$r'"
case "$r" in *"WAVE REQUEUE #1349: the push to $W was refused: HEAD is not a fast-forward of its tip (git: "?*")"*) ok "P: a rejected push refuses, and its reason quotes git's last line";; *) ko "P: no git line in the refusal: '$r'";; esac

# E: a child whose review asked for changes never enters the wave branch (#376). It is held where it stands: nothing is
# requeued, so the fix round can work on the same branch and the review can be read again.
before=$($O rev-parse $W); printf 'run-1\n' > "$T/st/auto/disp-20"; child 20 g20; r=$(REV=CHANGES en 20)
[ "$($O rev-parse $W)" = "$before" ] && [ "$(entered 20)" = no ] && ok "E: a child whose review asked for changes never enters" || ko "E: #20 entered: '$r'"
case "$r" in *"#20"*CHANGES*) ok "E: the changes verdict is said, and names the child";; *) ko "E: no reason for the hold: '$r'";; esac
case "$(wfact 20 run-1)" in "held the review of gui/20 did not approve it"*) ok "E: the hold and its cause are a fact in the issue's ledger";; *) ko "E: ledger fact of #20: '$(wfact 20 run-1)'";; esac
[ -e "$T/st/auto/disp-20" ] && ok "E: a held child keeps its dispatch marker for the fix round" || ko "E: the marker was dropped"
/usr/bin/grep -q " 20\$" "$T/board.log" 2>/dev/null && ko "E: a held child moved a card" || ok "E: a held child moves no card"

# F: a review read against another base (dev, the whole wave) does not admit the child: the review must read only its commits.
before=$($O rev-parse $W); printf 'run-1\n' > "$T/st/auto/disp-21"; child 21 g21; r=$(REV_BASE=dev en 21)
[ "$($O rev-parse $W)" = "$before" ] && [ "$(entered 21)" = no ] && ok "F: a review of the whole wave (base dev) does not admit the child" || ko "F: #21 entered on a dev review: '$r'"
case "$r" in *dev*"$W"*) ok "F: the reason names both the base it was read against and the wave branch";; *) ko "F: no wave base in the reason: '$r'";; esac

# G: a review of an older tip does not admit the child.
before=$($O rev-parse $W); printf 'run-1\n' > "$T/st/auto/disp-22"; child 22 g22; r=$(REV_SHA=0000000000000000000000000000000000000000 en 22)
[ "$($O rev-parse $W)" = "$before" ] && [ "$(entered 22)" = no ] && ok "G: a review of an older tip does not admit the child" || ko "G: #22 entered on a stale review: '$r'"

# H: no review at all: a child never enters a wave unreviewed.
before=$($O rev-parse $W); printf 'run-1\n' > "$T/st/auto/disp-23"; child 23 g23; r=$(REV=none en 23)
[ "$($O rev-parse $W)" = "$before" ] && [ "$(entered 23)" = no ] && ok "H: a child never enters a wave unreviewed" || ko "H: #23 entered with no review: '$r'"
case "$r" in *"#23"*review*) ok "H: the missing review is said";; *) ko "H: the missing review is silent: '$r'";; esac

# (#392) A holder of the machine-wide `gate` lock; the runner's gate then waits for it, and runs after it, never beside it. The hold
# outlasts the runner's own startup, so the wait is proved on a busy machine. `hold` returns only once the lock the holder took is
# the holder's own -- the pid the lock records is the holder's own pid -- and gives up as a failure if that lock never comes to it.
# The wait is generous (tens of seconds), never the old five: a busy machine delays the holder's own startup, and a runner started
# before the holder is inside the lock passes first and proves nothing.
hold() { SG_STATE=$T/st bash "$T/s/serial.sh" gate sh -c "sleep 10; echo holder >> $T/gl" & holder=$!
  for _ in $(seq 1 600); do
    [ "$(cat "$T/st/locks/gate/pid" 2>/dev/null)" = "$holder" ] && return 0
    kill -0 "$holder" 2>/dev/null || return 1   # the holder is gone: it never came to hold the lock
    sleep 0.1; done
  return 1; }
# I (#392, #575): the entry gate takes the machine-wide gate lock and never runs beside its holder. It does not wait for it
# either (try mode): the entry runs inline in the fast lane, so it holds the child out, and the next pass, after the holder, enters it.
printf 'run-1\n' > "$T/st/auto/disp-24"; child 24 g24; rev 24; rm -f "$T/gl"
# The runner is started by a path relative to another cwd, as the loop does (`bash scripts/wave.sh` from the repository root), and
# enter works in the child's worktree: the gate.sh beside wave.sh is found by wave.sh's own folder, never by a path after the cd.
if hold; then
  r=$(cd "$T/c24" && SG_WAVE_GATES="echo gate >> $T/gl" bash "../s/wave.sh" enter run-1 24 2>&1)
  case "$r" in *"WAVE HOLD #24"*) ok "I: under a held gate lock the entry holds the child out, on one line";; *) ko "I: no hold: '$r'";; esac
  # The hold line begins with its UTC stamp; on a machine not on UTC, a local stamp is hours off and fails.
  stamp=$(printf '%s\n' "$r" | sed -n 's/^\([0-9-]* [0-9:]*\) WAVE HOLD #24: .*/\1/p' | head -n 1)
  [ -n "$stamp" ] && python3 -c 'import calendar,sys,time; s=calendar.timegm(time.strptime(sys.argv[1],"%Y-%m-%d %H:%M:%S")); sys.exit(0 if abs(s-time.time())<120 else 1)' "$stamp" 2>/dev/null \
    && ok "I2: the WAVE HOLD line begins with its UTC stamp" || ko "I2: no UTC stamp on the WAVE HOLD line: '$r'"
  /usr/bin/grep -q gate "$T/gl" 2>/dev/null && ko "I: the entry gate ran beside the holder" || ok "I: the entry gate never runs beside the holder"
  wait; rev 24; r=$(cd "$T/c24" && SG_WAVE_GATES="echo gate >> $T/gl" bash "../s/wave.sh" enter run-1 24 2>&1)
  [ "$(tr '\n' ' ' < "$T/gl")" = "holder gate " ] && ok "I: the next pass, after the holder, runs the entry gate" || ko "I: gate order '$(tr '\n' ' ' < "$T/gl")': '$r'"
  [ "$(entered 24)" = yes ] && ok "I: the waiting gate is not dropped: the child enters" || ko "I: #24 did not enter"
else
  wait 2>/dev/null   # drain the holder, if it is still there, so no stray writer touches $T/gl later
  ko "I: the holder never came to hold the gate lock, so the entry wait could not be proved"
fi

# R2272 (#2272): the wave was cut from a base that carried a red, and the base moved with the fix before the child's gate
# ran. The child's gate fails on the stale wave tip; the entry rebases the child on the current base once, runs the gate
# again on the rebased tree, and a pass enters it. The record names the failed test and the base tip's own answer for it.
# enw <run> <issue>: the entry of a child into its own wave, with the gate command the case names (GATES).
enw() { local r=$1 n=$2; REV_BASE=wave/$r rev "$n"; (cd "$T/c$n" && SG_WAVE_GATES=${GATES-true} bash "$T/s/wave.sh" enter "$r" "$n"); }
start2 run-30 >/dev/null
devmoved
printf 'run-30\n' > "$T/st/auto/disp-30"; child 30 g30 "origin/wave/run-30"
G30='if test -f other; then exit 0; fi; printf "fail\tpytest the base battery failed at tests/test_hub_mobile_move.py::test_the_mobile_page_holds_no_column_list\truff mypy pytest\t-\n" > "$GATE_OUTCOME_FILE"; exit 1'
r=$(SG_WAVE_BASE_PYTEST='exit 0' GATES="$G30" enw run-30 30)
[ "$(entered 30)" = yes ] && ok "R2272: a pass on the rebased tip enters the child" || ko "R2272: #30 did not enter: '$r'"
$O cat-file -e "wave/run-30:other" 2>/dev/null && ok "R2272: the rebased child carries the base's fix" || ko "R2272: the wave lacks the base's commit: '$r'"
entry30=$(wfact 30 run-30)
case "$entry30" in "entered "*test_the_mobile_page_holds_no_column_list*) ok "R2272: the entry names the test the first gate failed";; *) ko "R2272: ledger fact of #30: '$entry30'";; esac
case "$entry30" in *"the base tip passes"*) ok "R2272: the entry says the base tip passes the named test";; *) ko "R2272: no base answer: '$entry30'";; esac

# R2272b: the base moved but still fails the named test: the rebased tree is red again, the child goes back to the queue,
# and the record names the test and says the base tip fails it too.
start2 run-31 >/dev/null
printf 'run-31\n' > "$T/st/auto/disp-31"; child 31 g31 "origin/wave/run-31"
G31='printf "fail\tpytest the base battery failed at tests/test_hub_mobile_move.py::test_the_mobile_page_holds_no_column_list\truff mypy pytest\t-\n" > "$GATE_OUTCOME_FILE"; exit 1'
r=$(SG_WAVE_BASE_PYTEST='echo "FAILED tests/test_hub_mobile_move.py::test_the_mobile_page_holds_no_column_list - assert 0"; exit 1' GATES="$G31" enw run-31 31)
[ "$(entered 31)" = no ] && ok "R2272b: a red on the rebased tip keeps the child out" || ko "R2272b: #31 entered: '$r'"
case "$r" in *"WAVE REQUEUE #31"*test_the_mobile_page_holds_no_column_list*) ok "R2272b: the requeue names the failed test";; *) ko "R2272b: no test in the requeue: '$r'";; esac
case "$(wfact 31 run-31)" in *test_the_mobile_page_holds_no_column_list*"the base tip fails it"*) ok "R2272b: the record says the base tip fails the named test";; *) ko "R2272b: ledger fact of #31: '$(wfact 31 run-31)'";; esac
/usr/bin/grep -qx 'o 3 Todo 31' "$T/board.log" && ok "R2272b: the child goes back to the queue" || ko "R2272b: no Todo card for #31"

# R2272c: the child's own red, the base tip passes it: the record says so, and the requeue stands as before.
start2 run-32 >/dev/null
printf 'run-32\n' > "$T/st/auto/disp-32"; child 32 g32 "origin/wave/run-32"
G32='printf "fail\tpytest the base battery failed at tests/test_child_own.py::test_own\truff mypy pytest\t-\n" > "$GATE_OUTCOME_FILE"; exit 1'
r=$(SG_WAVE_BASE_PYTEST='exit 0' GATES="$G32" enw run-32 32)
[ "$(entered 32)" = no ] && ok "R2272c: a child's own red keeps it out" || ko "R2272c: #32 entered: '$r'"
case "$(wfact 32 run-32)" in *test_child_own.py::test_own*"the base tip passes"*) ok "R2272c: the record says the base tip passes the child's red";; *) ko "R2272c: ledger fact of #32: '$(wfact 32 run-32)'";; esac

rm -rf "$T"; [ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
