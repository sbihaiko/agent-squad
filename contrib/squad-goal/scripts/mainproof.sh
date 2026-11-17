#!/bin/bash
# usage: . "$(dirname "$0")/ghcall.sh"; . "$(dirname "$0")/mainproof.sh"
#        then: sg_complete_proof <sha> [<workflow file>]   or: sg_check_run_rows <sha> <names-json> <unproven-json>
#
# The one reader of a sha's complete tier (ADR-0173, #2039). promote-main.sh <sha> reads it before it pushes to main, and
# promote-main-pass.sh reads it to call a stg tip green, so the script that moves main and the pass that asks for the proof
# cannot disagree about what a green tier is. The caller sources ghcall.sh first: the reader calls gh_api.
#
# sg_complete_proof <sha> [<workflow file>]: one row per complete-tier job of the sha, `<name> TAB <state> [TAB <detail>]`.
# The complete tier is the jobs the workflow starts on the `promote` label, read by promote-pass.sh --ci-complete-job-names.
# The workflow file is the second argument, else SG_CI_WORKFLOW, else the checkout's ci.yml. The checkout is SG_CHECKOUT, else
# SG_SQUAD, else this script's own tree, so a hand run needs no environment. A workflow file that cannot be read answers
# exit 3 and names the path: it proves nothing, and never reads as a vacuous pass.
# sg_check_run_rows <sha> <names-json> <unproven-json>: the rows for exactly the names and the unproven set it is given, with
# no second resolution. promote-main-pass.sh calls it with the names and the set it already resolved, so the pass names the
# tier once and the row rule stays the one rule.
#   green     the job's newest completed, non-skipped run ended success
#   missing   the job has no completed, non-skipped run: no run yet, a skip, or a run still open
#   red       that run ended a conclusion that measured a fail
#   unproven  that run ended a conclusion of the one unproven set (promote-pass.sh --ci-unproven-conclusions): it measured
#             nothing, so it is never a pass
# The detail names the conclusion, so a caller can say which job and what it answered. A skipped run is no evidence (another
# event's skip), so it never answers for a name. A workflow file that names no complete-tier job answers exit 3, and its
# caller reads that as no proof, never as a vacuous pass.
# Exit 0: the rows are printed. 2: the check runs could not be read, or the answer is not a check-run list: nothing is
# proven. 3: the workflow names no complete-tier job, or cannot be read: nothing can prove the sha. On any failure stdout
# carries one line that names the cause, so a caller says why instead of a bare failure: a caller reads it in its command
# substitution, where the exit code arrives with it.
sg_complete_checkout() {  # <this script's dir>: the checkout whose workflow file answers a caller that named none
  if [ -n "${SG_CHECKOUT:-}" ]; then printf '%s' "$SG_CHECKOUT"; return 0; fi
  if [ -n "${SG_SQUAD:-}" ]; then printf '%s' "$SG_SQUAD"; return 0; fi
  (cd "$1/../../.." && pwd)
}
sg_check_run_rows() {  # <sha> <names-json> <unproven-json>
  local sha=${1:?sg_check_run_rows: a sha is required} names=${2:-[]} unp=${3:-[]} cr
  [ "$names" = "[]" ] && { echo "the complete tier names no job, so nothing proves $sha"; return 3; }
  cr=$(gh_api "repos/$SG_REPO/commits/$sha/check-runs?per_page=100&filter=all" 2>/dev/null) \
    || { echo "the check runs of $sha could not be read"; return 2; }
  printf '%s' "$cr" | jq -e '.check_runs | type == "array"' >/dev/null 2>&1 \
    || { echo "the check-run answer for $sha is not a list"; return 2; }
  printf '%s' "$cr" | jq -r --argjson names "$names" --argjson unp "$unp" '
    .check_runs as $r | $names[] as $n
    | ([$r[] | select(.name == $n) | select(.status == "completed") | select(.conclusion != "skipped")] | sort_by(.id) | last) as $l
    | if $l == null then "\($n)\tmissing\t"
      elif $l.conclusion == "success" then "\($n)\tgreen\t"
      elif any($unp[]; . == $l.conclusion) then "\($n)\tunproven\t\($l.conclusion)"
      else "\($n)\tred\t\($l.conclusion)" end' \
    || { echo "the check runs of $sha could not be read as rows"; return 2; }
  return 0
}
sg_complete_proof() {  # <sha> [<workflow file>]
  local sha=${1:?sg_complete_proof: a sha is required} f=${2:-} d names unp
  [ -n "$sha" ] || { echo "no sha is named"; return 2; }
  d=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  if [ -z "$f" ]; then
    case "${SG_CI_WORKFLOW:-}" in
      "") f=$(sg_complete_checkout "$d")/.github/workflows/ci.yml;;
      */*) f=$SG_CI_WORKFLOW;;
      *) f=$(sg_complete_checkout "$d")/.github/workflows/$SG_CI_WORKFLOW;;
    esac
  fi
  [ -f "$f" ] || { echo "the workflow file $f cannot be read, so nothing proves $sha"; return 3; }
  names=$(bash "$d/promote-pass.sh" --ci-complete-job-names "$f" 2>/dev/null | jq -Rnc '[inputs | select(. != "")]' 2>/dev/null)
  names=${names:-[]}
  [ "$names" = "[]" ] && { echo "the workflow $f names no complete tier job, so nothing proves $sha"; return 3; }
  unp=$(bash "$d/promote-pass.sh" --ci-unproven-conclusions | jq -Rnc '[inputs | select(. != "")]' 2>/dev/null)
  sg_check_run_rows "$sha" "$names" "${unp:-[]}"
}
