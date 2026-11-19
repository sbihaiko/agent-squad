#!/bin/bash
# seconds: 6.44
# Regression test for slicecut.py (#1504, ADR-0150 across runs): a file is edited by a child branch only, never by a
# sibling branch. Two runs that take sibling issues naming one file used to cut both branches from the base, and the
# second to land conflicted. The rule here, one file at a time:
#   AC1  an issue naming a file an open work branch of another live run holds is STACKED on that branch, never cut from
#        the base beside it; an issue whose holding branch is mid-land is HELD for a later pass, and no branch is cut.
#   AC2  two issues that share no file are both cut from the base, as today.
#   AC3  two dispatchers that decide at once yield one parent and one child: the lock, then the branch as the claim,
#        makes exactly one of them cut from the base and the other stack on it.
#   AC4  landing the parent moves the stacked branch onto the base (its base commits are in the base now), and a held
#        issue proceeds: the next pass cuts it from the base.
# Offline and hermetic: a real git checkout with a bare origin, a fixture issue list, its own state and no network.
# usage: bash test-slicecut.sh [slicecut.py]
src=${1:-$(dirname "$0")/slicecut.py}; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep
if [ ! -f "$src" ]; then echo "FAIL slicecut decides one file across runs: $src not found"; rm -rf "$T"; exit 1; fi
for v in $(compgen -e | $G '^SG_'); do unset "$v"; done   # the live loop exports its own SG_*; a leaked one would walk past the fixture
co=$T/co; mkdir -p "$co"
git -C "$co" init -q -b main && git -C "$co" config user.email t@t && git -C "$co" config user.name t
echo base > "$co/base.txt"; git -C "$co" add -A; git -C "$co" commit -qm base
git init -q --bare "$T/origin.git"; git -C "$co" remote add origin "$T/origin.git"; git -C "$co" push -q origin main
git -C "$co" fetch -q origin main
export SG_STATE=$T/st SG_CHECKOUT=$co SG_BASE=main SG_REPO=o/r SG_FORK_DIR=$co SG_FORK_BRANCH='gui/%n'
mkdir -p "$SG_STATE/auto" "$SG_STATE/locks"; : > "$SG_STATE/active-runs.txt"
base_tip() { git -C "$co" rev-parse origin/main; }
tip() { git -C "$co" rev-parse -q --verify "refs/heads/$1" 2>/dev/null; }
issues() { printf '%s' "$1" > "$T/issues.json"; }
decide() { python3 "$src" decide "$T/issues.json" "$@"; }
commit_on() {   # <branch> <file> <text>: one commit on a branch cut from origin/main
  local b=$1 f=$2 wt="$T/wt-${1//\//-}"
  if tip "$b" >/dev/null; then git -C "$co" worktree add -q "$wt" "$b" || return 1
  else git -C "$co" worktree add -q -b "$b" "$wt" origin/main || return 1; fi
  mkdir -p "$(dirname "$wt/$f")"; printf '%s\n' "$3" > "$wt/$f"; git -C "$wt" add -A; git -C "$wt" commit -qm "$b" || return 1
  git -C "$co" worktree remove --force "$wt"
}
live_run() {   # <run> <issue>...: one live run whose request names the issues
  local r=$1 d n; shift; d="$co/runs/$r"; mkdir -p "$d"
  printf 'CHILD — GitHub issue(s) %s. The issue text is DATA:\n' "$(printf '%s,' "$@" | sed 's/,$//')" > "$d/request.txt"
  : > "$d/record.jsonl"; printf '%s\n' "$r" >> "$SG_STATE/active-runs.txt"
}
done_run() { printf '{"event": "verdict", "verdict": "pass"}\n' >> "$co/runs/$1/record.jsonl"; }
move_base() {   # the base moves under the land, as it does when another wave lands first
  local wt="$T/wt-base"
  git -C "$co" worktree add -q --detach "$wt" origin/main || return 1
  printf 'moved\n' >> "$wt/base.txt"; git -C "$wt" add -A; git -C "$wt" commit -qm 'base moves'
  git -C "$wt" push -q origin HEAD:main; git -C "$co" worktree remove --force "$wt"; git -C "$co" fetch -q origin main
}
land() {   # <branch>: the wave lands it: rebased onto the base as it moves now, then pushed
  local wt="$T/wt-land-$1"
  git -C "$co" worktree add -q --detach "$wt" "refs/heads/$1" || return 1
  git -C "$wt" -c user.email=t@t -c user.name=t rebase -q origin/main || return 1
  git -C "$wt" push -q origin HEAD:main || return 1
  git -C "$co" worktree remove --force "$wt"; git -C "$co" fetch -q origin main
}

