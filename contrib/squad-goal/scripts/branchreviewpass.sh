#!/bin/bash
# usage: branchreviewpass.sh   one pass over the pushed work branches: review each tip that has no review yet (#397).
#
# branchland.sh lands a work branch only when the review approved its tip, and branchreview.sh keeps that verdict and the sha it read
# under the branch key ($SG_STATE/review-<branch with / as _>.{md,sha}). This pass lists the work branches as branchland does
# (workbranches.sh: an open issue, no open pull request) and runs branchreview.sh once for each branch whose kept sha is not its tip.
# A tip with a kept review is not reviewed again. At most SG_REVIEW_PASS_MAX branches (default 3) are reviewed per pass; the rest wait
# for the next pass. watch.sh runs this under single.sh, so two passes never overlap.
. "$(dirname "$0")/env.sh" 2>/dev/null || exit 0
here="$(cd "$(dirname "$0")" && pwd)"
export SG_GH_PASS=branchreview
SG_GHREAD="python3 $here/ghthrottle.py"; . "$here/ghcall.sh"; . "$here/workbranches.sh"
remote=${SG_LAND_REMOTE:-https://github.com/$SG_REPO.git}; max=${SG_REVIEW_PASS_MAX:-3}
export GIT_TERMINAL_PROMPT=0
cd "$SG_STATE" || exit 0
sg_work_branches "$remote" || { echo "BRANCHREVIEW: $wb_err"; exit 0; }
A="$SG_STATE/auto"; mkdir -p "$A"; tries=${SG_REVIEW_TRIES:-3}
done=0
while read -r sha br i; do
  [ -n "$br" ] && [ "$done" -lt "$max" ] || continue
  key=$(printf '%s' "$br" | tr '/' '_'); [ "$(cat "$SG_STATE/review-$key.sha" 2>/dev/null)" = "$sha" ] && continue
  # A review that keeps no sha at the tip failed. Its count is kept per tip (ADR-0134): after $tries failed tries at one tip the
  # branch stops, said once; a new tip has a new count, and the old tip's files go.
  c="$A/branchreview-tries-$key-${sha:0:9}"; n=$(cat "$c" 2>/dev/null || echo 0); [ "$n" -ge "$tries" ] && continue
  find "$A" -maxdepth 1 -name "branchreview-*-$key-*" ! -name "*-${sha:0:9}" -delete 2>/dev/null
  out=$(bash "$here/branchreview.sh" "$br" "$i" 2>&1 | tail -1); done=$((done+1))
  if [ "$(cat "$SG_STATE/review-$key.sha" 2>/dev/null)" = "$sha" ]; then rm -f "$c"; echo "BRANCHREVIEW $out"; continue; fi
  n=$((n+1)); echo "$n" > "$c"; echo "BRANCHREVIEW FAILED $br ${sha:0:9} (try $n of $tries): $out"
  [ "$n" -ge "$tries" ] && [ ! -e "$A/branchreview-stop-$key-${sha:0:9}" ] && { touch "$A/branchreview-stop-$key-${sha:0:9}"
    echo "BRANCHREVIEW STOP $br: the review of ${sha:0:9} failed $n times; a new push starts a new count"; }
done <<EOT
$wb_list
EOT
exit 0
