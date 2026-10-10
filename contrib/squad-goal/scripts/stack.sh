#!/bin/bash
# usage: stack.sh <issue>   Optimistic stacking decision for one issue, from its "## Blocked by" section:
#   READY                     every blocker is closed, or its card is in Testing (code merged, its e2e or fork suite
#                             running: optimistic while testing; a PR that merely says "Refs #N" proves nothing; a red e2e reopens the blocker's fix, not this child): branch from origin/$SG_BASE
#   STACK <pr> <branch>       exactly one blocker is open and it has an open PR based on $SG_BASE (one level only), with no
#                             failing CI on its head: branch from origin/<branch>, open the PR with base <branch>, and put
#                             "Merge after #<pr>" in the body (autopilot never merges it first and retargets it after)
#   WAIT <reason>             anything else (two open blockers, a blocker with no PR, a parent that is itself stacked, red CI)
. "$(dirname "$0")/env.sh"; iss=$1
body=$(gh api repos/$SG_REPO/issues/$iss -q .body)
bl=$(printf '%s\n' "$body" | awk '/^## Blocked by/{f=1;next} /^## /{f=0} f' | grep -oE '(^|[^/[:alnum:]])#[0-9]+' | grep -oE '[0-9]+' | sort -u)
testing=" $(jq -r '.items[]?|select(.st=="Testing" and .s=="open")|.n' "${SG_HUB_DIR:-$SG_CHECKOUT/runs/.hub}/hub-board.json" 2>/dev/null | tr '\n' ' ') "   # hub's board cache
open=(); for b in $bl; do [ "$(gh api repos/$SG_REPO/issues/$b -q .state)" = open ] || continue
  case "$testing" in *" $b "*) continue;; esac   # its card is in Testing (code merged, test pass running): does not gate
  open+=("$b"); done
[ ${#open[@]} = 0 ] && { echo READY; exit 0; }
[ ${#open[@]} = 1 ] || { echo "WAIT ${#open[@]} open blockers: ${open[*]/#/#}"; exit 0; }
b=${open[0]}
pr=$(gh api "repos/$SG_REPO/pulls?state=open&per_page=100" | jq -r --arg b "$b" '[.[]|select(((.title+" "+(.body//""))|test("Refs #"+$b+"\\b")))][0]|select(.)|"\(.number) \(.head.ref) \(.base.ref) \(.head.sha)"')
[ -n "$pr" ] || { echo "WAIT blocker #$b has no open PR"; exit 0; }
read -r n ref base sha <<<"$pr"
[ "$base" = "$SG_BASE" ] || { echo "WAIT blocker #$b's PR #$n is itself stacked on $base"; exit 0; }
gh api repos/$SG_REPO/commits/$sha/check-runs -q '[.check_runs[].conclusion]|any(.=="failure" or .=="timed_out")' | grep -q true && { echo "WAIT blocker #$b's PR #$n has failing CI"; exit 0; }
echo "STACK $n $ref"
