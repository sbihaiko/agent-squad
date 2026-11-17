#!/bin/bash
# usage: . "$(dirname "$0")/mainproof.sh"      then: sg_complete_proof <sha> [<workflow file>]
#
# The one reader of a sha's complete tier (ADR-0173, #2039). promote-main.sh <sha> reads it before it pushes to main, and
# promote-main-pass.sh reads it to call a stg tip green, so the script that moves main and the pass that asks for the proof
# cannot disagree about what a green tier is.
#
# sg_complete_proof <sha> [<workflow file>]: one row per complete-tier job of the sha, `<name> TAB <state> [TAB <detail>]`.
# The complete tier is the jobs the workflow starts on the `promote` label, read by promote-pass.sh --ci-complete-job-names
# from the same workflow file the pass resolves: the second argument, else SG_CI_WORKFLOW, else the checkout's ci.yml.
#   green     the job's newest completed, non-skipped run ended success
#   missing   the job has no completed, non-skipped run: no run yet, a skip, or a run still open
#   red       that run ended a conclusion that measured a fail
#   unproven  that run ended a conclusion of the one unproven set (promote-pass.sh --ci-unproven-conclusions): it measured
#             nothing, so it is never a pass
# The detail names the conclusion, so a caller can say which job and what it answered. A skipped run is no evidence (another
# event's skip), so it never answers for a name. A workflow file that names no complete-tier job answers exit 3, and its
# caller reads that as no proof, never as a vacuous pass.
# Exit 0: the rows are printed. 2: the check runs could not be read, or the answer is not a check-run list: nothing is
# proven. 3: the workflow names no complete-tier job: nothing can prove the sha.
sg_complete_proof() {
  local sha=${1:?sg_complete_proof: a sha is required} f=${2:-} d names unp cr
  [ -n "$sha" ] || return 2
  d=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  if [ -z "$f" ]; then
    case "${SG_CI_WORKFLOW:-}" in
      "") f=${SG_CHECKOUT:-$SG_SQUAD}/.github/workflows/ci.yml;;
      */*) f=$SG_CI_WORKFLOW;;
      *) f=${SG_CHECKOUT:-$SG_SQUAD}/.github/workflows/$SG_CI_WORKFLOW;;
    esac
  fi
  names=$(bash "$d/promote-pass.sh" --ci-complete-job-names "$f" 2>/dev/null | jq -Rnc '[inputs | select(. != "")]' 2>/dev/null)
  names=${names:-[]}
  [ "$names" = "[]" ] && return 3
  unp=$(bash "$d/promote-pass.sh" --ci-unproven-conclusions | jq -Rnc '[inputs | select(. != "")]' 2>/dev/null)
  unp=${unp:-[]}
  cr=$(gh_api "repos/$SG_REPO/commits/$sha/check-runs?per_page=100&filter=all" 2>/dev/null) || return 2
  printf '%s' "$cr" | jq -e '.check_runs | type == "array"' >/dev/null 2>&1 || return 2
  printf '%s' "$cr" | jq -r --argjson names "$names" --argjson unp "$unp" '
    .check_runs as $r | $names[] as $n
    | ([$r[] | select(.name == $n) | select(.status == "completed") | select(.conclusion != "skipped")] | sort_by(.id) | last) as $l
    | if $l == null then "\($n)\tmissing\t"
      elif $l.conclusion == "success" then "\($n)\tgreen\t"
      elif any($unp[]; . == $l.conclusion) then "\($n)\tunproven\t\($l.conclusion)"
      else "\($n)\tred\t\($l.conclusion)" end' || return 2
  return 0
}
