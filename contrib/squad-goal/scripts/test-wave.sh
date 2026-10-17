#!/bin/bash
# Regression test for wave.sh: the run's wave branch and the child that enters it by a fast-forward (#374), and the wave's
# landing pass: the rebase on the moved base, the one push, the push that repeats from the fetch, and the closes (#375).
# A child enters only after its review approved its own diff against the wave branch (#376): a review that asked for changes,
# one read against another base or an older tip, and no review at all each hold the child out, with nothing requeued.
# A bare local remote, a stub gh, a stub board, a temp state dir: every case runs on this machine, with no GitHub call at all.
# A pre-receive hook on the bare remote logs every ref a push writes, so one push and "no child branch" are read, not assumed.
# usage: bash test-wave.sh [scripts dir]
unset REV REV_BASE REV_SHA   # each case decides the review; an ambient one from a live loop must not
# The machine-wide gate this suite is often run under exports its own settings into every command it starts (gate.sh, serial.sh;
# #392, #393): the promotion gate runs the whole battery inside the gate lock. This suite takes its own lock and writes its own
# status line, so none of them may reach it -- an inherited SG_SERIAL_HELD re-enters the lock (case I is never proved), an
# inherited SG_GATE_STATUS=self stops the status line being written (case B reads a file that is not there), and an inherited
# SG_GATE_KIND or SERIAL_* decides a lock and a deadline the suite did not take. gate.sh exports SERIAL_WAIT_SECONDS and
# SERIAL_HOLD_SECONDS; serial.sh exports SG_SERIAL_HELD and reads SERIAL_POLL_SECONDS; a gate caller sets SG_GATE_KIND and
# SG_GATE_STATUS. All of them go, beside REV.
unset SG_SERIAL_HELD SG_GATE_STATUS SG_GATE_KIND SERIAL_WAIT_SECONDS SERIAL_HOLD_SECONDS SERIAL_POLL_SECONDS
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/co" "$T/bin"
cp "$d/wave.sh" "$d/address.sh" "$d/gate.sh" "$d/serial.sh" "$d/ghcall.sh" "$T/s/" 2>/dev/null
{
  printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s SG_LAND_REMOTE=%s/origin.git BOARD=%s/board.sh\n' "$T/st" "$T/co" "$T" "$T"
  printf ': "${SG_WAVE_TIP_GATES:=true}"; export SG_WAVE_TIP_GATES\n'   # the tip's check (#377): `true` unless a case replaces it
  printf 'export SG_BOARD_VOCAB=shipped\n'   # the vocabulary every wave profile runs on (#468): its columns are the ones the landing writes
  printf '. %s/address.sh\n' "$T/s"
} > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
$G init -q -b dev "$T/co"; (cd "$T/co" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init)
$G clone -q --bare "$T/co" "$T/origin.git"; O="$G -C $T/origin.git"; W=wave/run-1
# Every ref a push writes is logged by the remote itself (<old> <new> <ref>, one line per ref): the landing pass pushes
# exactly one ref, and no child branch ever reaches the remote (#375).
mkdir -p "$T/origin.git/hooks"
printf '#!/bin/bash\nwhile read -r old new ref; do printf "%%s\\t%%s\\t%%s\\n" "$old" "$new" "$ref" >> "%s/pushes.log"; done\n' "$T" > "$T/origin.git/hooks/pre-receive"
chmod +x "$T/origin.git/hooks/pre-receive"
pushes() { awk -F'\t' -v r="${1:-}" '$3==r || r=="" {print $1, $2, $3}' "$T/pushes.log" 2>/dev/null; }
# The stub gh: every argv is logged, and a close answers GitHub's own shape. SWEEPS resets the log between cases.
printf '#!/bin/bash\necho "$*" >> "%s/gh.log"\ncase "$*" in *"issues/"*) echo "{\\"number\\":1,\\"state\\":\\"closed\\"}";; *) echo "{}";; esac\n' "$T" > "$T/bin/gh"
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
# The remote is a bare repository on this machine: the whole suite runs against it, never against GitHub.
[ "$($O rev-parse --is-bare-repository)" = true ] && ok "the suite runs against a bare local remote" || ko "the remote is not a bare repository"
[ -x "$T/origin.git/hooks/pre-receive" ] && ok "the remote logs every pushed ref" || ko "the remote has no push hook"
start() { (cd "$T/co" && bash "$T/s/wave.sh" start run-1); }
# A child worktree: a clone cut from the wave branch, on branch gui/<issue>, one commit that writes <file>. A machine under
# load can fail a clone or a checkout, and a test that swallows it reads a worktree that is not there for every case behind it
# (#377): the failure is named here, at the child it belongs to, never discarded. No quiet machine is assumed.
child() { local n=$1 f=$2 tip=${3:-origin/$W} o
  rm -rf "$T/c$n"
  o=$($G clone -q "$T/origin.git" "$T/c$n" 2>&1) || { ko "child #$n: the clone of the wave branch failed: $o"; return 1; }
  o=$((cd "$T/c$n" && $G checkout -q -b "gui/$n" "$tip" && echo "$n" > "$f" && $G add "$f" && $G -c user.email=a@b -c user.name=t commit -q -m "work $n (Refs #$n)") 2>&1) \
    || { ko "child #$n: the worktree on $tip is not ready: $o"; return 1; }; }
