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
cp "$d/wave.sh" "$d/issue_ledger.py" "$d/ghread.py" "$d/wavepause.py" "$d/child_issues.py" "$d/address.sh" "$d/gate.sh" "$d/caps.sh" "$d/serial.sh" "$d/ghcall.sh" "$d/base-battery.sh" "$T/s/" 2>/dev/null
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps   # a gate takes gate slots (#567): this repository's squad.caps and a throwaway budget
{
  printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s BOARD=%s/board.sh\n' "$T/st" "$T/co" "$T"
  printf ': "${SG_LAND_REMOTE:=%s/origin.git}"; export SG_LAND_REMOTE\n' "$T"   # a case may set its own remote (K3: one that fails on the network)
  printf ': "${SG_WAVE_TIP_GATES:=true}"; export SG_WAVE_TIP_GATES\n'   # the tip's check (#377): `true` unless a case replaces it
  printf 'export SG_BOARD_VOCAB=shipped\n'   # the vocabulary every wave profile runs on (#468): its columns are the ones the landing writes
  printf '. %s/address.sh\n' "$T/s"
} > "$T/s/env.sh"
# The seam notes each items read on stderr (issue_state.py); the stub does too, so a write that worked is never read as refused (#526).
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\necho "issue_state.py: items read 3; project total: none on the REST items route" >&2\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
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
# The entry leaves a durable record per issue (#467): the janitor reads Code Complete from it, never from the run's own land.
[ -e "$T/st/auto/entered-run-1-7" ] && [ -e "$T/st/auto/entered-run-1-8" ] && ok "B: each entry leaves its entered-<run>-<issue> record" || ko "B: no entered record: $(ls "$T/st/auto")"
IFS=$'\t' read -r _ kind res at < "$T/st/gate-status" 2>/dev/null
[ "$kind $res $at" = "fast pass $($G -C "$T/c8" rev-parse HEAD)" ] && ok "B: the entry gate leaves one fast status line: its result and the sha it ran at" || ko "B: status line: '$(cat "$T/st/gate-status" 2>&1)'"

# C: a child whose gates fail never enters, and its issue is back in the queue.
printf 'run-1\n' > "$T/st/auto/disp-9"; child 9 g9; before=$($O rev-parse $W); r=$(GATES=false en 9)
[ "$($O rev-parse $W)" = "$before" ] && [ "$(entered 9)" = no ] && ok "C: a child whose gates fail never enters" || ko "C: #9 entered anyway: '$r'"
case "$r" in *"WAVE REQUEUE #9"*gates*) ok "C: the failed gates are said";; *) ko "C: the failure is silent: '$r'";; esac
[ ! -e "$T/st/auto/disp-9" ] && ok "C: its dispatch marker is dropped, so the frontier builds it again" || ko "C: the marker is kept"
/usr/bin/grep -qx 'o 3 Todo 9' "$T/board.log" && ok "C: its card is back in the queue" || ko "C: no Todo card for #9"
[ ! -e "$T/st/auto/entered-run-1-9" ] && ok "C: a child that never entered leaves no entered record" || ko "C: #9 has an entered record"

