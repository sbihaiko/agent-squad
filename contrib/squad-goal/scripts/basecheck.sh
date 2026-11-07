#!/bin/bash
# basecheck.sh (#786): the base branch's own check. A red completed check on the base head holds every landing into that base
# (branchland.sh, wave.sh) until the head is green. Sourced by those scripts after ghcall.sh, and by promote-stg.sh.
#
# sg_base_red <base>: while the head of origin/<base> has a completed check that did not end success, skipped, neutral, cancelled
# or timed_out, prints "base <base> is red at <sha9>: <check> ended <conclusion>" and returns 0. Otherwise returns 1. A pending,
# missing or unreadable check does not block. A cancelled or timed-out check measured nothing (the complete tier ends cancelled at
# its job timeout): the head is unproven, does not block, and is not cached (ADR-0164, #1532). The answer is cached per head under $SG_STATE/auto/basecheck-<sha>, written through a temporary file:
#   ok <epoch>            green; kept only once the newest completed check is at least SG_BASE_CI_GRACE seconds old (default 900),
#                         and read again once SG_BASE_CI_OK_TTL seconds have passed since the read (default 600), so a re-run is seen (#790)
#   red <epoch> <check>   red; read again once SG_BASE_CI_RECHECK seconds have passed (default 600)
# Any other content is no answer, and the head is read again. A red head is reported once: one alerts.log line per head, and one
# queued comment on each issue whose landed.tsv row names that head. An unreadable read is one alerts.log line per head.
# SG_BASE_CI_GATE=0 turns the check off.
#
# sg_ci_class: reads a check-runs answer on stdin and prints red, running, unproven, green or none (zero check runs; the caller decides what
# none means). Exits 1 when the input is not a check-run list. promote-stg.sh uses it too, so the two readers cannot disagree.
sg_ci_class() { jq -r 'if (.check_runs | type) != "array" then error("not a check-run list") elif (.check_runs | length) == 0 then "none" elif ([.check_runs[] | select(.status == "completed") | select(.conclusion != "success" and .conclusion != "skipped" and .conclusion != "neutral" and .conclusion != "cancelled" and .conclusion != "timed_out")] | length) > 0 then "red" elif ([.check_runs[] | select(.status != "completed")] | length) > 0 then "running" elif ([.check_runs[] | select(.conclusion == "cancelled" or .conclusion == "timed_out")] | length) > 0 then "unproven" else "green" end' 2>/dev/null; }

# sg_ci_bad: the first completed check that did not end success, skipped, neutral, cancelled or timed_out, as "<name> ended <conclusion>"; empty when none.
sg_ci_bad() { jq -r '[.check_runs[] | select(.status == "completed") | select(.conclusion != "success" and .conclusion != "skipped" and .conclusion != "neutral" and .conclusion != "cancelled" and .conclusion != "timed_out") | "\(.name) ended \(.conclusion)"] | first // ""' 2>/dev/null; }

sg_base_red() { local base=${1:?sg_base_red: a base branch is required} sha sn cache now line re ep bad="" have="" cr cls newest
  [ "${SG_BASE_CI_GATE:-1}" = 0 ] && return 1
  sha=$(/usr/bin/git ls-remote "${SG_LAND_REMOTE:-https://github.com/${SG_REPO:-}.git}" "refs/heads/$base" 2>/dev/null | awk 'NR==1 {print $1}')
  [ -n "$sha" ] || return 1
  sn=${sha:0:9}; cache="$SG_STATE/auto/basecheck-$sha"; now=$(date +%s)
  line=$(head -n 1 "$cache" 2>/dev/null)
  re='^ok ([0-9]+)$'
  if [[ $line =~ $re ]]; then ep=${BASH_REMATCH[1]}
    if [ $((now - ep)) -ge 0 ] && [ $((now - ep)) -lt "${SG_BASE_CI_OK_TTL:-600}" ]; then have=1; fi
  fi
  re='^red ([0-9]+) (.+ ended .+)$'
  if [[ $line =~ $re ]]; then ep=${BASH_REMATCH[1]}
    if [ $((now - ep)) -ge 0 ] && [ $((now - ep)) -lt "${SG_BASE_CI_RECHECK:-600}" ]; then have=1; bad=${BASH_REMATCH[2]}; fi
  fi
  if [ -z "$have" ]; then
    cr=$(gh_api "repos/$SG_REPO/commits/$sha/check-runs?per_page=100&filter=latest") || { sg_base_unreadable "$base" "$sha" "its check runs are not readable"; return 1; }
    cls=$(printf '%s' "$cr" | sg_ci_class) || cls=
    case "$cls" in
      "") sg_base_unreadable "$base" "$sha" "its check runs are not a check-run list"; return 1;;
      red) bad=$(printf '%s' "$cr" | sg_ci_bad); sg_base_put "$cache" "red $now $bad";;
      green) newest=$(printf '%s' "$cr" | jq -r '[.check_runs[] | .completed_at // empty | fromdateiso8601] | max // 0' 2>/dev/null)
        [ "${newest:-0}" -gt 0 ] && [ $((now - newest)) -ge "${SG_BASE_CI_GRACE:-900}" ] && sg_base_put "$cache" "ok $now";;
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
