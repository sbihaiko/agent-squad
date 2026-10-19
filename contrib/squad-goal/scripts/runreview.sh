#!/bin/bash
# usage: runreview.sh <run> <child> <issue>   the loop workflow's review gate (#601, ADR-0145 rule 9), in the child's worktree
# A child of the loop is reviewed by its own run: the dispatch review gate (#599) runs this command in a passed child's
# worktree, before the run merges it. It wraps childreview.sh, never review.sh: childreview.sh fetches the wave, writes the
# `.base` file the wave entry requires, and queues the status comment. An approved head is the one childcc's entry then reads,
# so the child enters its wave with no second review.
# Exit: 0 approved (the run merges the child); 3 "no review here" (the run merges it as before, and childcc reviews it later):
# no issue, or no wave for the run yet or at all; 5 "try again" (#635): a slot wait past its bound, or a review that wrote no
# verdict. The run tries again within the gate's deadline, and holds the child when the deadline runs out; 1 CHANGES or
# BLOCKED: the issue goes back to the queue at once (wave.sh requeue) with every blocking finding, and the marker
# auto/requeued-<run>-<child> holds the reviewed head, so childcc never requeues it a second time. The run holds the child.
# 4 "fix" (#570): the run offers a fix round (SQUAD_REVIEW_FIX=1: it holds the grant of the child's next entry) and the review
# is CHANGES whose findings are new or changed. Nothing is requeued; the run fixes the child in its worktree and reviews the new
# head again (SQUAD_REVIEW_ROUND names the round). Findings a round left as they were count toward the stall (revformat.py,
# markers auto/runfix-fp-<branch key> and -same-, ADR-0134); at SG_RUN_FIX_STALLS (default 3) the issue is requeued once, exit 1.
# BLOCKED, or CHANGES with no fix round on offer, requeues as above. SG_REQUEUE_CAP in wave.sh stays the outer bound.
# A requeue frees the issue while other children of the same run are still live; that is accepted.
# The review takes one `reviews` slot of the machine-wide budget (squad/caps.py), waiting for it: caps says so on one line.
# Stdout is the review (the run record keeps it as the gate's findings).
here="$(cd "$(dirname "$0")" && pwd)"
. "$here/env.sh"; . "$here/caps.sh"
run=$1 child=$2 iss=$3; NONE=3 FIX=4 AGAIN=5
{ [ -n "$run" ] && [ -n "$child" ]; } || { echo "usage: runreview.sh <run> <child> <issue>" >&2; exit 2; }
[ -n "$iss" ] || { echo "RUNREVIEW $run $child: no issue, no review here" >&2; exit $NONE; }
[ -e "$SG_STATE/auto/wave-$run" ] || { echo "RUNREVIEW $run $child: the run has no wave, no review here (#$iss)" >&2; exit $NONE; }
br=$(git symbolic-ref --quiet --short HEAD) || { echo "RUNREVIEW $PWD is on no branch (#$iss)" >&2; exit 2; }
key=$(printf '%s' "$br" | tr / _); head=$(git rev-parse HEAD); mk="$SG_STATE/auto/requeued-$run-$child"
holder="runreview-$run-$child"
sg_caps take reviews "$holder" --pid $$ --wait "${SG_RUN_REVIEW_WAIT:-1800}" >/dev/null
rc=$?; [ "$rc" = 0 ] || { echo "RUNREVIEW $run $child: no reviews slot (exit $rc), try again (#$iss)" >&2; exit $AGAIN; }
trap 'sg_caps release reviews "$holder" >/dev/null 2>&1' EXIT
out=$(bash "$here/childreview.sh" "$run" "$iss" 2>&1); rc=$?
printf '%s\n' "$out" >&2
md="$SG_STATE/review-$key.md"
[ "$rc" = 0 ] && [ -s "$md" ] || { echo "RUNREVIEW $run $child: no verdict for ${head:0:9} (exit $rc), try again (#$iss)" >&2; exit $AGAIN; }
cat "$md"
v=$(head -1 "$md" | tr -d '*'); v=${v#VERDICT: }; v=${v%% *}
case "$v" in
  APPROVE) exit 0;;
  CHANGES|BLOCKED) ;;
  *) echo "RUNREVIEW $run $child: verdict '$v' is none of APPROVE, CHANGES, BLOCKED: try again (#$iss)" >&2; exit $AGAIN;;
esac
if [ "$v" = CHANGES ] && [ "${SQUAD_REVIEW_FIX:-0}" = 1 ]; then
  fp=$(python3 "$here/revformat.py" --fingerprint "$md") && same=$(python3 "$here/revformat.py" --stall "$SG_STATE/auto" runfix "$key" "$fp") || same=
  if [ -n "$same" ] && [ "$same" -lt "${SG_RUN_FIX_STALLS:-3}" ]; then
    python3 "$here/revformat.py" --record-stall "$SG_STATE/auto" runfix "$key" "$fp" "$same"
    echo "RUNREVIEW $run $child: CHANGES at ${head:0:9}, round ${SQUAD_REVIEW_ROUND:-?}, $same failed rounds with no change before it: fix round (#$iss)" >&2
    exit $FIX
  fi
  echo "RUNREVIEW $run $child: ${same:-?} fix rounds left the same findings at ${head:0:9}: the fix rounds stop (ADR-0134) (#$iss)" >&2
fi
# Every blocking finding: the `## Blocking` section when the review has one, else the whole body.
f=$(awk 'NR==1{next} /^## /{b=($0 ~ /^## Blocking/)} b' "$md")
[ -n "$f" ] || f=$(tail -n +2 "$md")
f=$(printf '%s\n' "$f" | /usr/bin/grep -v '^## Blocking' | /usr/bin/grep -v '^[[:space:]]*$')
if [ "$(cat "$mk" 2>/dev/null)" = "$head" ]; then
  echo "RUNREVIEW $run $child: ${head:0:9} was already requeued (#$iss)" >&2
else
  SG_REQUEUE_RUN=$run bash "$here/wave.sh" requeue "$iss" "the review of its own diff in run $run asked for changes ($(head -1 "$md" | tr -d '*')) at ${head:0:9}:
$f" >&2
  mkdir -p "$SG_STATE/auto"; printf '%s\n' "$head" > "$mk"
fi
exit 1