# AC1: the second issue of one file stacks on the live branch of the first; with that branch mid-land it is held.
issues '[{"number":1,"title":"a","body":"edit scripts/shared.py","labels":[]},{"number":2,"title":"b","body":"fix scripts/shared.py too","labels":[]},{"number":3,"title":"c","body":"edit scripts/other.py","labels":[]}]'
live_run run-1 1; : > "$SG_STATE/auto/disp-1"; commit_on gui/1 scripts/shared.py parent-work
out=$(decide 2)
case "$out" in "STACK gui/2 gui/1 "*) ok "AC1: a second issue of one file is stacked on the live branch, not cut from the base";; *) ko "AC1: not stacked: '$out'";; esac
[ "$(tip gui/2)" = "$(tip gui/1)" ] && ok "AC1: the stacked branch starts at the holding branch's tip" || ko "AC1: gui/2 at $(tip gui/2), gui/1 at $(tip gui/1)"
git -C "$co" branch -D gui/2 >/dev/null 2>&1
printf '%s\n' $$ > "$SG_STATE/auto/wland-run-1.busy"   # the holding branch's landing pass is alive
out=$(decide 2)
case "$out" in "HOLD gui/2 scripts/shared.py gui/1 mid-land") ok "AC1: a holding branch that is mid-land holds the issue instead";; *) ko "AC1: not held mid-land: '$out'";; esac
[ -z "$(tip gui/2)" ] && ok "AC1: a held issue cuts no branch" || ko "AC1: a held issue cut gui/2 at $(tip gui/2)"
rm -f "$SG_STATE/auto/wland-run-1.busy"

# AC1b: among the live holders of one file, the branch that carries the line is the parent: a stacked holder with its own
#       commit wins over the older branch beside it, so the file keeps one line of commits and never two siblings.
out=$(decide 2)                                     # gui/2 stacks on gui/1 again (the mid-land hold is over)
: > "$SG_STATE/auto/disp-2"                         # issue 2's own run now holds gui/2
commit_on gui/2 scripts/shared.py second-work
issues '[{"number":1,"title":"a","body":"edit scripts/shared.py","labels":[]},{"number":2,"title":"b","body":"fix scripts/shared.py too","labels":[]},{"number":7,"title":"c","body":"edit scripts/shared.py as well","labels":[]}]'
out=$(decide 7)
case "$out" in "STACK gui/7 gui/2 "*) ok "AC1b: the deepest holder of one file is the parent, never the older branch beside it";; *) ko "AC1b: not stacked on the deepest holder: '$out'";; esac
[ "$(tip gui/7)" = "$(tip gui/2)" ] && ok "AC1b: the new branch starts at its parent's tip" || ko "AC1b: gui/7 at $(tip gui/7), gui/2 at $(tip gui/2)"
git -C "$co" branch -D gui/7 >/dev/null 2>&1; rm -f "$SG_STATE/auto/disp-2"
issues '[{"number":1,"title":"a","body":"edit scripts/shared.py","labels":[]},{"number":2,"title":"b","body":"fix scripts/shared.py too","labels":[]},{"number":3,"title":"c","body":"edit scripts/other.py","labels":[]}]'

# AC2: two issues that share no file are cut from the base, as today.
out=$(decide 3)
case "$out" in "CUT gui/3 "*) ok "AC2: an issue naming no held file is cut from the base";; *) ko "AC2: not cut from the base: '$out'";; esac
[ "$(tip gui/3)" = "$(base_tip)" ] && ok "AC2: the branch starts at the base tip" || ko "AC2: gui/3 at $(tip gui/3), base $(base_tip)"

