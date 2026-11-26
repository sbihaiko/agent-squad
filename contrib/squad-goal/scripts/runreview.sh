#!/bin/bash
# usage: runreview.sh <run> <child> <issue>   the loop workflow's review gate (#601, ADR-0145 rule 9), in the child's worktree
# A child of the loop is reviewed by its own run: the dispatch review gate (#599) runs this command in a passed child's
# worktree, before the run merges it. It wraps childreview.sh, never review.sh: childreview.sh fetches the wave, writes the
# `.base` file the wave entry requires, and queues the status comment. An approved head is the one childcc's entry then reads,
# so the child enters its wave with no second review.
# Exit: 0 approved (the run merges the child); 3 "no review here" (the run merges it as before, and childcc reviews it later):
# no issue, or no wave for the run yet or at all; 5 "try again" (#635): a slot wait past its bound, or a review that wrote no
# verdict. The run tries again within the gate's deadline, and holds the child when the deadline runs out; 1 CHANGES or
# BLOCKED: the issue goes back to the queue at once (wave.sh requeue) with every blocking finding, and the issue's
# `requeue` fact holds the reviewed head (ADR-0149), so childcc never requeues it a second time. The run holds the child.
# 6 "no change" (#1406): the child's head equals its base, so there is nothing to review; the issue ends one no-change fail
# with the child's reason (`wave.sh fail ... nochange`, #2245, #2984), never a requeue that a later run repeats on the same
# base (no review retry, no model call), and the run exits 1. A probe child (SG_CHILD_KIND=probe, #1574) has a report
# or an issue comment for its deliverable and no tree change by design: it exits 7 (NOCHANGE_PROBE) first, with no requeue and no
# ledger `requeue` fact, and the run records `review_nochange` as a pass. A code child never takes that path.
# 4 "fix" (#570): the run offers a fix round (SQUAD_REVIEW_FIX=1: it holds the grant of the child's next entry) and the review
# is CHANGES whose findings are new or changed. Nothing is requeued; the run fixes the child in its worktree and reviews the new
# head again (SQUAD_REVIEW_ROUND names the round). Findings a round left as they were count toward the stall (revformat.py,
# markers auto/runfix-fp-<branch key> and -same-, ADR-0134); at SG_RUN_FIX_STALLS (default 3) the issue is requeued once, exit 1.
# BLOCKED, or CHANGES with no fix round on offer, requeues as above. SG_REQUEUE_CAP in wave.sh stays the outer bound.
# A requeue frees the issue while other children of the same run are still live; that is accepted.
# Every APPROVE and CHANGES verdict is a `review` fact in the issue's ledger (ADR-0151, ADR-0149): three CHANGES verdicts in
# a row end the issue needs-human, so the card takes frozen:owner over the column its work reached, one comment of its own
# names the head the last review read and its findings (#2960), and the run holds the child with no requeue (ADR-0029); identical findings twice move the next fix round
# one place down the coder chain. An APPROVE resets the streak. A BLOCKED verdict enters no streak.
# The review takes one `reviews` slot of the machine-wide budget (squad/caps.py), waiting for it: caps says so on one line.
# Stdout is the review (the run record keeps it as the gate's findings).
here="$(cd "$(dirname "$0")" && pwd)"
. "$here/env.sh"; . "$here/caps.sh"; . "$here/ghcall.sh"
run=$1 child=$2 iss=$3; NONE=3 FIX=4 AGAIN=5 NOCHANGE=6 NOCHANGE_PROBE=7
{ [ -n "$run" ] && [ -n "$child" ]; } || { echo "usage: runreview.sh <run> <child> <issue>" >&2; exit 2; }
[ -n "$iss" ] || { echo "RUNREVIEW $run $child: no issue, no review here" >&2; exit $NONE; }
[ -e "$SG_STATE/auto/wave-$run" ] || { echo "RUNREVIEW $run $child: the run has no wave, no review here (#$iss)" >&2; exit $NONE; }
br=$(git symbolic-ref --quiet --short HEAD) || { echo "RUNREVIEW $PWD is on no branch (#$iss)" >&2; exit 2; }
key=$(printf '%s' "$br" | tr / _); head=$(git rev-parse HEAD)
holder="runreview-$run-$child"
drain_stopping && { echo "RUNREVIEW $run $child: a drain is stopping the loop, no review starts (#$iss)" >&2; exit $AGAIN; }
sg_caps take reviews "$holder" --pid $$ --wait "${SG_RUN_REVIEW_WAIT:-1800}" >/dev/null
rc=$?; [ "$rc" = 0 ] || { echo "RUNREVIEW $run $child: no reviews slot (exit $rc), try again (#$iss)" >&2; exit $AGAIN; }
trap 'sg_caps release reviews "$holder" >/dev/null 2>&1' EXIT
out=$(bash "$here/childreview.sh" "$run" "$iss" 2>&1); rc=$?
printf '%s\n' "$out" >&2
md="$SG_STATE/review-$key.md"
# #1406: exit 6 is a child with no change past its base. Its review input is empty and never changes, so it is no "try again":
# the issue ends one no-change fail at this head with the child's own reason, and the run exits 1 (no review slot or model spent).
if [ "$rc" = $NOCHANGE ] && [ "${SG_CHILD_KIND:-}" = probe ]; then
  echo "RUNREVIEW $run $child: a probe child made no change by design, nothing to review or requeue (#$iss)" >&2
  exit $NOCHANGE_PROBE
