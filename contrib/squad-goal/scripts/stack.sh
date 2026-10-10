#!/bin/bash
# usage: stack.sh <issue>   Optimistic stacking decision for one issue, from its "## Blocked by" section:
#   READY [FORK <br>...]      every blocker is closed, or its card is in Testing or Code Complete (child passed; owner's pick
#                             2026-10-10: dispatch on Code Complete). A Code Complete blocker with fork-only work names its
#                             unmerged fork branch after FORK: merge it into the new fork branch. Testing (code merged, its e2e or fork suite
#                             running: optimistic while testing; a PR that merely says "Refs #N" proves nothing; a red e2e reopens the blocker's fix, not this child): branch from origin/$SG_BASE
#   STACK <pr> <branch> [FORK <br>...]  exactly one blocker is open (or Code Complete with a PR) and it has an open PR based on $SG_BASE (one level only), with no
#                             failing CI on its head: branch from origin/<branch>, open the PR with base <branch>, and put
#                             "Merge after #<pr>" in the body (autopilot never merges it first and retargets it after)
#   WAIT <reason>             anything else (two open blockers, a blocker with no PR, a parent that is itself stacked, red CI)
. "$(dirname "$0")/env.sh"; iss=$1
body=$(gh api repos/$SG_REPO/issues/$iss -q .body)
bl=$(printf '%s\n' "$body" | awk '/^## Blocked by/{f=1;next} /^## /{f=0} f' | grep -oE '(^|[^/[:alnum:]])#[0-9]+' | grep -oE '[0-9]+' | sort -u)
hb="${SG_HUB_DIR:-$SG_CHECKOUT/runs/.hub}/hub-board.json"   # hub's board cache
testing=" $(jq -r '.items[]?|select(.st=="Testing" and .s=="open")|.n' "$hb" 2>/dev/null | tr '\n' ' ') "
cc=" $(jq -r '.items[]?|select(.st=="Code Complete" and .s=="open")|.n' "$hb" 2>/dev/null | tr '\n' ' ') "
prs=$(gh api "repos/$SG_REPO/pulls?state=open&per_page=100")
prof() { printf '%s' "$prs" | jq -r --arg b "$1" '[.[]|select(((.title+" "+(.body//""))|test("Refs #"+$b+"\\b")))][0]|select(.)|"\(.number) \(.head.ref) \(.base.ref) \(.head.sha)"'; }
open=(); fork=(); for b in $bl; do [ "$(gh api repos/$SG_REPO/issues/$b -q .state)" = open ] || continue
  case "$testing" in *" $b "*) continue;; esac   # its card is in Testing (code merged, test pass running): does not gate
  case "$cc" in *" $b "*)   # Code Complete: a PR stacks like an open blocker; fork-only work is merged into the child's fork branch
    br=${SG_FORK_BRANCH:-gui/%n}; br=${br//%n/$b}   # an issue can carry both a PR and fork work: hand over both
    [ -n "${SG_FORK_DIR:-}" ] && [ -n "$(/usr/bin/git -C "$SG_FORK_DIR" rev-list -1 "${SG_FORK_BASE:-main}..$br" 2>/dev/null)" ] && fork+=("$br")
    [ -n "$(prof "$b")" ] && open+=("$b")
    continue;; esac
  open+=("$b"); done
fx=""; [ ${#fork[@]} -gt 0 ] && fx=" FORK ${fork[*]}"
[ ${#open[@]} = 0 ] && { echo "READY$fx"; exit 0; }
[ ${#open[@]} = 1 ] || { echo "WAIT ${#open[@]} open blockers: ${open[*]/#/#}"; exit 0; }
b=${open[0]}
pr=$(prof "$b")
[ -n "$pr" ] || { echo "WAIT blocker #$b has no open PR"; exit 0; }
read -r n ref base sha <<<"$pr"
[ "$base" = "$SG_BASE" ] || { echo "WAIT blocker #$b's PR #$n is itself stacked on $base"; exit 0; }
gh api repos/$SG_REPO/commits/$sha/check-runs -q '[.check_runs[].conclusion]|any(.=="failure" or .=="timed_out")' | grep -q true && { echo "WAIT blocker #$b's PR #$n has failing CI"; exit 0; }
echo "STACK $n $ref$fx"