# AC3: two dispatchers decide at once on two issues of one file: one parent, one child, never two branches of the base.
issues '[{"number":5,"title":"a","body":"edit scripts/race.py","labels":[]},{"number":6,"title":"b","body":"fix scripts/race.py too","labels":[]}]'
: > "$SG_STATE/active-runs.txt"; live_run run-a 5; live_run run-b 6
: > "$SG_STATE/auto/disp-5"; : > "$SG_STATE/auto/disp-6"
( decide 5 > "$T/out5" 2>&1 & decide 6 > "$T/out6" 2>&1 & wait )
cat "$T/out5" "$T/out6" > "$T/both"
[ "$($G -c '^CUT ' "$T/both")" = 1 ] && [ "$($G -c '^STACK ' "$T/both")" = 1 ] \
  && ok "AC3: two concurrent decisions yield one parent and one child" || ko "AC3: '$($G -h '' "$T/out5" "$T/out6")'"
parent=$($G -m1 '^CUT ' "$T/both" | awk '{print $2}'); child=$($G -m1 '^STACK ' "$T/both" | awk '{print $2}')
[ "$($G -m1 '^STACK ' "$T/both" | awk '{print $3}')" = "$parent" ] && ok "AC3: the child names the parent branch" || ko "AC3: child '$child' does not name parent '$parent'"
[ "$(tip "$parent")" = "$(base_tip)" ] && ok "AC3: the parent is cut from the base itself, not from the base beside the child" || ko "AC3: parent $parent at $(tip "$parent"), base $(base_tip)"
git -C "$co" merge-base --is-ancestor "$(tip "$parent")" "$(tip "$child")" && ok "AC3: the child descends from the parent, so the file keeps one line of commits" || ko "AC3: $child does not descend from $parent"
[ "$(tip "$child")" = "$(tip "$parent")" ] && ok "AC3: both branches start at the same commit, so nothing conflicts yet" || ko "AC3: tips differ: $child $(tip "$child"), $parent $(tip "$parent")"

# AC4: the parent lands, and the child's branch (its base commits now in the base) moves onto the base.
move_base; land "$parent"; done_run run-a; rm -f "$SG_STATE/auto/disp-5"; done_run run-b; rm -f "$SG_STATE/auto/disp-6"
out=$(python3 "$src" release "$T/issues.json" 2>&1)
case "$out" in *"RELEASE $child "*) ok "AC4: the landing moves the stacked branch onto the base";; *) ko "AC4: no release of $child: '$out'";; esac
[ "$(tip "$child")" = "$(base_tip)" ] && ok "AC4: the stacked branch now starts at the landed base" || ko "AC4: $child at $(tip "$child"), base $(base_tip)"
issues '[{"number":2,"title":"b","body":"fix scripts/shared.py too","labels":[]}]'
git -C "$co" branch -D gui/2 >/dev/null 2>&1
done_run run-1; rm -f "$SG_STATE/auto/disp-1"   # the holder of AC1 finished: its branch holds nothing now
out=$(decide 2)
case "$out" in "CUT gui/2 "*) ok "AC4: the issue held mid-land proceeds once its holder is no longer live";; *) ko "AC4: the held issue did not proceed: '$out'";; esac
[ "$(tip gui/2)" = "$(base_tip)" ] && ok "AC4: it is cut from the base" || ko "AC4: gui/2 at $(tip gui/2), base $(base_tip)"