# The review of the child's branch, read against the wave branch (#376): branchreview.sh keeps the verdict (review-<key>.md),
# the sha it read (review-<key>.sha) and the base it was taken against (review-<key>.base). REV, REV_BASE and REV_SHA let one
# case hand in a verdict that asked for changes, a review read against another base, or a review of an older tip.
rev() { local n=$1 sha; sha=$($G -C "$T/c$n" rev-parse HEAD); rm -f "$T/st/review-gui_$n".*
  [ "${REV:-APPROVE}" = none ] && return 0
  printf 'VERDICT: %s\n' "${REV:-APPROVE}" > "$T/st/review-gui_$n.md"
  printf '%s\n' "${REV_SHA-$sha}" > "$T/st/review-gui_$n.sha"; printf '%s\n' "${REV_BASE-$W}" > "$T/st/review-gui_$n.base"; }
# enter runs where the child works. GATES is the local gate command the profile sets (SG_WAVE_GATES).
en() { local n=$1; rev "$n"; (cd "$T/c$n" && SG_WAVE_GATES=${GATES-true} bash "$T/s/wave.sh" enter run-1 "$n"); }
entered() { $O merge-base --is-ancestor "$($G -C "$T/c$1" rev-parse HEAD)" $W 2>/dev/null && echo yes || echo no; }
# The wave's landing pass runs beside the run, never in the owner's checkout (it works in a throwaway clone).
land() { (cd "$T/co" && bash "$T/s/wave.sh" land "$@"); }
# One commit lands on dev while the wave is open: the base a wave lands on has moved under it.
devmoved() { rm -rf "$T/w2"; $G clone -q "$T/origin.git" "$T/w2" 2>/dev/null
  (cd "$T/w2" && $G checkout -q dev && echo other > other && $G add other && $G -c user.email=a@b -c user.name=t commit -q -m "other work" && $G push -q origin HEAD:dev); }

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
/usr/bin/grep -qx 'o 3 Code Complete 7' "$T/board.log" && /usr/bin/grep -qx 'o 3 Code Complete 8' "$T/board.log" && ok "B: the card of a child that entered reads Code Complete" || ko "B: no Code Complete card: $(cat "$T/board.log" 2>/dev/null)"
IFS=$'\t' read -r _ kind res at < "$T/st/gate-status" 2>/dev/null
[ "$kind $res $at" = "fast pass $($G -C "$T/c8" rev-parse HEAD)" ] && ok "B: the entry gate leaves one fast status line: its result and the sha it ran at" || ko "B: status line: '$(cat "$T/st/gate-status" 2>&1)'"

# C: a child whose gates fail never enters, and its issue is back in the queue.
printf 'run-1\n' > "$T/st/auto/disp-9"; child 9 g9; before=$($O rev-parse $W); r=$(GATES=false en 9)
[ "$($O rev-parse $W)" = "$before" ] && [ "$(entered 9)" = no ] && ok "C: a child whose gates fail never enters" || ko "C: #9 entered anyway: '$r'"
case "$r" in *"WAVE REQUEUE #9"*gates*) ok "C: the failed gates are said";; *) ko "C: the failure is silent: '$r'";; esac
[ ! -e "$T/st/auto/disp-9" ] && ok "C: its dispatch marker is dropped, so the frontier builds it again" || ko "C: the marker is kept"
/usr/bin/grep -qx 'o 3 Todo 9' "$T/board.log" && ok "C: its card is back in the queue" || ko "C: no Todo card for #9"

# The gates run in the child's own worktree, before the entry: a gate that only passes on the child's file proves it.
child 10 g10; r=$(GATES='test -f g10' en 10)
[ "$(entered 10)" = yes ] && ok "C: the gates run in the child's own worktree" || ko "C: #10 did not enter with a gate over its own tree: '$r'"
# No gate command set: the entry is refused whole, so no child ever enters a wave ungated.
before=$($O rev-parse $W); printf 'run-1\n' > "$T/st/auto/disp-13"; child 13 g13; r=$(GATES= en 13)
[ "$($O rev-parse $W)" = "$before" ] && [ "$(entered 13)" = no ] && ok "C: no gates set: a child never enters a wave ungated" || ko "C: #13 entered ungated: '$r'"
case "$r" in *"WAVE-ERROR"*gates*) ok "C: the missing gates are said";; *) ko "C: the missing gate is silent: '$r'";; esac
[ -e "$T/st/auto/disp-13" ] && ok "C: a bad call touches nothing, so the marker stays" || ko "C: the marker was dropped over a bad call"

