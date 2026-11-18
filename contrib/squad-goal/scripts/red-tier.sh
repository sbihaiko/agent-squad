#!/bin/bash
# usage: red-tier.sh record <state> <tree> <sha> <workflow>   the failing job names of a red tier arrive on stdin
#        red-tier.sh files <state>                            the recorded test files, one per line
#        red-tier.sh run <state> <checkout>                   the recorded files, run in a checkout of the dev tip; its output is $state/red-tier-run.log
#        red-tier.sh failed <state>                           the failing tests the run's log names, else a line that names none
#        red-tier.sh alert <state> <sha>                      one alerts.log line per tip, naming the failing tests
#        red-tier.sh clear <state>                            stg holds a green tier: the record goes
# The record is $state/promote-red-tier: the red sha on its first line, then the test files of the failing jobs. A job's
# files are the FAILED or ERROR files its job log names, or else the shard it runs: the workflow names `tests/ci_shards.py <k>`
# in its job, and the shard is read at the red sha. A job that names no shard adds no file.
d=$(cd "$(dirname "$0")" && pwd)
cmd=$1; state=$2; rec=$state/promote-red-tier; runlog=$state/red-tier-run.log
# red_job_files <sha> <job>: the files a job's log names as FAILED or ERROR, one per line; none when no log names one
red_job_files() {
  . "$d/ghcall.sh"
  local id
  id=$(gh_api "repos/$SG_REPO/commits/$1/check-runs?per_page=100&filter=all" -q '[.check_runs[] | select(.name == "'"$2"'" and .status == "completed" and .conclusion != "skipped")] | sort_by(.id) | last | .id // empty' 2>/dev/null) || return 0
  [ -n "$id" ] || return 0
  gh_get "repos/$SG_REPO/actions/jobs/$id/logs" 2>/dev/null | grep -oE '(FAILED|ERROR) tests/[^ :]+[.]py' | awk '{ print $2 }'
}
case "$cmd" in
record)
  tree=$3; sha=$4; wf=$5
  shards=$(awk '
    /^    name:[[:space:]]+/ { cur = $0; sub(/^    name:[[:space:]]+/, "", cur) }
    /ci_shards[.]py [0-9]/ { k = $0; sub(/.*ci_shards[.]py /, "", k); sub(/[^0-9].*/, "", k); print cur "\t" k }' "$wf")
  files=$(while IFS= read -r job; do
      [ -n "$job" ] || continue
      k=$(printf '%s\n' "$shards" | awk -F'\t' -v j="$job" '$1 == j { print $2; exit }')
      [ -n "$k" ] || continue
      named=$(red_job_files "$sha" "$job")
      if [ -n "$named" ]; then printf '%s\n' "$named"; else git -C "$tree" show "$sha:tests/ci_shards.py" | python3 - "$k" 2>/dev/null; fi
    done | tr ' ' '\n' | sed '/^$/d' | awk '!seen[$0]++')
  mkdir -p "$state"
  { printf '%s\n' "$sha"; [ -z "$files" ] || printf '%s\n' "$files"; } > "$rec.$$" && mv -f "$rec.$$" "$rec"
  ;;
files)
  if [ -s "$rec" ]; then tail -n +2 "$rec"; fi
  ;;
run)
  files=$(bash "$d/red-tier.sh" files "$state")
  [ -n "$files" ] || exit 0
  cd "$3" || exit 1
  if [ -n "${SG_RED_TIER_PYTEST:-}" ]; then "$SG_RED_TIER_PYTEST" $files > "$runlog" 2>&1
  else
    . "$d/caps.sh"
    "$(sg_gate_python)" -m pytest -p no:deepeval -q $files > "$runlog" 2>&1
  fi
  ;;
failed)
  names=$(grep -oE '(FAILED|ERROR) [^ ]+' "$runlog" 2>/dev/null | awk '{ print $2 }' | awk '!seen[$0]++' | tr '\n' ' ')
  printf '%s\n' "${names:-no failing test named; output in $runlog}"
  ;;
alert)
  sha=$3; m=$state/auto/red-tier-${sha:0:12}
  [ -e "$m" ] && exit 0
  mkdir -p "$state/auto"; : > "$m"
  printf '%s PROMOTE RED TIER %s: the tests of the red complete tier fail on the dev tip: %s\n' "$(date '+%F %T')" "${sha:0:9}" \
    "$(bash "$d/red-tier.sh" failed "$state")" >> "$state/alerts.log"
  ;;
clear)
  rm -f "$rec"
  ;;
esac
