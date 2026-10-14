#!/bin/bash
# usage: ffland.sh <pr> <branch> <issue>      land one pull request by a git fast-forward push (no merge API call).
#
# A git push counts against no REST, GraphQL or secondary write limit; the merge API and the close/comment writes that
# followed it did. So: rebase the PR branch on $SG_BASE, put `Closes #<issue>` on the tip commit (the one place the loop
# allows a closing keyword: the landed commit, never the PR body), push that head to the PR branch, then push the same
# sha to $SG_BASE as a fast-forward. GitHub marks the PR merged because its head is now in the base, and closes the issue
# from the commit. The branch goes with `git push origin :<branch>`. The work happens in a throwaway clone, never in a
# checkout of the owner's.
#
# Exit 0: landed; stdout `FFLAND <sha>`. Exit 1: nothing landed; stdout `FFLAND-FALLBACK <reason>`, and the caller merges
# through the API and says so. A push a ruleset refuses is the expected reason. $SG_LAND_REMOTE overrides the remote.
pr=$1 br=$2 iss=$3; base=${SG_BASE:-main}; remote=${SG_LAND_REMOTE:-https://github.com/$SG_REPO.git}
case "$pr$iss" in ''|*[!0-9]*) echo "FFLAND-FALLBACK bad arguments"; exit 1;; esac
[ -n "$br" ] || { echo "FFLAND-FALLBACK no branch"; exit 1; }
export GIT_TERMINAL_PROMPT=0
W=$(mktemp -d) || { echo "FFLAND-FALLBACK no scratch dir"; exit 1; }; trap 'rm -rf "$W"' EXIT
g() { /usr/bin/git -C "$W" "$@"; }
fb() { echo "FFLAND-FALLBACK $*"; exit 1; }
g init -q . 2>/dev/null; g remote add origin "$remote"
[ -n "$(g config user.name)" ] || g config user.name "${SG_LAND_NAME:-squad-goal}"; [ -n "$(g config user.email)" ] || g config user.email "${SG_LAND_EMAIL:-squad-goal@localhost}"
o=$(g fetch -q origin "+refs/heads/$base:refs/remotes/origin/$base" "+refs/heads/$br:refs/remotes/origin/$br" 2>&1) || fb "fetch failed: $(printf '%s' "$o" | tail -1)"
orig=$(g rev-parse "origin/$br") || fb "no branch $br"
g checkout -q -B land "origin/$br" 2>/dev/null || fb "checkout failed"
o=$(g rebase "origin/$base" 2>&1) || { g rebase --abort >/dev/null 2>&1; fb "rebase on $base conflicts: $(printf '%s' "$o" | tail -1)"; }
[ "$(g rev-list --count "origin/$base..HEAD")" -gt 0 ] || fb "nothing ahead of $base"
if ! g log -1 --format=%B | grep -qiE "^Closes #$iss\$"; then
  g commit -q --amend -m "$(g log -1 --format=%B)

Closes #$iss" || fb "could not write Closes #$iss on the tip"
fi
head=$(g rev-parse HEAD)
if [ "$head" != "$orig" ]; then   # the PR head must equal the pushed sha, or GitHub would not see the PR as merged
  o=$(g push -q --force-with-lease="refs/heads/$br:$orig" origin "HEAD:refs/heads/$br" 2>&1) || fb "the PR branch push was refused: $(printf '%s' "$o" | tail -1)"
fi
o=$(g push -q origin "HEAD:refs/heads/$base" 2>&1) || {
  case "$o" in *GH006*|*GH013*|*"rule violations"*|*"protected branch"*|*"remote rejected"*) fb "a ruleset refused the push to $base: $(printf '%s' "$o" | tr '\n' ' ' | cut -c1-200)";; esac
  fb "the push to $base failed: $(printf '%s' "$o" | tail -1)"; }
g push -q origin ":refs/heads/$br" >/dev/null 2>&1 || echo "FFLAND-NOTE branch $br was not deleted" >&2
echo "FFLAND $head"