# The gates run in the child's own worktree, before the entry: a gate that only passes on the child's file proves it.
child 10 g10; r=$(GATES='test -f g10' en 10)
[ "$(entered 10)" = yes ] && ok "C: the gates run in the child's own worktree" || ko "C: #10 did not enter with a gate over its own tree: '$r'"
# The gates read the child rebased on the wave tip, never the stale base it was cut from: a child cut from an old dev failed
# a gate the wave tip already passes, and every child went back to the queue (#514).
child 14 g14 origin/dev; r=$(GATES='test -f g7 && test -f g14' en 14)
[ "$(entered 14)" = yes ] && ok "C: the gates run on the child rebased on the wave tip" || ko "C: #14, cut from an old base, was gated on it: '$r'"
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
# I (#392, #575): the entry gate takes the machine-wide gate lock and never runs beside its holder. It does not wait for it
# either (try mode): the entry runs inline in the fast lane, so it holds the child out, and the next pass, after the holder, enters it.
printf 'run-1\n' > "$T/st/auto/disp-24"; child 24 g24; rev 24; rm -f "$T/gl"
# The runner is started by a path relative to another cwd, as the loop does (`bash scripts/wave.sh` from the repository root), and
# enter works in the child's worktree: the gate.sh beside wave.sh is found by wave.sh's own folder, never by a path after the cd.
if hold; then
  r=$(cd "$T/c24" && SG_WAVE_GATES="echo gate >> $T/gl" bash "../s/wave.sh" enter run-1 24 2>&1)
  case "$r" in *"WAVE HOLD #24"*) ok "I: under a held gate lock the entry holds the child out, on one line";; *) ko "I: no hold: '$r'";; esac
  /usr/bin/grep -q gate "$T/gl" 2>/dev/null && ko "I: the entry gate ran beside the holder" || ok "I: the entry gate never runs beside the holder"
  wait; rev 24; r=$(cd "$T/c24" && SG_WAVE_GATES="echo gate >> $T/gl" bash "../s/wave.sh" enter run-1 24 2>&1)
  [ "$(tr '\n' ' ' < "$T/gl")" = "holder gate " ] && ok "I: the next pass, after the holder, runs the entry gate" || ko "I: gate order '$(tr '\n' ' ' < "$T/gl")': '$r'"
  [ "$(entered 24)" = yes ] && ok "I: the waiting gate is not dropped: the child enters" || ko "I: #24 did not enter"
else
  wait 2>/dev/null   # drain the holder, if it is still there, so no stray writer touches $T/gl later
  ko "I: the holder never came to hold the gate lock, so the entry wait could not be proved"
fi


# J: the wave lands in dev with one push and one CI run, closes what it carries, and pushes no child branch (#375).
WAVE_ISSUES='7 8 10 11 14 24 30 31'                 # every child the wave carries: a gate failure and a conflict never entered
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
case "$r" in *refused*) ko "J: a board move that worked is logged as refused: '$r'";; *) ok "J: a board move that worked names no refusal";; esac
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
[ "$(/usr/bin/grep -c 'state=closed' "$T/gh.log")" = 8 ] && ok "J: each issue of the wave is closed through the API" || ko "J: $(/usr/bin/grep -c 'state=closed' "$T/gh.log") closes for 8 issues"
for n in $WAVE_ISSUES; do ls "$T/st/cq/"x__y#$n-delivery~* >/dev/null 2>&1 || ko "J: no delivery comment is queued for #$n"; done
[ "$(ls "$T/st/cq/"*-delivery~* 2>/dev/null | wc -l | tr -d ' ')" = 8 ] && ok "J: each issue of the wave has its one delivery comment queued" || ko "J: $(ls "$T/st/cq" 2>/dev/null | tr '\n' ' ')"
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
# E3b (#475): an issue the landing could not close stays owed. The landing exits non-zero, so the run is not marked seen, and
# the run's wave marker stays; a later pass tries the close again, and the delivery is recorded once the close succeeds.
[ "$rc" != 0 ] && ok "E3b: a landing that could not close an issue exits non-zero" || ko "E3b: the landing exited 0 with #40 still open"
[ -e "$T/st/auto/wave-run-3" ] && ok "E3b: the run keeps its wave marker, so a later pass lands it again" || ko "E3b: the wave marker is gone: nothing tries the close again"
printf '#!/bin/bash\necho "$*" >> "%s/gh.log"\ncase "$*" in *"issues/"*) echo "{\\"number\\":1,\\"state\\":\\"closed\\"}";; *) echo "{}";; esac\n' "$T" > "$T/bin/gh"; chmod +x "$T/bin/gh"
: > "$T/gh.log"; : > "$T/board.log"; : > "$T/pushes.log"; dev0=$($O rev-parse dev)
r=$(land2 run-3); rc=$?
[ $rc = 0 ] && ok "E3b: the later pass delivers the owed issue (exit 0)" || ko "E3b: the later pass exited $rc: '$r'"
[ "$(/usr/bin/grep -c 'issues/40 -f state=closed' "$T/gh.log")" = 1 ] && ok "E3b: the close is tried again on the later pass" || ko "E3b: $(/usr/bin/grep -c 'issues/40 -f state=closed' "$T/gh.log") close attempts on the later pass"
[ "$(ls "$T/st/cq/"x__y#40-delivery~* 2>/dev/null | wc -l | tr -d ' ')" = 1 ] && ok "E3b: the delivery comment is queued once" || ko "E3b: no single delivery comment for #40"
/usr/bin/grep -qx "o 3 Closed 40" "$T/board.log" && ok "E3b: the card moves once the close succeeds" || ko "E3b: the card of #40 did not move"
[ "$(awk -F'\t' '$4=="40"' "$T/st/landed.tsv" 2>/dev/null | wc -l | tr -d ' ')" = 1 ] && ok "E3b: the ledger carries one row for #40" || ko "E3b: the ledger row for #40 is not single"
[ "$($O rev-parse dev)" = "$dev0" ] && [ -z "$(cat "$T/pushes.log")" ] && ok "E3b: the later pass pushes nothing" || ko "E3b: the later pass pushed"
[ ! -e "$T/st/auto/wave-run-3" ] && ok "E3b: the wave marker goes once the issue is delivered" || ko "E3b: the wave marker stays after delivery"

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

