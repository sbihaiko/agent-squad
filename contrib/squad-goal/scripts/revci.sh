#!/bin/bash
# usage: revci.sh <pr> <issue>   (run with nohup ... > $SG_STATE/revci-<pr>.out &)
# Moves the card to Code Complete (Testing is the post-merge e2e), reviews, waits for CI, prints one summary line and queues issue comments.
. "$(dirname "$0")/env.sh"; pr=$1 iss=$2
$BOARD $SG_OWNER $SG_PROJECT "Code Complete" $iss >/dev/null
printf "%s" "PR #$pr open; independent review running (chain in models.json)." > "$SG_STATE/cq/$iss-open$$.md"
rm=$(bash "$(dirname "$0")/review.sh" $pr $iss | head -1 | sed -n "s/^model=\([^ ]*\).*/\1/p")
sha=$(gh api repos/$SG_REPO/pulls/$pr -q .head.sha); t0=$(date +%s)
while :; do
  now=$(gh api repos/$SG_REPO/pulls/$pr -q .head.sha 2>/dev/null)
  [ -n "$now" ] && [ "$now" != "$sha" ] && { echo "#$pr ${sha:0:9} SUPERSEDED by ${now:0:9}"; exit 0; }
  r=$(gh api repos/$SG_REPO/commits/$sha/check-runs -q '[.check_runs[]|select((.name//"")!="CodeRabbit" and (.name//"")!="")]' 2>/dev/null) \
    && [ "$(echo "$r"|jq length)" -gt 0 ] && [ "$(echo "$r"|jq '[.[]|select(.status!="completed")]|length')" = 0 ] && break
  [ $(( $(date +%s) - t0 )) -gt 2700 ] && { echo "#$pr ${sha:0:9} CI_TIMEOUT after 45m"; printf "STALL CI: PR #%s @ %s has checks unfinished or missing after 45 min.\n" $pr ${sha:0:9} > "$SG_STATE/cq/$iss-cistall$$.md"; exit 1; }
  sleep 15; done
ci=$(echo "$r"|jq -r '[.[]|.conclusion]|group_by(.)|map("\(.[0]):\(length)")|join(",")')
v=$(head -1 "$SG_STATE/review-$pr.md" | tr -d '*')
echo "#$pr ${sha:0:9} $(gh api repos/$SG_REPO/pulls/$pr -q .mergeable_state) CI=$ci $v"
{ echo "Status update — PR #$pr @ ${sha:0:9}: CI $ci; review ($rm): $v."; echo; tail -n +2 "$SG_STATE/review-$pr.md"; } > "$SG_STATE/cq/$iss-res$$.md"
