#!/bin/bash
# basecheck.sh (#786): the base branch's own check. A red completed check on the base head holds every landing into that base
# (branchland.sh, wave.sh) until the head is green. Sourced by those scripts after ghcall.sh, and by promote-stg.sh.
#
# sg_base_red <base>: while the head of origin/<base> has a completed check that did not end success, skipped, neutral, cancelled
# or timed_out, prints "base <base> is red at <sha9>: <check> ended <conclusion>" and returns 0. Otherwise returns 1. A pending,
# missing or unreadable check does not block. A cancelled or timed-out check measured nothing (the complete tier ends cancelled at
# its job timeout): the head is unproven, does not block, and is not cached (ADR-0164, #1532). A failure check is red only when a
# job of its workflow run failed after it ran a step; a job that never started measured nothing, so a job GitHub could not start
# never holds a landing (#1629, ADR-0164). The answer is cached per head under $SG_STATE/auto/basecheck-<sha>, written through a
# temporary file:
#   ok <epoch>            green; kept only once the newest completed check is at least SG_BASE_CI_GRACE seconds old (default 900),
#                         and read again once SG_BASE_CI_OK_TTL seconds have passed since the read (default 600), so a re-run is seen (#790)
#   red2 <epoch> <check>  red; read again once SG_BASE_CI_RECHECK seconds have passed (default 600)
# Any other content is no answer, and the head is read again; an older `red <epoch> <check>` is such content, so a head cached
# before #1629 is refined again. A red head is reported once: one alerts.log line per head, and one queued comment on each issue
# whose landed.tsv row names that head. An unreadable read is one alerts.log line per head. An unproven or unknown answer is
# never cached and never holds the landing; it writes one alerts.log line per head naming the cause (ADR-0164, #1629).
# SG_BASE_CI_GATE=0 turns the check off.
#
# sg_ci_class: reads a check-runs answer on stdin and prints red, running, unproven, green or none (zero check runs; the caller decides what
# none means). Exits 1 when the input is not a check-run list. promote-stg.sh uses it too, so the two readers cannot disagree.
#
sg_ci_class() { jq -r 'if (.check_runs | type) != "array" then error("not a check-run list") elif (.check_runs | length) == 0 then "none" elif ([.check_runs[] | select(.status == "completed") | select(.conclusion != "success" and .conclusion != "skipped" and .conclusion != "neutral" and .conclusion != "cancelled" and .conclusion != "timed_out")] | length) > 0 then "red" elif ([.check_runs[] | select(.status != "completed")] | length) > 0 then "running" elif ([.check_runs[] | select(.conclusion == "cancelled" or .conclusion == "timed_out")] | length) > 0 then "unproven" else "green" end' 2>/dev/null; }

# sg_ci_bad: the first completed check that did not end success, skipped, neutral, cancelled or timed_out, as "<name> ended <conclusion>"; empty when none.
sg_ci_bad() { jq -r '[.check_runs[] | select(.status == "completed") | select(.conclusion != "success" and .conclusion != "skipped" and .conclusion != "neutral" and .conclusion != "cancelled" and .conclusion != "timed_out") | "\(.name) ended \(.conclusion)"] | first // ""' 2>/dev/null; }

# sg_ci_jobs <run-id>: every job of one workflow run, as one JSON array, read through the door a page at a time (#1629). The jobs
# route answers an object, {"total_count":n,"jobs":[...]}, and gh_pages reads array pages only, so each page is asked through
# gh_api and projected to its `.jobs`; a page shorter than per_page ends the read. A page the door refuses or fails ends the read
# with that page's outcome: 2 is a refusal, 1 is a failure, and neither is flattened into the other.
sg_ci_jobs() { local id=${1:?sg_ci_jobs: a run id is required} p=1 size=100 o arr n all="[]"
  while :; do
    if ! o=$(gh_api "repos/$SG_REPO/actions/runs/$id/jobs?per_page=$size&page=$p"); then
      local rc=$?; printf '%s\n' "$o"; return "$rc"; fi
    arr=$(printf '%s' "$o" | jq -c 'if type == "object" and (.jobs | type) == "array" then .jobs else error("not a job list") end' 2>/dev/null) || return 1
    all=$(printf '%s\n%s' "$all" "$arr" | jq -s 'add') || return 1
    n=$(printf '%s' "$arr" | jq 'length') || return 1
    [ "$n" -ge "$size" ] || break
    p=$((p + 1))
  done
  printf '%s' "$all"; }

