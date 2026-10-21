#!/bin/bash
# usage: childfix.sh <run> <issue>   start one FIX RUN in the child's kept worktree, the current directory (#636); prints the run folder
# childcc.py calls it when its review of a child's own diff asks for CHANGES. The child's branch is pushed nowhere (#375), so the
# pushed-branch fix lane (branchfixpass.sh) never reaches it, and a requeue at once threw the child's work away. The fix run's
# child commits on top of the child's HEAD in this worktree and pushes nothing; childcc reviews the new head again, and an
# APPROVE enters the wave. The request is sent through mkreq.sh and launch.py, as every pass sends a run. It carries no
# `CHILD ... GitHub issue` line, so launch.py cuts no wave for it; SG_RUN_TARGET makes the kept worktree its target, and it delivers nothing.
. "$(dirname "$0")/env.sh"; here="$(cd "$(dirname "$0")" && pwd)"
run=$1 iss=$2
{ [ -n "$run" ] && [ -n "$iss" ]; } || { echo "usage: childfix.sh <run> <issue>" >&2; exit 2; }
br=$(git symbolic-ref --quiet --short HEAD) || { echo "CHILDFIX $PWD is on no branch (#$iss)" >&2; exit 2; }
wt=$(pwd -P); sha=$(git rev-parse HEAD); f="$SG_STATE/review-$(printf '%s' "$br" | tr / _).md"
head -1 "$f" 2>/dev/null | /usr/bin/grep -q CHANGES || { echo "CHILDFIX $br: no CHANGES review to fix (#$iss)" >&2; exit 2; }
b=$(mktemp)
{ echo "IMPORTANT: your first and only action is to call the spawn tool for the child below; ending the turn without a spawn call fails the run. Spawn the child with $(bash "$here/envelope-spend.sh" 2>/dev/null)."
  echo "One child, a FIX RUN in the kept worktree $wt (branch $br, child of run $run) for GitHub issue #$iss (repo $SG_REPO). Work only in $wt: commit on top of its HEAD ${sha:0:9} there. Push no branch, and open no pull request. Never write outside $wt; never run publish-live.sh and never touch ~/.claude/skills (#658). Keep \"Refs #$iss\" (never Closes). Rules: $SG_CODING_RULES."
  echo; echo "[review] The review of the child's own diff says VERDICT: CHANGES. Address every finding (fix it, or say why it is wrong, with file:line evidence):"
  tail -n +2 "$f"
  pc=${SG_PREPUSH_CMD-bash contrib/squad-goal/scripts/prepush.sh --local}   # (#740) the profile's prepush command, or none
  pc=${pc/#bash contrib\//bash $wt/contrib/}   # a relative script path is anchored at the kept worktree
  if sg_profile_resolves && [ -n "$pc" ]; then
    echo "Before every commit, run in $wt: $pc   (it must print nothing)."
  elif sg_profile_resolves; then
    echo "No prepush check is configured for this repository, so run none: a missing check is not a failure; name that in your report."
  else
    echo "The prepush check is unavailable: no squad-goal profile resolves for this session (SG_PROFILE='${SG_PROFILE:-}', SG_PROFILES_DIR='$(sg_profiles_dir)'), so do not run prepush.sh --local; name that in your report."
  fi; } > "$b"
req=$(bash "$here/mkreq.sh" "auto-childfix-$run-$iss-${sha:0:9}" "$b") || { rm -f "$b"; echo "CHILDFIX $br @ ${sha:0:9}: refused by mkreq.sh (#$iss)" >&2; exit 1; }
rm -f "$b"
# The kept worktree is the run's target (#654): ac-verify and every gate that reads a tree read the child's files there, and the
# engine commits the fix on the child's branch. A fresh run tree at the base tip lacks them, so every fix run failed its final gate.
id=$(cd "$SG_CHECKOUT" && SG_RUN_TARGET="$wt" python3 "$here/launch.py" "$req" 2>/dev/null | tail -1)
/usr/bin/git -C "$wt" checkout -q -- docs/squad/workflows/dynamic.graph.json 2>/dev/null   # launch.py renames the graph root in the target in place
[ -n "$id" ] || { echo "CHILDFIX $br @ ${sha:0:9}: launch.py printed no run id, so no fix run started (#$iss)" >&2; exit 1; }
d=$(/usr/bin/grep "/$id\$" "$SG_STATE/active-runs.txt" 2>/dev/null | tail -1)
[ -n "$d" ] || { echo "CHILDFIX $br @ ${sha:0:9}: run $id is not in active-runs.txt (#$iss)" >&2; exit 1; }
echo "$d"
