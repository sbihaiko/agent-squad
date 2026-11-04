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
# The clones and the wave's rebase replay commits, and a rebase needs a committer. A runner has none set and cannot
# auto-detect one, so the suite names its own identity and never reads the machine's (#1062).
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=a@b GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=a@b
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/co" "$T/bin"
cp "$d/wave.sh" "$d/issue_ledger.py" "$d/ghread.py" "$d/wavepause.py" "$d/child_issues.py" "$d/address.sh" "$d/gate.sh" "$d/caps.sh" "$d/serial.sh" "$d/ghcall.sh" "$d/base-battery.sh" "$d/basecheck.sh" "$T/s/" 2>/dev/null
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps   # a gate takes gate slots (#567): this repository's squad.caps and a throwaway budget
{
  printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s BOARD=%s/board.sh\n' "$T/st" "$T/co" "$T"
  printf ': "${SG_LAND_REMOTE:=%s/origin.git}"; export SG_LAND_REMOTE\n' "$T"   # a case may set its own remote (K3: one that fails on the network)
  printf ': "${SG_WAVE_TIP_GATES:=true}"; export SG_WAVE_TIP_GATES\n'   # the tip's check (#377): `true` unless a case replaces it
  printf 'export SG_BOARD_VOCAB=shipped\n'   # the vocabulary every wave profile runs on (#468): its columns are the ones the landing writes
  printf '. %s/address.sh\n' "$T/s"
} > "$T/s/env.sh"
# The seam notes each items read on stderr (issue_state.py); the stub does too, so a write that worked is never read as refused (#526).
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\necho "${SG_MOVE_REASON:-none} $*" >> "%s/board-reason.log"\necho "issue_state.py: items read 3; project total: none on the REST items route" >&2\n' "$T" "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
$G init -q -b dev "$T/co"; (cd "$T/co" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init)
$G clone -q --bare "$T/co" "$T/origin.git"; O="$G -C $T/origin.git"; W=wave/run-1
# Every ref a push writes is logged by the remote itself (<old> <new> <ref>, one line per ref): the landing pass pushes
# exactly one ref, and no child branch ever reaches the remote (#375).
mkdir -p "$T/origin.git/hooks"
printf '#!/bin/bash\nwhile read -r old new ref; do printf "%%s\\t%%s\\t%%s\\n" "$old" "$new" "$ref" >> "%s/pushes.log"; done\n' "$T" > "$T/origin.git/hooks/pre-receive"
chmod +x "$T/origin.git/hooks/pre-receive"
pushes() { awk -F'\t' -v r="${1:-}" '$3==r || r=="" {print $1, $2, $3}' "$T/pushes.log" 2>/dev/null; }
# The stub gh: every argv is logged, and a close answers GitHub's own shape. SWEEPS resets the log between cases.
# RED_BASE=1 answers a red completed check on every check-runs read (the base's own check, #786); otherwise the read is not a check-run list.
printf '#!/bin/bash\necho "$*" >> "%s/gh.log"\ncase "$*" in *"issues/"*) echo "{\\"number\\":1,\\"state\\":\\"closed\\"}";; *"/check-runs"*) if [ -n "${RED_BASE:-}" ]; then echo "{\\"check_runs\\":[{\\"id\\":1,\\"name\\":\\"check (3.11)\\",\\"status\\":\\"completed\\",\\"conclusion\\":\\"failure\\"}]}"; else echo "{}"; fi;; *) echo "{}";; esac\n' "$T" > "$T/bin/gh"
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
# wfact <issue> <run>: the wave fact the issue's ledger holds for the run, "<state> <cause>" (ADR-0149: wave events write it).
wfact() { SG_STATE=$T/st python3 "$T/s/issue_ledger.py" read "x/y#$1" wave 2>/dev/null | python3 -c 'import json,sys; r=json.load(sys.stdin).get(sys.argv[1]) or {}; print(r.get("state",""), r.get("cause",""))' "$2" 2>/dev/null; }
tries() { SG_STATE=$T/st python3 "$T/s/issue_ledger.py" read "x/y#$1" wave 2>/dev/null | python3 -c 'import json,sys; print((json.load(sys.stdin).get(sys.argv[1]) or {}).get("tries",""))' "$2" 2>/dev/null; }
# obl_for <tree sha>: the obligation record the gate wrote for that tree, "" when there is none (spec 1163).
obl_for() { local f; for f in "$T/st/obligations"/*; do [ -f "$f" ] || continue; [ "$(cut -f2 "$f")" = "$1" ] && { printf '%s\n' "$f"; return 0; }; done; return 1; }
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
# (#786) A red base holds the wave's landing: the base is read before the tip's check, so the pass names the base, pushes nothing to
# dev and says it once on alerts.log. The red answer is cleared after the case, so the landing below reads the base afresh.
r=$(cd "$T/co" && RED_BASE=1 bash "$T/s/wave.sh" land run-1 2>&1); rc=$?
case "$r" in *"WAVE WAIT $W: base dev is red at "*) ok "J: a red base holds the wave: the pass names the base and waits";; *) ko "J: a red base did not hold the wave: '$r' (exit $rc)";; esac
[ "$rc" = 1 ] && ok "J: a red base is a wait (exit 1), not a refusal" || ko "J: a red base exited $rc"
[ "$(pushes refs/heads/dev | wc -l | tr -d ' ')" = 0 ] && ok "J: a red base: the wave pushes nothing to dev" || ko "J: a red base pushed to dev: $(pushes refs/heads/dev)"
[ "$(/usr/bin/grep -c 'is red at' "$T/st/alerts.log" 2>/dev/null)" = 1 ] && ok "J: a red base raises one alerts.log line" || ko "J: alerts.log: $(cat "$T/st/alerts.log" 2>/dev/null)"
rm -f "$T/st/auto/basecheck-"*
r=$(land run-1); rc=$?
case "$r" in "WAVE $W "*) ok "J: land names the wave branch and its sha";; *) ko "J: land said nothing: '$r' (exit $rc)";; esac
[ $rc = 0 ] && ok "J: the wave landed (exit 0)" || ko "J: exit $rc: '$r'"
# (#790) The base is read again right after the push: the head the wave landed at has its own answer (unreadable here, the stub has no checks).
[ -e "$T/st/auto/basecheck-unreadable-$($O rev-parse dev)" ] && ok "J: the wave reads the base head it pushed" || ko "J: no read of the pushed head: $(ls "$T/st/auto" 2>/dev/null)"
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
for n in $WAVE_ISSUES; do case "$(wfact $n run-1)" in "delivered "*) ;; *) ko "J: #$n has no delivered fact in its ledger: '$(wfact $n run-1)'";; esac; done
# The landing is also the issue's `landed` fact (ADR-0149, #695): rule R reads it, never the run's land heads.
landedfacts() { SG_STATE=$T/st python3 "$T/s/issue_ledger.py" landed x/y 2>/dev/null; }
[ "$(landedfacts | cut -f2 | sort -n | tr '\n' ' ')" = "$(printf '%s\n' $WAVE_ISSUES | sort -n | tr '\n' ' ')" ] && ok "J: each issue the wave landed has its landed fact" || ko "J: landed facts: '$(landedfacts)'"
landedfacts | awk -F'\t' '$1!="run-1" {bad=1} END {exit bad}' && ok "J: the landed fact is keyed by the run that landed it" || ko "J: landed facts: '$(landedfacts)'"
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
case "$(wfact 40 run-3)" in "owed "*) [ ! -e "$T/st/auto/wowed-run-3" ] && ok "E3b: the owed close is a fact in the issue's ledger, with no wowed- marker" || ko "E3b: a wowed- marker is written";; *) ko "E3b: ledger fact of #40: '$(wfact 40 run-3)'";; esac
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
case "$(wfact 40 run-3)" in "delivered "*) ok "E3b: the owed fact turns delivered";; *) ko "E3b: ledger fact of #40 after delivery: '$(wfact 40 run-3)'";; esac

# E4 (#869, re-anchored: each commit here names one issue, so the one-issue-per-commit rule expects the same closes): the issues a wave carries are read from the whole message of each commit and named once (#375 review). A `Refs #N`
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

# K (#844): a rebase conflict in the landing is terminal for this wave head: no retry on the next cycle. The wave's issues go back
# to the queue through the requeue path, the run's wave marker goes (so the loop stops calling the landing), and the wave branch
# stays on the remote with its commits. A conflict is not a stuck landing: it never reaches the stuck cap (SG_WAVE_LAND_TRIES).
W7=wave/run-7; start2 run-7 >/dev/null; child 70 cf "origin/$W7"; en2 run-7 70 >/dev/null
rm -rf "$T/w3"; $G clone -q "$T/origin.git" "$T/w3" 2>/dev/null
(cd "$T/w3" && $G checkout -q dev && echo other > cf && $G add cf && $G -c user.email=a@b -c user.name=t commit -q -m "conflicting work" && $G push -q origin HEAD:dev)
: > "$T/gh.log"; : > "$T/board.log"; before=$($O rev-parse dev)
r1=$(land2 run-7); rc1=$?
case "$r1" in "WAVE CONFLICT $W7: the rebase on dev conflicts at "*"; #70 requeued on the current base"*) ok "K: the conflict is logged once, naming the commit and the issue";; *) ko "K: no conflict line: '$r1'";; esac
[ $rc1 = 1 ] && ok "K: the conflicting landing does not land (exit 1)" || ko "K: exit $rc1"
[ -e "$T/st/auto/wave-run-7" ] && ko "K: the wave marker is kept, so the loop retries the landing" || ok "K: the wave marker goes, so the loop stops retrying"
/usr/bin/grep -qx "o 3 Todo 70" "$T/board.log" && ok "K: #70 is back in Todo" || ko "K: #70 not in Todo: '$(cat "$T/board.log")'"
/usr/bin/grep -qx "no-landing o 3 Todo 70" "$T/board-reason.log" && ok "K: #70 moves back to Todo as no-landing (#1340)" || ko "K: #70 move reason: '$(cat "$T/board-reason.log")'"
case "$(wfact 70 run-7)" in "refused the rebase on dev conflicts at"*) ok "K: the conflict is a fact in the issue's ledger";; *) ko "K: ledger fact of #70: '$(wfact 70 run-7)'";; esac
$O rev-parse -q --verify "refs/heads/$W7" >/dev/null && ok "K: the wave branch keeps the work" || ko "K: the wave branch is gone"
[ "$($O rev-parse dev)" = "$before" ] && ok "K: dev is not moved by a conflicting landing" || ko "K: dev moved"
/usr/bin/grep -q 'labels\[\]=frozen:owner' "$T/gh.log" && ko "K: a conflict below the cap froze the issue" || ok "K: a conflict below the cap freezes nothing"
case "$r1" in *"WAVE-ERROR"*|*"WAVE NEEDS-HUMAN"*) ko "K: the stuck path was taken: '$r1'";; *) ok "K: the stuck path is not taken";; esac

# KC (#1061): two waves of one issue, cut on one base, conflict with dev on the same path. The first conflict re-dispatches the
# child once, with the path named in its request and a rework label on its card; the second identical conflict files one loop
# bug and does not requeue the child again.
start2 run-k8 >/dev/null; start2 run-k10 >/dev/null
child 71 cf2 origin/dev; en2 run-k8 71 >/dev/null
child 71 cf2 origin/dev; en2 run-k10 71 >/dev/null
rm -rf "$T/w4"; $G clone -q "$T/origin.git" "$T/w4" 2>/dev/null
(cd "$T/w4" && $G checkout -q dev && echo dev > cf2 && $G add cf2 && $G -c user.email=a@b -c user.name=t commit -q -m "dev moves cf2" && $G push -q origin HEAD:dev)
: > "$T/gh.log"; rm -f "$T"/st/cq/*71-*
rk8=$(land2 run-k8); rk10=$(land2 run-k10)
case "$rk8" in *"WAVE REQUEUE #71"*) ok "KC: the first conflict re-dispatches its child";; *) ko "KC: no requeue of #71: '$rk8'";; esac
cat "$T"/st/cq/*71-* 2>/dev/null | /usr/bin/grep -q cf2 && ok "KC: the child's next request names the conflicting path" || ko "KC: the request names no path: '$(cat "$T"/st/cq/*71-* 2>/dev/null)'"
/usr/bin/grep -q 'issues/71/labels.*labels\[\]=rework' "$T/gh.log" && ok "KC: the card of #71 carries rework" || ko "KC: no rework label for #71"
case "$rk10" in *"WAVE REQUEUE #71"*) ko "KC: the repeated conflict requeued #71 again: '$rk10'";; *) ok "KC: the repeated conflict does not requeue #71";; esac
[ "$(/usr/bin/grep -c 'labels\[\]=bug' "$T/gh.log")" = 1 ] && ok "KC: the repeated conflict files one bug" || ko "KC: $(/usr/bin/grep -c 'labels\[\]=bug' "$T/gh.log") bugs filed"

# K3 (#844): the requeue cap bounds a conflict that keeps coming back: at SG_LOOP_REQUEUE_CAP the issue goes to the owner, as any
# requeue does (requeue, loop_stop). A second wave of #73 conflicts with dev moved again after its entry.
W44=wave/run-44; start2 run-44 >/dev/null; child 73 cf "origin/$W44"; en2 run-44 73 >/dev/null
(cd "$T/w3" && $G pull -q origin dev && echo other2 > cf && $G add cf && $G -c user.email=a@b -c user.name=t commit -q -m "more conflicting work" && $G push -q origin HEAD:dev)
r4=$(SG_LOOP_REQUEUE_CAP=1 land2 run-44); rc4=$?
case "$r4" in *"WAVE NEEDS-HUMAN #73"*) ok "K3: at the requeue cap the conflicting issue goes to the owner";; *) ko "K3: no needs-human line: '$r4'";; esac
[ $rc4 = 1 ] && ok "K3: the capped conflict does not land (exit 1)" || ko "K3: exit $rc4"

# K2 (#477): a landing that exits 2 -- the wave branch is gone, the base is gone, or the checkout failed -- is a landing
# that failed, so it counts toward the same cap. Below the cap the marker stays and the next cycle tries again; at the cap
# the marker and the wave start go, so no wave-<run> hold outlives a landing that can never happen.
mkdir -p "$T/st/auto" "$T/co/runs/run-gone"; echo wave/run-gone > "$T/st/auto/wave-run-gone"; date +%s > "$T/st/auto/wave-start-run-gone"; echo "$T/co/runs/run-gone" >> "$T/st/active-runs.txt"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #95 work"}}' > "$T/co/runs/run-gone/record.jsonl"
r1=$(land2 run-gone); rc1=$?; r2=$(land2 run-gone); rc2=$?
[ "$(tries 95 run-gone)" = 2 ] && [ ! -e "$T/st/auto/wland-tries-run-gone" ] && ok "K2: each try is counted in the ledger of the run's issue, with no wland-tries- marker" || ko "K2: tries '$(tries 95 run-gone)', $(/bin/ls "$T/st/auto")"
[ $rc1 = 2 ] && [ $rc2 = 2 ] && [ -e "$T/st/auto/wave-run-gone" ] && ok "K2: a landing with no wave branch exits 2 and keeps its marker below the cap" || ko "K2: exits $rc1 $rc2, marker $([ -e "$T/st/auto/wave-run-gone" ] && echo kept || echo gone): '$r1'"
r3=$(land2 run-gone); rc3=$?
[ $rc3 = 2 ] && [ ! -e "$T/st/auto/wave-run-gone" ] && [ ! -e "$T/st/auto/wave-start-run-gone" ] && ok "K2: the third exit-2 landing clears the hold" || ko "K2: exit $rc3, marker $([ -e "$T/st/auto/wave-run-gone" ] && echo kept || echo gone): '$r3'"
case "$r3" in *"WAVE NEEDS-HUMAN wave/run-gone"*) ok "K2: the cleared hold is named on one NEEDS-HUMAN line";; *) ko "K2: no NEEDS-HUMAN line: '$r3'";; esac
# K2b: a wave that names no issue (no Refs ahead of the base, no run record) has no ledger to count in and holds no issue,
# so its first failed landing clears its hold.
echo wave/run-none > "$T/st/auto/wave-run-none"; r=$(land2 run-none); rc=$?
[ $rc = 2 ] && [ ! -e "$T/st/auto/wave-run-none" ] && case "$r" in *"WAVE NEEDS-HUMAN wave/run-none"*"names no issue"*) true;; *) false;; esac \
  && ok "K2b: a wave with no issue clears its hold at its first failed landing" || ko "K2b: exit $rc, $(/bin/ls "$T/st/auto" | /usr/bin/grep run-none): '$r'"

# AB (#1310): a wave branch absent on the remote was never pushed. The landing names that cause and never claims the branch
# stays on the remote. A run that holds no slot (ended, dropped from active-runs.txt) stops waiting at once: its markers go.
mkdir -p "$T/co/runs/run-abs"; printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #96 work"}}' '{"event": "run_start"}' '{"event": "verdict", "verdict": "failed"}' > "$T/co/runs/run-abs/record.jsonl"
echo wave/run-abs > "$T/st/auto/wave-run-abs"; date +%s > "$T/st/auto/wave-start-run-abs"
ra=$(land2 run-abs); rca=$?
case "$ra" in *"stays on the remote"*) ko "AB: an absent wave branch is said to stay on the remote: '$ra'";; *) ok "AB: no claim that an absent wave branch stays on the remote";; esac
case "$ra" in *"wave/run-abs is not on "*"the run never pushed it"*) ok "AB: the absent wave branch is named as the cause";; *) ko "AB: the cause is not named: '$ra'";; esac
[ $rca = 2 ] && [ ! -e "$T/st/auto/wave-run-abs" ] && [ ! -e "$T/st/auto/wave-start-run-abs" ] && ok "AB: a run with no slot stops waiting at once" || ko "AB: exit $rca, markers kept for a run with no slot: '$ra'"
# AB2: a run that still holds its slot keeps the absent wave on its count: one try, the marker stays for the next cycle.
mkdir -p "$T/co/runs/run-abl"; printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #97 work"}}' '{"event": "run_start"}' > "$T/co/runs/run-abl/record.jsonl"; echo "$T/co/runs/run-abl" >> "$T/st/active-runs.txt"
echo wave/run-abl > "$T/st/auto/wave-run-abl"; date +%s > "$T/st/auto/wave-start-run-abl"
rl=$(land2 run-abl); rcl=$?
[ $rcl = 2 ] && [ -e "$T/st/auto/wave-run-abl" ] && ok "AB2: a run that holds its slot keeps its absent wave for the next cycle" || ko "AB2: exit $rcl: '$rl'"
case "$rl" in *"stays on the remote"*) ko "AB2: an absent wave branch is said to stay on the remote: '$rl'";; *) ok "AB2: a live run's absent wave is not said to stay on the remote";; esac

# K3 (#656): a fetch that fails on a network error (DNS, connect, timeout) is no landing that failed for good: the next
# cycle can land. It counts no try, freezes nothing, prints one WAVE-NETWORK line and exits 2. The remote is reached through
# a stub ssh: while $T/netdown is there it answers as a DNS outage does, else it runs the git command on the local remote.
printf '#!/bin/bash\nwhile [ $# -gt 0 ] && [ "$1" != stub ]; do shift; done; shift\n[ -e "%s/netdown" ] && { echo "ssh: Could not resolve hostname stub: nodename nor servname provided, or not known" >&2; exit 255; }\nexec sh -c "$*"\n' "$T" > "$T/bin/ssh-stub"; chmod +x "$T/bin/ssh-stub"
netland() { (cd "$T/co" && SG_LAND_REMOTE="ssh://stub$T/origin.git" GIT_SSH_COMMAND="$T/bin/ssh-stub" bash "$T/s/wave.sh" land "$1" 2>&1); }
W8=wave/run-8; start2 run-8 >/dev/null; child 80 nf "origin/$W8"; en2 run-8 80 >/dev/null
: > "$T/netdown"; : > "$T/gh.log"; before=$($O rev-parse dev); nr=""
tr=""; for i in 1 2 3; do r=$(netland run-8); rc=$?; nr="$nr$rc "; tr="$tr$(tries 80 run-8)"; done
[ "$nr" = "2 2 2 " ] && ok "K3: a landing whose fetch fails on the network exits 2" || ko "K3: exits $nr: '$r'"
case "$r" in *"WAVE-NETWORK"*"Could not resolve host"*"lands on a later cycle"*) ok "K3: the network failure is named on one WAVE-NETWORK line";; *) ko "K3: no WAVE-NETWORK line: '$r'";; esac
case "$r" in *"WAVE-ERROR"*|*"NEEDS-HUMAN"*) ko "K3: a network failure is read as a stuck landing: '$r'";; *) ok "K3: a network failure is no stuck landing";; esac
[ -z "$tr" ] && ok "K3: three network failures count no landing try" || ko "K3: tries counted: '$tr'"
/usr/bin/grep -q 'frozen:owner' "$T/gh.log" && ko "K3: a network failure froze the issue" || ok "K3: a network failure freezes nothing"
[ -e "$T/st/auto/wave-run-8" ] && ok "K3: the run keeps its wave marker" || ko "K3: the wave marker is gone"
[ "$($O rev-parse dev)" = "$before" ] && ok "K3: dev does not move while the network is down" || ko "K3: dev moved"
rm -f "$T/netdown"; r=$(netland run-8); rc=$?
[ $rc = 0 ] && [ "$($O cat-file -e dev:nf 2>/dev/null && echo yes)" = yes ] && ok "K3: the next good landing lands the wave" || ko "K3: exit $rc: '$r'"
# A fetch that answers and lacks the ref still counts, on the same remote.
echo wave/run-gone8 > "$T/st/auto/wave-run-gone8"; mkdir -p "$T/co/runs/run-gone8"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #96 work"}}' > "$T/co/runs/run-gone8/record.jsonl"
r=$(netland run-gone8); rc=$?
[ $rc = 2 ] && [ "$(tries 96 run-gone8)" = 1 ] && ok "K3: a fetch that lacks the ref still counts a try" || ko "K3: exit $rc, tries '$(tries 96 run-gone8)': '$r'"
rm -f "$T/st/auto/wave-run-gone8"

# K4 (#663): a landing that failed on a cause the loop itself caused -- here the base battery is red and no child's own
# suite names the cause -- counts no landing try and freezes nothing: it is named once per issue it carries as
# WAVE-LOOP-CAUSED, and the wave stays to land on a later cycle. A real work failure still counts and freezes at the cap (K).
W16=wave/run-16; start2 run-16 >/dev/null; child 160 lg "origin/$W16"; en2 run-16 160 >/dev/null
: > "$T/gh.log"; before=$($O rev-parse dev); lr=""; tr=""
for i in 1 2 3; do r=$(SG_WAVE_TIP_GATES=false SG_WAVE_CHILD_GATES=true land2 run-16); rc=$?; lr="$lr$rc "; tr="$tr$(tries 160 run-16)"; done
[ "$lr" = "1 1 1 " ] && ok "K4: a loop-caused landing failure exits 1" || ko "K4: exits $lr: '$r'"
case "$r" in *"WAVE-LOOP-CAUSED #160:"*"no child's own suite names the cause; not counted"*) ok "K4: the loop-caused failure is named once per issue";; *) ko "K4: no WAVE-LOOP-CAUSED line: '$r'";; esac
[ -z "$tr" ] && ok "K4: three loop-caused failures count no landing try" || ko "K4: tries counted: '$tr'"
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
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\necho "${SG_MOVE_REASON:-none} $*" >> "%s/board-reason.log"\necho "issue_state.py: items read 3; project total: none on the REST items route" >&2\n' "$T" "$T" > "$T/board.sh"
chmod +x "$T/board.sh"
# A write that worked names no refusal: the seam's read note is not an error.
r=$(cd "$T/c310" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-14 310 2>&1); rc=$?
[ $rc = 0 ] || ko "T: the second entry exited $rc: '$r'"
case "$r" in *refused*) ko "T: a working board write is named refused: '$r'";; *) ok "T: a working board write names no refusal";; esac

# U (#620): a child that ENTERED its wave (its ledger's entered fact) whose commit reached dev by another route makes the wave land
# empty, and dev holds no `Refs #N` line for it (a squash rewrote the message). The entry is the proof: the issue is
# delivered, never requeued, even past the age cap.
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
rm -rf "$T/st/locks/gate"; sleep 60 & hs=$!; PYTHONPATH="$SG_SQUAD" python3 -m squad.caps take gates other --count 12 --pid "$hs" >/dev/null; : > "$T/board.log"
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
case "$(wfact 400 run-40)" in "entered "*) ok "X: a child that entered records its entry, no exclusion";; *) ko "X: ledger fact of #400: '$(wfact 400 run-40)'";; esac
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
case "$(wfact 401 run-40)" in "refused the local gates failed: red after the rebase"*) ok "X2: the exclusion cause is recorded";; *) ko "X2: ledger fact '$(wfact 401 run-40)'";; esac
# Y (#682): a hold records its cause too, so the janitor names it when the wave lands without the child.
child 402 g402 "origin/$W40"; REV=none rev 402
r=$(cd "$T/c402" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-40 402 2>&1)
case "$(wfact 402 run-40)" in "held no review of gui/402 yet"*) ok "Y: a review hold records its cause";; *) ko "Y: ledger fact '$(wfact 402 run-40)': '$r'";; esac

# Z (#694): a run whose final gate (ac-verify) failed takes no child into its wave: the child is refused, its cause is recorded,
# and the wave lands empty. A run that passed, or one with no verdict yet, lets its child enter as before.
for rn in 41 42 43; do start2 run-$rn >/dev/null; mkdir -p "$T/co/runs/run-$rn"; done
printf '%s\n' '{"event": "run_start"}' \
  '{"event": "verdict", "verdict": "failed", "reasons": ["ac-verify:ac_failed:bash contrib/squad-goal/scripts/fast-gate.sh"]}' > "$T/co/runs/run-41/record.jsonl"
printf '%s\n' '{"event": "run_start"}' '{"event": "verdict", "verdict": "completed", "reasons": []}' > "$T/co/runs/run-42/record.jsonl"
printf '%s\n' '{"event": "run_start"}' > "$T/co/runs/run-43/record.jsonl"
child 410 g410 origin/wave/run-41; REV_BASE=wave/run-41 rev 410; before=$($O rev-parse wave/run-41)
r=$(cd "$T/c410" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-41 410 2>&1); rc=$?
[ $rc != 0 ] && [ "$($O rev-parse wave/run-41)" = "$before" ] && ok "Z: a child of a run whose final gate failed never enters" || ko "Z: exit $rc, tip moved: '$r'"
case "$(wfact 410 run-41)" in "refused the run's final gate failed"*) ok "Z: the exclusion names the failed final gate";; *) ko "Z: ledger fact '$(wfact 410 run-41)': '$r'";; esac
for rn in 42 43; do n=$((410 + rn - 41))
  child $n g$n origin/wave/run-$rn; REV_BASE=wave/run-$rn rev $n
  r=$(cd "$T/c$n" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-$rn $n 2>&1); rc=$?
  [ $rc = 0 ] && $O cat-file -e wave/run-$rn:g$n 2>/dev/null && ok "Z: a child of run-$rn (passed or live) enters" || ko "Z: run-$rn exit $rc: '$r'"
done


# AA (spec 1163, #1174): an unproven outcome admits with its obligation. At the entry the child enters and the run's entry
# record carries the obligation; at the tip the wave lands on the same word and no child is blamed. A measured red still
# refuses (case C) -- only a word the gate wrote can admit.
start2 run-50 >/dev/null; child 500 g500 "origin/wave/run-50"; REV_BASE=wave/run-50 rev 500; : > "$T/board.log"; rm -rf "$T/st/obligations" "$T/st/proof-card"
r=$(cd "$T/c500" && SG_WAVE_GATES='printf "unproven\tsaturation: the battery crashed twice\tunknown\truff\n" > "$GATE_OUTCOME_FILE"; exit 4' bash "$T/s/wave.sh" enter run-50 500 2>&1); rc=$?
[ $rc = 0 ] && $O cat-file -e wave/run-50:g500 2>/dev/null && ok "AA: an unproven entry gate admits the child" || ko "AA: entry exit $rc: '$r'"
case "$(wfact 500 run-50)" in "entered "*unproven*obligation*) ok "AA: the entry fact carries the obligation in the run's record";; *) ko "AA: entry fact '$(wfact 500 run-50)'";; esac
obf=$(obl_for "$($G -C "$T/c500" rev-parse HEAD)"); [ -n "$obf" ] && [ "$(cut -f4 "$obf")" = ruff ] && ok "AA: the obligation record names the check that did not run" || ko "AA: obligation '$(cat "$T/st/obligations"/* 2>/dev/null)'"
/usr/bin/grep -qx 'o 3 Code Complete 500' "$T/board.log" && ok "AA: an unproven entry moves the card to Code Complete" || ko "AA: card: $(cat "$T/board.log" 2>/dev/null)"
# AD (#1176): the card carries the fact of the unproven entry while the obligation is open, and the entry records which
# obligation the settlement answers.
/usr/bin/grep -qx -- '--proof o 3 500 unproven' "$T/board.log" && ok "AD: the unproven entry puts the fact on the card" || ko "AD: no proof fact: $(cat "$T/board.log" 2>/dev/null)"
[ -n "$(/bin/ls -A "$T/st/proof-card/500" 2>/dev/null)" ] && ok "AD: the issue maps to the obligation the settlement answers" || ko "AD: no proof-card mapping: $(/bin/ls -A "$T/st/proof-card" 2>&1)"
# AF (#1176): the fact stays while the obligation is open. A later entry on a passing gate proves the new tree, not the tree
# the obligation names, so only the settlement answers it; the entry neither clears the card nor the mapping.
(cd "$T/c500" && echo fix > g500fix && $G add g500fix && $G -c user.email=a@b -c user.name=t commit -q -m "fix 500 (Refs #500)") \
  && REV_BASE=wave/run-50 rev 500
: > "$T/board.log"
r=$(cd "$T/c500" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-50 500 2>&1); rc=$?
[ $rc = 0 ] && [ -n "$(/bin/ls -A "$T/st/proof-card/500" 2>/dev/null)" ] && [ -z "$(/usr/bin/grep -- '--proof' "$T/board.log" 2>/dev/null)" ] \
  && ok "AF: a later pass entry keeps the fact while the obligation is open" \
  || ko "AF: rc $rc, board '$(cat "$T/board.log" 2>/dev/null)', mapping '$(/bin/ls -A "$T/st/proof-card/500" 2>&1)'"
# AC: a pass entry carries no obligation: the run's entry record names none, and no obligation record names its tree.
start2 run-52 >/dev/null; child 520 g520 "origin/wave/run-52"; REV_BASE=wave/run-52 rev 520; rm -rf "$T/st/obligations" "$T/st/proof-card"; : > "$T/board.log"
r=$(cd "$T/c520" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-52 520 2>&1); rc=$?
case "$(wfact 520 run-52)" in *obligation*) ko "AC: a pass entry's fact carries an obligation: '$(wfact 520 run-52)'";; *) ok "AC: a pass entry's fact carries no obligation";; esac
[ $rc = 0 ] && [ -z "$(obl_for "$($G -C "$T/c520" rev-parse HEAD)")" ] && ok "AC: a pass entry writes no obligation record" || ko "AC: pass obligation '$(cat "$T/st/obligations"/* 2>/dev/null)'"
[ -z "$(/usr/bin/grep -- '--proof' "$T/board.log" 2>/dev/null)" ] && [ ! -e "$T/st/proof-card/520" ] && ok "AC: a proven entry never carries the fact" || ko "AC: a proven entry carried the fact: $(cat "$T/board.log" 2>/dev/null)"
# AE (#1176): a wave whose tip check is unproven lands, and every issue it delivers carries the fact of the tip's
# obligation. The tip gate goes through gate.sh, so the obligation record exists for the settlement to answer.
printf '#!/bin/bash\nprintf "unproven\\tsaturation: the battery crashed twice\\tunknown\\truff\\n" > "$GATE_OUTCOME_FILE"\nexit 4\n' > "$T/unproven-tip.sh"
start2 run-51 >/dev/null; child 510 g510 "origin/wave/run-51"; en2 run-51 510 >/dev/null; : > "$T/board.log"; : > "$T/st/gate-history"; rm -rf "$T/st/obligations" "$T/st/proof-card"
r=$(SG_WAVE_TIP_GATES="bash '$T/s/gate.sh' bash '$T/unproven-tip.sh'" SG_WAVE_CHILD_GATES='exit 1' land2 run-51 2>&1); rc=$?
[ $rc = 0 ] && $O cat-file -e dev:g510 2>/dev/null && case "$r" in *unproven*) true;; *) false;; esac && ok "AA: an unproven tip check lands the wave and says so" || ko "AA: tip exit $rc: '$r'"
case "$r" in *"WAVE CHECK RED"*|*REQUEUE*) ko "AA: an unproven tip check blamed a child: '$r'";; *) ok "AA: an unproven tip check blames no child";; esac
[ -z "$(tries 510 run-51)" ] && ok "AA: an unproven tip check counts no landing try" || ko "AA: tries $(tries 510 run-51)"
/usr/bin/grep -qx -- '--proof o 3 510 unproven' "$T/board.log" && ok "AE: an unproven tip landing puts the fact on the delivered card" || ko "AE: no proof fact on the tip landing: $(cat "$T/board.log" 2>/dev/null)"
[ -n "$(/bin/ls -A "$T/st/proof-card/510" 2>/dev/null)" ] && ok "AE: the delivered issue maps to the tip's obligation" || ko "AE: no proof-card mapping: $(/bin/ls -A "$T/st/proof-card" 2>&1)"
[ "$(awk -F'\t' -v s="$($O rev-parse dev)" '$4 == s && $8 == "unproven"' "$T/st/gate-history" 2>/dev/null | wc -l | tr -d ' ')" = 1 ] \
  && ok "AE: the gate record names the tip's unproven outcome at the landed sha" || ko "AE: gate history: '$(cat "$T/st/gate-history" 2>&1)'"

# The ledger replaced the auto/excluded-<run>-<n> marker (ADR-0149 rule 6): no case leaves one.
[ -z "$(/bin/ls "$T/st/auto" | /usr/bin/grep '^excluded-')" ] && ok "no excluded- marker is written" || ko "excluded markers: $(/bin/ls "$T/st/auto" | /usr/bin/grep '^excluded-' | tr '\n' ' ')"

# S775 (#775): a wave closes only the issues its run was dispatched for. A `Refs #N` outside the run's dispatch set (an engine
# commit that named the first #N of the issue's prose) is reported in one WAVE-NOTE line and a row of wave-outside.tsv, and
# that issue is never closed, never moved, never given a delivery comment.
W14=wave/run-14; start2 run-14 >/dev/null
child 140 g140 "origin/$W14" && (cd "$T/c140" && echo e > e140 && $G add e140 && $G -c user.email=a@b -c user.name=t commit -q -m "squad: route.router.1 passed for #141" -m "Refs #141")
en2 run-14 140 >/dev/null
RD="$T/co/runs/run-14"; mkdir -p "$RD"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #140 work; it is the work #141 waits on"}}' > "$RD/record.jsonl"
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/cq/"*141* "$T/st/wave-outside.tsv"
r=$(cd "$T/co" && bash "$T/s/wave.sh" land run-14 "$RD" 2>&1); rc=$?
[ $rc = 0 ] && /usr/bin/grep -q 'issues/140 -f state=closed' "$T/gh.log" && ok "S775: the dispatched issue closes with the landing" || ko "S775: #140 not closed: rc $rc '$r'"
/usr/bin/grep -q 'issues/141 -f state=closed' "$T/gh.log" && ko "S775: #141, outside the dispatch set, was closed: '$r'" || ok "S775: a Refs outside the dispatch set is not closed"
/usr/bin/grep -q ' 141$' "$T/board.log" && ko "S775: #141's card moved" || ok "S775: the card of an issue outside the dispatch set does not move"
[ -z "$(/bin/ls "$T/st/cq" | /usr/bin/grep '#141-')" ] && ok "S775: no delivery comment is queued for #141" || ko "S775: a comment is queued for #141"
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c 'WAVE-NOTE.*#141.*outside.*dispatch')" = 1 ] && ok "S775: the outside Refs is named in one log line" || ko "S775: no single WAVE-NOTE for #141: '$r'"
awk -F '\t' '$2 == "run-14" && $3 == "141"' "$T/st/wave-outside.tsv" 2>/dev/null | /usr/bin/grep -q . && ok "S775: the outside Refs is recorded in wave-outside.tsv" || ko "S775: no wave-outside.tsv row: '$(cat "$T/st/wave-outside.tsv" 2>&1)'"

# S776 (#776): a landing that gives up acts only on the issues its run was dispatched for, as the close does (#775). A
# `Refs #N` outside the dispatch set gets no label, no card move, no comment and no ledger try; it is reported in one
# WAVE-NOTE line and a row of wave-outside.tsv.
W15=wave/run-15; start2 run-15 >/dev/null
child 150 g150 "origin/$W15" && (cd "$T/c150" && echo e > e150 && $G add e150 && $G -c user.email=a@b -c user.name=t commit -q -m "squad: route.router.1 passed for #151" -m "Refs #151")
en2 run-15 150 >/dev/null
RD="$T/co/runs/run-15"; mkdir -p "$RD"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #150 work; it is the work #151 waits on"}}' > "$RD/record.jsonl"
rm -rf "$T/w15"; $G clone -q "$T/origin.git" "$T/w15" 2>/dev/null
(cd "$T/w15" && $G checkout -q dev && echo other > g150 && $G add g150 && $G -c user.email=a@b -c user.name=t commit -q -m "conflicting work" && $G push -q origin HEAD:dev)
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/cq/"*151* "$T/st/wave-outside.tsv"
r=$(cd "$T/co" && SG_WAVE_LAND_TRIES=1 bash "$T/s/wave.sh" land run-15 "$RD" 2>&1); rc=$?
[ $rc = 1 ] && /usr/bin/grep -qx "o 3 Todo 150" "$T/board.log" && /usr/bin/grep -q 'issues/150/labels.*labels\[\]=rework' "$T/gh.log" && ok "S776: the dispatched issue is requeued with rework when the conflicting landing gives up (#844, #1061)" || ko "S776: #150 not requeued: rc $rc '$r'"
/usr/bin/grep -q 'issues/151/' "$T/gh.log" && ko "S776: #151, outside the dispatch set, was written: '$(cat "$T/gh.log")'" || ok "S776: a Refs outside the dispatch set gets no label"
/usr/bin/grep -q ' 151$' "$T/board.log" && ko "S776: #151's card moved" || ok "S776: the card of an issue outside the dispatch set does not move"
[ -z "$(/bin/ls "$T/st/cq" | /usr/bin/grep '#151-')" ] && ok "S776: no comment is queued for #151" || ko "S776: a comment is queued for #151"
[ -z "$(tries 151 run-15)" ] && ok "S776: no landing try is counted for #151" || ko "S776: tries of #151: $(tries 151 run-15)"
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c 'WAVE-NOTE.*#151.*outside.*dispatch')" = 1 ] && ok "S776: the outside Refs is named in one log line" || ko "S776: no single WAVE-NOTE for #151: '$r'"
awk -F '\t' '$2 == "run-15" && $3 == "151"' "$T/st/wave-outside.tsv" 2>/dev/null | /usr/bin/grep -q . && ok "S776: the outside Refs is recorded in wave-outside.tsv" || ko "S776: no wave-outside.tsv row: '$(cat "$T/st/wave-outside.tsv" 2>&1)'"

# S777 (#1340): a landing whose push to the base is refused twice takes the stuck path: its issue goes back to Todo as
# no-landing (board.py refuses any other automated backward move from Code Complete). The origin refuses every push to dev.
W1340=wave/run-1340; start2 run-1340 >/dev/null; child 1340 g1340 "origin/$W1340"; en2 run-1340 1340 >/dev/null
cp "$T/origin.git/hooks/pre-receive" "$T/pre-receive.keep"; printf '#!/bin/bash\necho "refused by the test remote" >&2\nexit 1\n' > "$T/origin.git/hooks/pre-receive"
: > "$T/gh.log"; : > "$T/board.log"; : > "$T/board-reason.log"; rm -f "$T/st/cq/"*1340*
r=$(SG_WAVE_LAND_TRIES=1 land2 run-1340); rc=$?
cp "$T/pre-receive.keep" "$T/origin.git/hooks/pre-receive"
case "$r" in *"WAVE-ERROR the push of wave/run-1340 to dev was refused"*) true;; *) false;; esac && [ $rc = 1 ] && ok "S777: a push refused twice takes the stuck path" || ko "S777: exit $rc: '$r'"
/usr/bin/grep -qx "no-landing o 3 Todo 1340" "$T/board-reason.log" && ok "S777: the stuck path moves #1340 back to Todo as no-landing (#1340)" || ko "S777: #1340 move reason: '$(cat "$T/board-reason.log")', board '$(cat "$T/board.log")'"

# (#646) The wave tip's check is the base battery over origin/$SG_BASE, and its pytest runs over the selection. The check's command
# is built from the shared definition and run in a clone of the remote, as wave.sh runs it; a stub python logs each call's argv.
printf '#!/bin/bash\necho "$*" >> "%s/argv.log"\ncase "$*" in *squad.testselect*) cat "%s/select.out" 2>/dev/null;; esac\nexit 0\n' "$T" "$T" > "$T/selpy"; chmod +x "$T/selpy"
tipcmd=$(SG_GATE_WORKERS=3 sg_base_gate "origin/dev" "$T/selpy" wave-tip 2>/dev/null)
rm -rf "$T/tipclone"; $G clone -q "$T/origin.git" "$T/tipclone" 2>/dev/null
printf 'tests/test_a.py\n' > "$T/select.out"; rm -f "$T/argv.log"
r=$(cd "$T/tipclone" && SG_GATE_WORKERS=3 SG_STATE="$T/st" bash -c "$tipcmd" 2>&1); rc=$?
[ $rc = 0 ] && /usr/bin/grep -qx -- "-m pytest -p no:deepeval -o heavy_cap_seconds=20 -n 3 -m not heavy tests/test_a.py" "$T/argv.log" && ok "the wave tip check passes the selection to pytest" || ko "tip check argv: rc $rc: '$(cat "$T/argv.log" 2>&1)' $r"
/usr/bin/grep -q -- "squad.testselect --stage wave-tip --base origin/dev" "$T/argv.log" && ok "the wave tip check selects the delta at the wave-tip stage" || ko "wave-tip stage: '$(cat "$T/argv.log" 2>&1)'"
IFS=$'\t' read -r _ kind res _ sel scope < "$T/st/gate-status" 2>/dev/null
[ "$kind $res $sel $scope" = "fast pass selected 1 files delta" ] && ok "the wave tip check's status line states the selected file count and the delta scope" || ko "tip check status: '$(cat "$T/st/gate-status" 2>&1)'"

# X (#836): the empty landing names the issues of the run's own request (request.txt, its CHILD header), before the record: the
# record's #710 is not named when the request names other issues.
W21=wave/run-836a; start2 run-836a >/dev/null
mkdir -p "$T/co/runs/run-836a"
printf '%s\n%s\n' 'CHILD — GitHub issue(s) 712, 713, 714 (bug; one root cause, one branch):' '--- issue #712' > "$T/co/runs/run-836a/request.txt"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #710 work"}}' > "$T/co/runs/run-836a/record.jsonl"
r=$(land2 run-836a); rc=$?
[ $rc = 0 ] && case "$r" in *"landed empty"*) ok "Y1: the wave of a bundled request lands empty (exit 0)";; *) ko "Y1: not an empty landing: '$r'";; esac || ko "Y1: exit $rc: '$r'"
case "$r" in *"dispatched for #712, #713, #714"*) ok "Y1: the landing names every issue of the request's CHILD header";; *) ko "Y1: the request's issues are not named: '$r'";; esac
case "$r" in *"#710"*) ko "Y1: the record's #710 is named beside the request's issues: '$r'";; *) ok "Y1: the landing names no record-only issue";; esac

# Y2: a child commit of this run (Refs #721) on the run's own branch only, in neither the wave nor dev: the wave is refused and kept.
W22=wave/run-836b; start2 run-836b >/dev/null
mkdir -p "$T/co/runs/run-836b"; printf '%s\n' 'CHILD — GitHub issue #721 (bug; one root cause, one branch):' > "$T/co/runs/run-836b/request.txt"
printf '%s\n%s\n' '{"event": "run_start"}' '{"event": "verdict", "verdict": "completed", "reasons": []}' > "$T/co/runs/run-836b/record.jsonl"
x2=$(mktemp -d); $G clone -q "$T/origin.git" "$x2/c" 2>/dev/null
(cd "$x2/c" && $G checkout -q -b tmp dev && echo fix > f721 && $G add f721 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 721" -m "Refs #721")
X2SHA=$($G -C "$x2/c" rev-parse HEAD); $G -C "$T/co" fetch -q "$x2/c" HEAD:refs/heads/squad/run/run-836b 2>/dev/null
: > "$T/pushes.log"
r=$(land2 run-836b 2>&1); rc=$?
[ $rc = 1 ] && ok "Y2: a child commit on the run's branch refuses the empty landing (exit 1)" || ko "Y2: exit $rc: '$r'"
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c '^WAVE REFUSE')" = 1 ] && ok "Y2: one WAVE REFUSE line" || ko "Y2: no single WAVE REFUSE line: '$r'"
case "$r" in *"${X2SHA:0:9}"*) ok "Y2: the refusal names the child commit";; *) ko "Y2: the commit is not named: '$r'";; esac
case "$r" in *"landed empty"*) ko "Y2: the wave landed empty: '$r'";; *) ok "Y2: the wave does not land empty";; esac
$O rev-parse -q --verify "refs/heads/$W22" >/dev/null && ok "Y2: the wave branch is kept on the remote" || ko "Y2: the wave branch was deleted"
/usr/bin/grep -q ":refs/heads/$W22" "$T/pushes.log" && ko "Y2: the wave branch was pushed for deletion" || ok "Y2: no deletion push for the wave branch"

# Y2b (#836): the same commit already on a remote fix/ branch is salvaged, so it holds nothing: the wave lands empty.
W25=wave/run-836e; start2 run-836e >/dev/null
mkdir -p "$T/co/runs/run-836e"; printf '%s\n' 'CHILD — GitHub issue #722 (bug; one root cause, one branch):' > "$T/co/runs/run-836e/request.txt"
x5=$(mktemp -d); $G clone -q "$T/origin.git" "$x5/c" 2>/dev/null
(cd "$x5/c" && $G checkout -q -b tmp dev && echo salv > f722 && $G add f722 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 722" -m "Refs #722" && $G push -q origin HEAD:refs/heads/fix/722-salvage 2>/dev/null)
X5SHA=$($G -C "$x5/c" rev-parse HEAD); $G -C "$T/co" fetch -q "$x5/c" HEAD:refs/heads/squad/run/run-836e 2>/dev/null
$G -C "$T/co" fetch -q "$T/origin.git" '+refs/heads/*:refs/remotes/origin/*' 2>/dev/null
[ "$($G -C "$T/co" branch -r --contains "$X5SHA" 2>/dev/null | /usr/bin/grep -c .)" -ge 1 ] && ok "Y2b: the commit is on a remote fix/ branch" || ko "Y2b: the setup did not put the commit on a remote branch"
r=$(land2 run-836e 2>&1); rc=$?
[ $rc = 0 ] && case "$r" in *"landed empty"*) ok "Y2b: a commit already on a remote fix/ branch does not hold the wave";; *) ko "Y2b: not an empty landing: '$r'";; esac || ko "Y2b: exit $rc: '$r'"
case "$r" in *"WAVE REFUSE"*) ko "Y2b: a remote-reachable commit was refused: '$r'";; *) ok "Y2b: no refusal names the salvaged commit";; esac

# Y3 (#836): a leftover squad/ branch of another run, holding a commit that names an issue of this run's request, does not hold
# this run's wave: only the run's own branch and trees are read, and the commit is local to that other branch only.
W23=wave/run-836c; start2 run-836c >/dev/null
mkdir -p "$T/co/runs/run-836c"; printf '%s\n' 'CHILD — GitHub issue #731 (bug; one root cause, one branch):' > "$T/co/runs/run-836c/request.txt"
x3=$(mktemp -d); $G clone -q "$T/origin.git" "$x3/c" 2>/dev/null
(cd "$x3/c" && $G checkout -q -b tmp dev && echo other > f731 && $G add f731 && $G -c user.email=a@b -c user.name=t commit -q -m "other 731" -m "Refs #731")
$G -C "$T/co" fetch -q "$x3/c" HEAD:refs/heads/squad/run/run-836-old 2>/dev/null
r=$(land2 run-836c 2>&1); rc=$?
[ $rc = 0 ] && case "$r" in *"landed empty"*) ok "Y3: a leftover branch of another run does not hold the wave";; *) ko "Y3: not empty: '$r'";; esac || ko "Y3: exit $rc: '$r'"

# Y4 (#836): past the age cap an orphan child commit does not hold the wave: the capped wave lands empty and its issue goes back
# to the queue, as the capped landing requires (ADR-0145 rule 13).
W26=wave/run-836d; start2 run-836d >/dev/null
mkdir -p "$T/co/runs/run-836d"; printf '%s\n' 'CHILD — GitHub issue #741 (bug; one root cause, one branch):' > "$T/co/runs/run-836d/request.txt"
x4=$(mktemp -d); $G clone -q "$T/origin.git" "$x4/c" 2>/dev/null
(cd "$x4/c" && $G checkout -q -b tmp dev && echo cap > f741 && $G add f741 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 741" -m "Refs #741")
$G -C "$T/co" fetch -q "$x4/c" HEAD:refs/heads/squad/run/run-836d 2>/dev/null
printf '%s\n' "$(( $(date +%s) - 7201 ))" > "$T/st/auto/wave-start-run-836d"
: > "$T/board.log"
r=$(land2 run-836d 2>&1); rc=$?
[ $rc = 0 ] && case "$r" in *"landed empty"*) ok "Y4: a capped wave with an orphan child commit lands empty (exit 0)";; *) ko "Y4: not an empty landing: '$r'";; esac || ko "Y4: exit $rc: '$r'"
case "$r" in *"WAVE REFUSE"*) ko "Y4: the capped wave was refused: '$r'";; *) ok "Y4: the capped wave is not refused";; esac
case "$r" in *"WAVE REQUEUE #741"*"age cap"*) ok "Y4: the orphan's issue goes back to the queue";; *) ko "Y4: the issue was not requeued: '$r'";; esac
/usr/bin/grep -qx 'o 3 Todo 741' "$T/board.log" && ok "Y4: the card of the requeued issue reads Todo" || ko "Y4: no Todo card for #741"
$O rev-parse -q --verify "refs/heads/$W26" >/dev/null && ko "Y4: the capped wave branch is kept" || ok "Y4: the capped wave branch goes"
# Y5 (#1106): an issue the run itself requeued is no orphan. Its child commit stays on the run's branch and the loop builds the
# issue again, so the wave lands empty instead of refusing on every later cycle.
W27=wave/run-1106; start2 run-1106 >/dev/null
mkdir -p "$T/co/runs/run-1106"; printf '%s\n' 'CHILD — GitHub issue #761 (bug; one root cause, one branch):' > "$T/co/runs/run-1106/request.txt"
printf '%s\n%s\n' '{"event": "run_start"}' '{"event": "verdict", "verdict": "completed", "reasons": []}' > "$T/co/runs/run-1106/record.jsonl"
x7=$(mktemp -d); $G clone -q "$T/origin.git" "$x7/c" 2>/dev/null
(cd "$x7/c" && $G checkout -q -b tmp dev && echo fix > f761 && $G add f761 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 761" -m "Refs #761")
X7SHA=$($G -C "$x7/c" rev-parse HEAD); $G -C "$T/co" fetch -q "$x7/c" HEAD:refs/heads/squad/run/run-1106 2>/dev/null
SG_STATE=$T/st python3 "$T/s/issue_ledger.py" requeue "x/y#761" run-1106 "$X7SHA" >/dev/null 2>&1
r=$(land2 run-1106 2>&1); rc=$?
[ $rc = 0 ] && case "$r" in *"landed empty"*) ok "Y5: a commit of an issue the run requeued does not hold the wave";; *) ko "Y5: not an empty landing: '$r'";; esac || ko "Y5: exit $rc: '$r'"
case "$r" in *"WAVE REFUSE"*) ko "Y5: the wave was refused: '$r'";; *) ok "Y5: no WAVE REFUSE line";; esac

# (#845) The orphan-commit refusal applies only to a run whose verdict is completed. A failed, needs-human, stopped or verdict-less
# run's child commit never holds its wave: the wave takes its normal path (an empty landing here, with no requeue).
for k in failed:751 needs-human:752 stopped:753 none:754; do
  v=${k%%:*}; n=${k##*:}; run=run-845-$v; W=wave/$run; start2 $run >/dev/null
  mkdir -p "$T/co/runs/$run"; printf 'CHILD — GitHub issue #%s (bug; one root cause, one branch):\n' "$n" > "$T/co/runs/$run/request.txt"
  if [ "$v" = none ]; then printf '%s\n' '{"event": "run_start"}' > "$T/co/runs/$run/record.jsonl"
  else printf '%s\n%s\n' '{"event": "run_start"}' "{\"event\": \"verdict\", \"verdict\": \"$v\", \"reasons\": []}" > "$T/co/runs/$run/record.jsonl"; fi
  x=$(mktemp -d); $G clone -q "$T/origin.git" "$x/c" 2>/dev/null
  (cd "$x/c" && $G checkout -q -b tmp dev && echo o > "f$n" && $G add "f$n" && $G -c user.email=a@b -c user.name=t commit -q -m "fix $n" -m "Refs #$n")
  $G -C "$T/co" fetch -q "$x/c" HEAD:refs/heads/squad/run/$run 2>/dev/null
  r=$(land2 $run 2>&1); rc=$?
  [ $rc = 0 ] && case "$r" in *"landed empty"*) ok "Z1 $v: the orphan child commit does not hold the wave (exit 0, landed empty)";; *) ko "Z1 $v: not an empty landing: '$r'";; esac || ko "Z1 $v: exit $rc: '$r'"
  case "$r" in *"WAVE REFUSE"*) ko "Z1 $v: the wave was refused: '$r'";; *) ok "Z1 $v: no WAVE REFUSE line";; esac
  rm -rf "$x"
done

# (#845) A completed run's orphan child commit is refused once per wave and commit: over two passes the refusal line appears once,
# and the wave is kept both times.
W=wave/run-845-done; start2 run-845-done >/dev/null
mkdir -p "$T/co/runs/run-845-done"; printf '%s\n' 'CHILD — GitHub issue #755 (bug; one root cause, one branch):' > "$T/co/runs/run-845-done/request.txt"
printf '%s\n%s\n' '{"event": "run_start"}' '{"event": "verdict", "verdict": "completed", "reasons": []}' > "$T/co/runs/run-845-done/record.jsonl"
x=$(mktemp -d); $G clone -q "$T/origin.git" "$x/c" 2>/dev/null
(cd "$x/c" && $G checkout -q -b tmp dev && echo o > f755 && $G add f755 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 755" -m "Refs #755")
$G -C "$T/co" fetch -q "$x/c" HEAD:refs/heads/squad/run/run-845-done 2>/dev/null
r1=$(land2 run-845-done 2>&1); rc1=$?
r2=$(land2 run-845-done 2>&1); rc2=$?
[ $rc1 = 1 ] && [ $rc2 = 1 ] && ok "Z2: a completed run's orphan commit still refuses the wave on both passes" || ko "Z2: exit $rc1 $rc2: '$r1' / '$r2'"
[ "$(printf '%s\n%s\n' "$r1" "$r2" | /usr/bin/grep -c '^WAVE REFUSE')" = 1 ] && ok "Z2: the refusal line appears once over two passes" || ko "Z2: refusal lines over two passes: '$r1' / '$r2'"
$O rev-parse -q --verify "refs/heads/$W" >/dev/null && ok "Z2: the wave branch is kept" || ko "Z2: the wave branch was deleted"
rm -rf "$x"

# (#866) A reviewed child commit that names an issue of the run never leaves the wave closed empty while it sits on a local branch
# only. R1: a completed run whose child's fix run committed the fix in its own worktree: the fix run's squad/<fix run>/ branch
# holds the only copy, so the wave is kept and refused. R2: a failed run whose reviewed head (auto/rvd-<run>-<child>) is a local-only
# commit that names its issue: the wave is kept and refused too. Z1 still lets an unreviewed commit of a failed run go.
W=wave/run-866a; start2 run-866a >/dev/null
mkdir -p "$T/co/runs/run-866a" "$T/co/runs/run-866fix"; printf '%s\n' 'CHILD — GitHub issue #787 (bug; one root cause, one branch):' > "$T/co/runs/run-866a/request.txt"
printf '%s\n%s\n' '{"event": "run_start"}' '{"event": "verdict", "verdict": "completed", "reasons": []}' > "$T/co/runs/run-866a/record.jsonl"
printf '%s\n%s\n' '{"event": "run_start"}' '{"event": "verdict", "verdict": "completed", "reasons": []}' > "$T/co/runs/run-866fix/record.jsonl"
x=$(mktemp -d); $G clone -q "$T/origin.git" "$x/c" 2>/dev/null
(cd "$x/c" && $G checkout -q -b tmp dev && echo fix > f787 && $G add f787 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 787" -m "Refs #787")
$G -C "$T/co" fetch -q "$x/c" HEAD:refs/heads/squad/run-866fix/worktrees_child-route.router.1 2>/dev/null
printf '%s\n' "$T/co/runs/run-866fix" > "$T/st/auto/ccfix-run-866a-route.router.1"
r=$(land2 run-866a 2>&1); rc=$?
[ $rc = 1 ] && case "$r" in *"WAVE REFUSE"*"787"*) ok "R1: a fix run's reviewed commit keeps its completed run's wave (refused, exit 1)";; *) ko "R1: not refused: '$r' (exit $rc)";; esac || ko "R1: exit $rc: '$r'"
$O rev-parse -q --verify "refs/heads/$W" >/dev/null && ok "R1: the wave branch is kept" || ko "R1: the wave branch was deleted"
case "$r" in *"landed empty"*) ko "R1: the wave landed empty: '$r'";; *) ok "R1: the wave does not land empty";; esac
rm -f "$T/st/auto/ccfix-run-866a-route.router.1"; rm -rf "$x"

W=wave/run-866b; start2 run-866b >/dev/null
mkdir -p "$T/co/runs/run-866b"; printf '%s\n' 'CHILD — GitHub issue #794 (bug; one root cause, one branch):' > "$T/co/runs/run-866b/request.txt"
printf '%s\n%s\n' '{"event": "run_start"}' '{"event": "verdict", "verdict": "failed", "reasons": ["ac-verify:ac_failed"]}' > "$T/co/runs/run-866b/record.jsonl"
x=$(mktemp -d); $G clone -q "$T/origin.git" "$x/c" 2>/dev/null
(cd "$x/c" && $G checkout -q -b tmp dev && echo fix > f794 && $G add f794 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 794" -m "Refs #794")
$G -C "$T/co" fetch -q "$x/c" HEAD:refs/heads/squad/run/run-866b 2>/dev/null
printf '%s 4242\n' "$($G -C "$x/c" rev-parse HEAD)" > "$T/st/auto/rvd-run-866b-route.router.1"
r=$(land2 run-866b 2>&1); rc=$?
[ $rc = 1 ] && case "$r" in *"WAVE REFUSE"*"794"*) ok "R2: a failed run's reviewed commit keeps its wave (refused, exit 1)";; *) ko "R2: not refused: '$r' (exit $rc)";; esac || ko "R2: exit $rc: '$r'"
$O rev-parse -q --verify "refs/heads/$W" >/dev/null && ok "R2: the wave branch is kept" || ko "R2: the wave branch was deleted"
rm -f "$T/st/auto/rvd-run-866b-route.router.1"; rm -rf "$x"

rm -rf "$x2" "$x3" "$x4" "$x5"


# Z3 (#869): one commit per issue. A cluster child that writes one fix commit per issue (each naming exactly one `Refs #N`)
# closes every issue its commits fix: the wave closes the siblings, not only the first issue its tip names.
W17=wave/run-17; start2 run-17 >/dev/null
rm -rf "$T/c870"; $G clone -q "$T/origin.git" "$T/c870" 2>/dev/null
(cd "$T/c870" && $G checkout -q -b gui/870 "origin/$W17" && for n in 870 871 872; do echo "$n" > "g$n" && $G add "g$n" && $G -c user.email=a@b -c user.name=t commit -q -m "fix $n" -m "Refs #$n"; done)
en2 run-17 870 >/dev/null; mkdir -p "$T/co/runs/run-17"
printf '%s\n%s\n%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #870 work"}}' \
  '{"event": "spawn_accepted", "child": "b", "definition": {"prompt": "GitHub issue #871 work"}}' \
  '{"event": "spawn_accepted", "child": "c", "definition": {"prompt": "GitHub issue #872 work"}}' > "$T/co/runs/run-17/record.jsonl"
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/landed.tsv"
r=$(land2 run-17 2>&1); rc=$?
[ $rc = 0 ] && [ "$($O cat-file -e dev:g871 2>/dev/null && echo yes)" = yes ] && ok "Z3: the cluster wave lands one commit per issue (exit 0)" || ko "Z3: the cluster wave did not land: '$r' (exit $rc)"
for n in 870 871 872; do [ "$(/usr/bin/grep -c "issues/$n -f state=closed" "$T/gh.log")" = 1 ] && ok "Z3: #$n, fixed by its own commit, closes once" || ko "Z3: closes for #$n: '$(cat "$T/gh.log")'"; done

# Z4 (#869): a commit that names several issues delivers none of them. The wave closes none of them, and each one of the run's
# issues is requeued with that reason (cap: it counts no failed dispatch, since its work reached the base).
W18=wave/run-18; start2 run-18 >/dev/null
rm -rf "$T/c880"; $G clone -q "$T/origin.git" "$T/c880" 2>/dev/null
(cd "$T/c880" && $G checkout -q -b gui/880 "origin/$W18" && echo cluster > g880 && $G add g880 && $G -c user.email=a@b -c user.name=t commit -q -m "cluster work" -m "Refs #880
Refs #881
Refs #882")
en2 run-18 880 >/dev/null; mkdir -p "$T/co/runs/run-18"
printf '%s\n%s\n%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #880 work"}}' \
  '{"event": "spawn_accepted", "child": "b", "definition": {"prompt": "GitHub issue #881 work"}}' \
  '{"event": "spawn_accepted", "child": "c", "definition": {"prompt": "GitHub issue #882 work"}}' > "$T/co/runs/run-18/record.jsonl"
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/landed.tsv"
r=$(land2 run-18 2>&1); rc=$?
[ $rc = 0 ] && [ "$($O cat-file -e dev:g880 2>/dev/null && echo yes)" = yes ] && ok "Z4: the multi-issue commit lands (exit 0)" || ko "Z4: did not land: '$r' (exit $rc)"
[ -z "$(/usr/bin/grep 'issues/[0-9]* -f state=closed' "$T/gh.log")" ] && ok "Z4: a commit naming several issues closes none of them" || ko "Z4: closes: '$(cat "$T/gh.log")'"
for n in 880 881 882; do /usr/bin/grep -qx "o 3 Todo $n" "$T/board.log" && ok "Z4: #$n is requeued" || ko "Z4: #$n not requeued: '$(cat "$T/board.log")'"; done
case "$r" in *"WAVE REQUEUE #881"*"several issues"*) ok "Z4: the requeue names the multi-issue commit as its reason";; *) ko "Z4: no reason named for #881: '$r'";; esac

# Z5 (#869): the empty-wave route reads the same rule. A multi-issue commit that reached the base after the wave was cut
# delivers none of its issues to a later empty wave of a run dispatched for one of them; a single-issue commit still does.
W19=wave/run-19; start2 run-19 >/dev/null
rm -rf "$T/out19"; $G clone -q "$T/origin.git" "$T/out19" 2>/dev/null
(cd "$T/out19" && $G checkout -q dev && echo m > o890 && $G add o890 && $G -c user.email=a@b -c user.name=t commit -q -m "cluster work" -m "Refs #890
Refs #891
Refs #892" && echo s > o893 && $G add o893 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 893" -m "Refs #893" && $G push -q origin HEAD:dev)
mkdir -p "$T/co/runs/run-19"
printf '%s\n%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #891 work"}}' \
  '{"event": "spawn_accepted", "child": "b", "definition": {"prompt": "GitHub issue #893 work"}}' > "$T/co/runs/run-19/record.jsonl"
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/landed.tsv"
r=$(land2 run-19 2>&1); rc=$?
case "$r" in *"landed empty"*) ok "Z5: the wave lands empty (exit 0)";; *) ko "Z5: not an empty landing: '$r' (exit $rc)";; esac
/usr/bin/grep -q 'issues/891 -f state=closed' "$T/gh.log" && ko "Z5: a sibling of a multi-issue commit was closed" || ok "Z5: a multi-issue commit delivers none of its issues to an empty wave"
[ "$(/usr/bin/grep -c 'issues/893 -f state=closed' "$T/gh.log")" = 1 ] && ok "Z5: a single-issue commit still delivers to an empty wave" || ko "Z5: closes for #893: '$(cat "$T/gh.log")'"

# Z6 (#869): a capped landing requeues no child that entered the wave. Child 940 entered; its commit reached the base before the
# landing, so the rebase drops it and $issues does not carry it. It is not missing from the wave, so it is not requeued.
W20=wave/run-20; start2 run-20 >/dev/null
child 940 g940 "origin/$W20"; en2 run-20 940 >/dev/null
child 941 g941 "origin/$W20"; en2 run-20 941 >/dev/null
rm -rf "$T/dup20"; $G clone -q "$T/origin.git" "$T/dup20" 2>/dev/null
(cd "$T/dup20" && $G checkout -q dev && $G fetch -q "$T/c940" gui/940 && GIT_COMMITTER_NAME=other $G -c user.email=a@b -c user.name=t cherry-pick FETCH_HEAD >/dev/null 2>&1 && $G push -q origin HEAD:dev)
mkdir -p "$T/co/runs/run-20"; printf '%s\n%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #940 work"}}' \
  '{"event": "spawn_accepted", "child": "b", "definition": {"prompt": "GitHub issue #941 work"}}' > "$T/co/runs/run-20/record.jsonl"
printf '%s\n' "$(( $(date +%s) - 7201 ))" > "$T/st/auto/wave-start-run-20"
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/landed.tsv"
r=$(land2 run-20 2>&1); rc=$?
[ $rc = 0 ] && ok "Z6: the capped wave lands (exit 0)" || ko "Z6: exit $rc: '$r'"
/usr/bin/grep -qx 'o 3 Todo 940' "$T/board.log" && ko "Z6: a child that entered the wave was requeued as missing: '$r'" || ok "Z6: a child that entered the wave is not requeued"
[ "$(/usr/bin/grep -c 'issues/941 -f state=closed' "$T/gh.log")" = 1 ] && ok "Z6: the sibling the wave carries closes" || ko "Z6: closes for #941: '$(cat "$T/gh.log")'"

# Z7 (#872): a squashed RED+fix commit delivers its Refs. Only a commit whose SUBJECT starts with "TDD RED" is skipped; a body
# line that names "TDD RED" (prepush needs it) does not hide the `Refs #N` of its commit.
W21=wave/run-21; start2 run-21 >/dev/null
rm -rf "$T/c895"; $G clone -q "$T/origin.git" "$T/c895" 2>/dev/null
(cd "$T/c895" && $G checkout -q -b gui/895 "origin/$W21" && echo r895 > g895 && $G add g895 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 895" -m "TDD RED: test_x
Refs #895" && echo r896 > g896 && $G add g896 && $G -c user.email=a@b -c user.name=t commit -q -m "TDD RED: test_y" -m "Refs #896")
en2 run-21 895 >/dev/null; mkdir -p "$T/co/runs/run-21"
printf '%s\n%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #895 work"}}' \
  '{"event": "spawn_accepted", "child": "b", "definition": {"prompt": "GitHub issue #896 work"}}' > "$T/co/runs/run-21/record.jsonl"
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/landed.tsv"
r=$(land2 run-21 2>&1); rc=$?
[ "$(/usr/bin/grep -c 'issues/895 -f state=closed' "$T/gh.log")" = 1 ] && ok "Z7: a squashed RED+fix commit closes its Refs #895 once" || ko "Z7: closes for #895: '$(cat "$T/gh.log")' (exit $rc: '$r')"
/usr/bin/grep -q 'issues/896 -f state=closed' "$T/gh.log" && ko "Z7: a commit whose subject is TDD RED closed #896" || ok "Z7: a commit whose subject is TDD RED does not close #896"

# W1290 (#1290): a landing that runs more than one gate round (a red tip, the child that broke it leaves, the tip again) marks each
# round on its held lock (locks/single-wland/round), so the held alert reads the last round's start as progress. The tip gate
# records whether the mark is there when it runs, then clears it: a mark the second round did not set reads as absent.
W93=wave/run-93; start2 run-93 >/dev/null; child 930 g930 "origin/$W93"; en2 run-93 930 >/dev/null; child 931 g931 "origin/$W93"; en2 run-93 931 >/dev/null
mkdir -p "$T/st/locks/single-wland"; rm -f "$T/st/rounds.log"; printf 'run-93\n' > "$T/st/auto/disp-931"
r=$(SG_WAVE_TIP_GATES='[ -e "$SG_STATE/locks/single-wland/round" ] && echo marked >> "$SG_STATE/rounds.log"; rm -f "$SG_STATE/locks/single-wland/round"; ! /usr/bin/git log --format=%s origin/dev..HEAD | /usr/bin/grep -q "work 931"' SG_WAVE_CHILD_GATES='[ "$SG_WAVE_CHILD" = 931 ] && exit 1; exit 0' land2 run-93); rc=$?
[ "$(/usr/bin/grep -c '^marked$' "$T/st/rounds.log" 2>/dev/null)" = 2 ] && ok "W1290: each gate round of the landing marks its held lock" || ko "W1290: marks at the tip gate: '$(cat "$T/st/rounds.log" 2>/dev/null)' (exit $rc: '$r')"
[ $rc = 0 ] && ok "W1290: the two-round landing lands the wave without the child that broke it" || ko "W1290: exit $rc: '$r'"
rm -rf "$T/st/locks/single-wland"

rm -rf "$T"; [ $fail = 0 ] && { printf 'TDD GREEN\n'; exit 0; } || { printf 'TDD RED\n'; exit $fail; }