# sg_ci_refine <check-runs-json>: the base check's answer with a red refined by the evidence its jobs hold (#1629, ADR-0164). A
# completed check that did not end success, skipped, neutral, cancelled or timed_out is red unless its workflow run shows, by
# affirmative evidence, that every failed job ended before its first step: a completed job whose steps list is empty. Only such
# evidence releases a fail; a run whose read was refused, failed or malformed, whose job list is empty, whose job carries no steps
# array, or whose check names no run (a check outside Actions) leaves the fail standing. A job that has not completed is neither
# a pass nor a fail, and never makes a run red by itself. A run that released its fails is quiet. The head is red when any run is
# red, else unproven. Prints one line: the class unchanged (green, running, none, unproven), "red <check> ended <conclusion>" or
# "unproven <reason>". The answer is the argument, or stdin when it is omitted. Exits 1 when the answer is not a check-run
# list. Zero steps proves "never started", never "billing".
sg_ci_refine() { local cr=${1:-} cls bad url id jobs n rd lines seen="" line
  [ -n "$cr" ] || cr=$(cat 2>/dev/null); [ -n "$cr" ] || return 1   # the answer comes as an argument or on stdin
  cls=$(printf '%s' "$cr" | sg_ci_class) || return 1
  [ "$cls" = red ] || { printf '%s\n' "$cls"; return 0; }
  lines=$(printf '%s' "$cr" | jq -r '[.check_runs[] | select(.status == "completed") | select(.conclusion != "success" and .conclusion != "skipped" and .conclusion != "neutral" and .conclusion != "cancelled" and .conclusion != "timed_out") | "\(.name) ended \(.conclusion)\t\(.details_url // "")"] | .[]' 2>/dev/null) || lines=
  [ -n "$lines" ] || { printf 'red its failed check is not readable\n'; return 0; }   # silence releases nothing
  while IFS=$'\t' read -r bad url; do
    [ -n "$bad" ] || continue
    id=$(printf '%s' "$url" | sed -n 's#.*/actions/runs/\([0-9][0-9]*\).*#\1#p')
    [ -n "$id" ] || { printf 'red %s\n' "$bad"; return 0; }   # nothing to read: the fail stands
    case " $seen " in *" $id "*) continue;; esac   # one run, one read
    seen="$seen $id"
    jobs=$(sg_ci_jobs "$id") || { printf 'red %s\n' "$bad"; return 0; }   # no evidence to release it: the fail stands
    printf '%s' "$jobs" | jq -e 'type == "array"' >/dev/null 2>&1 || { printf 'red %s\n' "$bad"; return 0; }
    n=$(printf '%s' "$jobs" | jq 'length' 2>/dev/null) || n=0
    [ "${n:-0}" -gt 0 ] || { printf 'red %s\n' "$bad"; return 0; }   # an empty list says nothing about the fail
    # A failed job releases the fail only by affirmative evidence: it completed and its steps list is empty. A failed job that
    # ran a step, that has not completed, or whose steps evidence is missing leaves the fail standing (#1629, ADR-0164).
    rd=$(printf '%s' "$jobs" | jq '[.[] | select(.conclusion == "failure") | select((.status != "completed") or ((.steps | type) != "array") or ((.steps | length) > 0))] | length' 2>/dev/null) || rd=0
    [ "${rd:-0}" -gt 0 ] && { printf 'red %s\n' "$bad"; return 0; }
  done <<< "$lines"
  printf 'unproven its failed jobs never ran a step\n'; }

sg_base_red() { local base=${1:?sg_base_red: a base branch is required} sha sn cache now line re ep ref bad="" have="" cr newest
  [ "${SG_BASE_CI_GATE:-1}" = 0 ] && return 1
  sha=$(/usr/bin/git ls-remote "${SG_LAND_REMOTE:-https://github.com/${SG_REPO:-}.git}" "refs/heads/$base" 2>/dev/null | awk 'NR==1 {print $1}')
  [ -n "$sha" ] || return 1
  sn=${sha:0:9}; cache="$SG_STATE/auto/basecheck-$sha"; now=$(date +%s)
  line=$(head -n 1 "$cache" 2>/dev/null)
  re='^ok ([0-9]+)$'
  if [[ $line =~ $re ]]; then ep=${BASH_REMATCH[1]}
    if [ $((now - ep)) -ge 0 ] && [ $((now - ep)) -lt "${SG_BASE_CI_OK_TTL:-600}" ]; then have=1; fi
  fi
  re='^red2 ([0-9]+) (.+ ended .+)$'
  if [[ $line =~ $re ]]; then ep=${BASH_REMATCH[1]}
    if [ $((now - ep)) -ge 0 ] && [ $((now - ep)) -lt "${SG_BASE_CI_RECHECK:-600}" ]; then have=1; bad=${BASH_REMATCH[2]}; fi
  fi
  if [ -z "$have" ]; then
    cr=$(gh_api "repos/$SG_REPO/commits/$sha/check-runs?per_page=100&filter=latest") || { sg_base_unreadable "$base" "$sha" "its check runs are not readable"; return 1; }
    ref=$(sg_ci_refine "$cr") || ref=
    case "$ref" in
      "") sg_base_unreadable "$base" "$sha" "its check runs are not a check-run list"; return 1;;
      red\ *) bad=${ref#red }; sg_base_put "$cache" "red2 $now $bad";;
      green) newest=$(printf '%s' "$cr" | jq -r '[.check_runs[] | .completed_at // empty | fromdateiso8601] | max // 0' 2>/dev/null)
        [ "${newest:-0}" -gt 0 ] && [ $((now - newest)) -ge "${SG_BASE_CI_GRACE:-900}" ] && sg_base_put "$cache" "ok $now";;
      unproven) sg_base_inconclusive "$base" "$sha" unproven "a check measured nothing (cancelled or timed out)"; return 1;;
      unproven\ *) sg_base_inconclusive "$base" "$sha" unproven "${ref#unproven }"; return 1;;
    esac
  fi
  [ -n "$bad" ] || return 1
  sg_base_report "$base" "$sha" "$sn" "$bad"
  printf 'base %s is red at %s: %s\n' "$base" "$sn" "$bad"
  return 0; }