# V (#667): a child's fix run holds the wave. A wave with nothing ahead of the base is not landed empty while a
# `ccfix-<run>-<child>` marker names a fix run with no verdict: that run may still put the child in the wave, and an empty
# landing deletes the wave branch, cleans the child's worktree and sends the issue back to the queue while it works.
W7=wave/run-7; start2 run-7 >/dev/null; : > "$T/pushes.log"
mkdir -p "$T/st/fix7"; : > "$T/st/fix7/record.jsonl"
printf '%s\n' "$T/st/fix7" > "$T/st/auto/ccfix-run-7-route.router.1"
r=$(land2 run-7); rc=$?
[ $rc = 1 ] && ok "V: a wave whose child has a fix run with no verdict does not land empty (exit 1)" || ko "V: exit $rc: '$r'"
$O rev-parse -q --verify "refs/heads/$W7" >/dev/null && ok "V: the wave branch stays" || ko "V: the wave branch was deleted"
[ -e "$T/st/auto/wave-run-7" ] && ok "V: the run's wave marker stays" || ko "V: the wave marker went"
case "$r" in *"WAVE WAIT"*) ok "V: the pass names the wait";; *) ko "V: not named: '$r'";; esac
# A fix run whose folder is gone is no verdict either: the marker alone holds the wave, and the age cap is what frees it.
rm -rf "$T/st/fix7"
r=$(land2 run-7); rc=$?
[ $rc = 1 ] && ok "V: a fix run whose folder is gone still holds the wave (exit 1)" || ko "V: exit $rc: '$r'"
rm -f "$T/st/auto/ccfix-run-7-route.router.1"
r=$(land2 run-7); rc=$?
[ $rc = 0 ] && case "$r" in *"landed empty"*) ok "V: with the marker gone the wave lands empty";; *) ko "V: not empty: '$r'";; esac || ko "V: exit $rc: '$r'"

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
: > "$T/board.log"; r3=$(land2 run-7); rc3=$?
[ $rc3 = 1 ] && case "$r3" in *"WAVE NEEDS-HUMAN"*) ok "K: the third failure escalates to the owner";; *) ko "K: no escalation: '$r3'";; esac || ko "K: exit $rc3: '$r3'"
/usr/bin/grep -q 'issues/70/labels -f labels\[\]=frozen:owner' "$T/gh.log" && ok "K: the issue the wave carries is labelled frozen:owner" || ko "K: no label: $(cat "$T/gh.log")"
ls "$T/st/cq/"x__y#70-wavestuck* >/dev/null 2>&1 && ok "K: one comment is queued on the issue" || ko "K: no queued comment for #70"
# #657: nothing landed, so the card leaves Code Complete for Todo; it keeps frozen:owner, so the frontier does not take it.
/usr/bin/grep -qx "o 3 Todo 70" "$T/board.log" && ok "K: the stopped wave moves its issue card to Todo" || ko "K: the card stays where it was: '$(cat "$T/board.log")'"
[ ! -e "$T/st/auto/wave-run-7" ] && ok "K: the marker is cleared, so the loop stops retrying" || ko "K: the marker is kept after the escalation"
$O rev-parse -q --verify "refs/heads/$W7" >/dev/null && ok "K: the wave branch keeps the work for the owner" || ko "K: the wave branch is gone"
[ "$($O rev-parse dev)" = "$before" ] && ok "K: dev is not moved by a failing landing" || ko "K: dev moved"
# K2 (#477): a landing that exits 2 -- the wave branch is gone, the base is gone, or the checkout failed -- is a landing
# that failed, so it counts toward the same cap. Below the cap the marker stays and the next cycle tries again; at the cap
# the marker and the wave start go, so no wave-<run> hold outlives a landing that can never happen.
mkdir -p "$T/st/auto"; echo wave/run-gone > "$T/st/auto/wave-run-gone"; date +%s > "$T/st/auto/wave-start-run-gone"
r1=$(land2 run-gone); rc1=$?; r2=$(land2 run-gone); rc2=$?
[ $rc1 = 2 ] && [ $rc2 = 2 ] && [ -e "$T/st/auto/wave-run-gone" ] && ok "K2: a landing with no wave branch exits 2 and keeps its marker below the cap" || ko "K2: exits $rc1 $rc2, marker $([ -e "$T/st/auto/wave-run-gone" ] && echo kept || echo gone): '$r1'"
r3=$(land2 run-gone); rc3=$?
[ $rc3 = 2 ] && [ ! -e "$T/st/auto/wave-run-gone" ] && [ ! -e "$T/st/auto/wave-start-run-gone" ] && ok "K2: the third exit-2 landing clears the hold" || ko "K2: exit $rc3, marker $([ -e "$T/st/auto/wave-run-gone" ] && echo kept || echo gone): '$r3'"
case "$r3" in *"WAVE NEEDS-HUMAN wave/run-gone"*) ok "K2: the cleared hold is named on one NEEDS-HUMAN line";; *) ko "K2: no NEEDS-HUMAN line: '$r3'";; esac

