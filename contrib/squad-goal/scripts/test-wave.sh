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
# gate.sh exports GATE_REUSE_FILE to the command it runs (its --run), so this suite run under a gate inherits it: a memo hit of
# an entry then marks the outer gate's row instead of writing its own reuse row, and the row this suite reads is missing (#1798).
unset GATE_REUSE_FILE
# The clones and the wave's rebase replay commits, and a rebase needs a committer. A runner has none set and cannot
# auto-detect one, so the suite names its own identity and never reads the machine's (#1062).
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=a@b GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=a@b
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/co" "$T/bin"
cp "$d/wave.sh" "$d/issue_ledger.py" "$d/ghread.py" "$d/wavepause.py" "$d/child_issues.py" "$d/address.sh" "$d/gate.sh" "$d/caps.sh" "$d/serial.sh" "$d/ghcall.sh" "$d/base-battery.sh" "$d/basecheck.sh" "$d/fast-gate.sh" "$d/quality-reading.py" "$d/siblings.py" "$d/cluster.py" "$d/autohunt.sh" "$d/revformat.py" "$T/s/" 2>/dev/null
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps   # a gate takes gate slots (#567): this repository's squad.caps and a throwaway budget
# A capped run here is an ended run: the retirement an age-cap requeue waits for (#1970) is test-wave-retire.sh's, and here
# a stub proves the end at once, so no process table is read and no process is signalled.
export SG_RETIRE_CMD=/usr/bin/true
{
  printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s BOARD=%s/board.sh\n' "$T/st" "$T/co" "$T"
  printf ': "${SG_LAND_REMOTE:=%s/origin.git}"; export SG_LAND_REMOTE\n' "$T"   # a case may set its own remote (K3: one that fails on the network)
  printf ': "${SG_WAVE_TIP_GATES:=true}"; export SG_WAVE_TIP_GATES\n'   # the tip's check (#377): `true` unless a case replaces it
  printf ': "${SG_WAVE_ENTRY_GATE:=on}"; export SG_WAVE_ENTRY_GATE\n'   # the entry cases prove the entry gate, so it is on here (#1802); Z10 names its own
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
printf '#!/bin/bash\necho "$*" >> "%s/gh.log"\nfor a in "$@"; do case "$a" in body=@*) cat "${a#body=@}" >> "%s/gh-bodies.log";; esac; done\ncase "$*" in *"issues/"*) echo "{\\"number\\":1,\\"state\\":\\"closed\\"}";; *"/actions/runs/"*"/jobs"*) echo "{\\"total_count\\":1,\\"jobs\\":[{\\"conclusion\\":\\"failure\\",\\"steps\\":[{\\"name\\":\\"run\\"}]}]}";; *"/check-runs"*) if [ -n "${RED_BASE:-}" ]; then echo "{\\"check_runs\\":[{\\"id\\":1,\\"name\\":\\"check (3.11)\\",\\"status\\":\\"completed\\",\\"conclusion\\":\\"failure\\",\\"details_url\\":\\"https://github.com/x/y/actions/runs/7801/job/1\\"}]}"; else echo "{}"; fi;; *) echo "{}";; esac\n' "$T" "$T" > "$T/bin/gh"
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
printf '#!/bin/bash\necho "$*" >> "%s/gh.log"\nfor a in "$@"; do case "$a" in body=@*) cat "${a#body=@}" >> "%s/gh-bodies.log";; esac; done\ncase "$*" in *"issues/"*) echo "{\\"number\\":1,\\"state\\":\\"closed\\"}";; *) echo "{}";; esac\n' "$T" "$T" > "$T/bin/gh"; chmod +x "$T/bin/gh"
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
case "$r1" in "WAVE CONFLICT $W7: the rebase on dev conflicts at "*"; #70 kept on fix/70-land-run-7 for one rebase round"*) ok "K: the conflict is logged once, naming the commit and the issue";; *) ko "K: no conflict line: '$r1'";; esac
[ $rc1 = 1 ] && ok "K: the conflicting landing does not land (exit 1)" || ko "K: exit $rc1"
[ -e "$T/st/auto/wave-run-7" ] && ko "K: the wave marker is kept, so the loop retries the landing" || ok "K: the wave marker goes, so the loop stops retrying"
/usr/bin/grep -qx "o 3 Todo 70" "$T/board.log" && ko "K: #70 went back to Todo, so its work is rebuilt (#1500)" || ok "K: #70 stays put: its work is kept for one rebase round (#1500)"
case "$(wfact 70 run-7)" in "refused the rebase on dev conflicts at"*) ok "K: the conflict is a fact in the issue's ledger";; *) ko "K: ledger fact of #70: '$(wfact 70 run-7)'";; esac
$O rev-parse -q --verify "refs/heads/$W7" >/dev/null && ok "K: the wave branch keeps the work" || ko "K: the wave branch is gone"
[ "$($O rev-parse dev)" = "$before" ] && ok "K: dev is not moved by a conflicting landing" || ko "K: dev moved"
/usr/bin/grep -q 'labels\[\]=frozen:owner' "$T/gh.log" && ko "K: a conflict below the cap froze the issue" || ok "K: a conflict below the cap freezes nothing"
case "$r1" in *"WAVE-ERROR"*|*"WAVE NEEDS-HUMAN"*) ko "K: the stuck path was taken: '$r1'";; *) ok "K: the stuck path is not taken";; esac