# D: a child that conflicts with the wave tip never enters, and says why.
tip0=$($O rev-parse $W)                       # the tip before #11 entered
child 11 g11; en 11 >/dev/null                # #11 adds g11 and enters
child 12 g11 "$tip0"                          # #12 adds the same file from the older tip: the rebase conflicts
printf 'run-1\n' > "$T/st/auto/disp-12"; before=$($O rev-parse $W); r=$(en 12)
[ "$($O rev-parse $W)" = "$before" ] && [ "$(entered 12)" = no ] && ok "D: a child that conflicts with the wave tip never enters" || ko "D: #12 entered: '$r'"
case "$r" in *"WAVE REQUEUE #12"*conflict*g11*) ok "D: the conflict is said, and names the file";; *) ko "D: no reason for the conflict: '$r'";; esac
[ ! -e "$($G -C "$T/c12" rev-parse --git-path rebase-merge)" ] && ok "D: the failed rebase is aborted, so the worktree carries no half-rebase" || ko "D: the child is left mid-rebase"
[ ! -e "$T/st/auto/disp-12" ] && /usr/bin/grep -qx 'o 3 Todo 12' "$T/board.log" && ok "D: a conflicting child is back in the queue" || ko "D: #12 was not requeued"

# E: a child whose review asked for changes never enters the wave branch (#376). It is held where it stands: nothing is
# requeued, so the fix round can work on the same branch and the review can be read again.
before=$($O rev-parse $W); printf 'run-1\n' > "$T/st/auto/disp-20"; child 20 g20; r=$(REV=CHANGES en 20)
[ "$($O rev-parse $W)" = "$before" ] && [ "$(entered 20)" = no ] && ok "E: a child whose review asked for changes never enters" || ko "E: #20 entered: '$r'"
case "$r" in *"#20"*CHANGES*) ok "E: the changes verdict is said, and names the child";; *) ko "E: no reason for the hold: '$r'";; esac
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
# I (#392): the entry gate takes the machine-wide gate lock: it waits for a holder, says so on one line, and then runs.
printf 'run-1\n' > "$T/st/auto/disp-24"; child 24 g24; rev 24; rm -f "$T/gl"
# The runner is started by a path relative to another cwd, as the loop does (`bash scripts/wave.sh` from the repository root), and
# enter works in the child's worktree: the gate.sh beside wave.sh is found by wave.sh's own folder, never by a path after the cd.
if hold; then
  r=$(cd "$T/c24" && SG_WAVE_GATES="echo gate >> $T/gl" bash "../s/wave.sh" enter run-1 24 2>&1); wait
  [ "$(tr '\n' ' ' < "$T/gl")" = "holder gate " ] && ok "I: the entry gate waits for the holder of the gate lock, then runs" || ko "I: gate order '$(tr '\n' ' ' < "$T/gl")': '$r'"
  [ "$(printf '%s\n' "$r" | /usr/bin/grep -c '^serial: waiting for lock gate held by pid')" = 1 ] && ok "I: the wait shows on one line" || ko "I: wait lines: '$r'"
  [ "$(entered 24)" = yes ] && ok "I: the waiting gate is not dropped: the child enters" || ko "I: #24 did not enter"
else
  wait 2>/dev/null   # drain the holder, if it is still there, so no stray writer touches $T/gl later
  ko "I: the holder never came to hold the gate lock, so the entry wait could not be proved"
fi


