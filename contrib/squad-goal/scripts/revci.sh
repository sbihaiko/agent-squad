#!/bin/bash
# usage: revci.sh <pr> <issue>   (run with nohup ... > $SG_STATE/revci-<pr>.out &)
# Moves the card to Code Complete (Testing is the post-merge e2e), reviews, waits for CI, prints one summary line and queues issue comments.
# Only the summary line is printed under the `#<pr> <sha9> ` prefix: that namespace is the selector every reader uses to
# read the run's result (autopilot.sh's `line`, autofix.sh, hub.py's pr_state, watch.sh, janitor.py). A progress or terminal
# notice has no `CI=` and no verdict, so a reader that picked one up would read a run that said nothing: it stops
# re-queueing reviews for that head and the card claims a state with no verdict behind it (PR #1706, 2026-10-11; #1767).
# Notices go out with a `NOTICE ` prefix.
. "$(dirname "$0")/env.sh"; pr=$1 iss=$2
$BOARD $SG_OWNER $SG_PROJECT "Code Complete" $iss >/dev/null
printf "%s" "PR #$pr open; independent review running (chain in models.json)." > "$(sg_cq_file "$iss-open$$.md")"
sha=$(gh api repos/$SG_REPO/pulls/$pr -q .head.sha)   # the head the review is about: a push during the review ends this run as SUPERSEDED
rm=$(bash "$(dirname "$0")/review.sh" $pr $iss | head -1 | sed -n "s/^model=\([^ ]*\).*/\1/p")
t0=$(date +%s)
dk=0; A="$SG_STATE/auto"; mkdir -p "$A"
while :; do
  now=$(gh api repos/$SG_REPO/pulls/$pr -q .head.sha 2>/dev/null)
  [ -n "$now" ] && [ "$now" != "$sha" ] && { echo "NOTICE #$pr ${sha:0:9} SUPERSEDED by ${now:0:9}"; exit 0; }
  ms=$(gh api repos/$SG_REPO/pulls/$pr -q .mergeable_state 2>/dev/null)   # a conflicting head gets no CI: report it at once so autopilot queues the rebase instead of waiting out the 45 min
  if [ "$ms" = dirty ]; then dk=$((dk+1)); [ "$dk" -ge 2 ] && { r='[]'; break; }; else dk=0; fi
  r=$(gh api repos/$SG_REPO/commits/$sha/check-runs -q '[.check_runs[]|select((.name//"")!="CodeRabbit" and (.name//"")!="")]' 2>/dev/null) \
    && [ "$(echo "$r"|jq length)" -gt 0 ] && [ "$(echo "$r"|jq '[.[]|select(.status!="completed")]|length')" = 0 ] && break
  # A push can arrive with no workflow run behind it: the required checks then sit at "Expected - waiting for status
  # to be reported" and this loop spins to the 45 min timeout. Measured on PR #1285 (2026-10-10): the fix run's push of
  # 8ced7e274 created no run at all, while the same `git push` by hand minutes later created both gates. Past the grace
  # window a head with zero check runs is dispatched once, which is what a person would do.
  if [ ! -e "$A/cidisp-${sha:0:9}" ] && [ $(( $(date +%s) - t0 )) -gt 90 ] && [ "$(gh api "repos/$SG_REPO/commits/$sha/check-runs?per_page=1" -q .total_count 2>/dev/null)" = 0 ]; then
    touch "$A/cidisp-${sha:0:9}"
    ref=$(gh api repos/$SG_REPO/pulls/$pr -q .head.ref 2>/dev/null)
    if [ -n "$ref" ] && gh workflow run "${SG_CI_WORKFLOW:-checks.yml}" -R "$SG_REPO" --ref "$ref" >/dev/null 2>&1; then
      echo "NOTICE #$pr ${sha:0:9} CI_DISPATCH: the head has no check run at all, so the gate would wait forever; ${SG_CI_WORKFLOW:-checks.yml} dispatched on $ref"
      printf "CI dispatch: PR #%s @ %s came out of the fix run with no workflow run behind it, so none of the required checks would ever report. \`%s\` was dispatched on \`%s\`.\n" $pr ${sha:0:9} "${SG_CI_WORKFLOW:-checks.yml}" "$ref" > "$(sg_cq_file "$iss-cidisp$$.md")"
    fi
  fi
  [ $(( $(date +%s) - t0 )) -gt 2700 ] && { echo "NOTICE #$pr ${sha:0:9} CI_TIMEOUT after 45m"; printf "STALL CI: PR #%s @ %s has checks unfinished or missing after 45 min.\n" $pr ${sha:0:9} > "$(sg_cq_file "$iss-cistall$$.md")"; exit 1; }
  sleep 15; done
ci=$(echo "$r"|jq -r '[.[]|.conclusion]|group_by(.)|map("\(.[0]):\(length)")|join(",")'); [ -n "$ci" ] || ci=none
v=$(head -1 "$SG_STATE/review-$pr.md" | tr -d '*')
# the run's summary line: the only line this script prints in the `#<pr> <sha9> ` namespace, and so the only one a reader selects
echo "#$pr ${sha:0:9} $(gh api repos/$SG_REPO/pulls/$pr -q .mergeable_state) CI=$ci $v"
{ echo "Status update — PR #$pr @ ${sha:0:9}: CI $ci; review ($rm): $v."; echo; tail -n +2 "$SG_STATE/review-$pr.md"; } > "$(sg_cq_file "$iss-res$$.md")"