# K3 (#656): a fetch that fails on a network error (DNS, connect, timeout) is no landing that failed for good: the next
# cycle can land. It counts no try, freezes nothing, prints one WAVE-NETWORK line and exits 2. The remote is reached through
# a stub ssh: while $T/netdown is there it answers as a DNS outage does, else it runs the git command on the local remote.
printf '#!/bin/bash\nwhile [ $# -gt 0 ] && [ "$1" != stub ]; do shift; done; shift\n[ -e "%s/netdown" ] && { echo "ssh: Could not resolve hostname stub: nodename nor servname provided, or not known" >&2; exit 255; }\nexec sh -c "$*"\n' "$T" > "$T/bin/ssh-stub"; chmod +x "$T/bin/ssh-stub"
netland() { (cd "$T/co" && SG_LAND_REMOTE="ssh://stub$T/origin.git" GIT_SSH_COMMAND="$T/bin/ssh-stub" bash "$T/s/wave.sh" land "$1" 2>&1); }
W8=wave/run-8; start2 run-8 >/dev/null; child 80 nf "origin/$W8"; en2 run-8 80 >/dev/null
: > "$T/netdown"; : > "$T/gh.log"; before=$($O rev-parse dev); nr=""
tr=""; for i in 1 2 3; do r=$(netland run-8); rc=$?; nr="$nr$rc "; tr="$tr$(cat "$T/st/auto/wland-tries-run-8" 2>/dev/null)"; done
[ "$nr" = "2 2 2 " ] && ok "K3: a landing whose fetch fails on the network exits 2" || ko "K3: exits $nr: '$r'"
case "$r" in *"WAVE-NETWORK"*"Could not resolve host"*"lands on a later cycle"*) ok "K3: the network failure is named on one WAVE-NETWORK line";; *) ko "K3: no WAVE-NETWORK line: '$r'";; esac
case "$r" in *"WAVE-ERROR"*|*"NEEDS-HUMAN"*) ko "K3: a network failure is read as a stuck landing: '$r'";; *) ok "K3: a network failure is no stuck landing";; esac
[ -z "$tr" ] && [ ! -e "$T/st/auto/wland-tries-run-8" ] && ok "K3: three network failures count no landing try" || ko "K3: tries counted: '$tr'"
/usr/bin/grep -q 'frozen:owner' "$T/gh.log" && ko "K3: a network failure froze the issue" || ok "K3: a network failure freezes nothing"
[ -e "$T/st/auto/wave-run-8" ] && ok "K3: the run keeps its wave marker" || ko "K3: the wave marker is gone"
[ "$($O rev-parse dev)" = "$before" ] && ok "K3: dev does not move while the network is down" || ko "K3: dev moved"
rm -f "$T/netdown"; r=$(netland run-8); rc=$?
[ $rc = 0 ] && [ "$($O cat-file -e dev:nf 2>/dev/null && echo yes)" = yes ] && ok "K3: the next good landing lands the wave" || ko "K3: exit $rc: '$r'"
# A fetch that answers and lacks the ref still counts, on the same remote.
echo wave/run-gone8 > "$T/st/auto/wave-run-gone8"; r=$(netland run-gone8); rc=$?
[ $rc = 2 ] && [ "$(cat "$T/st/auto/wland-tries-run-gone8" 2>/dev/null)" = 1 ] && ok "K3: a fetch that lacks the ref still counts a try" || ko "K3: exit $rc, tries '$(cat "$T/st/auto/wland-tries-run-gone8" 2>/dev/null)': '$r'"
rm -f "$T/st/auto/wave-run-gone8" "$T/st/auto/wland-tries-run-gone8"

