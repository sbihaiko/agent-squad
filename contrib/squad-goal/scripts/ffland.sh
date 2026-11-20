#!/bin/bash
# usage: ffland.sh <branch> <issue>      land one work branch in $SG_BASE by a git fast-forward push (no pull request).
#
# A git push counts against no REST, GraphQL or secondary write limit; a merge API call and the close/comment writes that
# followed it did. So: rebase the work branch on $SG_BASE, push the rebased tip straight to $SG_BASE as a fast-forward, and
# let the commit keep `Refs` (owner, #330): the caller closes the issue through the API, as after a merge. The branch goes
# with `git push origin :<branch>`. The work happens in a throwaway clone, never in a checkout of the owner's.
#
# Exit 0: landed; stdout `FFLAND <sha>`. Exit 1: nothing landed; stdout `FFLAND-FALLBACK <reason>`. A push a ruleset
# refuses is the expected reason. $SG_LAND_REMOTE overrides the remote.
br=$1 iss=$2; base=${SG_BASE:-}; remote=${SG_LAND_REMOTE:-https://github.com/$SG_REPO.git}
# (#1846) The base is never guessed. A hand caller that loads no profile used to land on `main`, one commit ahead of stg and
# unprovable, and the rules refuse to rewind main, so the repair took a merge. env.sh sets SG_BASE (dev) for every loop call.
[ -n "$base" ] || { echo "FFLAND-FALLBACK SG_BASE is not set: name the branch a wave lands in (env.sh sets it), never guess"; exit 1; }
d=$(cd "$(dirname "$0")" && pwd)
case "$iss" in ''|*[!0-9]*) echo "FFLAND-FALLBACK bad arguments"; exit 1;; esac
[ -n "$br" ] || { echo "FFLAND-FALLBACK no branch"; exit 1; }
export GIT_TERMINAL_PROMPT=0
W=$(mktemp -d) || { echo "FFLAND-FALLBACK no scratch dir"; exit 1; }; trap 'rm -rf "$W"' EXIT
g() { /usr/bin/git -C "$W" "$@"; }
# The landing's own commit status (#1836): `pending` on the branch tip when the landing starts, and its end on the tip the
# push landed. GitHub keeps created_at and updated_at per status, so the time a landing took sits on the commit beside the
# checks it waited for, and a status left pending is a landing that died. A report, never a gate: landstatus.sh exits 0 on
# every refusal, and SG_LAND_STATUS=off silences it.
pendsha=
status() { [ -n "$1" ] && bash "$d/landstatus.sh" "$@" >&2; return 0; }   # its report is stderr: stdout carries the one contract line

fb() { echo "FFLAND-FALLBACK $*"; status "$pendsha" failure "landing #$iss refused"; exit 1; }

g init -q . 2>/dev/null; g remote add origin "$remote"
[ -n "$(g config user.name)" ] || g config user.name "${SG_LAND_NAME:-squad-goal}"; [ -n "$(g config user.email)" ] || g config user.email "${SG_LAND_EMAIL:-squad-goal@localhost}"
o=$(g fetch -q origin "+refs/heads/$base:refs/remotes/origin/$base" "+refs/heads/$br:refs/remotes/origin/$br" 2>&1) || fb "fetch failed: $(printf '%s' "$o" | tail -1)"
g rev-parse "origin/$br" >/dev/null || fb "no branch $br"
g checkout -q -B land "origin/$br" 2>/dev/null || fb "checkout failed"
o=$(g rebase "origin/$base" 2>&1) || { g rebase --abort >/dev/null 2>&1; fb "rebase on $base conflicts: $(printf '%s' "$o" | tail -1)"; }
[ "$(g rev-list --count "origin/$base..HEAD")" -gt 0 ] || { status "$(g rev-parse "origin/$br")" success "already in $base"; echo "FFLAND-FALLBACK nothing ahead of $base"; exit 1; }
head=$(g rev-parse HEAD)
# The landing's facts are every issue the pushed commits name, not only $iss: one branch can carry two issues (#2532).
named=$(g log --format=%B "origin/$base..HEAD" | grep -oE 'Refs #[0-9]+' | grep -oE '[0-9]+' | tr '\n' ' ')
pendsha=$(g rev-parse "origin/$br")
status "$pendsha" pending "landing #$iss"
o=$(g push -q origin "HEAD:refs/heads/$base" 2>&1) || {
  case "$o" in *GH006*|*GH013*|*"rule violations"*|*"protected branch"*|*"remote rejected"*) fb "a ruleset refused the push to $base: $(printf '%s' "$o" | tr '\n' ' ' | cut -c1-200)";; esac
  fb "the push to $base failed: $(printf '%s' "$o" | tail -1)"; }
g push -q origin ":refs/heads/$br" >/dev/null 2>&1 || echo "FFLAND-NOTE branch $br was not deleted" >&2
for ci in $(printf '%s\n' "$iss" $named | sort -un); do
  python3 "$d/issue_ledger.py" landed "$SG_REPO#$ci" "$br" branch "$head" >/dev/null 2>&1 || echo "FFLAND-NOTE the landing fact of #$ci was not recorded" >&2
done
status "$head" success "landed #$iss"
echo "FFLAND $head"