# J: the wave lands in dev with one push and one CI run, closes what it carries, and pushes no child branch (#375).
WAVE_ISSUES='7 8 10 11 24 30 31'                 # every child the wave carries: a gate failure and a conflict never entered
: > "$T/gh.log"
child 30 g30; en 30 >/dev/null; child 31 g31; r=$(en 31)
[ "$(entered 30)" = yes ] && [ "$(entered 31)" = yes ] && ok "J: the wave of two children holds both" || ko "J: a child of the wave did not enter: '$r'"
/usr/bin/grep -q 'refs/heads/gui/' "$T/pushes.log" && ko "J: a child branch reached the remote: $(/usr/bin/grep 'refs/heads/gui/' "$T/pushes.log")" || ok "J: an entry pushes the wave ref, never a child branch"
# The base moves under the open wave: it is rebased onto before the one push, and only then (ADR-0145 rule 14).
en30head=$($G -C "$T/c30" rev-parse HEAD); en31head=$($G -C "$T/c31" rev-parse HEAD); wtip=$($O rev-parse $W)
devmoved; moved=$($O rev-parse dev)
cp "$T/pushes.log" "$T/pushes.before"; : > "$T/pushes.log"   # from here, the only push left is the landing's own
[ "$($O rev-parse $W)" = "$en31head" ] && [ "$($O rev-parse $W)" = "$wtip" ] && ok "J: the wave tip is the last entrant's head, and the moved dev did not move it" || ko "J: the wave tip is not the last entrant's head"
$O merge-base --is-ancestor "$moved" $W && ko "J: the wave carries the moved dev before the landing" || ok "J: mid-wave the wave does not carry the moved dev (it is not rebased on dev yet)"
$O merge-base --is-ancestor "$en30head" $W && ok "J: the entry of a child is what moves the wave tip" || ko "J: #30's entry is not in the wave"
r=$(land run-1); rc=$?
case "$r" in "WAVE $W "*) ok "J: land names the wave branch and its sha";; *) ko "J: land said nothing: '$r' (exit $rc)";; esac
[ $rc = 0 ] && ok "J: the wave landed (exit 0)" || ko "J: exit $rc: '$r'"
# One push to dev, and it is a fast-forward of the dev that moved: the tip lands whole, in one update.
[ "$(pushes refs/heads/dev | wc -l | tr -d ' ')" = 1 ] && ok "J: the wave lands in dev with one push" || ko "J: $(pushes refs/heads/dev | wc -l | tr -d ' ') pushes to dev: $(pushes refs/heads/dev)"
[ "$(pushes refs/heads/dev | cut -d' ' -f1)" = "$moved" ] && ok "J: the one push starts from the dev that moved" || ko "J: the push started from '$(pushes refs/heads/dev | cut -d' ' -f1)', not the moved dev '$moved'"
$O merge-base --is-ancestor "$moved" dev && ok "J: the moved dev is rebased onto before the push" || ko "J: the moved dev is not in the landed dev"
[ "$($O rev-parse dev)" = "$(pushes refs/heads/dev | cut -d' ' -f2)" ] && ok "J: the push is the one that landed (dev is at the pushed sha)" || ko "J: dev is not at the pushed sha"
$O rev-list --merges dev | /usr/bin/grep -q . && ko "J: a merge commit landed" || ok "J: the landing keeps the history linear"
# The rebase on the moved base rewrites the shas, so what proves a child is in dev is its work, not its sha.
$O cat-file -e dev:g30 2>/dev/null && $O cat-file -e dev:g31 2>/dev/null && ok "J: the work of both children of the wave is in dev" || ko "J: a child of the wave is not in dev"
# No child branch is on the remote, now that the whole cycle has run; the wave branch itself goes with the landing.
/usr/bin/grep -q 'refs/heads/gui/' "$T/pushes.log" "$T/pushes.before" && ko "J: a child branch reached the remote" || ok "J: no child branch is pushed to the remote, ever"
$O rev-parse -q --verify "refs/heads/$W" >/dev/null && ko "J: the landed wave branch survives" || ok "J: the wave branch is deleted once its work is in dev"
[ "$(pushes | awk -v w="refs/heads/$W" '$3!="refs/heads/dev" && $3!=w')" = "" ] && ok "J: the landing writes dev and the wave ref, nothing else" || ko "J: the landing wrote $(pushes)"
# Each issue of the wave is closed through the API, and its one delivery comment is queued, never posted.
for n in $WAVE_ISSUES; do /usr/bin/grep -q "issues/$n -f state=closed" "$T/gh.log" || ko "J: #$n is not closed through the API"; done
[ "$(/usr/bin/grep -c 'state=closed' "$T/gh.log")" = 7 ] && ok "J: each issue of the wave is closed through the API" || ko "J: $(/usr/bin/grep -c 'state=closed' "$T/gh.log") closes for 7 issues"
for n in $WAVE_ISSUES; do ls "$T/st/cq/"x__y#$n-delivery~* >/dev/null 2>&1 || ko "J: no delivery comment is queued for #$n"; done
[ "$(ls "$T/st/cq/"*-delivery~* 2>/dev/null | wc -l | tr -d ' ')" = 7 ] && ok "J: each issue of the wave has its one delivery comment queued" || ko "J: $(ls "$T/st/cq" 2>/dev/null | tr '\n' ' ')"
/usr/bin/grep -qE 'issues/[0-9]+/comments' "$T/gh.log" && ko "J: the pass posted a comment itself" || ok "J: the pass posts no comment itself (the flush does)"
# The shipped vocabulary has no Done column (#468): the landing writes Closed, the column the board carries.
for n in $WAVE_ISSUES; do /usr/bin/grep -qx "o 3 Closed $n" "$T/board.log" || ko "J: the card of #$n does not read Closed"; done
[ "$(/usr/bin/grep -cE '^o 3 Closed ' "$T/board.log")" -ge 7 ] && ok "J: each card of the wave reads Closed" || ko "J: $(/usr/bin/grep -cE '^o 3 Closed ' "$T/board.log") cards read Closed"
/usr/bin/grep -qE '^o 3 Done ' "$T/board.log" && ko "J: the landing still writes Done, which the shipped board does not carry" || ok "J: no Done write on the shipped vocabulary"
# A landing pass with no wave branch to land writes nothing: the second call is refused, and dev does not move.
before=$($O rev-parse dev); r=$(land run-1); rc=$?
[ $rc = 2 ] && case "$r" in *"WAVE-ERROR"*) ok "J: a wave branch that is not there is refused";; *) ko "J: the refusal is silent: '$r'";; esac || ko "J: a second land exited $rc: '$r'"
[ "$($O rev-parse dev)" = "$before" ] && ok "J: a refused landing moves nothing" || ko "J: dev moved on a refused landing"
[ ! -e "$T/st/auto/wave-run-1" ] && ok "J: the landing clears the run's wave marker" || ko "J: the wave marker outlives the landing"