# K4 (#663): a landing that failed on a cause the loop itself caused -- here the base battery is red and no child's own
# suite names the cause -- counts no landing try and freezes nothing: it is named once per issue it carries as
# WAVE-LOOP-CAUSED, and the wave stays to land on a later cycle. A real work failure still counts and freezes at the cap (K).
W16=wave/run-16; start2 run-16 >/dev/null; child 160 lg "origin/$W16"; en2 run-16 160 >/dev/null
: > "$T/gh.log"; before=$($O rev-parse dev); lr=""; tr=""
for i in 1 2 3; do r=$(SG_WAVE_TIP_GATES=false SG_WAVE_CHILD_GATES=true land2 run-16); rc=$?; lr="$lr$rc "; tr="$tr$(cat "$T/st/auto/wland-tries-run-16" 2>/dev/null)"; done
[ "$lr" = "1 1 1 " ] && ok "K4: a loop-caused landing failure exits 1" || ko "K4: exits $lr: '$r'"
case "$r" in *"WAVE-LOOP-CAUSED #160:"*"no child's own suite names the cause; not counted"*) ok "K4: the loop-caused failure is named once per issue";; *) ko "K4: no WAVE-LOOP-CAUSED line: '$r'";; esac
[ -z "$tr" ] && [ ! -e "$T/st/auto/wland-tries-run-16" ] && ok "K4: three loop-caused failures count no landing try" || ko "K4: tries counted: '$tr'"
/usr/bin/grep -q 'frozen:owner' "$T/gh.log" && ko "K4: a loop-caused failure froze the issue" || ok "K4: a loop-caused failure freezes nothing"
[ -e "$T/st/auto/wave-run-16" ] && ok "K4: the run keeps its wave marker" || ko "K4: the wave marker is gone"
[ "$($O rev-parse dev)" = "$before" ] && ok "K4: dev does not move on a loop-caused failure" || ko "K4: dev moved"

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
[ ! -e "$T/st/state/x__y__82.json" ] && ok "L: a cap requeue does not count toward the requeue cap" || ko "L: the cap requeue was counted: $(cat "$T/st/state/x__y__82.json")"
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
[ ! -e "$T/st/state/x__y__81.json" ] && ok "L: the landing's cap requeue does not count toward the requeue cap" || ko "L: the landing's cap requeue was counted"
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
  && echo b > o202 && $G add o202 && $G -c user.email=a@b -c user.name=t commit -q -m "TDD RED: case for 202" -m "Refs #202" \
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

