#!/bin/bash
# usage: red-tier.sh record <state> <tree> <sha> <workflow>   the failing job names of a red tier arrive on stdin
#        red-tier.sh files <state>                            the recorded test files, one per line
#        red-tier.sh run <state> <checkout>                   the recorded files, run in a checkout of the dev tip
#        red-tier.sh alert <state> <sha>                      one alerts.log line per tip, naming the files
#        red-tier.sh clear <state>                            stg holds a green tier: the record goes
# The record is $state/promote-red-tier: the red sha on its first line, then the test files of the failing jobs. A job's
# files are the shard it runs: the workflow names `tests/ci_shards.py <k>` in its job, and the shard is read at the red sha.
# A job that names no shard adds no file.
d=$(cd "$(dirname "$0")" && pwd)
cmd=$1; state=$2; rec=$state/promote-red-tier
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
      git -C "$tree" show "$sha:tests/ci_shards.py" | python3 - "$k" 2>/dev/null
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
  if [ -n "${SG_RED_TIER_PYTEST:-}" ]; then "$SG_RED_TIER_PYTEST" $files
  else
    . "$d/caps.sh"
    "$(sg_gate_python)" -m pytest -p no:deepeval -q $files
  fi
  ;;
alert)
  sha=$3; m=$state/auto/red-tier-${sha:0:12}
  [ -e "$m" ] && exit 0
  mkdir -p "$state/auto"; : > "$m"
  printf '%s PROMOTE RED TIER %s: the files of the red complete tier fail on the dev tip: %s\n' "$(date '+%F %T')" "${sha:0:9}" \
    "$(bash "$d/red-tier.sh" files "$state" | tr '\n' ' ')" >> "$state/alerts.log"
  ;;
clear)
  rm -f "$rec"
  ;;
esac