# E2: a push the remote refuses repeats from the fetch (ADR-0145 rule 2). The base moves between the pass's rebase and its
# push -- a concurrent landing, made by the remote's own hook -- so the first push to dev is refused. The pass fetches the
# moved base, rebases on it, and pushes again: the wave lands, and dev carries both the wave's work and the concurrent one.
# start2, en2 and land2 are the setup calls the cases from E2 on run. A failed start cuts no wave branch and a failed entry
# leaves the child out of the wave; either reads as a case that proves nothing if it is swallowed, so both are named with the
# line wave.sh printed. land2's own output and exit code are what each case reads, so it is left as it is.
start2() { local o; o=$((cd "$T/co" && bash "$T/s/wave.sh" start "$1") 2>&1) || { ko "start2 $1: the wave branch was not cut: $o"; return 1; }; }
en2() { local r=$1 n=$2 o; REV_BASE=wave/$r rev "$n"; o=$((cd "$T/c$n" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter "$r" "$n") 2>&1) \
  || { ko "en2 $r #$n: the child did not enter its wave: $o"; return 1; }; printf '%s\n' "$o"; }
land2() { (cd "$T/co" && bash "$T/s/wave.sh" land "$1"); }
rm -rf "$T/race"; $G clone -q "$T/origin.git" "$T/race" 2>/dev/null
(cd "$T/race" && $G checkout -q -b raceline dev && echo race > race && $G add race && $G -c user.email=a@b -c user.name=t commit -q -m "a concurrent landing" && $G push -q origin HEAD:raceline)
RACE=$($G -C "$T/race" rev-parse HEAD)
cat > "$T/origin.git/hooks/pre-receive" <<H
#!/bin/bash
reject=
while read -r old new ref; do printf '%s\t%s\t%s\n' "\$old" "\$new" "\$ref" >> "$T/pushes.log"; [ "\$ref" = refs/heads/dev ] && [ -e "$T/hook-hold" ] && reject=1; done
[ -n "\$reject" ] || exit 0
rm -f "$T/hook-hold"
printf '%s\n' "$RACE" > "$T/origin.git/refs/heads/dev"   # the concurrent landing, between the pass's rebase and its push
exit 1
H
chmod +x "$T/origin.git/hooks/pre-receive"
W2=wave/run-2; start2 run-2 >/dev/null; child 50 g50 "origin/$W2"; en2 run-2 50
: > "$T/hook-hold"; : > "$T/pushes.log"; : > "$T/gh.log"
r=$(land2 run-2); rc=$?
case "$r" in "WAVE $W2 "*) ok "E2: a refused push repeats from the fetch and the wave lands";; *) ko "E2: the refused push did not land: '$r' (exit $rc)";; esac
[ $rc = 0 ] && ok "E2: the landing exits 0" || ko "E2: exit $rc: '$r'"
[ "$($O cat-file -e dev:race 2>/dev/null && echo yes)" = yes ] && ok "E2: the base that moved between the rebase and the push is in dev" || ko "E2: the concurrent landing is not in dev"
[ "$($O cat-file -e dev:g50 2>/dev/null && echo yes)" = yes ] && ok "E2: the wave's work is in dev beside it" || ko "E2: the wave's work is not in dev"
[ "$($O rev-parse dev)" = "$(pushes refs/heads/dev | tail -1 | cut -d' ' -f2)" ] && ok "E2: dev is at the sha the repeat push wrote" || ko "E2: dev is not at the last pushed sha"
$O rev-parse -q --verify "refs/heads/$W2" >/dev/null && ko "E2: the landed wave branch survives" || ok "E2: the wave branch goes with its landing"
[ "$(/usr/bin/grep -c 'issues/50 -f state=closed' "$T/gh.log")" = 1 ] && ok "E2: the issue of the landed wave closes through the API" || ko "E2: $(/usr/bin/grep -c 'issues/50 -f state=closed' "$T/gh.log") closes for #50"