# T (#593): a refused Code Complete board write on entry is named, in the shape the landing names its refused write.
# Before, the entry discarded the write's output, so a refused card move was never named and the card looked moved.
W14=wave/run-14; start2 run-14 >/dev/null; child 310 g310 "origin/$W14"
# env.sh names $T/board.sh, so the refusing board replaces it for this case and the working one comes back after it.
printf '#!/bin/bash\necho "$*" >> "%s/board-refuse.log"\necho "board refused: the project is locked" >&2\nexit 1\n' "$T" > "$T/board.sh"
chmod +x "$T/board.sh"; REV_BASE=$W14 rev 310; : > "$T/board-refuse.log"
r=$(cd "$T/c310" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-14 310 2>&1); rc=$?
[ $rc = 0 ] && $O merge-base --is-ancestor "$($G -C "$T/c310" rev-parse HEAD)" "$W14" && ok "T: a refused card write does not keep the child out of the wave" || ko "T: exit $rc: '$r'"
case "$r" in *"WAVE-BOARD #310 Code Complete refused:"*) ok "T: the refused entry card write is named";; *) ko "T: the refused write is silent: '$r'";; esac
case "$r" in *"board refused: the project is locked"*) ok "T: the refusal names the board's own error";; *) ko "T: the board error is not named: '$r'";; esac
/usr/bin/grep -qx 'o 3 Code Complete 310' "$T/board-refuse.log" && ok "T: the entry still attempted the Code Complete write" || ko "T: no Code Complete write was attempted"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\necho "issue_state.py: items read 3; project total: none on the REST items route" >&2\n' "$T" > "$T/board.sh"
chmod +x "$T/board.sh"
# A write that worked names no refusal: the seam's read note is not an error.
r=$(cd "$T/c310" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-14 310 2>&1); rc=$?
[ $rc = 0 ] || ko "T: the second entry exited $rc: '$r'"
case "$r" in *refused*) ko "T: a working board write is named refused: '$r'";; *) ok "T: a working board write names no refusal";; esac

# U (#620): a child that ENTERED its wave (entered-<run>-<n>) whose commit reached dev by another route makes the wave land
# empty, and dev holds no `Refs #N` line for it (a squash rewrote the message). The entry is the proof: the issue is
# delivered, never requeued, even past the age cap.
W15=wave/run-15; start2 run-15 >/dev/null
printf 'run-15\n' > "$T/st/auto/disp-231"; : > "$T/st/auto/entered-run-15-231"
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
rm -rf "$T/st/locks/gate"; sleep 60 & hs=$!; PYTHONPATH="$SG_SQUAD" python3 -m squad.caps take gates other --count 12 --pid "$hs" >/dev/null; : > "$T/board.log"
t0=$(date +%s); r=$(REV_BASE=$W30 rev 301; cd "$T/c301" && SERIAL_WAIT_SECONDS=8 SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-30 301 2>&1); rc=$?; t1=$(date +%s)
[ $rc != 0 ] && [ $((t1 - t0)) -lt 6 ] && ok "Q2: an entry under full gate slots returns at once" || ko "Q2: entry exit $rc after $((t1 - t0)) s: '$r'"
case "$r" in *"WAVE HOLD #301"*"gate slots"*"other"*) ok "Q2: the entry names the full gate slots and their holder";; *) ko "Q2: no hold line: '$r'";; esac
case "$r" in *"WAVE REQUEUE"*) ko "Q2: full gate slots requeued the child: '$r'";; *) ok "Q2: full gate slots requeue nothing";; esac
PYTHONPATH="$SG_SQUAD" python3 -m squad.caps release gates other; kill $hs 2>/dev/null
rm -rf "$T/st/locks/gate"; en2 run-30 301 >/dev/null
mkdir -p "$T/st/locks/gate"; echo "$hp" > "$T/st/locks/gate/pid"; before=$($O rev-parse dev)
t0=$(date +%s); r=$(SERIAL_WAIT_SECONDS=8 SG_WAVE_TIP_GATES="bash '$T/s/gate.sh' true" land2 run-30 2>&1); rc=$?; t1=$(date +%s)
[ $rc != 0 ] && [ $((t1 - t0)) -lt 6 ] && ok "Q: a landing under a held gate lock returns at once" || ko "Q: land exit $rc after $((t1 - t0)) s: '$r'"
case "$r" in *"WAVE WAIT $W30"*"gate lock"*) ok "Q: the landing names the held gate lock";; *) ko "Q: no wait line: '$r'";; esac
case "$r" in *"WAVE CHECK RED"*) ko "Q: a held gate lock read as a red tip: '$r'";; *) ok "Q: a held gate lock is no red tip";; esac
[ ! -e "$T/st/auto/wland-tries-run-30" ] && ok "Q: a held gate lock spends no landing try" || ko "Q: a try was counted"
[ "$($O rev-parse dev)" = "$before" ] && ok "Q: dev does not move while the lock is held" || ko "Q: dev moved"
rm -rf "$T/st/locks/gate"; kill $hp 2>/dev/null
r=$(SG_WAVE_TIP_GATES="bash '$T/s/gate.sh' true" land2 run-30 2>&1); rc=$?
[ $rc = 0 ] && ok "Q: the wave lands on the next pass, once the lock is free" || ko "Q: exit $rc: '$r'"