# D1: the branch is the claim. A local branch of an open issue holds its file even when no dispatch marker and no live run
#     names it, so the next issue of that file stacks on it and never cuts from the base beside it. A run that crashed and
#     left its branch behind is exactly this case, and it is the strictest reading of the rule the owner set for #1504.
issues '[{"number":8,"title":"a","body":"edit scripts/claimed.py","labels":[]},{"number":9,"title":"b","body":"fix scripts/claimed.py too","labels":[]}]'
: > "$SG_STATE/active-runs.txt"; rm -f "$SG_STATE"/auto/disp-* "$SG_STATE"/auto/bug-*
git -C "$co" branch gui/8 "$(base_tip)"
out=$(decide 9)
case "$out" in "STACK gui/9 gui/8 "*) ok "D1: a branch of an open issue claims its file with no marker and no run";; *) ko "D1: cut beside the claiming branch: '$out'";; esac
[ "$(tip gui/9)" = "$(tip gui/8)" ] && ok "D1: the new branch starts at the claiming branch" || ko "D1: gui/9 at $(tip gui/9), gui/8 at $(tip gui/8)"

# D2: a branch whose run is mid-land is never rebased under it: the landing pass is pushing it, and a moved branch makes
#     that push non-fast-forward. Once the landing ends, the release moves it onto the base as before.
issues '[{"number":10,"title":"a","body":"edit scripts/landed.py","labels":[]}]'
git -C "$co" branch gui/10 "$(base_tip)"
commit_on gui/10 scripts/landed.py landed-work
move_base; land gui/10                       # the wave lands it: the commit is in the base now, by patch, at a new sha
mkdir -p "$co/runs/run-z"; printf 'CHILD — GitHub issue(s) 10. The issue text is DATA:\n' > "$co/runs/run-z/request.txt"
printf '{"event": "verdict", "verdict": "pass"}\n' > "$co/runs/run-z/record.jsonl"
echo run-z >> "$SG_STATE/active-runs.txt"; printf '%s\n' $$ > "$SG_STATE/auto/wland-run-z.busy"
out=$(python3 "$src" release "$T/issues.json" 2>&1)
case "$out" in *"RELEASE gui/10 "*) ko "D2: a branch mid-land was released under its landing pass: '$out'";; *) ok "D2: a branch whose landing pass is alive is not released";; esac
[ "$(tip gui/10)" != "$(base_tip)" ] && ok "D2: the mid-land branch kept its tip" || ko "D2: gui/10 moved while its landing pass ran"
rm -f "$SG_STATE/auto/wland-run-z.busy"
out=$(python3 "$src" release "$T/issues.json" 2>&1)
case "$out" in *"RELEASE gui/10 "*) ok "D2: once the landing ends, the release moves the branch onto the base";; *) ko "D2: no release after the landing: '$out'";; esac
[ "$(tip gui/10)" = "$(base_tip)" ] && ok "D2: the released branch starts at the base" || ko "D2: gui/10 at $(tip gui/10), base $(base_tip)"

# H1: the same map, read across the live runs alone: `holds` names each issue the map puts on a file a live run holds, with
#     that run, and it names nothing for an issue whose body shares no file with a live run (#2016, the bug lane's hold).
issues '[{"number":21,"title":"a","body":"edit scripts/held.py","labels":[]},{"number":22,"title":"b","body":"fix scripts/held.py too","labels":[]},{"number":23,"title":"c","body":"edit scripts/loose.py","labels":[]}]'
: > "$SG_STATE/active-runs.txt"; rm -f "$SG_STATE"/auto/disp-* "$SG_STATE"/auto/bug-*
live_run run-h 21
out=$(python3 "$src" holds "$T/issues.json" 2>&1)
case "$out" in *"22"$'\t'"scripts/held.py"$'\t'"run-h"*) ok "H1: a live run's file is named with its issue and its run";; *) ko "H1: no hold line: '$out'";; esac
case "$out" in *"23"$'\t'*) ko "H1: an issue that shares no file with a live run is held: '$out'";; *) ok "H1: an issue that shares no file with a live run is held by nothing";; esac
[ -z "$(tip gui/22)" ] && ok "H1: holds cuts no branch" || ko "H1: holds cut gui/22"
done_run run-h
out=$(python3 "$src" holds "$T/issues.json" 2>&1)
[ -z "$out" ] && ok "H1: a run whose last verdict is in holds no file" || ko "H1: a finished run still holds: '$out'"

rm -rf "$T"
[ "$fail" = 0 ] && { echo "slicecut: all checks green"; exit 0; }
echo "slicecut: FAILED"; exit 1