# E3: a close the API refuses queues no delivery comment (#375 debt): the issue stays open, and a comment claiming it
# closed would be false delivery evidence. The close repeats once from the refusal, then the pass names it.
printf '#!/bin/bash\necho "$*" >> "%s/gh.log"\nexit 1\n' "$T" > "$T/bin/gh"; chmod +x "$T/bin/gh"
W3=wave/run-3; start2 run-3 >/dev/null; child 40 g40 "origin/$W3"; en2 run-3 40 >/dev/null
: > "$T/gh.log"; : > "$T/pushes.log"
r=$(land2 run-3); rc=$?
[ "$($O cat-file -e dev:g40 2>/dev/null && echo yes)" = yes ] && ok "E3: the wave still lands when the close is refused" || ko "E3: the wave did not land: '$r' (exit $rc)"
[ "$($O rev-parse dev)" = "$(pushes refs/heads/dev | tail -1 | cut -d' ' -f2)" ] && ok "E3: and dev is at the pushed sha" || ko "E3: dev is not at the pushed sha"
[ "$(/usr/bin/grep -c 'issues/40 -f state=closed' "$T/gh.log")" = 2 ] && ok "E3: the refused close is retried once" || ko "E3: $(/usr/bin/grep -c 'issues/40 -f state=closed' "$T/gh.log") close attempts for #40"
ls "$T/st/cq/"x__y#40-delivery~* >/dev/null 2>&1 && ko "E3: a delivery comment is queued for an issue that did not close" || ok "E3: no delivery comment is queued for an issue that did not close"
/usr/bin/grep -qx "o 3 Closed 40" "$T/board.log" && ko "E3: the card of an issue that did not close reads Closed" || ok "E3: the card of an issue that did not close keeps its column"
[ -z "$(awk -F'\t' '$4=="40"' "$T/st/landed.tsv" 2>/dev/null)" ] && ok "E3: the ledger carries no row for an issue that did not close" || ko "E3: the ledger claims a delivery that did not happen: '$(awk -F'\t' '$4=="40"' "$T/st/landed.tsv")'"
case "$r" in *"#40"*"did not close"*) ok "E3: the close that failed is named";; *) ko "E3: the failed close is silent: '$r'";; esac
printf '#!/bin/bash\necho "$*" >> "%s/gh.log"\ncase "$*" in *"issues/"*) echo "{\\"number\\":1,\\"state\\":\\"closed\\"}";; *) echo "{}";; esac\n' "$T" > "$T/bin/gh"; chmod +x "$T/bin/gh"

# E4: the issues a wave carries are read from the whole message of each commit and named once (#375 review). A `Refs #N`
# that sits in a commit body closes its issue like one in a subject, and an issue two commits (or one commit twice) name is
# closed one time.
W4=wave/run-4; start2 run-4 >/dev/null
rm -rf "$T/c60"; $G clone -q "$T/origin.git" "$T/c60" 2>/dev/null
(cd "$T/c60" && $G checkout -q -b gui/60 "origin/$W4" && echo 60 > g60 && $G add g60 && $G -c user.email=a@b -c user.name=t commit -q -m "work 60" -m "Refs #60"
  echo more > g60b && $G add g60b && $G -c user.email=a@b -c user.name=t commit -q -m "more work" -m "Refs #60"
  echo third > g63 && $G add g63 && $G -c user.email=a@b -c user.name=t commit -q -m "third (Refs #63)" -m "Refs #63")
