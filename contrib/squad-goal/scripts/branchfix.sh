#!/bin/bash
# usage: branchfix.sh [repo] <branch> <issue>   print the request of one FIX ROUND on a work branch (#357); AF_DRY=1 is the same
# The round works on the branch itself: it checks out the tip and commits on top, and opens no pull request. It pushes nothing, as
# every child: once the run has a verdict, branchfixpass.sh pushes the run's head to the SAME branch, fast-forward only (#768). It
# names no prepush.sh: that check is about a push the child does not make, and the child sandbox denies its path.
# Its problems are the findings of the latest review of the tip (review-<key>.md); a review that approved asks for nothing.
. "$(dirname "$0")/env.sh"; repo=$SG_REPO; [ "$#" = 3 ] && { repo=$1; shift; }
br=$1 iss=$2; key=$(printf '%s' "$br" | tr / _); f="$SG_STATE/review-$key.md"
head -1 "$f" 2>/dev/null | grep -q CHANGES || exit 0
# The kept marker names the ref the round rebases on (#1344): the branch name alone proves nothing, so a hand-made
# fix/*-rebase-* branch takes the ordinary round.
# (#2571) A marker with `salvage <tip> <base>` lines asks the round of its last tip only: a later head gets the ordinary round.
km="$SG_STATE/auto/kept-$key"; st=$(/usr/bin/grep '^salvage ' "$km" 2>/dev/null | tail -1 | awk '{print $2}')
if [ -s "$km" ] && { [ -z "$st" ] || [ "$(cat "$SG_STATE/review-$key.sha" 2>/dev/null)" = "$st" ]; }; then work="REBASE ROUND: the branch's commits were kept when their rebase conflicted (#1344, #2571). Your worktree starts on the tip of $br; rebase the branch it has checked out on $(head -1 "$km"), resolve each conflict in the files the review names so the work is kept, and commit; push nothing and open no pull request: once the run ends, the loop publishes the run's head to $br under a lease on the tip it reviewed. Your worktree's branch is named squad/<run>/... and differs from $br by design: never check, switch or create a branch. The router passes this sentence to the child verbatim."
else work="commit on top of the branch your worktree has checked out (that branch is the tip of $br), and create no branch of your own; push nothing and open no pull request: once the run ends, the loop pushes the run's head to $br (fast-forward only). Your worktree's branch is named squad/<run>/... and differs from $br by design: never check, switch or create a branch. The router passes this sentence to the child verbatim."; fi
echo "One child, a FIX RUN on the work branch $br for GitHub issue #$iss (repo $repo). In your worktree, $work Keep \"Refs #$iss\" (never Closes). Rules: $SG_CODING_RULES."
# (#2350) The one base the round names: launch.py cuts the run worktree from this branch's tip, on its own branch squad/<run>/...
# (#2864), so the child's head is a fast-forward of the work branch. The request sends the child to no other base, or it is not.
echo "CUT FROM: $br"
echo; echo "[review] The latest automated review of the branch says VERDICT: CHANGES. Address every finding (fix it, or say why it is wrong, with file:line evidence):"
tail -n +2 "$f"
