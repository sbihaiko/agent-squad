#!/bin/bash
# usage: stack.sh <issue>   Optimistic stacking decision for one issue, from its "## Blocked by" section:
#   READY                    every blocker is closed, or its card is in Testing or Code Complete. Testing (code merged, its e2e
#                             running: optimistic while testing; a PR that merely says "Refs #N" proves nothing; a red e2e reopens the blocker's fix, not this child): branch from origin/$SG_BASE
#   STACK <pr> <branch>      exactly one blocker is open (or Code Complete with a PR) and it has an open PR (stacked chains up to SG_STACK_DEPTH, default 3), with no
#                             failing CI on its head: branch from origin/<branch>, open the PR with base <branch>, and put
#                             "Merge after #<pr>" in the body (autopilot never merges it first and retargets it after)
#   WAIT <reason>             anything else (two open blockers, a blocker with no PR, a parent that is itself stacked, red CI)
# A caller that already holds the facts hands them over and saves the reads (#383): SG_STACK_ISSUE names a file with the
# issue's entry of the open-issue list (ghread.py's shape, each blocker's state beside it), SG_STACK_PULLS a file with
# the open-PR list. Without them, or for a blocker whose state the entry lacks, the issue, the list and the state are read.
. "$(dirname "$0")/env.sh"; . "$(dirname "$0")/ghcall.sh"; iss=$1
if [ -n "${SG_STACK_ISSUE:-}" ] && [ -s "$SG_STACK_ISSUE" ]; then issue=$(cat "$SG_STACK_ISSUE")
else issue=$(gh_api "repos/$SG_REPO/issues/$iss") || { echo "WAIT issue #$iss is not readable"; exit 0; }; fi
# An answer that does not carry the relation (no issue, a REST shape) is not "no blockers": dispatch waits until it is read.
printf '%s' "$issue" | jq -e '.blocked_by_read == true' >/dev/null 2>&1 || { echo "WAIT the blocked-by relation of #$iss is not readable"; exit 0; }
body=$(printf '%s' "$issue" | jq -r '.body // ""')
# GitHub's blocked-by relation is the dispatch fact. The body remains a human-facing mirror, so a stale body edge is named
# once but never gates this decision.
bl=$(printf '%s' "$issue" | jq -r '.blocked_by[]?' | sort -nu)
body_bl=$(printf '%s\n' "$body" | awk '/^## Blocked by/{f=1;next} f&&/^## /{exit} f&&/^[[:space:]]*$/{next} f&&!/^[[:space:]]*([-*] |[0-9]+\. )/{exit} f' | grep -oE '(^|[^/[:alnum:]])#[0-9]+' | grep -oE '[0-9]+' | sort -nu)
for b in $body_bl; do case " $(echo $bl) " in *" $b "*) ;; *) echo "STACK NOTE #$iss: body names blocker #$b without a native blocked-by relation" >&2;; esac; done
hb="${SG_HUB_DIR:-$SG_CHECKOUT/runs/.hub}/hub-board.json"   # hub's board cache
testing=" $(jq -r '.items[]?|select(.st=="Testing" and .s=="open")|.n' "$hb" 2>/dev/null | tr '\n' ' ') "
cc=" $(jq -r '.items[]?|select(.st=="Code Complete" and .s=="open")|.n' "$hb" 2>/dev/null | tr '\n' ' ') "
if [ -n "${SG_STACK_PULLS:-}" ] && [ -f "$SG_STACK_PULLS" ]; then prs=$(cat "$SG_STACK_PULLS"); else prs=$(gh api "repos/$SG_REPO/pulls?state=open&per_page=100"); fi
prof() { printf '%s' "$prs" | jq -r --arg b "$1" '[.[]|select(((.title+" "+(.body//""))|test("Refs #"+$b+"\\b")))][0]|select(.)|"\(.number) \(.head.ref) \(.base.ref) \(.head.sha)"'; }
open=(); for b in $bl; do st=$(printf '%s' "$issue" | jq -r --argjson b "$b" '[.blockers[]?|select(.number==$b)|.state|select(.)][0] // empty' 2>/dev/null)
  [ -n "$st" ] || st=$(gh api repos/$SG_REPO/issues/$b -q .state 2>/dev/null)
  [ "$st" = closed ] && continue
  [ "$st" = open ] || { echo "WAIT blocker #$b is not readable"; exit 0; }   # an unread state never counts as closed
  case "$testing" in *" $b "*) continue;; esac   # its card is in Testing (code merged, test pass running): does not gate
  case "$cc" in *" $b "*)   # Code Complete: a PR stacks like an open blocker; fork-only work is merged into the child's fork branch
    br=$(sg_fork_branch "$b")   # an issue can carry both a PR and fork work: hand over both
    [ -n "${SG_FORK_DIR:-}" ] && [ -n "$(/usr/bin/git -C "$SG_FORK_DIR" rev-list -1 "${SG_FORK_BASE:-main}..$br" 2>/dev/null)" ] && fork+=("$br")
    open+=("$b")   # with no open PR it gates (WAIT below): a column is not proof that the work exists
    continue;; esac
  open+=("$b"); done
fx=""; [ ${#fork[@]} -gt 0 ] && fx=" FORK ${fork[*]}"
[ ${#open[@]} = 0 ] && { echo "READY$fx"; exit 0; }
[ ${#open[@]} = 1 ] || { echo "WAIT ${#open[@]} open blockers: ${open[*]/#/#}"; exit 0; }
b=${open[0]}
pr=$(prof "$b")
[ -n "$pr" ] || { echo "WAIT blocker #$b has no open PR"; exit 0; }
read -r n ref base sha <<<"$pr"
if [ "$base" != "$SG_BASE" ]; then   # nested stack: allowed up to SG_STACK_DEPTH levels (autopilot retargets one level per parent merge)
  d=1 cur=$base; while [ "$cur" != "$SG_BASE" ]; do d=$((d+1)); [ $d -lt "${SG_STACK_DEPTH:-3}" ] || { echo "WAIT blocker #$b's PR #$n sits $d levels deep (cap ${SG_STACK_DEPTH:-3})"; exit 0; }
    cur=$(printf '%s' "$prs" | jq -r --arg h "$cur" '[.[]|select(.head.ref==$h)][0].base.ref // empty'); [ -n "$cur" ] || { echo "WAIT blocker #$b's PR #$n is stacked on $base, whose parent PR is gone"; exit 0; }; done
fi
gh api repos/$SG_REPO/commits/$sha/check-runs -q '[.check_runs[].conclusion]|any(.=="failure" or .=="timed_out")' | grep -q true && { echo "WAIT blocker #$b's PR #$n has failing CI"; exit 0; }
echo "STACK $n $ref"