en2 run-4 60 >/dev/null; : > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/landed.tsv"
r=$(land2 run-4); rc=$?
[ $rc = 0 ] && [ "$($O cat-file -e dev:g60 2>/dev/null && echo yes)" = yes ] && ok "E4: the wave of a body-only Refs lands" || ko "E4: the wave did not land: '$r' (exit $rc)"
[ "$(/usr/bin/grep -c 'issues/60 -f state=closed' "$T/gh.log")" = 1 ] && ok "E4: an issue named in a commit body is closed, once" || ko "E4: $(/usr/bin/grep -c 'issues/60 -f state=closed' "$T/gh.log") closes for #60"
ls "$T/st/cq/"x__y#60-delivery~* >/dev/null 2>&1 && ok "E4: the issue named in a body has its delivery comment queued" || ko "E4: no delivery comment for #60"
/usr/bin/grep -qx "o 3 Closed 60" "$T/board.log" && ok "E4: the card of the issue named in a body reads Closed" || ko "E4: no Closed card for #60: '$(cat "$T/board.log")'"
[ "$(awk -F'\t' '$4=="60"' "$T/st/landed.tsv" 2>/dev/null | wc -l | tr -d ' ')" = 1 ] && ok "E4: the ledger carries one row for an issue two commits name" || ko "E4: the ledger rows for #60: '$(cat "$T/st/landed.tsv" 2>/dev/null)'"
[ "$(/usr/bin/grep -c 'issues/63 -f state=closed' "$T/gh.log")" = 1 ] && ok "E4: an issue a subject and a body both name is closed once" || ko "E4: $(/usr/bin/grep -c 'issues/63 -f state=closed' "$T/gh.log") closes for #63"

# E5: a commit the base already holds is dropped by the rebase, and its issue is neither closed nor logged (#375 review):
# the wave did not deliver it. The issues are read after the rebase, from what is left ahead of the base.
W5=wave/run-5; start2 run-5 >/dev/null; child 61 g61 "origin/$W5"; en2 run-5 61 >/dev/null; child 62 g62 "origin/$W5"; en2 run-5 62 >/dev/null
rm -rf "$T/dup"; $G clone -q "$T/origin.git" "$T/dup" 2>/dev/null
(cd "$T/dup" && $G checkout -q dev && $G fetch -q "$T/c61" gui/61 && GIT_COMMITTER_NAME=other $G -c user.email=a@b -c user.name=t cherry-pick FETCH_HEAD >/dev/null 2>&1 && $G push -q origin HEAD:dev)
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/landed.tsv"
r=$(land2 run-5); rc=$?
[ $rc = 0 ] && [ "$($O cat-file -e dev:g62 2>/dev/null && echo yes)" = yes ] && ok "E5: the wave lands what the base did not hold" || ko "E5: the wave did not land: '$r' (exit $rc)"
/usr/bin/grep -q 'issues/61 -f state=closed' "$T/gh.log" && ko "E5: the issue of a commit the base already held is closed" || ok "E5: the issue of a commit the base already held is not closed"
[ -z "$(awk -F'\t' '$4=="61"' "$T/st/landed.tsv" 2>/dev/null)" ] && ok "E5: the ledger carries no row for it" || ko "E5: the ledger claims #61: '$(cat "$T/st/landed.tsv")'"
/usr/bin/grep -q 'issues/62 -f state=closed' "$T/gh.log" && [ -n "$(awk -F'\t' '$4=="62"' "$T/st/landed.tsv")" ] && ok "E5: the issue the wave did deliver is closed and logged" || ko "E5: #62 is not closed and logged"

# J: a wave with nothing ahead of the base landed empty (a run with no dispatch, or every child failed its gates): the landing
# is done, not failed -- the wave ref goes, the marker goes, exit 0 -- or the loop retries it every cycle for good (#375 review).
# `waved-<run>` outlives the landing: the durable record that the run cut a wave, which childcc.py reads.
W6=wave/run-6; start2 run-6 >/dev/null; before=$($O rev-parse dev); : > "$T/pushes.log"
r=$(land2 run-6); rc=$?
[ $rc = 0 ] && ok "J: a wave with nothing ahead of dev lands empty (exit 0)" || ko "J: exit $rc: '$r'"
$O rev-parse -q --verify "refs/heads/$W6" >/dev/null && ko "J: the empty wave branch stays on the remote" || ok "J: the empty wave branch is deleted"
[ ! -e "$T/st/auto/wave-run-6" ] && ok "J: the run's wave marker is cleared" || ko "J: the wave marker outlives an empty landing"
[ -e "$T/st/auto/waved-run-6" ] && ok "J: waved-<run> stays, the durable record that the run had a wave" || ko "J: no waved-run-6 record"
[ "$($O rev-parse dev)" = "$before" ] && ok "J: an empty landing moves dev nowhere" || ko "J: dev moved on an empty landing"