# R (#566): the wave tip's check is the loop's one base battery -- ruff, mypy and the fast pytest -- read against
# origin/$SG_BASE, in the fast gate, with a bounded worker count instead of `auto`. wave.sh keeps no copy of it. The count
# is set here, so this suite reads no profile's default and depends on no worker count.
. "$d/base-battery.sh" 2>/dev/null
wcmd=$(SG_GATE_WORKERS=3 sg_base_gate "origin/dev" /usr/bin/true 2>/dev/null)
wbat=$(SG_GATE_WORKERS=3 sg_base_battery /usr/bin/true 2>/dev/null)
case "$wcmd" in *"SG_FAST_BASE=origin/dev "*"fast-gate.sh"*) ok "R: the tip check reads the changed scripts against origin/\$SG_BASE";; *) ko "R: the shared base gate is not the fast gate at origin/\$SG_BASE: '$wcmd'";; esac
case "$wbat" in *"ruff check squad tests"*"mypy squad"*'-n ${SQUAD_GATE_WORKERS:-3}'*"not heavy"*) ok "R: the base gate runs ruff, mypy and the fast pytest with SG_GATE_WORKERS";; *) ko "R: the base gate command: '$wbat'";; esac
case "$wbat$wcmd" in *"-n auto"*) ko "R: the base gate runs pytest -n auto";; *) ok "R: the base gate never runs pytest -n auto";; esac
/usr/bin/grep -q 'sg_base_gate "origin/\$base"' "$T/s/wave.sh" && ok "R: wave.sh builds its tip check from the shared definition" || ko "R: wave.sh does not use the shared base gate"
! /usr/bin/grep -v '^[[:space:]]*#' "$T/s/wave.sh" | /usr/bin/grep -q 'ruff check squad tests' && ok "R: wave.sh keeps no copy of the base battery" || ko "R: wave.sh still carries a copy of the base battery"

