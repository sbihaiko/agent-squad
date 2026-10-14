#!/bin/bash
# Regression test for wave.sh (#374): the run's wave branch, and the child that enters it by a fast-forward.
# A child enters only after its review approved its own diff against the wave branch (#376): a review that asked for changes,
# one read against another base or an older tip, and no review at all each hold the child out, with nothing requeued.
# A bare local remote, a stub board, a temp state dir: every case runs on this machine, with no GitHub call at all.
# usage: bash test-wave.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
unset REV REV_BASE REV_SHA   # each case decides the review; an ambient one from a live loop must not
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/co"
cp "$d/wave.sh" "$d/address.sh" "$T/s/" 2>/dev/null
printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s SG_LAND_REMOTE=%s/origin.git BOARD=%s/board.sh\n' "$T/st" "$T/co" "$T" "$T" > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
$G init -q -b dev "$T/co"; (cd "$T/co" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init)
$G clone -q --bare "$T/co" "$T/origin.git"; O="$G -C $T/origin.git"; W=wave/run-1
# The remote is a bare repository on this machine: the whole suite runs against it, never against GitHub.
[ "$($O rev-parse --is-bare-repository)" = true ] && ok "the suite runs against a bare local remote" || ko "the remote is not a bare repository"
start() { (cd "$T/co" && bash "$T/s/wave.sh" start run-1); }
# A child worktree: a clone cut from the wave branch, on branch gui/<issue>, one commit that writes <file>.
child() { local n=$1 f=$2 tip=${3:-origin/$W}
  rm -rf "$T/c$n"; $G clone -q "$T/origin.git" "$T/c$n" 2>/dev/null
  (cd "$T/c$n" && $G checkout -q -b "gui/$n" "$tip" && echo "$n" > "$f" && $G add "$f" && $G -c user.email=a@b -c user.name=t commit -q -m "work $n (Refs #$n)"); }
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

# A: the run cuts one wave branch, named for the run, from dev.
r=$(start)
[ "$($O rev-parse -q --verify $W)" = "$($O rev-parse dev)" ] && ok "A: the run cuts its wave branch from dev" || ko "A: no wave branch: '$r'"
case "$r" in "WAVE $W "*) ok "A: start names the wave branch and its sha";; *) ko "A: start said nothing: '$r'";; esac
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

rm -rf "$T"; exit $fail
