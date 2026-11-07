#!/bin/bash
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

rm -rf "$T"
[ "$fail" = 0 ] && { echo "slicecut: all checks green"; exit 0; }
echo "slicecut: FAILED"; exit 1
