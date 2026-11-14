#!/bin/bash
# usage: promote-main.sh [<sha>]   fast-forward main to the stg tip, or to <sha> (ADR-0145, ADR-0173).
#   no argument: the owner command. main moves to the stg tip.
#   <sha>: the stg-to-main pass (promote-main-pass.sh) names the sha whose complete tier is green. The sha must be on the
#     first-parent chain of stg, so main only ever takes a commit stg itself stood at, and stg may already be ahead of it.
# Refuses, and changes nothing, when main is not an ancestor of the target: main then holds work the target lacks, and a
# fast-forward would lose it. The push is plain, never forced. Exit 0: main is at the target. Exit 1: refused, with the reason
# on stderr. The branches are the profile's: SG_MAIN_BRANCH (main) and SG_STG_BRANCH (stg). $SG_PROMOTE_REMOTE overrides the
# remote. The work happens in a throwaway clone.
remote=${SG_PROMOTE_REMOTE:-https://github.com/$SG_REPO.git}
main=${SG_MAIN_BRANCH:-main}; stg=${SG_STG_BRANCH:-stg}
export GIT_TERMINAL_PROMPT=0
W=$(mktemp -d) || exit 1; trap 'rm -rf "$W"' EXIT
g() { /usr/bin/git -C "$W" "$@"; }
no() { echo "promote-main: $*" >&2; exit 1; }
g init -q . 2>/dev/null; g remote add origin "$remote"
o=$(g fetch -q origin "+refs/heads/$main:refs/remotes/origin/$main" "+refs/heads/$stg:refs/remotes/origin/$stg" 2>&1) || no "fetch failed: $(printf '%s' "$o" | tail -1)"
tip=$(g rev-parse "origin/$stg") || no "no $stg branch"
what=$stg
if [ -n "${1:-}" ]; then
  sha=$(g rev-parse --verify -q "$1^{commit}") || no "refused: $1 is not a commit of $stg"
  g rev-list --first-parent "origin/$stg" | /usr/bin/grep -qxF "$sha" || no "refused: $sha is not on the first-parent chain of $stg"
  tip=$sha; what=$sha
fi
[ "$(g rev-parse "origin/$main")" = "$tip" ] && { echo "$main is already at $what $tip"; exit 0; }
g merge-base --is-ancestor "origin/$main" "$tip" || no "refused: $main is not an ancestor of $what, so $main holds work $what lacks"
o=$(g push -q origin "$tip:refs/heads/$main" 2>&1) || no "the push to $main was refused: $(printf '%s' "$o" | tail -1)"
echo "PROMOTED $main $tip"