# X (#682): a push to the wave that the remote refuses repeats once from the fetch and the rebase on the moved tip, and the
# gates run again on that rebased tree before the second push: what enters is what the gates passed. A sibling enters
# between the gates and the push (the remote's hook moves the wave tip and refuses the push once).
W40=wave/run-40; start2 run-40 >/dev/null
rm -rf "$T/sib"; $G clone -q "$T/origin.git" "$T/sib" 2>/dev/null
(cd "$T/sib" && $G checkout -q -b sib "origin/$W40" && echo s > s40 && $G add s40 && $G -c user.email=a@b -c user.name=t commit -q -m "a sibling (Refs #499)" && $G push -q origin HEAD:refs/heads/sib40)
SIB=$($G -C "$T/sib" rev-parse HEAD)
cat > "$T/origin.git/hooks/pre-receive" <<H
#!/bin/bash
reject=
while read -r old new ref; do [ "\$ref" = refs/heads/$W40 ] && [ -e "$T/hook40" ] && reject=1; done
[ -n "\$reject" ] || exit 0
rm -f "$T/hook40"; printf '%s\n' "$SIB" > "$T/origin.git/refs/heads/$W40"; exit 1
H
chmod +x "$T/origin.git/hooks/pre-receive"
child 400 g400 "origin/$W40"; REV_BASE=$W40 rev 400; : > "$T/hook40"; : > "$T/gl40"
r=$(cd "$T/c400" && SG_WAVE_GATES="echo gate >> '$T/gl40'" bash "$T/s/wave.sh" enter run-40 400 2>&1); rc=$?
[ $rc = 0 ] && $O merge-base --is-ancestor "$SIB" $W40 && $O cat-file -e $W40:g400 2>/dev/null && ok "X: a refused entry push rebases and enters" || ko "X: exit $rc: '$r'"
[ "$(wc -l < "$T/gl40" | tr -d ' ')" = 2 ] && ok "X: the gates run again on the rebased tree before the second push" || ko "X: the gates ran $(wc -l < "$T/gl40" | tr -d ' ') times: '$r'"
[ ! -e "$T/st/auto/excluded-run-40-400" ] && ok "X: a child that entered records no exclusion" || ko "X: excluded marker for an entered child"
# X2: the gates fail on the rebased tree, so the child does not enter, and the cause is recorded for the janitor.
child 401 g401 "origin/$W40"; REV_BASE=$W40 rev 401; rm -f "$T/g401"
rm -rf "$T/sib2"; $G clone -q "$T/origin.git" "$T/sib2" 2>/dev/null
(cd "$T/sib2" && $G checkout -q -b sib "origin/$W40" && echo s > s41 && $G add s41 && $G -c user.email=a@b -c user.name=t commit -q -m "another sibling (Refs #498)")
SIB=$($G -C "$T/sib2" rev-parse HEAD); $G -C "$T/sib2" push -q origin HEAD:refs/heads/sib41
cat > "$T/origin.git/hooks/pre-receive" <<H
#!/bin/bash
reject=
while read -r old new ref; do [ "\$ref" = refs/heads/$W40 ] && [ -e "$T/hook40" ] && reject=1; done
[ -n "\$reject" ] || exit 0
rm -f "$T/hook40"; printf '%s\n' "$SIB" > "$T/origin.git/refs/heads/$W40"; exit 1
H
: > "$T/hook40"; before=$SIB
r=$(cd "$T/c401" && SG_WAVE_GATES="[ -e '$T/g401' ] && { echo red after the rebase; exit 1; }; touch '$T/g401'" bash "$T/s/wave.sh" enter run-40 401 2>&1); rc=$?
[ $rc != 0 ] && [ "$($O rev-parse $W40)" = "$before" ] && ok "X2: a rebased tree the gates fail never enters" || ko "X2: exit $rc, tip $($O rev-parse $W40): '$r'"
case "$(cat "$T/st/auto/excluded-run-40-401" 2>/dev/null)" in *"the local gates failed: red after the rebase"*) ok "X2: the exclusion cause is recorded";; *) ko "X2: excluded marker '$(cat "$T/st/auto/excluded-run-40-401" 2>/dev/null)'";; esac
# Y (#682): a hold records its cause too, so the janitor names it when the wave lands without the child.
child 402 g402 "origin/$W40"; REV=none rev 402
r=$(cd "$T/c402" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-40 402 2>&1)
case "$(cat "$T/st/auto/excluded-run-40-402" 2>/dev/null)" in *"no review of gui/402 yet"*) ok "Y: a review hold records its cause";; *) ko "Y: excluded marker '$(cat "$T/st/auto/excluded-run-40-402" 2>/dev/null)': '$r'";; esac

rm -rf "$T"; [ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