# sg_base_put <file> <line>: the line replaces the file by one rename, so a kill leaves the old answer or the new one, never part of one.
sg_base_put() { mkdir -p "$(dirname "$1")"; printf '%s\n' "$2" > "$1.tmp.$$" && mv -f "$1.tmp.$$" "$1"; }

# sg_base_unreadable <base> <sha> <why>: one alerts.log line per head. A read that failed holds nothing.
sg_base_unreadable() { local m="$SG_STATE/auto/basecheck-unreadable-$2"
  [ -e "$m" ] && return 0
  mkdir -p "$SG_STATE/auto"; touch "$m"
  printf '%s BASE CI base %s at %s is not read: %s; it does not hold the landings\n' "$(date '+%F %T')" "$1" "${2:0:9}" "$3" >> "$SG_STATE/alerts.log"; }

# sg_base_inconclusive <base> <sha> <unproven|unknown> <why>: one alerts.log line per head. The head holds nothing (ADR-0164): no
# failed job ran a step, or the jobs that would say are unreadable. One marker per head keeps the line to one, and a head read
# again raises nothing more; neither answer is cached (#1629).
sg_base_inconclusive() { local m="$SG_STATE/auto/basecheck-inconclusive-$2"
  [ -e "$m" ] && return 0
  mkdir -p "$SG_STATE/auto"
  printf '%s BASE CI base %s at %s is %s: %s; it does not hold the landings\n' "$(date '+%F %T')" "$1" "${2:0:9}" "$3" "$4" >> "$SG_STATE/alerts.log"
  : > "$m"; }   # the line first, the marker after: a kill between them repeats the line, never loses it (ADR-0030)

# sg_base_report <base> <sha> <sn> <check>: the alerts.log line once per head; one queued comment per landed issue at that head, once.
sg_base_report() { local base=$1 sha=$2 sn=$3 bad=$4 rep i m f
  mkdir -p "$SG_STATE/auto"; rep="$SG_STATE/auto/basecheck-reported-$sha"
  if [ ! -e "$rep" ]; then touch "$rep"
    printf '%s BASE CI base %s is red at %s: %s\n' "$(date '+%F %T')" "$base" "$sn" "$bad" >> "$SG_STATE/alerts.log"; fi
  declare -F sg_cq_file >/dev/null || return 0
  for i in $(sg_base_landed_issues "$sha"); do
    m="$SG_STATE/auto/basecheck-cq-$sha-$i"; [ -e "$m" ] && continue
    mkdir -p "$SG_STATE/cq"
    f=$(sg_cq_file "$i-basecheck-$sn.md") && printf 'Base CI: %s is red at %s: %s. Landings into %s wait until its head is green.\n' "$base" "$sn" "$bad" "$base" > "$f" && touch "$m"
  done; }

# sg_base_landed_issues <sha>: the issues whose landed.tsv row (epoch, branch or wave, sha, issue) names that head. No other issue is
# commented: a direct push or a bot commit on the base belongs to no issue of ours.
sg_base_landed_issues() { local f="$SG_STATE/landed.tsv"
  [ -s "$f" ] || return 0
  awk -F '\t' -v s="$1" 'length($3) >= 7 && index(s, $3) == 1 {print $4}' "$f" | sort -un | /usr/bin/grep -E '^[0-9]+$'; }
