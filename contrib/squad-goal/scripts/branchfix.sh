#!/bin/bash
# usage: branchfix.sh [repo] <branch> <issue>   print the request of one FIX ROUND on a work branch (#357); AF_DRY=1 is the same
# The round works on the branch itself: it checks out the tip and commits on top, and opens no pull request. It pushes nothing, as
# every child: once the run has a verdict, branchfixpass.sh pushes the run's head to the SAME branch, fast-forward only (#768). It
# names no prepush.sh: that check is about a push the child does not make, and the child sandbox denies its path.
# Its problems are the findings of the latest review of the tip (review-<key>.md); a review that approved asks for nothing.
. "$(dirname "$0")/env.sh"; repo=$SG_REPO; [ "$#" = 3 ] && { repo=$1; shift; }
br=$1 iss=$2; key=$(printf '%s' "$br" | tr / _); f="$SG_STATE/review-$key.md"
head -1 "$f" 2>/dev/null | grep -q CHANGES || exit 0
echo "One child, a FIX RUN on the work branch $br for GitHub issue #$iss (repo $repo). In your worktree, check out origin/$br and commit on top; push nothing and open no pull request: once the run ends, the loop pushes the run's head to $br (fast-forward only). Keep \"Refs #$iss\" (never Closes). Rules: $SG_CODING_RULES."
echo; echo "[review] The latest automated review of the branch says VERDICT: CHANGES. Address every finding (fix it, or say why it is wrong, with file:line evidence):"
tail -n +2 "$f"