fi
if [ "$rc" = $NOCHANGE ]; then
  rb9=$(printf '%s\n' "$out" | sed -n 's/.*no change past \([0-9a-f]*\),.*/\1/p' | head -1)
  why=$(head -1 "$SG_STATE/reason-$key" 2>/dev/null); [ -n "$why" ] || why="the child made no change"
  if [ "$(SG_STATE="$SG_STATE" SG_REPO="$SG_REPO" python3 "$here/issue_ledger.py" requeued "$SG_REPO#$iss" "$run" 2>/dev/null)" = "$head" ]; then
    echo "RUNREVIEW $run $child: ${head:0:9} was already requeued, no change to review (#$iss)" >&2
  else
    # #2984: a requeue let the next run take the same base and end the same way, three times. A run that changed no file ends
    # the issue's dispatch once, as the janitor's no-change landing does (#2245): wave.sh marks it for the owner with the reason.
    bash "$here/wave.sh" fail "$iss" "$run" "the child made no change past ${rb9:-its base} in run $run: $why" nochange >&2
    SG_STATE="$SG_STATE" SG_REPO="$SG_REPO" python3 "$here/issue_ledger.py" requeue "$SG_REPO#$iss" "$run" "$head" >/dev/null 2>&1   # ADR-0149
  fi
  exit 1
fi
[ "$rc" = 0 ] && [ -s "$md" ] || { echo "RUNREVIEW $run $child: no verdict for ${head:0:9} (exit $rc), try again (#$iss)" >&2; exit $AGAIN; }
cat "$md"
v=$(head -1 "$md" | tr -d '*'); v=${v#VERDICT: }; v=${v%% *}
case "$v" in
  APPROVE|CHANGES|BLOCKED) ;;
  *) echo "RUNREVIEW $run $child: verdict '$v' is none of APPROVE, CHANGES, BLOCKED: try again (#$iss)" >&2; exit $AGAIN;;
esac
fp=$(python3 "$here/revformat.py" --fingerprint "$md" 2>/dev/null) || fp=
# #2959: the key of each Blocking finding, and of each previous one the Resolved section answers, so the ledger restarts
# the streak when a round resolved every finding of the round before and raised only new ones (ADR-0151 rule 1).
fk=$(python3 "$here/revformat.py" --finding-keys "$md" 2>/dev/null | paste -sd, -) || fk=
rk=$(python3 "$here/revformat.py" --resolved-keys "$md" 2>/dev/null | paste -sd, -) || rk=
# One review of the issue is a ledger fact (ADR-0151, ADR-0149): three CHANGES verdicts in a row end it needs-human for the
# owner (a round that resolves every finding of the round before starts the count again), and identical findings twice move the next fix round one place down the coder chain. A BLOCKED verdict answers no
# finding the fix cycle can read, so it enters no streak. A ledger that refuses the event is named, never dropped.
st=; case "$v" in APPROVE) st=approve;; CHANGES) st=changes;; esac
if [ -n "$st" ]; then
  lv=$(SG_STATE="$SG_STATE" SG_REPO="$SG_REPO" python3 "$here/issue_ledger.py" review "$SG_REPO#$iss" "$run" "$st" "$fp" "${SQUAD_REVIEW_ROUND:-0}" "$fk" "$rk" 2>&1) \
    || echo "RUNREVIEW $run $child: the review fact of #$iss was refused: $lv" >&2
