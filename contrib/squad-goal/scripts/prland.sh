#!/bin/bash
# usage: prland.sh <N> <branch>   (forkclose.sh, after the local gate is green; env.sh / forkonly.sh beside it)
# The land of a fork branch is the merge of its pull request on a green check (#32): one route, so no issue is delivered by
# a local commit on an unpushed branch. The local gate (the merge into $SG_FORK_BASE and the fork suite) has already run as
# the fast path; this step pushes <branch> to the remote origin of $SG_FORK_DIR (a plain push, never a force push, never the
# base), opens the PR that names the issue when none is open, and merges it with the PUT merge endpoint once every check on
# the head has finished and none failed. The repository is the one the checkout resolves to (sg_resolve_repo). After the
# merge the fork's base takes the remote's base: a fast-forward, or the remote merge when its tree is the gated tree.
# Exit 0: merged (or the base on the remote already holds the branch). 1: a check failed. 2: wait (checks pending or the
# merge refused). 3: refused (no remote, push or PR creation failed). The last line names the PR when there is one.
here="$(cd "$(dirname "$0")" && pwd)"; [ -n "${SG_FORK_DIR:-}" ] || . "$here/env.sh"; . "$here/forkonly.sh"
n=${1:?issue}; br=${2:?branch}; base=${SG_FORK_BASE:-main}; G="/usr/bin/git -C $SG_FORK_DIR"
repo=$(sg_resolve_repo "$SG_FORK_DIR" 2>&1) || { echo "PRLAND REFUSED #$n: $repo"; exit 3; }
sync() {  # the fork's base takes origin/<base> when nothing local is lost: a fast-forward, or the same tree (the gated merge)
  $G fetch -q origin "$base" 2>/dev/null || return 1
  local o; o=$($G rev-parse "origin/$base" 2>/dev/null || $G rev-parse FETCH_HEAD) || return 1
  [ "$($G rev-parse "$base")" = "$o" ] && return 0
  if [ "$($G branch --show-current)" = "$base" ]; then
    $G merge-base --is-ancestor "$base" "$o" && { $G merge -q --ff-only "$o"; return; }
  else $G merge-base --is-ancestor "$base" "$o" && { $G update-ref "refs/heads/$base" "$o"; return; }; fi
  $G diff --quiet "$base" "$o" && $G update-ref "refs/heads/$base" "$o" "$($G rev-parse "$base")"
}
$G push -q origin "refs/heads/$br:refs/heads/$br" 2>"${TMPDIR:-/tmp}/prland.$$" || { echo "PRLAND PUSH-FAILED #$n: git push origin $br: $(tail -1 "${TMPDIR:-/tmp}/prland.$$")"; rm -f "${TMPDIR:-/tmp}/prland.$$"; exit 3; }
rm -f "${TMPDIR:-/tmp}/prland.$$"
head=$($G rev-parse "$br")
$G fetch -q origin "$base" 2>/dev/null
if $G merge-base --is-ancestor "$head" "origin/$base" 2>/dev/null; then sync; echo "PRLAND MERGED #$n: origin/$base already holds $br (${head:0:9})"; exit 0; fi
enc=$(printf %s "$br" | jq -sRr @uri)
pr=$(gh api "repos/$repo/pulls?state=open&head=${repo%%/*}%3A$enc&per_page=100" 2>/dev/null | jq -r --arg b "$br" '.[]?|select(.head.ref==$b)|.number' 2>/dev/null | head -1)
if [ -z "$pr" ]; then
  pr=$(gh api --method POST "repos/$repo/pulls" -f title="$br: GitHub issue #$n" -f head="$br" -f base="$base" \
       -f body="$(printf 'Refs #%s\n\nGitHub issue #%s, landed by its pull request: the local gate passed on %s at %s.' "$n" "$n" "$base" "${head:0:9}")" 2>/dev/null | jq -r '.number // empty' 2>/dev/null)
  case "$pr" in ''|*[!0-9]*) echo "PRLAND REFUSED #$n: no PR opened for $br in $repo"; exit 3;; esac
  echo "PRLAND OPENED #$n: $repo#$pr from $br (${head:0:9})"
fi
ck=$(gh api "repos/$repo/commits/$head/check-runs?per_page=100" 2>/dev/null | jq -r '[.check_runs[]|if .status!="completed" then "pending" elif (.conclusion|IN("success","neutral","skipped")) then "ok" else "red" end]|if length==0 then "none" elif any(.=="red") then "red" elif any(.=="pending") then "pending" else "green" end' 2>/dev/null)
case "$ck" in
  green) ;;
  red) echo "PRLAND RED #$n: a check failed on $repo#$pr at ${head:0:9}; not merged"; exit 1;;
  *) echo "PRLAND WAIT #$n: $repo#$pr checks ${ck:-unreadable} at ${head:0:9}; merged only on a green check"; exit 2;;
esac
gh api --method PUT "repos/$repo/pulls/$pr/merge" -f sha="$head" -f merge_method=merge -f commit_title="Merge $br (Refs #$n)" >/dev/null 2>&1 \
  || { echo "PRLAND WAIT #$n: $repo#$pr did not merge at ${head:0:9} (GitHub refused; the next pass retries)"; exit 2; }
sync || echo "PRLAND SYNC-WAIT #$n: $base keeps local commits origin/$base lacks; not moved"
echo "PRLAND MERGED #$n: $repo#$pr merged on a green check ($br ${head:0:9})"