# N (#449): an empty wave never runs the tip's check at all. A wave with nothing ahead of the base has no child commit for a
# red check to blame, so the check could only fail the landing of a wave that carries nothing -- and four runs went
# needs-human on exactly that (`WAVE CHECK RED` in the loop's own log, at ebb70f8).
W20=wave/run-20; start2 run-20 >/dev/null
r=$(SG_WAVE_TIP_GATES=false land2 run-20); rc=$?
[ $rc = 0 ] && ok "N: an empty wave lands empty though its tip's check fails" || ko "N: exit $rc: '$r'"
case "$r" in *"WAVE CHECK RED"*) ko "N: the tip's check ran on an empty wave: '$r'";; *) ok "N: the tip's check never runs on an empty wave";; esac
$O rev-parse -q --verify "refs/heads/$W20" >/dev/null && ko "N: the empty wave branch stays" || ok "N: the empty wave branch goes with its landing"

# K: a landing that fails the same way every pass stops at the cap (SG_WAVE_LAND_TRIES, default 3): each issue the wave
# carries gets frozen:owner and one queued comment, the marker is cleared so the loop stops, and the wave stays on the remote.
W7=wave/run-7; start2 run-7 >/dev/null; child 70 cf "origin/$W7"; en2 run-7 70 >/dev/null
rm -rf "$T/w3"; $G clone -q "$T/origin.git" "$T/w3" 2>/dev/null
(cd "$T/w3" && $G checkout -q dev && echo other > cf && $G add cf && $G -c user.email=a@b -c user.name=t commit -q -m "conflicting work" && $G push -q origin HEAD:dev)
: > "$T/gh.log"; before=$($O rev-parse dev)
r1=$(land2 run-7); rc1=$?; r2=$(land2 run-7); rc2=$?
[ $rc1 = 1 ] && [ $rc2 = 1 ] && [ -e "$T/st/auto/wave-run-7" ] && ok "K: a conflicting landing fails and keeps its marker below the cap" || ko "K: exits $rc1 $rc2, marker $([ -e "$T/st/auto/wave-run-7" ] && echo kept || echo gone)"
/usr/bin/grep -q 'issues/70/labels' "$T/gh.log" && ko "K: escalated below the cap" || ok "K: nothing is escalated below the cap"
r3=$(land2 run-7); rc3=$?
[ $rc3 = 1 ] && case "$r3" in *"WAVE NEEDS-HUMAN"*) ok "K: the third failure escalates to the owner";; *) ko "K: no escalation: '$r3'";; esac || ko "K: exit $rc3: '$r3'"
/usr/bin/grep -q 'issues/70/labels -f labels\[\]=frozen:owner' "$T/gh.log" && ok "K: the issue the wave carries is labelled frozen:owner" || ko "K: no label: $(cat "$T/gh.log")"
ls "$T/st/cq/"x__y#70-wavestuck* >/dev/null 2>&1 && ok "K: one comment is queued on the issue" || ko "K: no queued comment for #70"
[ ! -e "$T/st/auto/wave-run-7" ] && ok "K: the marker is cleared, so the loop stops retrying" || ko "K: the marker is kept after the escalation"
$O rev-parse -q --verify "refs/heads/$W7" >/dev/null && ok "K: the wave branch keeps the work for the owner" || ko "K: the wave branch is gone"
[ "$($O rev-parse dev)" = "$before" ] && ok "K: dev is not moved by a failing landing" || ko "K: dev moved"

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
# an expired wave takes no new child: its entry sends the child's issue back to the queue
before=$($O rev-parse $W8); child 82 g82 "origin/$W8"; REV_BASE=$W8 rev 82
r=$(cd "$T/c82" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-8 82 2>&1); rc=$?
[ $rc = 1 ] && case "$r" in *"WAVE REQUEUE #82"*"age cap"*) ok "L: an expired wave takes no new child, and its issue is back in the queue";; *) ko "L: no cap reason: '$r'";; esac || ko "L: the entry exited $rc: '$r'"
[ "$($O rev-parse $W8)" = "$before" ] && ok "L: the child that reached an expired wave does not enter" || ko "L: the wave tip moved on an expired wave"
: > "$T/gh.log"; : > "$T/board.log"
r=$(land2 run-8); rc=$?
[ $rc = 0 ] && ok "L: the expired wave lands what it holds (exit 0)" || ko "L: exit $rc: '$r'"
$O cat-file -e dev:g80 2>/dev/null && ok "L: the child the wave held is in dev" || ko "L: the held child is not in dev"
/usr/bin/grep -qx 'o 3 Closed 80' "$T/board.log" && ok "L: the card of the held child reads Closed" || ko "L: no Closed card for #80"
/usr/bin/grep -q 'issues/80 -f state=closed' "$T/gh.log" && ok "L: the held child's issue is closed" || ko "L: #80 was not closed"
[ ! -e "$T/st/auto/disp-81" ] && /usr/bin/grep -qx 'o 3 Todo 81' "$T/board.log" && ok "L: the child still running is back in the queue" || ko "L: #81 was not requeued"
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

rm -rf "$T"; [ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
