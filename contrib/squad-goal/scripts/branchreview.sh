#!/bin/bash
export SG_GH_PASS=${SG_GH_PASS:-branchreview}
# usage: branchreview.sh [repo] <branch> <issue>   review a pushed work branch, with no pull request (#357)
# The review reads the diff of the branch against dev (review.sh --branch). Its findings go to the one status comment of the issue:
# a queue file of the status kind, which cqflush.sh edits in place, so a second round edits the comment and posts no new one. The
# verdict stays in $SG_STATE/review-<branch with / as _>.md beside the reviewed sha; branchland.sh waits for an APPROVE there.
# Prints one line: `<branch> <sha9> <verdict>`.
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/ghcall.sh"; repo=$SG_REPO; [ "$#" = 3 ] && { repo=$1; shift; }   # a branch name holds a /, so the repo is told by the argument count
br=$1 iss=$2; key=$(printf '%s' "$br" | tr / _)
sha=$(gh_api repos/$repo/commits/$br -q .sha)
rm=$(bash "$(dirname "$0")/review.sh" $repo --branch "$br" $iss | head -1 | sed -n "s/^model=\([^ ]*\).*/\1/p")
v=$(head -1 "$SG_STATE/review-$key.md" | tr -d '*')
{ echo "Status update — branch $br @ ${sha:0:9}: review ($rm): $v."; echo; tail -n +2 "$SG_STATE/review-$key.md"; } > "$(sg_cq_status "$iss" "res$$")"
echo "$br ${sha:0:9} $v"
