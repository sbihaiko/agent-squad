#!/bin/bash
# basecheck.sh (#786): the base branch's own check. A red completed check on the base head holds every landing into that base
# (branchland.sh, wave.sh) until the head is green. Sourced by those scripts after ghcall.sh.
#
# sg_base_red <base>: while the head of origin/<base> has a completed check that did not end success, skipped or neutral, prints
# "base <base> is red at <sha9>: <check> ended <conclusion>" and returns 0. Otherwise returns 1. A pending or missing check does not
# block. A complete answer (every check completed, at least one) is cached per head under $SG_STATE/auto/basecheck-<sha>, so a head is
# read once. A red head is reported once: one alerts.log line and one queued comment on each issue landed at that head (landed.tsv;
# else the last landed issue). SG_BASE_CI_GATE=0 turns the check off.
sg_base_red() { local base=${1:?sg_base_red: a base branch is required} sha sn cache rep bad pend total cr
  [ "${SG_BASE_CI_GATE:-1}" = 0 ] && return 1
  sha=$(/usr/bin/git ls-remote "${SG_LAND_REMOTE:-https://github.com/${SG_REPO:-}.git}" "refs/heads/$base" 2>/dev/null | awk 'NR==1 {print $1}')
  [ -n "$sha" ] || return 1
  sn=${sha:0:9}; cache="$SG_STATE/auto/basecheck-$sha"
  if [ -s "$cache" ]; then bad=$(cat "$cache"); [ "$bad" = ok ] && bad=
  else
    cr=$(gh_api "repos/$SG_REPO/commits/$sha/check-runs") || return 1
    printf '%s' "$cr" | jq -e '.check_runs|type=="array"' >/dev/null 2>&1 || return 1
    total=$(printf '%s' "$cr" | jq -r '.check_runs|length')
    pend=$(printf '%s' "$cr" | jq -r '[.check_runs[]|select(.status!="completed")]|length')
    bad=$(printf '%s' "$cr" | jq -r '[.check_runs[]|select(.status=="completed")|select(.conclusion!="success" and .conclusion!="skipped" and .conclusion!="neutral")|"\(.name) ended \(.conclusion)"]|first // ""')
    if [ "$pend" = 0 ] && [ "$total" != 0 ]; then mkdir -p "$SG_STATE/auto"; printf '%s' "${bad:-ok}" > "$cache"; fi
  fi
  [ -n "$bad" ] || return 1
  rep="$SG_STATE/auto/basecheck-reported-$sha"
  if [ ! -e "$rep" ]; then
    mkdir -p "$SG_STATE/auto"; touch "$rep"
    printf '%s BASE CI base %s is red at %s: %s\n' "$(date '+%F %T')" "$base" "$sn" "$bad" >> "$SG_STATE/alerts.log"
    if declare -F sg_cq_file >/dev/null; then
      mkdir -p "$SG_STATE/cq"
      for i in $(sg_base_landed_issues "$sha"); do
        sg_cq_file "$i-basecheck-$sn.md" >/dev/null && printf 'Base CI: %s is red at %s: %s. Landings into %s wait until its head is green.\n' "$base" "$sn" "$bad" "$base" > "$(sg_cq_file "$i-basecheck-$sn.md")"
      done
    fi
  fi
  printf 'base %s is red at %s: %s\n' "$base" "$sn" "$bad"
  return 0; }

# sg_base_landed_issues <sha>: the issues landed at that head (landed.tsv: epoch, branch or wave, sha, issue); when none, the last one.
sg_base_landed_issues() { local f="$SG_STATE/landed.tsv" out
  [ -s "$f" ] || return 0
  out=$(awk -F '\t' -v s="$1" 'length($3) >= 7 && index(s, $3) == 1 {print $4}' "$f" | sort -un)
  [ -n "$out" ] || out=$(tail -1 "$f" | cut -f4)
  printf '%s\n' "$out" | /usr/bin/grep -E '^[0-9]+$'; }