# KC (#1061, #1500): two waves of one issue, cut on one base, conflict with dev on the same path. Each keeps its own wave head for
# one rebase round: neither requeues the child and neither files a bug, because a kept branch is not a rebuild.
start2 run-k8 >/dev/null; start2 run-k10 >/dev/null
child 71 cf2 origin/dev; en2 run-k8 71 >/dev/null
child 71 cf2 origin/dev; en2 run-k10 71 >/dev/null
rm -rf "$T/w4"; $G clone -q "$T/origin.git" "$T/w4" 2>/dev/null
(cd "$T/w4" && $G checkout -q dev && echo dev > cf2 && $G add cf2 && $G -c user.email=a@b -c user.name=t commit -q -m "dev moves cf2" && $G push -q origin HEAD:dev)
: > "$T/gh.log"; rm -f "$T"/st/cq/*71-*
rk8=$(land2 run-k8); rk10=$(land2 run-k10)
case "$rk8$rk10" in *"WAVE REQUEUE #71"*) ko "KC: a landing conflict requeued #71: '$rk8$rk10'";; *) ok "KC: neither conflict requeues #71";; esac
[ -s "$T/st/auto/kept-fix_71-land-run-k8" ] && [ -s "$T/st/auto/kept-fix_71-land-run-k10" ] && ok "KC: each wave keeps its own landing branch" || ko "KC: no kept marker for #71"
/usr/bin/grep -q 'labels\[\]=bug' "$T/gh.log" && ko "KC: a bug was filed for a first round" || ok "KC: no bug for a first round on each wave"

nokeep() { local c; c=$($O -c user.email=a@b -c user.name=t commit-tree "$($O mktree </dev/null)" -m "unrelated" 2>/dev/null) && $O update-ref "refs/heads/fix/$1-land-$2" "$c"; }
# The requeue path stays for a wave that cannot be kept (#1500): the keep push is refused because fix/<n>-land-<run> holds an
# unrelated commit. KC, KR, KD and K3 run on such a wave, so the same-paths rule (#1343) and the requeue cap (#844) keep their guards.
# KB (#1061): two waves of one issue, cut on one base, conflict with dev on the same path. The first conflict re-dispatches the
# child once, with the path named in its request and a rework label on its card; the second identical conflict files one loop
# bug and does not requeue the child again.
start2 run-kb8 >/dev/null; start2 run-kb10 >/dev/null
child 76 cf2b origin/dev; en2 run-kb8 76 >/dev/null
child 76 cf2b origin/dev; en2 run-kb10 76 >/dev/null
rm -rf "$T/w4b"; $G clone -q "$T/origin.git" "$T/w4b" 2>/dev/null
(cd "$T/w4b" && $G checkout -q dev && echo dev > cf2b && $G add cf2b && $G -c user.email=a@b -c user.name=t commit -q -m "dev moves cf2b" && $G push -q origin HEAD:dev)
: > "$T/gh.log"; rm -f "$T"/st/cq/*76-*
nokeep 76 run-kb8; nokeep 76 run-kb10; rk8=$(land2 run-kb8); rk10=$(land2 run-kb10)
case "$rk8" in *"WAVE REQUEUE #76"*) ok "KB: the first conflict re-dispatches its child";; *) ko "KB: no requeue of #76: '$rk8'";; esac
cat "$T"/st/cq/*76-* 2>/dev/null | /usr/bin/grep -q cf2b && ok "KB: the child's next request names the conflicting path" || ko "KB: the request names no path: '$(cat "$T"/st/cq/*76-* 2>/dev/null)'"
/usr/bin/grep -q 'issues/76/labels.*labels\[\]=rework' "$T/gh.log" && ok "KB: the card of #76 carries rework" || ko "KB: no rework label for #76"
case "$rk10" in *"WAVE REQUEUE #76"*) ko "KB: the repeated conflict requeued #76 again: '$rk10'";; *) ok "KB: the repeated conflict does not requeue #76";; esac
[ "$(/usr/bin/grep -c 'labels\[\]=bug' "$T/gh.log")" = 1 ] && ok "KB: the repeated conflict files one bug" || ko "KB: $(/usr/bin/grep -c 'labels\[\]=bug' "$T/gh.log") bugs filed"

# KR (#1343): the base moves with every landing, so the conflict key is the conflicting paths, not the base sha. The same path
# conflicting again at a new base is a repeat (one loop bug, no requeue); a different path is a first again (a requeue).
start2 run-kr1 >/dev/null; start2 run-kr2 >/dev/null
child 74 cfr origin/dev; en2 run-kr1 74 >/dev/null
child 74 cfr origin/dev; en2 run-kr2 74 >/dev/null
rm -rf "$T/w5"; $G clone -q "$T/origin.git" "$T/w5" 2>/dev/null
(cd "$T/w5" && $G checkout -q dev && echo dev1 > cfr && $G add cfr && $G -c user.email=a@b -c user.name=t commit -q -m "dev moves cfr" && $G push -q origin HEAD:dev)
: > "$T/gh.log"; rm -f "$T"/st/cq/*74-*
nokeep 74 run-kr1; kr1=$(land2 run-kr1)
(cd "$T/w5" && echo dev2 > cfr && $G add cfr && $G -c user.email=a@b -c user.name=t commit -q -m "dev moves cfr again" && $G push -q origin HEAD:dev)
nokeep 74 run-kr2; kr2=$(land2 run-kr2)
case "$kr1" in *"WAVE REQUEUE #74"*) ok "KR: the first conflict of #74 re-dispatches it";; *) ko "KR: no requeue of #74: '$kr1'";; esac
case "$kr2" in *"WAVE REQUEUE #74"*) ko "KR: the second conflict on the same path at a new base requeued #74: '$kr2'";; *) ok "KR: the second conflict on the same path at a new base does not requeue #74";; esac
case "$kr2" in *"WAVE-BUG #74"*) ok "KR: the second conflict on the same path files one loop bug";; *) ko "KR: no loop bug for #74: '$kr2'";; esac
[ "$(/usr/bin/grep -c 'labels\[\]=bug' "$T/gh.log")" = 1 ] && ok "KR: one bug issue is filed" || ko "KR: $(/usr/bin/grep -c 'labels\[\]=bug' "$T/gh.log") bugs filed"
/usr/bin/grep 'labels\[\]=bug' "$T/gh.log" | /usr/bin/grep -q 'labels\[\]=frozen:not-startable.*labels\[\]=loop-bug' && ok "KR: the loop bug goes through the one filer, held until its birth Priority reads back (#1978)" || ko "KR: the loop bug skips the filer: $(/usr/bin/grep 'labels\[\]=bug' "$T/gh.log")"
# KR (#1343): the repeat also stops the issue, so no frozen dispatch picks it again: frozen:owner on its card and the stop marker.
/usr/bin/grep -q 'issues/74/labels.*labels\[\]=frozen:owner' "$T/gh.log" && ok "KR: the repeated conflict freezes #74 (frozen:owner)" || ko "KR: no frozen:owner on #74 after the repeat"
ls "$T"/st/cq/*74-wavestop* >/dev/null 2>&1 && ok "KR: the repeated conflict writes the stop marker of #74" || ko "KR: no stop marker for #74 after the repeat"

start2 run-kd1 >/dev/null; start2 run-kd2 >/dev/null
child 75 cfd origin/dev; en2 run-kd1 75 >/dev/null
child 75 cfe origin/dev; en2 run-kd2 75 >/dev/null
rm -rf "$T/w6"; $G clone -q "$T/origin.git" "$T/w6" 2>/dev/null
(cd "$T/w6" && $G checkout -q dev && echo dev1 > cfd && $G add cfd && $G -c user.email=a@b -c user.name=t commit -q -m "dev moves cfd" && $G push -q origin HEAD:dev)
: > "$T/gh.log"; rm -f "$T"/st/cq/*75-*
nokeep 75 run-kd1; kd1=$(land2 run-kd1)
(cd "$T/w6" && echo dev1 > cfe && $G add cfe && $G -c user.email=a@b -c user.name=t commit -q -m "dev adds cfe" && $G push -q origin HEAD:dev)
nokeep 75 run-kd2; kd2=$(land2 run-kd2)
case "$kd1" in *"WAVE REQUEUE #75"*) ok "KR: a conflict on one path requeues #75";; *) ko "KR: no requeue of #75 on cfd: '$kd1'";; esac
case "$kd2" in *"WAVE REQUEUE #75"*) ok "KR: a conflict on a different path at a new base is a first again";; *) ko "KR: a different path did not requeue #75: '$kd2'";; esac

# K3 (#844): the requeue cap bounds a conflict that keeps coming back: at SG_LOOP_REQUEUE_CAP the issue goes to the owner, as any
# requeue does (requeue, loop_stop). A second wave of #73 conflicts with dev moved again after its entry.
W44=wave/run-44; start2 run-44 >/dev/null; child 73 cf "origin/$W44"; en2 run-44 73 >/dev/null
(cd "$T/w3" && $G pull -q origin dev && echo other2 > cf && $G add cf && $G -c user.email=a@b -c user.name=t commit -q -m "more conflicting work" && $G push -q origin HEAD:dev)
nokeep 73 run-44; r4=$(SG_LOOP_REQUEUE_CAP=1 land2 run-44); rc4=$?
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

# K6 (#1710): a local fetch fails when the machine cannot start git's transport helper, and git prints its own cause:
# `error: cannot fork() for git-upload-pack '<remote>': Resource temporarily unavailable` then `fatal: unable to fork`.
# A loaded machine produces it (a full process table, no memory); the fixture owns its repository and cannot avoid the
# fork, because a local fetch always starts git-upload-pack. It is the network class of #656: a repeat does not change it
# on this pass, so it names git's line, counts no landing try, freezes nothing, keeps the wave, and exits 2. The transport
# is stubbed the way K3 stubs ssh: the stub answers with the two lines git printed on a real fork failure.
W17=wave/run-17; start2 run-17 >/dev/null; child 170 nf17 "origin/$W17"; en2 run-17 170 >/dev/null
mkdir -p "$T/co/runs/run-17"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #170 work"}}' > "$T/co/runs/run-17/record.jsonl"
mkdir -p "$T/bin/stubexec"
printf '#!/bin/bash\necho "error: cannot fork() for git-upload-pack '"'"'$1'"'"': Resource temporarily unavailable" >&2\necho "fatal: unable to fork" >&2\nexit 128\n' > "$T/bin/stubexec/git-upload-pack"
chmod +x "$T/bin/stubexec/git-upload-pack"
noexec() { (cd "$T/co" && GIT_EXEC_PATH="$T/bin/stubexec" bash "$T/s/wave.sh" land "$1" 2>&1); }
: > "$T/gh.log"; before=$($O rev-parse dev); tr=""; nrc=""
for i in 1 2 3; do r=$(noexec run-17); nrc="$nrc$? "; tr="$tr$(tries 170 run-17)"; done
[ "$nrc" = "2 2 2 " ] && ok "K6: a landing whose fetch cannot start its transport exits 2" || ko "K6: exits $nrc: '$r'"
case "$r" in *"WAVE-NETWORK"*"fork"*"lands on a later cycle"*) ok "K6: the untransportable fetch is named on one WAVE-NETWORK line";; *) ko "K6: git's cause is not named: '$r'";; esac
case "$r" in *"WAVE-ERROR"*|*"NEEDS-HUMAN"*) ko "K6: a machine-caused fetch is read as a stuck landing: '$r'";; *) ok "K6: a machine-caused fetch is no stuck landing";; esac
[ -z "$tr" ] && ok "K6: three machine-caused fetches count no landing try" || ko "K6: tries counted: '$tr'"
/usr/bin/grep -q 'frozen:owner' "$T/gh.log" && ko "K6: a machine-caused fetch froze the issue" || ok "K6: a machine-caused fetch freezes nothing"
[ -e "$T/st/auto/wave-run-17" ] && ok "K6: the run keeps its wave marker" || ko "K6: the wave marker is gone"
[ "$($O rev-parse dev)" = "$before" ] && ok "K6: dev does not move while the machine cannot fork" || ko "K6: dev moved"
# A fetch failure no class names still carries git's own line, so the next one is diagnosable from the landing's own words.
printf '#!/bin/bash\necho "fatal: an unnamed transport defect" >&2\nexit 128\n' > "$T/bin/stubexec/git-upload-pack"
r=$(noexec run-17)
case "$r" in *"WAVE-ERROR the fetch of"*"an unnamed transport defect"*) ok "K6: an unnamed fetch failure carries git's own line";; *) ko "K6: git's line is dropped: '$r'";; esac
r=$(land2 run-17); rc=$?
[ $rc = 0 ] && [ "$($O cat-file -e dev:nf17 2>/dev/null && echo yes)" = yes ] && ok "K6: the next landing on a machine that can fork lands the wave" || ko "K6: exit $rc: '$r'"

# K7 (#1710): the machine that refuses git-upload-pack a fork refuses the helper commands the landing's own readers fork
# too. Case L failed once in a fast gate with `WAVE-ERROR the fetch of wave/run-8 from .../origin.git failed` and named no
# cause: netfail and gitwhy read the fetch's stderr through a pipeline (`grep`, `tail`, `head`), so the same full process
# table that refused the transport refused them, the machine cause was dropped exactly when it happens, and the landing
# took the stuck path -- a try counted and, at the cap, the wave marker cleared and the issue frozen. It is the reason the
# cases behind L cascaded. The cause is read with bash's own match and read loop here: no helper to fork, so the machine
# cause is named, no try is counted, the wave stays and nothing is frozen. The transport is stubbed the way K6 stubs it,
# and the helpers that must not be forked fail as they do on a machine with a full process table.
W71=wave/run-k7; start2 run-k7 >/dev/null; child 1710 nf1710 "origin/$W71"; en2 run-k7 1710 >/dev/null
mkdir -p "$T/co/runs/run-k7"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #1710 work"}}' > "$T/co/runs/run-k7/record.jsonl"
printf '#!/bin/bash\necho "error: cannot fork() for git-upload-pack '"'"'$1'"'"': Resource temporarily unavailable" >&2\necho "fatal: unable to fork" >&2\nexit 128\n' > "$T/bin/stubexec/git-upload-pack"
chmod +x "$T/bin/stubexec/git-upload-pack"
mkdir -p "$T/bin/nofork"
for c in grep tail head; do printf '#!/bin/bash\necho "bash: fork: Resource temporarily unavailable" >&2\nexit 128\n' > "$T/bin/nofork/$c"; chmod +x "$T/bin/nofork/$c"; done
nohelper() { (cd "$T/co" && PATH="$T/bin/nofork:$PATH" GIT_EXEC_PATH="$T/bin/stubexec" bash "$T/s/wave.sh" land "$1" 2>&1); }
: > "$T/gh.log"; before=$($O rev-parse dev); tr=""; nhr=""
for i in 1 2 3; do r=$(nohelper run-k7); nhr="$nhr$? "; tr="$tr$(tries 1710 run-k7)"; done
[ "$nhr" = "2 2 2 " ] && ok "K7: a machine-caused fetch exits 2 with the helper commands unable to start" || ko "K7: exits $nhr: '$r'"
case "$r" in *"WAVE-NETWORK"*"fork"*"lands on a later cycle"*) ok "K7: the machine cause is named with no helper command to fork";; *) ko "K7: the cause is dropped when the helpers cannot start: '$r'";; esac
case "$r" in *"WAVE-ERROR"*|*"NEEDS-HUMAN"*) ko "K7: the dropped cause took the stuck path: '$r'";; *) ok "K7: a cause read without a fork is no stuck landing";; esac
[ -z "$tr" ] && ok "K7: no landing try is counted with the helpers down" || ko "K7: tries counted with the helpers down: '$tr'"
/usr/bin/grep -q 'frozen:owner' "$T/gh.log" && ko "K7: the machine cause froze the issue with the helpers down" || ok "K7: nothing is frozen with the helpers down"
[ -e "$T/st/auto/wave-run-k7" ] && ok "K7: the wave marker is kept with the helpers down" || ko "K7: the wave marker is gone with the helpers down"
[ "$($O rev-parse dev)" = "$before" ] && ok "K7: dev does not move with the helpers down" || ko "K7: dev moved"
# A class no name covers still carries git's own first line, read without a helper command.
printf '#!/bin/bash\necho "warning: the remote is a local path" >&2\necho "fatal: an unnamed transport defect" >&2\nexit 128\n' > "$T/bin/stubexec/git-upload-pack"
r=$(nohelper run-k7)
case "$r" in *"WAVE-ERROR the fetch of"*"failed: warning: the remote is a local path"*) ok "K7: git's first line survives with no helper command";; *) ko "K7: git's line is dropped with the helpers down: '$r'";; esac

# K8 (#1710): the fixture owns its repository, and a second writer on it is not one of the causes the issue lists. Each
# suite process makes its own mktemp tree and its bare origin.git inside it, and the remote every landing fetches is that
# tree's own default, never a fixed path a second suite or a second gate would share. Every case writes the repository one
# at a time, and a ref another process pushes takes a lock on its own ref alone: a landing beside such a writer fetches and
# lands, and its fetch names no no-cause shape.
/usr/bin/grep -qF "SG_LAND_REMOTE:=$T/origin.git" "$T/s/env.sh" && ok "K8: the fixture's remote is the suite's own temp tree, never a shared path" || ko "K8: the fixture remote is not the suite's own: '$(/usr/bin/grep -n SG_LAND_REMOTE "$T/s/env.sh" 2>&1)'"
W72=wave/run-k9; start2 run-k9 >/dev/null; child 1711 nf1711 "origin/$W72"; en2 run-k9 1711 >/dev/null
mkdir -p "$T/co/runs/run-k9"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #1711 work"}}' > "$T/co/runs/run-k9/record.jsonl"
rm -rf "$T/w18"; $G clone -q "$T/origin.git" "$T/w18" 2>/dev/null
(cd "$T/w18" && $G checkout -q dev && echo noise > noise && $G add noise && $G -c user.email=a@b -c user.name=t commit -q -m noise 2>/dev/null \
  && for i in $(seq 1 20); do $G push -q origin "HEAD:refs/heads/noise-$i" 2>/dev/null; done) &
wpid=$!
: > "$T/gh.log"; before=$($O rev-parse dev)
r=$(land2 run-k9); rc=$?
wait "$wpid" 2>/dev/null
[ $rc = 0 ] && [ "$($O cat-file -e dev:nf1711 2>/dev/null && echo yes)" = yes ] && ok "K8: a landing fetches and lands beside a second writer on the fixture" || ko "K8: exit $rc beside a second writer: '$r'"
case "$r" in *"the fetch of"*"failed"*) ko "K8: the fetch failed beside a second writer: '$r'";; *) ok "K8: the fetched ref survives a second writer on the fixture";; esac
[ -n "$($O rev-parse -q --verify refs/heads/noise-20)" ] && [ "$($O rev-parse dev)" != "$before" ] && ok "K8: the second writer's ref and the wave's landing both reached the fixture" || ko "K8: the concurrent write did not happen: '$($O for-each-ref --format='%(refname)' | tr '\n' ' ')'"

# W1961 (#1961): a tip check that is red and names no child is a measured red, never the loop's cause. The first red on a
# tree runs the same check once more on the same tree. A green rerun releases the red: the wave lands, and the flaky test is
# filed as one bug, once per cause. A second red on the same tree with the same failing tests runs no third check until the
# tip moves: it is a counted landing try, so SG_WAVE_LAND_TRIES and the owner's alert apply. A moved tip re-arms the check.
# flaky <calls file> <reds>: a tip check that logs each call and is red, with one pytest failure line, on its first <reds> calls.
flaky() { printf '%s\n' "n=\$(( \$(/usr/bin/grep -c . '$1' 2>/dev/null || echo 0) + 1 )); echo call >> '$1'; [ \$n -le $2 ] || exit 0; echo 'FAILED tests/test_flaky.py::test_flaky - assert 0'; exit 1"; }
calls() { /usr/bin/grep -c . "$1" 2>/dev/null || echo 0; }
flakybugs() { /usr/bin/grep -c 'POST repos/x/y/issues -f title=.*test_flaky' "$T/gh.log" 2>/dev/null || echo 0; }
# a: red, then green on the same tree: the wave lands after two gate calls and files one bug with the failure lines.
W161=wave/run-161; start2 run-161 >/dev/null; child 1611 g1611 "origin/$W161"; en2 run-161 1611 >/dev/null
: > "$T/gh.log"; C161=$T/calls-161; rm -f "$C161"
r=$(SG_WAVE_TIP_GATES="$(flaky "$C161" 1)" SG_WAVE_CHILD_GATES=true land2 run-161 2>&1); rc=$?
[ $rc = 0 ] && $O cat-file -e dev:g1611 2>/dev/null && ok "W1961: a red tip that is green on its rerun lands" || ko "W1961: red then green: exit $rc: '$r'"
[ "$(calls "$C161")" = 2 ] && ok "W1961: red then green runs the tip check twice" || ko "W1961: tip check calls: $(calls "$C161")"
case "$r" in *"released by the green run on the same tree"*) ok "W1961: the landing names the green run on the same tree that released the red";; *) ko "W1961: no release record: '$r'";; esac
[ "$(flakybugs)" = 1 ] && ok "W1961: the flake files one bug" || ko "W1961: flaky bugs filed: $(flakybugs): '$(cat "$T/gh.log")'"
/usr/bin/grep -q 'FAILED tests/test_flaky.py::test_flaky - assert 0' "$T/gh-bodies.log" && ok "W1961: the bug carries the failure lines" || ko "W1961: no failure line in the bug: '$(cat "$T/gh-bodies.log" 2>/dev/null)'"
# The same flaky test on another wave is the same cause: no second bug.
W162=wave/run-162; start2 run-162 >/dev/null; child 1621 g1621 "origin/$W162"; en2 run-162 1621 >/dev/null
C162=$T/calls-162; rm -f "$C162"
r=$(SG_WAVE_TIP_GATES="$(flaky "$C162" 1)" SG_WAVE_CHILD_GATES=true land2 run-162 2>&1); rc=$?
[ $rc = 0 ] && [ "$(flakybugs)" = 1 ] && ok "W1961: the same flaky test files no second bug" || ko "W1961: second flake: exit $rc, bugs $(flakybugs): '$r'"
# b: red twice on the same tree with the same failing test: no third call, a named hold, a counted try, and the owner's alert
# at the cap. It is never the loop's cause.
W16=wave/run-16; start2 run-16 >/dev/null; child 160 lg "origin/$W16"; en2 run-16 160 >/dev/null
: > "$T/gh.log"; before=$($O rev-parse dev); C16=$T/calls-16; rm -f "$C16"; lr=""; tr=""; cr=""; all=""
for i in 1 2 3; do r=$(SG_WAVE_TIP_GATES="$(flaky "$C16" 99)" SG_WAVE_CHILD_GATES=true land2 run-16 2>&1); rc=$?; lr="$lr$rc "; tr="$tr$(tries 160 run-16) "; cr="$cr$(calls "$C16") "; all="$all$r"; done
[ "$lr" = "1 1 1 " ] && ok "W1961: a tip red twice on the same tree does not land" || ko "W1961: exits $lr: '$r'"
[ "$cr" = "2 2 2 " ] && ok "W1961: two reds on the same tree run no third check" || ko "W1961: tip check calls per pass: $cr"
case "$all" in *"WAVE-LOOP-CAUSED"*) ko "W1961: a measured red was read as the loop's cause: '$all'";; *) ok "W1961: a measured red is never the loop's cause";; esac
case "$all" in *"red twice on the same tree"*"held until the tip moves"*) ok "W1961: the second red names the hold";; *) ko "W1961: no hold named: '$all'";; esac
[ "$tr" = "1 2 3 " ] && ok "W1961: each held pass counts a landing try" || ko "W1961: tries: '$tr'"
/usr/bin/grep -q 'frozen:owner' "$T/gh.log" && case "$r" in *"WAVE NEEDS-HUMAN"*) true;; *) false;; esac && ok "W1961: the hold reaches the owner at the cap" || ko "W1961: no owner alert: '$r'"
[ "$($O rev-parse dev)" = "$before" ] && ok "W1961: dev does not move on a measured red" || ko "W1961: dev moved"
# c: a moved tip re-arms the check: the next pass runs it again.
W163=wave/run-163; start2 run-163 >/dev/null; child 1631 g1631 "origin/$W163"; en2 run-163 1631 >/dev/null
C163=$T/calls-163; rm -f "$C163"
for i in 1 2; do r=$(SG_WAVE_LAND_TRIES=9 SG_WAVE_TIP_GATES="$(flaky "$C163" 99)" SG_WAVE_CHILD_GATES=true land2 run-163 2>&1); done
c0=$(calls "$C163")
rm -rf "$T/w163"; $G clone -q "$T/origin.git" "$T/w163" 2>/dev/null
(cd "$T/w163" && $G checkout -q dev && echo m163 > m163 && $G add m163 && $G -c user.email=a@b -c user.name=t commit -q -m "dev moves under run-163" && $G push -q origin HEAD:dev)
r=$(SG_WAVE_LAND_TRIES=9 SG_WAVE_TIP_GATES="$(flaky "$C163" 99)" SG_WAVE_CHILD_GATES=true land2 run-163 2>&1); rc=$?
[ "$c0" = 2 ] && [ "$(calls "$C163")" = 4 ] && ok "W1961: a moved tip re-arms the check" || ko "W1961: calls before the move $c0, after $(calls "$C163"): '$r'"

# K5 (#1684): the words a full gate budget really carries are the loop's cause. The capacity answer names "caps.gates is full",
# and read as the child's defect it counted a failed dispatch and froze the issue after three, though the child did its work.
k5=0; for why in "fast-gate.sh exited 73 on two runs: caps.gates is full (6 slots), held by gate-88923 (live)" \
                "fast_gate_not_run: fast-gate.sh exited 73 (caps.gates full, 6 slots, held by gate-88923)"; do
  r=$((cd "$T/co" && bash "$T/s/wave.sh" fail "$((170+k5))" "run-caps$k5" "$why") 2>&1); k5=$((k5+1))
  case "$r" in *"loop-caused"*) ok "K5: a full gate budget is the loop's cause, not the issue's";; *) ko "K5: capacity read as the issue's own failure: '$r'";; esac
done
r=$((cd "$T/co" && bash "$T/s/wave.sh" fail 180 run-realred "pytest the base battery failed at tests/test_caps.py::test_limit - wrong limit for caps.gates") 2>&1)
case "$r" in *"loop-caused"*) ko "K5: a real red that names the budget read as the loop's: '$r'";; *) ok "K5: a real red that names caps.gates stays the issue's own";; esac

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
rm -f "$T/hook40"; mkdir -p "$T/origin.git/refs/heads/wave"; printf '%s\n' "$SIB" > "$T/origin.git/refs/heads/$W40"; exit 1
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
rm -f "$T/hook40"; mkdir -p "$T/origin.git/refs/heads/wave"; printf '%s\n' "$SIB" > "$T/origin.git/refs/heads/$W40"; exit 1
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
# AE (#1802, spec 1797): a wave whose tip check is unproven never lands. The entry runs no battery by default, so the tip
# check is the one proof of the wave's children, and an unproven one holds the wave until a pass or a fail. No child is
# blamed, no try is counted, and no card is delivered.
printf '#!/bin/bash\nprintf "unproven\\tsaturation: the battery crashed twice\\tunknown\\truff\\n" > "$GATE_OUTCOME_FILE"\nexit 4\n' > "$T/unproven-tip.sh"
start2 run-51 >/dev/null; child 510 g510 "origin/wave/run-51"; en2 run-51 510 >/dev/null; : > "$T/board.log"; : > "$T/st/gate-history"; rm -rf "$T/st/obligations" "$T/st/proof-card"
r=$(SG_WAVE_TIP_GATES="bash '$T/s/gate.sh' bash '$T/unproven-tip.sh'" SG_WAVE_CHILD_GATES='exit 1' land2 run-51 2>&1); rc=$?
[ $rc != 0 ] && ! $O cat-file -e dev:g510 2>/dev/null && case "$r" in *"WAVE WAIT"*unproven*) true;; *) false;; esac && ok "AE: an unproven tip check holds the wave and says so" || ko "AE: tip exit $rc: '$r'"
case "$r" in *"WAVE CHECK RED"*|*REQUEUE*) ko "AE: an unproven tip check blamed a child: '$r'";; *) ok "AE: an unproven tip check blames no child";; esac
[ -z "$(tries 510 run-51)" ] && ok "AE: an unproven tip check counts no landing try" || ko "AE: tries $(tries 510 run-51)"
[ -z "$(/usr/bin/grep -- '--proof\|Closed' "$T/board.log" 2>/dev/null)" ] && ok "AE: a held wave delivers no card" || ko "AE: a held wave wrote a card: $(cat "$T/board.log" 2>/dev/null)"
[ "$(awk -F'\t' '$8 == "unproven"' "$T/st/gate-history" 2>/dev/null | wc -l | tr -d ' ')" = 1 ] \
  && ok "AE: the gate record names the tip's unproven outcome" || ko "AE: gate history: '$(cat "$T/st/gate-history" 2>&1)'"

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
[ $rc = 1 ] && [ "$(/usr/bin/grep -c '^issue ' "$T/st/auto/kept-fix_150-land-run-15")" = 1 ] && ! /usr/bin/grep -q 'issue 151' "$T/st/auto/kept-fix_150-land-run-15" && ok "S776: the conflicting landing keeps the dispatched issue alone in its marker (#1500)" || ko "S776: #150 marker: rc $rc '$r' '$(cat "$T/st/auto/kept-fix_150-land-run-15" 2>&1)'"
/usr/bin/grep -q 'issues/151/' "$T/gh.log" && ko "S776: #151, outside the dispatch set, was written: '$(cat "$T/gh.log")'" || ok "S776: a Refs outside the dispatch set gets no label"
/usr/bin/grep -q ' 151$' "$T/board.log" && ko "S776: #151's card moved" || ok "S776: the card of an issue outside the dispatch set does not move"
[ -z "$(/bin/ls "$T/st/cq" | /usr/bin/grep '#151-')" ] && ok "S776: no comment is queued for #151" || ko "S776: a comment is queued for #151"
[ -z "$(tries 151 run-15)" ] && ok "S776: no landing try is counted for #151" || ko "S776: tries of #151: $(tries 151 run-15)"
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c 'WAVE-NOTE.*#151.*outside.*dispatch')" = 1 ] && ok "S776: the outside Refs is named in one log line" || ko "S776: no single WAVE-NOTE for #151: '$r'"
awk -F '\t' '$2 == "run-15" && $3 == "151"' "$T/st/wave-outside.tsv" 2>/dev/null | /usr/bin/grep -q . && ok "S776: the outside Refs is recorded in wave-outside.tsv" || ko "S776: no wave-outside.tsv row: '$(cat "$T/st/wave-outside.tsv" 2>&1)'"

# S777 (#1340): a landing whose push to the base is refused twice takes the stuck path: its issue goes back to Todo as
# no-landing (board.py refuses any other automated backward move from Code Complete). The origin refuses every push to dev.
W1340=wave/run-1340; start2 run-1340 >/dev/null; child 1340 g1340 "origin/$W1340"; en2 run-1340 1340 >/dev/null
cp "$T/origin.git/hooks/pre-receive" "$T/pre-receive.keep"; printf '#!/bin/bash\nprintf "error: refused by the test remote\\nhint: the operator reads the last line\\nfatal: the test remote refuses every push to dev\\n" >&2\nexit 1\n' > "$T/origin.git/hooks/pre-receive"
: > "$T/gh.log"; : > "$T/board.log"; : > "$T/board-reason.log"; rm -f "$T/st/cq/"*1340*
r=$(SG_WAVE_LAND_TRIES=1 land2 run-1340); rc=$?
cp "$T/pre-receive.keep" "$T/origin.git/hooks/pre-receive"
# The reason quotes the push's own LAST line, as it did before (#1710): the reader that picks it forks no helper, but it still
# picks the last one, so a pasted failure reads the same.
case "$r" in *"WAVE-ERROR the push of wave/run-1340 to dev was refused: error: failed to push some refs to '"*) true;; *) false;; esac && [ $rc = 1 ] && ok "S777: a push refused twice takes the stuck path, quoting the push's own last line" || ko "S777: exit $rc: '$r'"
/usr/bin/grep -qx "no-landing o 3 Todo 1340" "$T/board-reason.log" && ok "S777: the stuck path moves #1340 back to Todo as no-landing (#1340)" || ko "S777: #1340 move reason: '$(cat "$T/board-reason.log")', board '$(cat "$T/board.log")'"

# (#646) The wave tip's check is the base battery over origin/$SG_BASE, and its pytest runs over the selection. The check's command
# is built from the shared definition and run in a clone of the remote, as wave.sh runs it; a stub python logs each call's argv.
# (#1873, #1865) The count follows the caller: wave.sh runs its tip check with SG_GATE_CALLER=tip, so the gate keeps the full
# count; a gate that names no caller reads as a child's own and takes one slot. The fixture names the caller the wave names,
# else it proves a count the landing never runs.
printf '#!/bin/bash\necho "$*" >> "%s/argv.log"\ncase "$*" in *squad.testselect*) cat "%s/select.out" 2>/dev/null;; esac\nexit 0\n' "$T" "$T" > "$T/selpy"; chmod +x "$T/selpy"
tipcmd=$(SG_GATE_WORKERS=3 sg_base_gate "origin/dev" "$T/selpy" wave-tip 2>/dev/null)
rm -rf "$T/tipclone"; $G clone -q "$T/origin.git" "$T/tipclone" 2>/dev/null
printf 'tests/test_a.py\n' > "$T/select.out"; rm -f "$T/argv.log"
r=$(cd "$T/tipclone" && SG_GATE_CALLER=tip SG_GATE_WORKERS=3 SG_STATE="$T/st" bash -c "$tipcmd" 2>&1); rc=$?
[ $rc = 0 ] && /usr/bin/grep -qx -- "-m pytest -p no:deepeval -n 3 -m not heavy tests/test_a.py" "$T/argv.log" && ok "the wave tip check passes the selection to pytest" || ko "tip check argv: rc $rc: '$(cat "$T/argv.log" 2>&1)' $r"
rm -rf "$T/tipclone2"; $G clone -q "$T/origin.git" "$T/tipclone2" 2>/dev/null
printf '#!/bin/bash\necho "$*" >> "%s/argv2.log"\ncase "$*" in *squad.testselect*) cat "%s/select.out" 2>/dev/null;; esac\nexit 0\n' "$T" "$T" > "$T/selpy2"; chmod +x "$T/selpy2"
tipcmd2=$(SG_GATE_WORKERS=3 sg_base_gate "origin/dev" "$T/selpy2" wave-tip 2>/dev/null)
( cd "$T/tipclone2" && SG_GATE_WORKERS=3 SG_STATE="$T/st" bash -c "$tipcmd2" ) >/dev/null 2>&1
/usr/bin/grep -qx -- "-m pytest -p no:deepeval -n 1 -m not heavy tests/test_a.py" "$T/argv2.log" && ok "a tip check that names no caller reads as a child's and takes one slot (#1865)" || ko "no-caller tip check argv: '$(cat "$T/argv2.log" 2>&1)'"
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
# A run that ended keeps its record: without it `orphan_child` finds no issue of the run and the capped case never runs (#1835).
printf '%s\n%s\n' '{"event": "run_start"}' '{"event": "verdict", "verdict": "completed", "reasons": []}' > "$T/co/runs/run-836d/record.jsonl"
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
# Y6 (#1833): a wave already in the base keeps refusing its landing every cycle. The run's wave landed in an earlier cycle: its
# tip is dev's now, and one of its children entered it. One child of the run never entered, so its commit sits on the run's own
# branch only. The wave landed, so the pass writes its books and drops its markers as a kept landing branch does (#1826): the
# entered child is delivered, the child that never entered goes back to the queue, and no WAVE REFUSE line holds the run. The
# refusal for an unlanded wave with such a commit stays (Y2).
W29=wave/run-1833; start2 run-1833 >/dev/null
mkdir -p "$T/co/runs/run-1833"
printf '%s\n' 'CHILD — GitHub issue(s) 1752, 1753 (bug; one root cause, one branch):' > "$T/co/runs/run-1833/request.txt"
printf '%s\n%s\n' '{"event": "run_start"}' '{"event": "verdict", "verdict": "completed", "reasons": []}' > "$T/co/runs/run-1833/record.jsonl"
child 1752 g1752 "origin/$W29"; en2 run-1833 1752 >/dev/null
# The wave lands: dev carries its tip, and the wave branch stays behind (an earlier landing left it, its deletion unmet).
$O update-ref refs/heads/dev "$($G -C "$T/c1752" rev-parse HEAD)"
# The child that never entered: a commit of this run naming #1753, on a local branch of the checkout only.
x9=$(mktemp -d); $G clone -q "$T/origin.git" "$x9/c" 2>/dev/null
(cd "$x9/c" && $G checkout -q -b tmp dev && echo fix > f1753 && $G add f1753 && $G -c user.email=a@b -c user.name=t commit -q -m "fix 1753" -m "Refs #1753")
X9SHA=$($G -C "$x9/c" rev-parse HEAD); $G -C "$T/co" fetch -q "$x9/c" HEAD:refs/heads/squad/run/run-1833 2>/dev/null
: > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/auto/orphan-refused-run-1833"
r=$(land2 run-1833 2>&1); rc=$?
[ $rc = 0 ] && ok "Y6: a wave whose tip the base already carries lands (exit 0)" || ko "Y6: exit $rc: '$r'"
case "$r" in *"WAVE REFUSE"*) ko "Y6: the landed wave is still refused: '$r'";; *) ok "Y6: no WAVE REFUSE line";; esac
[ "$(printf '%s\n' "$r" | /usr/bin/grep -c '^WAVE .* already landed')" = 1 ] && ok "Y6: the landing names the wave as already landed, once" || ko "Y6: no single already-landed line: '$r'"
$O rev-parse -q --verify "refs/heads/$W29" >/dev/null && ko "Y6: the landed wave branch is kept" || ok "Y6: the landed wave branch goes"
[ ! -e "$T/st/auto/wave-run-1833" ] && ok "Y6: the run's wave marker goes" || ko "Y6: the run's wave marker is kept: '$(cat "$T/st/auto/wave-run-1833" 2>/dev/null)'"
[ ! -e "$T/st/auto/orphan-refused-run-1833" ] && ok "Y6: no orphan-refused marker is written" || ko "Y6: an orphan-refused marker was written"
/usr/bin/grep -q 'issues/1752 -f state=closed' "$T/gh.log" && ok "Y6: the child that entered the landed wave is delivered" || ko "Y6: #1752 is not closed: $(cat "$T/gh.log")"
/usr/bin/grep -q 'issues/1753 -f state=closed' "$T/gh.log" && ko "Y6: the child that never entered is closed" || ok "Y6: the child that never entered is not delivered"
case "$r" in *"WAVE REQUEUE #1753"*landed*) ok "Y6: the child that never entered goes back to the queue, its wave landed without it";; *) ko "Y6: #1753 is not requeued: '$r'";; esac
/usr/bin/grep -qx 'o 3 Todo 1753' "$T/board.log" && ok "Y6: the card of the requeued issue reads Todo" || ko "Y6: no Todo card for #1753: $(cat "$T/board.log" 2>/dev/null)"
[ -z "$(tries 1753 run-1833)" ] && ok "Y6: the requeue counts no landing try" || ko "Y6: tries of #1753: $(tries 1753 run-1833)"
rm -rf "$x9"

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
Refs #895" && mkdir -p tests && echo r896 > tests/test_896.py && $G add tests/test_896.py && $G -c user.email=a@b -c user.name=t commit -q -m "TDD RED: test_y" -m "Refs #896")
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

# W1409 (#1409): a Python-only red at the wave tip names the child whose own pytest delta fails. The child's pytest runs
# through gate.sh: under the gate lock (SG_SERIAL_HELD names gate) and holding gate slots (SQUAD_GATE_WORKERS), never beside them.
# SG_WAVE_CHILD_PYTEST replaces the delta command, as SG_WAVE_CHILD_GATES replaces the shell suites; the default child shell suites pass here.
W96=wave/run-96; start2 run-96 >/dev/null; child 1940 g1940 "origin/$W96"; en2 run-96 1940 >/dev/null; child 1941 g1941 "origin/$W96"; en2 run-96 1941 >/dev/null
printf 'run-96\n' > "$T/st/auto/disp-1941"; rm -f "$T/st/py.log"; : > "$T/gh.log"; : > "$T/board.log"
r=$(SG_WAVE_TIP_GATES='! /usr/bin/git log --format=%s origin/dev..HEAD | /usr/bin/grep -q "work 1941"' SG_WAVE_CHILD_PYTEST='echo "$SG_WAVE_CHILD held=${SG_SERIAL_HELD:-none} workers=${SQUAD_GATE_WORKERS:-none}" >> "$SG_STATE/py.log"; [ "$SG_WAVE_CHILD" = 1941 ] && exit 1; exit 0' land2 run-96 2>&1); rc=$?
[ $rc = 0 ] && ok "W1409: the wave without the child whose pytest delta fails lands" || ko "W1409: exit $rc: '$r'"
case "$r" in *"WAVE CHECK RED"*"#1941"*) ok "W1409: the red names the child whose pytest delta fails";; *) ko "W1409: no child named: '$r'";; esac
[ "$(head -1 "$T/st/py.log" 2>/dev/null | cut -d' ' -f1)" = 1941 ] && ok "W1409: the newest child's delta runs first" || ko "W1409: pytest order: '$(cat "$T/st/py.log" 2>/dev/null)'"
/usr/bin/grep -q '^1941 held=gate workers=[1-9]' "$T/st/py.log" 2>/dev/null && ok "W1409: the child's pytest runs under the gate lock with gate slots" || ko "W1409: lock/slots not held: '$(cat "$T/st/py.log" 2>/dev/null)'"
$O cat-file -e dev:g1941 2>/dev/null && ko "W1409: the failing child landed" || ok "W1409: the failing child did not land"
$O cat-file -e dev:g1940 2>/dev/null && ok "W1409: the other child landed" || ko "W1409: the other child did not land"
[ "$(/usr/bin/grep -c '^o 3 Todo 1941$' "$T/board.log")" = 1 ] && ok "W1409: the failing child is requeued once" || ko "W1409: requeue: '$(cat "$T/board.log")'"
# A green tip runs no child pytest.
W95=wave/run-95; start2 run-95 >/dev/null; child 950 g950 "origin/$W95"; en2 run-95 950 >/dev/null
rm -f "$T/st/py.log"
r=$(SG_WAVE_TIP_GATES=true SG_WAVE_CHILD_PYTEST='echo ran >> "$SG_STATE/py.log"; exit 1' land2 run-95 2>&1); rc=$?
[ $rc = 0 ] && [ ! -e "$T/st/py.log" ] && ok "W1409: a green tip runs no child pytest" || ko "W1409: exit $rc, py.log: '$(cat "$T/st/py.log" 2>/dev/null)': '$r'"

# A default-style tip red (a measured fail naming pytest at this HEAD, recorded by gate.sh as fast-gate.sh does) never blames a child whose own pytest passes, and the child
# gate writes no wave-level journal: the child runs with SG_GATE_STATUS=self and no SG_GATE_WAVE.
W97=wave/run-97; start2 run-97 >/dev/null; child 1944 g1944 "origin/$W97"; en2 run-97 1944 >/dev/null; child 1945 g1945 "origin/$W97"; en2 run-97 1945 >/dev/null
printf 'run-97\n' > "$T/st/auto/disp-1945"; : > "$T/gh.log"; : > "$T/board.log"
printf '%s\n' 'printf "fail\tred\tpytest\n" > "$GATE_OUTCOME_FILE"; exit 1' > "$T/tipred.sh"
r=$(SG_WAVE_TIP_GATES="SERIAL_TRY=1 SG_GATE_KIND=fast bash $T/s/gate.sh bash $T/tipred.sh" SG_WAVE_CHILD_PYTEST='exit 0' land2 run-97 2>&1); rc=$?
case "$r" in *"broke the wave tip"*) ko "W1409: a passing child was named after a tip red: '$r'";; *) ok "W1409: a passing child is not named after a measured tip red";; esac
[ "$(/usr/bin/grep -c '^o 3 Todo 194[45]$' "$T/board.log")" = 0 ] && ok "W1409: no passing child is requeued after a tip red" || ko "W1409: requeue: '$(cat "$T/board.log")'"

# The tip check of the wave second look (#1687, #1700): the fast gate through gate.sh, which writes its own history row for the
# tree it checked -- kind fast, the selection and the scope its battery named (#1700 finding 5). It fails, as a flaky red does.
printf '%s\n' 'printf "selected 2 files\n" > "$GATE_SELECT_FILE"; printf "delta\n" > "$GATE_SCOPE_FILE"; printf "fail\tred\tpytest\n" > "$GATE_OUTCOME_FILE"; exit 1' > "$T/tipred1700.sh"
TIP1700="SERIAL_TRY=1 SG_GATE_KIND=fast bash $T/s/gate.sh bash $T/tipred1700.sh"

# W1687 (#1687, #1700): a tip red on a tree a gate passed before may be the machine's (a timeout under load), so no child is
# blamed on it: the wave waits and the check runs again on a later pass. A second red on that same tip takes the red path.
# The covering pass is the row the child's entry gate wrote before the wave landed, with caller entry; the tip check runs
# through gate.sh and writes its own row, the red one. Both name the same tree and the same suite (#1700 finding 5).
W98=wave/run-98; start2 run-98 >/dev/null; child 1960 g1960 "origin/$W98"; en2 run-98 1960 >/dev/null
printf 'run-98\n' > "$T/st/auto/disp-1960"; : > "$T/gh.log"; : > "$T/board.log"; rm -f "$T/st/auto/tipred-wave_run-98"
# The covering pass is the wave entry's row: the child's own gate ran on this tree before the wave landed, with caller entry,
# over the same kind, selection and scope the tip check names (#1700 finding 5).
T98=$($G -C "$T/origin.git" rev-parse "$W98")
printf '1\tfast\tpass\t%s\t0\tselected 2 files\tdelta\tpass\t-\truff mypy pytest\t-\t2\t-\tentry\n' "$T98" >> "$T/st/gate-history"
r=$(SG_WAVE_TIP_GATES="$TIP1700" SG_WAVE_CHILD_GATES='exit 1' land2 run-98 2>&1); rc=$?
[ $rc != 0 ] && case "$r" in *"WAVE WAIT $W98"*"passed before"*) true;; *) false;; esac && ok "W1687: a tip red on a tree a gate passed holds the wave for a second look" || ko "W1687: first red: exit $rc: '$r'"
case "$r" in *"broke the wave tip"*) ko "W1687: a child was blamed on the first red of a passed tree: '$r'";; *) ok "W1687: no child is blamed on the first red of a passed tree";; esac
[ "$(/usr/bin/grep -c '^o 3 Todo 1960$' "$T/board.log")" = 0 ] && ok "W1687: the child is not requeued on that red" || ko "W1687: requeue: '$(cat "$T/board.log")'"
# W1700 finding 5: the fixture is production's. In production the entry gate writes the covering pass row on the wave tip
# before the check, with caller entry, and the tip check writes its own row, the red one. Neither row is written by the
# other's check.
hn=$(awk -F'\t' -v s="$($G -C "$T/origin.git" rev-parse "$W98")" '$4 == s && $14 == "entry" && $3 == "pass" && $6 == "selected 2 files"' "$T/st/gate-history" | /usr/bin/grep -c .)
[ "$hn" = 1 ] && ok "W1700: the covering pass is the entry gate's row on the wave tip" || ko "W1700: entry pass rows for the wave tip: '$hn' in '$(cat "$T/st/gate-history")'"
hn=$(awk -F'\t' -v s="$($G -C "$T/origin.git" rev-parse "$W98")" '$4 == s && $14 == "tip" && $3 == "fail"' "$T/st/gate-history" | /usr/bin/grep -c .)
[ "$hn" = 1 ] && ok "W1700: the tip check writes its own red row" || ko "W1700: tip red rows for the wave tip: '$hn' in '$(cat "$T/st/gate-history")'"
r=$(SG_WAVE_TIP_GATES="$TIP1700" SG_WAVE_CHILD_GATES='exit 1' land2 run-98 2>&1); rc=$?
case "$r" in *"WAVE CHECK RED"*"#1960"*) ok "W1687: a second red on the same tip names the child, as before";; *) ko "W1687: second red: exit $rc: '$r'";; esac
# After the child left, the tip without it is red too, on a tree with a pass of its own: that red takes the old path in
# the same pass. A second look there would move the one marker to the other tip, and the wave would wait for ever (#1699).
case "$r" in *"WAVE WAIT"*) ko "W1699: a red after a child left opened another second look: '$r'";; *) ok "W1699: a red after a child left opens no second look";; esac
[ "$(cat "$T/st/auto/tipred-wave_run-98" 2>/dev/null)" = "$($G -C "$T/origin.git" rev-parse "$W98" 2>/dev/null)" ] && ok "W1699: the marker stays on the tip that waited" || ko "W1699: the marker moved to another tip: '$(cat "$T/st/auto/tipred-wave_run-98" 2>/dev/null)'"
# A child's own pytest that was bounded (gate.sh exit 4) measured no red: the child is not blamed and the wave waits.
W99=wave/run-99; start2 run-99 >/dev/null; child 1962 g1962 "origin/$W99"; en2 run-99 1962 >/dev/null
printf 'run-99\n' > "$T/st/auto/disp-1962"; : > "$T/gh.log"; : > "$T/board.log"
r=$(SG_WAVE_TIP_GATES=false SG_WAVE_CHILD_PYTEST='echo "gate: bounded: the gate passed its 1s deadline and no check reported a defect"; exit 4' land2 run-99 2>&1); rc=$?
[ $rc != 0 ] && case "$r" in *"WAVE WAIT $W99"*"no verdict"*) true;; *) false;; esac && ok "W1687: a bounded child check holds the wave with no verdict" || ko "W1687: bounded child: exit $rc: '$r'"
case "$r" in *"broke the wave tip"*) ko "W1687: a bounded child check blamed the child: '$r'";; *) ok "W1687: a bounded child check blames no child";; esac
[ "$(/usr/bin/grep -c '^o 3 Todo 1962$' "$T/board.log")" = 0 ] && ok "W1687: the bounded child is not requeued" || ko "W1687: requeue: '$(cat "$T/board.log")'"
# An exit 4 with no bounded line is the command's own (pytest exits 4 on a conftest import error): the child is red (#1699).
W97=wave/run-97; start2 run-97 >/dev/null; child 1964 g1964 "origin/$W97"; en2 run-97 1964 >/dev/null
printf 'run-97\n' > "$T/st/auto/disp-1964"; : > "$T/gh.log"; : > "$T/board.log"
r=$(SG_WAVE_TIP_GATES=false SG_WAVE_CHILD_PYTEST='exit 4' land2 run-97 2>&1); rc=$?
case "$r" in *"#1964 broke the wave tip"*) ok "W1699: a child whose check exits 4 with no bounded line is blamed";; *) ko "W1699: bare exit 4: exit $rc: '$r'";; esac

# W1700 (#1700): the debt of the #1687 review. The second-look marker goes wherever the check settles and the wave lands or
# stops; the second look needs the same tree AND the same suite; the marker's write decides; the wait for a check with no
# verdict names its one cause and keeps the check's log. The marker names one wave: auto/tipred-<wave with / as _>.
# 1: a check that passes with no word still settles the check, so it clears the marker.
W70=wave/run-70; start2 run-70 >/dev/null; child 1701 g1701 "origin/$W70"; en2 run-70 1701 >/dev/null
M70="$T/st/auto/tipred-wave_run-70"; printf '%s\n' "$($G -C "$T/origin.git" rev-parse "$W70")" > "$M70"
r=$(SG_WAVE_TIP_GATES=true land2 run-70 2>&1); rc=$?
[ $rc = 0 ] && [ ! -e "$M70" ] && ok "W1700: a check that passes with no word clears the second-look marker" || ko "W1700: no-word pass: exit $rc, marker '$(cat "$M70" 2>/dev/null)': '$r'"
# 1: an unproven check holds the wave (#1802) and clears the second-look marker: the next pass runs the check again.
W71=wave/run-71; start2 run-71 >/dev/null; child 1702 g1702 "origin/$W71"; en2 run-71 1702 >/dev/null
M71="$T/st/auto/tipred-wave_run-71"; printf '%s\n' "$($G -C "$T/origin.git" rev-parse "$W71")" > "$M71"
r=$(SG_WAVE_TIP_GATES='printf "unproven\tinconclusive\t\tpytest\n" > "$GATE_OUTCOME_FILE"; exit 4' land2 run-71 2>&1); rc=$?
[ $rc != 0 ] && [ ! -e "$M71" ] && ! $O cat-file -e dev:g1702 2>/dev/null && ok "W1700: an unproven check holds the wave and clears the marker" || ko "W1700: unproven: exit $rc, marker '$(cat "$M71" 2>/dev/null)': '$r'"
# 1: an empty wave ends here: the branch goes, and so does its marker.
W72=wave/run-72; start2 run-72 >/dev/null
M72="$T/st/auto/tipred-wave_run-72"; printf 'deadbeef\n' > "$M72"
r=$(land2 run-72 2>&1); rc=$?
[ $rc = 0 ] && [ ! -e "$M72" ] && ok "W1700: an empty landing clears the marker" || ko "W1700: empty landing: exit $rc, marker '$(cat "$M72" 2>/dev/null)': '$r'"
# 1: a wave that freezes for good ends too, and its marker goes with it. The landing cannot fetch its base (a remote that is
# not there is no network cause), so it freezes before any check could settle the marker, at one try.
W73=wave/run-73; start2 run-73 >/dev/null; child 1703 g1703 "origin/$W73"; en2 run-73 1703 >/dev/null
M73="$T/st/auto/tipred-wave_run-73"; printf 'deadbeef\n' > "$M73"
r=$(SG_LAND_REMOTE="$T/nosuch.git" SG_WAVE_LAND_TRIES=1 SG_WAVE_TIP_GATES=true land2 run-73 2>&1); rc=$?
case "$r" in *"WAVE NEEDS-HUMAN"*) true;; *) false;; esac && [ ! -e "$M73" ] \
  && ok "W1700: a frozen wave clears the marker" || ko "W1700: frozen wave: exit $rc, marker '$(cat "$M73" 2>/dev/null)': '$r'"

# 2: the second look is the same tree AND the same suite. A pass row of the tip check's kind over another selection is another
# suite, and so is a pass of another kind over a delta: neither covers the tip check.
W74=wave/run-74; start2 run-74 >/dev/null; child 1704 g1704 "origin/$W74"; en2 run-74 1704 >/dev/null
: > "$T/st/gate-history"; M74="$T/st/auto/tipred-wave_run-74"; rm -f "$M74"
printf '1\tfast\tpass\t%s\t0\tselected 1 file\tdelta\tpass\t-\truff mypy pytest\t-\t2\t-\tentry\n' "$($G -C "$T/origin.git" rev-parse "$W74")" >> "$T/st/gate-history"
r=$(SG_WAVE_TIP_GATES="$TIP1700" SG_WAVE_CHILD_GATES='exit 0' land2 run-74 2>&1); rc=$?
case "$r" in *"passed before"*) ko "W1700: another selection covered the tip check: '$r'";; *) ok "W1700: a pass row over another selection does not cover the tip check";; esac
[ ! -e "$M74" ] && ok "W1700: no second look is spent on another suite" || ko "W1700: the second look was spent on another suite"
W75=wave/run-75; start2 run-75 >/dev/null; child 1705 g1705 "origin/$W75"; en2 run-75 1705 >/dev/null
: > "$T/st/gate-history"; M75="$T/st/auto/tipred-wave_run-75"; rm -f "$M75"
printf '1\tgate\tpass\t%s\t0\tselected 2 files\tdelta\tpass\t-\truff mypy pytest\t-\t2\t-\tentry\n' "$($G -C "$T/origin.git" rev-parse "$W75")" >> "$T/st/gate-history"
r=$(SG_WAVE_TIP_GATES="$TIP1700" SG_WAVE_CHILD_GATES='exit 0' land2 run-75 2>&1); rc=$?
case "$r" in *"passed before"*) ko "W1700: a delta pass of another gate kind covered the tip check: '$r'";; *) ok "W1700: a delta pass of another gate kind does not cover the tip check";; esac
# 2: a full battery covers any suite of the same tree: it ran every test the tip check asks for.
W76=wave/run-76; start2 run-76 >/dev/null; child 1706 g1706 "origin/$W76"; en2 run-76 1706 >/dev/null
: > "$T/st/gate-history"; M76="$T/st/auto/tipred-wave_run-76"; rm -f "$M76"
T76=$($G -C "$T/origin.git" rev-parse "$W76")
printf '1\tfull\tpass\t%s\t0\tall: the promotion proof runs the full battery\tfull\tpass\t-\tfull\t-\t2\t-\tother\n' "$T76" >> "$T/st/gate-history"
r=$(SG_WAVE_TIP_GATES="$TIP1700" SG_WAVE_CHILD_GATES='exit 0' land2 run-76 2>&1); rc=$?
case "$r" in *"passed before"*) ok "W1700: a full battery on the same tree covers the tip check";; *) ko "W1700: full battery: exit $rc: '$r'";; esac
[ "$(cat "$M76" 2>/dev/null)" = "$T76" ] && ok "W1700: the second look names the tip it was spent on" || ko "W1700: marker: '$(cat "$M76" 2>/dev/null)' for tip '$T76'"

# 3: the marker's write decides. A marker path that cannot hold the sha (a directory here) leaves no proof that the second
# look was spent, so the red takes its usual path and one line names the marker; an unchecked write would answer the second
# look on every pass and the wave would never land.
W77=wave/run-77; start2 run-77 >/dev/null; child 1707 g1707 "origin/$W77"; en2 run-77 1707 >/dev/null
: > "$T/st/gate-history"; : > "$T/board.log"; mkdir -p "$T/st/auto/tipred-wave_run-77"
printf '1\tfast\tpass\t%s\t0\tselected 2 files\tdelta\tpass\t-\truff mypy pytest\t-\t2\t-\tentry\n' "$($G -C "$T/origin.git" rev-parse "$W77")" >> "$T/st/gate-history"
r=$(SG_WAVE_TIP_GATES="$TIP1700" SG_WAVE_CHILD_GATES='exit 1' land2 run-77 2>&1); rc=$?
case "$r" in *"passed before"*) ko "W1700: an unwritable marker answered a second look it never recorded: '$r'";; *) ok "W1700: an unwritable marker spends no second look";; esac
case "$r" in *"WAVE-NOTE $W77"*"cannot be written"*) ok "W1700: an unwritable marker is named";; *) ko "W1700: the unwritable marker is silent: '$r'";; esac
case "$r" in *"#1707 broke the wave tip"*) ok "W1700: with no marker written, the red names the child";; *) ko "W1700: marker write: exit $rc: '$r'";; esac
rm -rf "$T/st/auto/tipred-wave_run-77"

# 4: the three causes of a check with no verdict are named apart, and each keeps the check's log, so a bounded child and a held
# lock leave a trace for the next pass. The holder of the gate lock is the same one Q uses.
W78=wave/run-78; start2 run-78 >/dev/null; child 1708 g1708 "origin/$W78"; en2 run-78 1708 >/dev/null
sleep 30 & hp=$!; mkdir -p "$T/st/locks/gate"; echo "$hp" > "$T/st/locks/gate/pid"
r=$(SERIAL_TRY=1 SG_WAVE_TIP_GATES="bash '$T/s/gate.sh' true" land2 run-78 2>&1); rc=$?
case "$r" in *"WAVE WAIT $W78"*"gate lock is held by pid $hp"*) ok "W1700: a held gate lock is named as its own cause";; *) ko "W1700: held lock: exit $rc: '$r'";; esac
case "$r" in *"gate slots are held by another gate, or a child's own check was bounded"*) ko "W1700: the causes still share one catch-all: '$r'";; *) ok "W1700: a held lock is not read as the catch-all";; esac
[ -f "$T/st/wave-check-wave_run-78.log" ] && ok "W1700: a check the lock held keeps its log" || ko "W1700: the check log of the held landing was deleted"
rm -rf "$T/st/locks/gate"; kill $hp 2>/dev/null
W79=wave/run-79; start2 run-79 >/dev/null; child 1709 g1709 "origin/$W79"; en2 run-79 1709 >/dev/null
r=$(SG_WAVE_TIP_GATES='echo tip-check-ran; exit 1' SG_WAVE_CHILD_PYTEST='echo "gate: bounded: the gate passed its 1s deadline and no check reported a defect"; exit 4' land2 run-79 2>&1); rc=$?
case "$r" in *"WAVE WAIT $W79"*"#1709"*"bounded"*) ok "W1700: a bounded child is named as its own cause";; *) ko "W1700: bounded child: exit $rc: '$r'";; esac
L79="$T/st/wave-check-wave_run-79.log"
[ -f "$L79" ] && /usr/bin/grep -q 'tip-check-ran' "$L79" && /usr/bin/grep -q 'gate: bounded: ' "$L79" \
  && ok "W1700: the bounded child's own line stays in the check log" || ko "W1700: check log: '$(cat "$L79" 2>/dev/null)'"

# SD (#1434): the sibling filter before dispatch reads the bodies; the entry also reads each child's own diff. Two siblings
# whose bodies name nothing in common but whose diffs touch one file are a conflict before the wave: the second never
# enters and its issue goes back to the queue, so the landing never attempts the merge of the pair. A sibling whose diff
# is disjoint from the wave's enters as it always did.
# The file both siblings change exists in the base first, and #61 is cut from that same base, not from the wave tip: the pair
# is genuinely independent, so neither diff carries a sibling's change and only the derived check can stop the second.
rm -rf "$T/basef"; $G clone -q "$T/origin.git" "$T/basef" 2>/dev/null
(cd "$T/basef" && $G checkout -q dev && printf 'a\nb\nc\nd\ne\nf\n' > shared60 && $G add shared60 \
  && $G -c user.email=a@b -c user.name=t commit -q -m "the shared file" && $G push -q origin HEAD:dev)
WS=wave/run-s; start2 run-s >/dev/null
inwave() { $O merge-base --is-ancestor "$($G -C "$T/c$2" rev-parse HEAD)" "$1" 2>/dev/null && echo yes || echo no; }
# The body filter passes the pair: neither body names a repository-relative file and neither names a spec shared with the
# other, so siblings.py lets them join. The entry's diff check is what must stop the second.
cat > "$T/issues-sd.json" <<'J'
[{"number": 60, "body": "Adjust the first line of the greeting."},
 {"number": 61, "body": "Adjust the last line of the greeting."}]
J
python3 "$d/siblings.py" "$T/issues-sd.json" 61 60 >/dev/null 2>&1 && ok "SD: the body filter lets the pair join, so only the diff check can stop it" || ko "SD: the body filter already refuses the pair, so this case would prove nothing"
rm -rf "$T/c60"; $G clone -q "$T/origin.git" "$T/c60" 2>/dev/null
(cd "$T/c60" && $G checkout -q -b gui/60 "origin/$WS" && printf 'A\nb\nc\nd\ne\nf\n' > shared60 && $G add shared60 \
  && $G -c user.email=a@b -c user.name=t commit -q -m "work 60 (Refs #60)")
en2 run-s 60 >/dev/null
# #61 is cut from the wave's base and changes another line of the same file: a rebase on the tip applies cleanly, so nothing
# but the derived check stops it. Neither body names the file.
rm -rf "$T/c61"; $G clone -q "$T/origin.git" "$T/c61" 2>/dev/null
(cd "$T/c61" && $G checkout -q -b gui/61 origin/dev && printf 'a\nb\nc\nd\ne\nF\n' > shared60 && $G add shared60 \
  && $G -c user.email=a@b -c user.name=t commit -q -m "work 61 (Refs #61)")
tip=$($O rev-parse "$WS"); printf 'run-s\n' > "$T/st/auto/disp-61"; : > "$T/board.log"
REV_BASE=$WS rev 61
r=$((cd "$T/c61" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-s 61) 2>&1)
[ "$($O rev-parse "$WS")" = "$tip" ] && [ "$(inwave "$WS" 61)" = no ] && ok "SD: a sibling whose diff shares a file never enters the wave" || ko "SD: #61 entered: '$r'"
case "$r" in *"WAVE REQUEUE #61"*shared60*) ok "SD: the shared file is named before the wave";; *) ko "SD: the shared file is not named: '$r'";; esac
python3 -c 'import json,sys; p=json.load(open(sys.argv[1])); sys.exit(0 if any(sorted(q["issues"])==[60,61] and q["paths"]==["shared60"] for q in p) else 1)' "$T/st/shared-pairs.json" 2>/dev/null && ok "SD: the refused pair is recorded with its shared path (#1941)" || ko "SD: no pair record: '$(cat "$T/st/shared-pairs.json" 2>&1)'"
/usr/bin/grep -qx 'o 3 Todo 61' "$T/board.log" && ok "SD: its issue is back in the queue, as a body conflict leaves it" || ko "SD: no Todo card for #61: '$(cat "$T/board.log")'"
[ ! -e "$T/st/auto/disp-61" ] && ok "SD: its dispatch marker is dropped, so a later run builds it alone" || ko "SD: the marker is kept"
# The merge of the pair is never attempted: the run recorded #60 alone, so the landing carries #60 alone and never names #61
# or a conflict. The base keeps the entrant's version of the file. #61's card and close never move.
mkdir -p "$T/co/runs/run-s"
printf '%s\n' '{"event": "spawn_accepted", "child": "a", "definition": {"prompt": "GitHub issue #60 work"}}' > "$T/co/runs/run-s/record.jsonl"
: > "$T/gh.log"
r=$(land2 run-s 2>&1); rc=$?
[ $rc = 0 ] && case "$r" in "WAVE $WS "*) ok "SD: the wave of the one entrant lands";; *) ko "SD: the landing is not named: '$r'";; esac || ko "SD: exit $rc: '$r'"
case "$r" in *#61*) ko "SD: the landing attempted #61, a child the wave never carried: '$r'";; *conflict*) ko "SD: the landing hit the conflict the entry prevents: '$r'";; *) ok "SD: the landing never attempts #61's merge and names no conflict";; esac
[ "$($O show dev:shared60 2>/dev/null | head -1)" = A ] && [ "$($O show dev:shared60 2>/dev/null | tail -1)" = f ] && ok "SD: the base holds the entrant's version of the file, never #61's" || ko "SD: dev:shared60 is '$($O show dev:shared60 2>/dev/null | tr '\n' ' ')'"
/usr/bin/grep -q 'issues/60 -f state=closed' "$T/gh.log" && ok "SD: the landing delivers the one entrant" || ko "SD: #60 is not closed: '$(cat "$T/gh.log")'"
/usr/bin/grep -q 'issues/61 -f state=closed' "$T/gh.log" && ko "SD: #61 was delivered by a landing that never carried it" || ok "SD: #61 is not closed: its merge was never attempted"
# The disjoint sibling passes the derived check unchanged: two children of one wave, each cut from its base on its own file,
# both enter.
WS2=wave/run-s2; start2 run-s2 >/dev/null
child 964 g964 origin/dev; en2 run-s2 964 >/dev/null
child 965 g965 origin/dev; en2 run-s2 965 >/dev/null
[ "$(inwave "$WS2" 964)" = yes ] && [ "$(inwave "$WS2" 965)" = yes ] && ok "SD: siblings with disjoint diffs enter, unchanged" || ko "SD: a disjoint sibling was held out"
[ "$($O cat-file -e "$WS2:g964" 2>/dev/null && echo yes)" = yes ] && [ "$($O cat-file -e "$WS2:g965" 2>/dev/null && echo yes)" = yes ] && ok "SD: the wave carries both disjoint siblings" || ko "SD: the wave does not carry both: '$(inwave "$WS2" 964) $(inwave "$WS2" 965)'"
# A diff the entry cannot read is a WAVE-ERROR, never a clean pass: the child's clone has no origin/dev, so the wave's side
# of the comparison cannot be computed and the child enters no wave.
child 966 g966 origin/dev
$G -C "$T/c966" update-ref -d refs/remotes/origin/dev
REV_BASE=$WS2 rev 966
r=$((cd "$T/c966" && SG_WAVE_GATES=true bash "$T/s/wave.sh" enter run-s2 966) 2>&1); rc=$?
[ $rc = 2 ] && ok "SD: a sibling diff the entry cannot read exits 2" || ko "SD: exit $rc, not 2: '$r'"
case "$r" in *"WAVE-ERROR"*"cannot be read"*) ok "SD: the unreadable diff is a WAVE-ERROR, never a clean pass";; *) ko "SD: the unreadable diff is silent: '$r'";; esac

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