else lv=; fi
# The streak ended the issue needs-human: the card keeps the column its work reached and carries the owner freeze marker
# (ADR-0029 rule 8), the run holds its child, and no dispatch follows the issue.
if [ "$lv" = frozen ]; then
  lout=$(gh_send -X POST "repos/$SG_REPO/issues/$iss/labels" -f "labels[]=frozen:owner" 2>&1) \
    || printf 'RUNREVIEW %s: the freeze of #%s was refused: %s\n' "$child" "$iss" "$lout" >&2
  # The freeze posts its own comment (#2960), never the edited status comment: an edited comment keeps the time of the first
  # review, so the timeline would show it above the fix rounds and the last head would look unreviewed. The comment names the
  # head the last review read, and a marker keeps it to one comment per head, so a freeze after a rearm repeats nothing.
  rsha=$(head -1 "$SG_STATE/review-$key.sha" 2>/dev/null); rsha=${rsha:-$head}
  mk="$SG_STATE/auto/reviewfreeze-$iss"
  if [ "$(cat "$mk" 2>/dev/null)" != "$rsha" ]; then
    rj=$(SG_STATE="$SG_STATE" SG_REPO="$SG_REPO" python3 "$here/issue_ledger.py" read "$SG_REPO#$iss" review 2>/dev/null)
    streak=$(printf '%s' "$rj" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("changes", "?"))
except Exception: print("?")' 2>/dev/null)
    total=$(printf '%s' "$rj" | python3 -c 'import json,sys
try: print(len(json.load(sys.stdin).get("verdicts") or []))
except Exception: print("?")' 2>/dev/null)
    f=$(sg_cq_file "$iss-reviewfreeze-${rsha:0:9}.md" 2>/dev/null) && {
      printf 'SQUAD-NEEDS-HUMAN: %s CHANGES verdicts in a row, of %s reviews of this issue, so the loop stops building it (ADR-0151). The card is frozen for the owner (frozen:owner); removing the label and rearming the issue lets it start again. Last findings at %s:\n\n' "${streak:-?}" "${total:-?}" "$rsha"
      awk 'NR==1{next} /^## /{b=($0 ~ /^## Blocking/)} b' "$md" | /usr/bin/grep -v '^## Blocking' | /usr/bin/grep -v '^[[:space:]]*$'
    } > "$f.tmp" && mv "$f.tmp" "$f" && printf '%s\n' "$rsha" > "$mk"
  fi
  echo "RUNREVIEW $run $child: the review streak ended #$iss needs-human, so the run holds its child (frozen:owner)" >&2
  exit 1
fi
# An issue the streak already stopped stays stopped: a review queued before the freeze (a fourth CHANGES, an approval, a
# retry of an old round) holds the child here, with no fix round and no requeue. The owner's lift is the only next move.
if [ "$(SG_STATE="$SG_STATE" SG_REPO="$SG_REPO" python3 "$here/issue_ledger.py" read "$SG_REPO#$iss" state 2>/dev/null)" = needs-human ]; then
  echo "RUNREVIEW $run $child: #$iss is frozen for the owner, so the run holds its child (#$iss)" >&2
  exit 1
fi
[ "$v" = APPROVE ] && exit 0
if [ "$v" = CHANGES ] && [ "${SQUAD_REVIEW_FIX:-0}" = 1 ]; then
  [ -n "$fp" ] && same=$(python3 "$here/revformat.py" --stall "$SG_STATE/auto" runfix "$key" "$fp") || same=
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
if [ "$(SG_STATE="$SG_STATE" SG_REPO="$SG_REPO" python3 "$here/issue_ledger.py" requeued "$SG_REPO#$iss" "$run" 2>/dev/null)" = "$head" ]; then
  echo "RUNREVIEW $run $child: ${head:0:9} was already requeued (#$iss)" >&2
else
  SG_REQUEUE_RUN=$run bash "$here/wave.sh" requeue "$iss" "the review of its own diff in run $run asked for changes ($(head -1 "$md" | tr -d '*')) at ${head:0:9}:
$f" >&2
  SG_STATE="$SG_STATE" SG_REPO="$SG_REPO" python3 "$here/issue_ledger.py" requeue "$SG_REPO#$iss" "$run" "$head" >/dev/null 2>&1   # the head the child was requeued on (ADR-0149)
fi
exit 1
