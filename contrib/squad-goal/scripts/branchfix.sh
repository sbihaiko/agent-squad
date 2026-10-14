#!/bin/bash
# usage: branchfix.sh [repo] <branch> <issue>   print the request of one FIX ROUND on a work branch (#357); AF_DRY=1 is the same
# The round works on the branch itself: it checks out the tip, commits on top and pushes to the SAME branch, and opens no pull
# request. Its problems are the findings of the latest review of the tip (review-<key>.md); a review that approved asks for nothing.
. "$(dirname "$0")/env.sh"; repo=$SG_REPO; [ "$#" = 3 ] && { repo=$1; shift; }
br=$1 iss=$2; key=$(printf '%s' "$br" | tr / _); f="$SG_STATE/review-$key.md"
head -1 "$f" 2>/dev/null | grep -q CHANGES || exit 0
echo "One child, a FIX RUN on the work branch $br for GitHub issue #$iss (repo $repo). Check out origin/$br, commit on top, push to the SAME branch (git push origin HEAD:$br); no pull request. Keep \"Refs #$iss\" (never Closes). Rules: $SG_CODING_RULES."
echo; echo "[review] The latest automated review of the branch says VERDICT: CHANGES. Address every finding (fix it, or say why it is wrong, with file:line evidence):"
tail -n +2 "$f"
echo "Before every push, run: bash $(cd "$(dirname "$0")" && pwd)/prepush.sh --local   (it must print nothing)."
