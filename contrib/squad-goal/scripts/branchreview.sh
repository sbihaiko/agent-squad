#!/bin/bash
export SG_GH_PASS=${SG_GH_PASS:-branchreview}
# usage: branchreview.sh [repo] <branch> <issue>   review a pushed work branch, with no pull request (#357)
# The review reads the diff of the branch against $SG_REVIEW_BASE (default $SG_LAND_BRANCH, dev), through review.sh --branch.
# Its findings go to the one status comment of the issue: a queue file of the status kind, which cqflush.sh edits in place, so a
# second round edits the comment and posts no new one. The verdict stays in $SG_STATE/review-<branch with / as _>.md beside the
# reviewed sha, and the base it was read against in review-<key>.base; branchland.sh waits for an APPROVE there, and wave.sh's
# entry reads the base: a child of a wave sets SG_REVIEW_BASE to the wave branch, so the diff is the child's own commits against
# their merge-base with the wave branch, never the whole wave (#376).
# Prints one line: `<branch> <sha9> <verdict>`.
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/ghcall.sh"; repo=$SG_REPO; [ "$#" = 3 ] && { repo=$1; shift; }   # a branch name holds a /, so the repo is told by the argument count
br=$1 iss=$2; key=$(printf '%s' "$br" | tr / _)
base=${SG_REVIEW_BASE:-${SG_LAND_BRANCH:-dev}}; export SG_REVIEW_BASE=$base   # the ref the diff is taken against: the wave branch for a child of a wave
sha=$(gh_api repos/$repo/commits/$br -q .sha)
rm=$(bash "$(dirname "$0")/review.sh" $repo --branch "$br" $iss | sed -n "s/^model=\([^ ]*\).*/\1/p" | sed -n 1p)   # every line is read: a closed pipe would kill review.sh mid-call (#772)
v=$(head -1 "$SG_STATE/review-$key.md" | tr -d '*')
printf '%s\n' "$base" > "$SG_STATE/review-$key.base"   # the base beside the reviewed sha, so wave.sh's entry can require the wave branch
{ echo "Status update — branch $br @ ${sha:0:9}: review ($rm) against $base: $v."; echo; tail -n +2 "$SG_STATE/review-$key.md"; } > "$(sg_cq_status "$iss" "res$$")"
echo "$br ${sha:0:9} $v"
