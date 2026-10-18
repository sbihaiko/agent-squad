#!/bin/bash
# usage: branchfixpass.sh   one pass over the pushed work branches: one fix run for each reviewed tip whose review says CHANGES (#401).
#
# branchreviewpass.sh keeps the verdict of each tip in $SG_STATE/review-<key>.{md,sha}. This pass lists the work branches as the
# review pass does (workbranches.sh) and, for each branch whose kept sha is its tip and whose verdict is CHANGES, dispatches one fix
# run on the same branch: the request is branchfix.sh's text, sent through mkreq.sh and launch.py as every pass sends a run.
# The fix run pushes a new tip; the review pass reviews it; an APPROVE lets branchland.sh land it.
# Once per reviewed tip: auto/branchfix-<key>-<sha9> holds the run id. A launch that starts no run keeps no marker, so the next pass
# retries the tip.
# Stop (ADR-0134): a round fails when the review of the next tip has the same findings (line numbers, shas and whitespace removed).
# auto/branchfix-fp-<key> keeps the findings of the last dispatched round and auto/branchfix-same-<key> the count of failed rounds in
# a row. At SG_BRANCHFIX_STALLS (default 3) the rounds stop: one status line on the issue and the label frozen:owner, once. Changed
# findings restart the count. At most SG_FIX_PASS_MAX fix runs (default 3) start per pass. watch.sh runs this under single.sh.
. "$(dirname "$0")/env.sh" 2>/dev/null || exit 0
here="$(cd "$(dirname "$0")" && pwd)"
export SG_GH_PASS=branchfix
SG_GHREAD="python3 $here/ghthrottle.py"; . "$here/ghcall.sh"; . "$here/workbranches.sh"
remote=${SG_LAND_REMOTE:-https://github.com/$SG_REPO.git}; max=${SG_FIX_PASS_MAX:-3}; cap=${SG_BRANCHFIX_STALLS:-3}
export GIT_TERMINAL_PROMPT=0
cd "$SG_STATE" || exit 0
sg_work_branches "$remote" || { echo "BRANCHFIX: $wb_err"; exit 0; }
A="$SG_STATE/auto"; mkdir -p "$A"
# An issue that carries a label the frontier skips (SG_FRONTIER_SKIP_LABELS: frozen:owner, needs-ratification, spec, ...) gets no fix
# run. The labels come from the open-issue list sg_work_branches already read, so this costs no call.
held=" $(printf '%s' "$wb_issues" | jq -r --arg s "${SG_FRONTIER_SKIP_LABELS:-}" '($s|split(",")|map(select(.!=""))) as $k
  | .[] | select([.labels[]?.name] | any(. as $l | $k | index($l))) | .number' 2>/dev/null | tr '\n' ' ') "
# The fingerprint of a review's findings and the failed rounds in a row are revformat.py's reading (ADR-0134), so a
# child fix round asks the same rule: a moved line or a new sha is not a change.
done=0
while read -r sha br i; do
  [ -n "$br" ] && [ "$done" -lt "$max" ] || continue
  case "$held" in *" $i "*) continue;; esac
  key=$(printf '%s' "$br" | tr '/' '_'); s9=${sha:0:9}; f="$SG_STATE/review-$key.md"
  [ "$(cat "$SG_STATE/review-$key.sha" 2>/dev/null)" = "$sha" ] || continue
  head -1 "$f" 2>/dev/null | grep -q CHANGES || continue
  # a CHANGES with no Blocking finding (debt only) is an approval: no fix round, as the loop's own passes treat it (revformat.py count)
  python3 "$here/revformat.py" --summary "$f" 2>/dev/null | grep -q '^blocking=0 ' && continue
  [ -e "$A/branchfix-$key-$s9" ] && continue
  fp=$(python3 "$here/revformat.py" --fingerprint "$f"); same=$(python3 "$here/revformat.py" --stall "$A" branchfix "$key" "$fp")
  if [ "$same" -ge "$cap" ]; then
    [ "$(cat "$A/branchfix-stop-$key" 2>/dev/null)" = "$fp" ] && continue
    printf 'Status update — branch %s @ %s: %s fix rounds left the same review findings, so the loop stopped its fix rounds (ADR-0134). The card is frozen for the owner (frozen:owner). A push with other findings restarts the rounds.\n' "$br" "$s9" "$same" > "$(sg_cq_status "$i" "branchfix$$")"
    o=$(gh_send --method POST "repos/$SG_REPO/issues/$i/labels" -f "labels[]=frozen:owner") \
      || { echo "BRANCHFIX STOP $br: the label write failed, the next pass retries: $o"; continue; }
    echo "$fp" > "$A/branchfix-stop-$key"
    echo "BRANCHFIX STOP $br @ $s9 (issue #$i): $same fix rounds with no change; card frozen for the owner"; continue
  fi
  b=$(mktemp)
  { echo "IMPORTANT: your first and only action is to call the spawn tool for the child below; ending the turn without a spawn call fails the run. Spawn the child with $(bash "$here/envelope-spend.sh" 2>/dev/null)."
    bash "$here/branchfix.sh" "$br" "$i"; } > "$b"
  done=$((done+1))
  if ! req=$(bash "$here/mkreq.sh" "auto-branchfix-$key-$s9" "$b"); then rm -f "$b"; echo "BRANCHFIX $br @ $s9 refused by mkreq.sh; the next pass retries" >&2; continue; fi
  rm -f "$b"
  run=$(cd "$SG_CHECKOUT" && python3 "$here/launch.py" "$req" 2>/dev/null | tail -1)
  [ -n "$run" ] || { echo "BRANCHFIX $br @ $s9: launch.py printed no run id, so no fix run started; the next pass retries"; continue; }
  /usr/bin/git -C "$SG_CHECKOUT" checkout -q -- docs/squad/workflows/dynamic.graph.json 2>/dev/null   # launch.py renames the graph root in place
  echo "$run" > "$A/branchfix-$key-$s9"; python3 "$here/revformat.py" --record-stall "$A" branchfix "$key" "$fp" "$same"; rm -f "$A/branchfix-stop-$key"
  find "$A" -maxdepth 1 -name "branchfix-$key-?????????" ! -name "*-$s9" -delete 2>/dev/null
  echo "BRANCHFIX $br @ $s9 (issue #$i): fix round dispatched ($same failed rounds with no change before it) -> $run"
done <<EOT
$wb_list
EOT
exit 0
