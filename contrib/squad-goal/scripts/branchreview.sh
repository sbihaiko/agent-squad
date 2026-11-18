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
base=${SG_REVIEW_BASE:-${SG_LAND_BRANCH:-dev}}; export SG_REVIEW_BASE=$base   # the diff is taken against this ref: the wave branch for a child of a wave
sha=$(gh_api repos/$repo/commits/$br -q .sha)
# (#2310) A branch whose head carries no commit the base does not already hold by patch has no work of its own: the review
# would approve an empty change, the landing can add nothing, and the frontier skips the issue while the branch stays open.
# The check reads the two refs in the checkout, as branchland reads its base there; a read that fails proves nothing, so the
# review runs as before. The refusal keeps the reviewed sha and the base beside it, as a review does, so the pass reads it as
# this tip's verdict and spends no second review.
remote=${SG_LAND_REMOTE:-https://github.com/$SG_REPO.git}; rf=$(printf '%s' "$base" | tr / _)
if /usr/bin/git -C "${SG_CHECKOUT:-.}" fetch -q "$remote" "+refs/heads/$base:refs/sg-rev/$rf" "+refs/heads/$br:refs/sg-rev/$key" >/dev/null 2>&1 \
   && /usr/bin/git -C "$SG_CHECKOUT" rev-parse -q --verify "refs/sg-rev/$rf" >/dev/null 2>&1; then
  if ! /usr/bin/git -C "$SG_CHECKOUT" cherry "refs/sg-rev/$rf" "refs/sg-rev/$key" 2>/dev/null | /usr/bin/grep -q '^+'; then
    why="the branch carries no commit that is not on $base by patch"
    printf 'VERDICT: BLOCKED (%s)\n' "$why" > "$SG_STATE/review-$key.md"
    printf '%s\n' "$sha" > "$SG_STATE/review-$key.sha"; printf '%s\n' "$base" > "$SG_STATE/review-$key.base"
    printf 'Status update — branch %s @ %s: review refused: %s.\n' "$br" "${sha:0:9}" "$why" > "$(sg_cq_status "$iss" "br$$")"
    echo "$br ${sha:0:9} VERDICT: BLOCKED ($why)"; exit 0
  fi
fi
rm=$(bash "$(dirname "$0")/review.sh" $repo --branch "$br" $iss | sed -n "s/^model=\([^ ]*\).*/\1/p" | sed -n 1p)   # every line is read: a closed pipe would kill review.sh mid-call (#772)
v=$(head -1 "$SG_STATE/review-$key.md" | tr -d '*')
printf '%s\n' "$base" > "$SG_STATE/review-$key.base"   # the base beside the reviewed sha, so wave.sh's entry can require the wave branch
{ echo "Status update — branch $br @ ${sha:0:9}: review ($rm) against $base: $v."; echo; tail -n +2 "$SG_STATE/review-$key.md"; } > "$(sg_cq_status "$iss" "res$$")"
echo "$br ${sha:0:9} $v"
