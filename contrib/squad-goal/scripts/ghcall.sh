#!/bin/bash
# usage: . "$(dirname "$0")/ghcall.sh"      then: gh_api <route> [-q <jq filter>] | gh_get <gh api args> | gh_send <gh api args>
#
# The loop's one door to GitHub (#8, ADR-0277; the read half landed as #1893). Every script reaches GitHub through one
# of the three functions below and never through `gh` itself, and `check-gh-interface.sh` refuses a live script that
# does otherwise, naming the script and the route.
#
# A read has three outcomes and they are never mixed (`ghread.py` decides them): the answer on stdout with exit 0, a
# refusal with exit 2 whose line carries GitHub's own message and request id, and a failure before GitHub reached a
# verdict with exit 1. `gh api` writes its error body to *stdout* and exits non-zero and ignores `-q` on a failed
# request, so a bare `o=$(gh api …)` used to read the words of a rate limit where a JSON list belonged; the exit code
# is the contract here because a script cannot see a Python return value.
#
# A write is the half with the worse consequence, and it is the reason `gh_send` exists beside `gh_api`: a refused read
# looks like an answer, but a refused write looks like *success*. `gh api -X PATCH … && touch moved` marks the card
# moved while GitHub refused it, and the loop reads that marker as state. `gh_send` returns 0 only when GitHub accepted
# the write, so `if gh_send -X PATCH …; then touch moved; fi` cannot be fooled. It is not a throttle -- the token
# bucket and the cooldown are a separate clause of ADR-0277 (#4) -- it is the outcome contract, which is what a caller
# can be wrong about.
#
# A read that may be answered from the snapshot (#6) has its own door, `gh_snapshot`, beside `gh_api` and never instead of
# it: `gh_api` is always live, and it is what a read that decides a write calls -- is the branch still eligible, is the base
# red, is the PR still open. `gh_snapshot` goes through ghthrottle.py, which answers a repeated read inside
# `SG_GH_CACHE_WINDOW` seconds from `$SG_STATE/gh-cache` with no request, and which reads live every route that is not one
# of the few it classifies as cheap or keyed -- so a gating route handed to `gh_snapshot` by mistake is still live.
#
# The spend is visible (ADR-0277): every door appends one line to $SG_STATE/api.log -- timestamp, transport, route, cache hit or
# miss, outcome and the spending pass (`SG_GH_PASS`, which each pass script exports for itself; a caller that names none is
# `unattributed`). gh_api and gh_snapshot log through ghread.py / ghthrottle.py; gh_get and gh_send reach `gh` themselves, so they
# log through `ghread.py --log`, with the outcome the call had. health.sh reads the log (ghspend.py).
#
# `-q`/`--jq` is the caller's projection and is applied HERE, on the answer, with `jq -r`, so the transport always
# speaks raw JSON: a filtering transport cannot tell a refusal apart from an answer, and `gh` cannot either.

ghcall_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${SG_GHREAD:=python3 $ghcall_here/ghread.py}"   # the interface module: one place to point at another build of it
: "${SG_GHSNAP:=python3 $ghcall_here/ghthrottle.py}"   # the throttled, snapshotting reader behind gh_snapshot

# _gh_refused <body>: 0 when the body is GitHub's own refusal shape: a REST message beside a status, or a GraphQL `errors[]`
# list with no `data` (a rate limit comes back that way, with gh exit 0 or 1). Any body that is not JSON, or is JSON without
# either shape, is not a refusal -- `jq -e` exits non-zero for both.
# _gh_log <outcome> <gh api args…>: one call-log line for a call this file makes itself. The route is the one argument that is
# neither a flag nor the value of one; the transport is REST (a write, a diff and a Project v2 read are all REST).
_gh_log() {
  local outcome=$1 route="" a; shift
  while [ $# -gt 0 ]; do
    case "$1" in
      -H|--header|-X|--method|-f|--raw-field|-F|--field|-q|--jq|--input|--cache|-t|--template|--hostname) shift 2;;
      -*) shift;;
      *) route=$1; break;;
    esac
  done
  [ -n "$route" ] || return 0
  $SG_GHREAD --log REST "$route" miss "$outcome" >/dev/null 2>&1 || true
}

# _gh_child_merge <gh args…>: 0 when a child session asks to merge a PR, by `pr merge` or by `PUT .../pulls/N/merge`. A child
# session is a call the engine started (`SG_CHILD_SESSION`, exported by the call environment): the loop merges, so the door
# refuses the merge there and names the refusal. The loop's own mergers run outside a call and never carry the marker.
_gh_child_merge() {
  [ -n "${SG_CHILD_SESSION:-}" ] || return 1
  local words=() put=0 a
  for a in "$@"; do
    case "$a" in
      -X=PUT|--method=PUT) put=1;;
      PUT) put=1;;
      -*) ;;
      *) words+=("$a");;
    esac
  done
  [ "${words[0]:-}" = pr ] && [ "${words[1]:-}" = merge ] && return 0
  [ "$put" -eq 1 ] && [[ " ${words[*]} " =~ /pulls/[0-9]+/merge ]] && return 0
  return 1
}

_gh_child_merge_line() {
  printf 'refused: CHILD-MERGE-REFUSED: a child session never merges a PR; the loop merges it once review and CI pass\n'
}

_gh_refused() {
  printf '%s' "$1" | jq -e 'type == "object" and (((.message | type == "string") and has("status"))
    or ((.errors | type == "array") and (.errors | length > 0) and (.data == null)))' >/dev/null 2>&1
}

_gh_refusal_line() {   # the one line a caller logs when GitHub refused: GitHub's own words, and nothing invented
  printf 'refused: GitHub refused: %s\n' "$(printf '%s' "$1" | jq -r '.message // .errors[0].message // "no message"' 2>/dev/null)"
}

# gh_api <route> [-q <jq filter>]   A read. Answers on stdout with exit 0; a refusal is exit 2 and its line is not an
# answer; a failure is exit 1. A caller that only ever learns from an answer keeps what it already knew.
gh_api() {
  local route=${1:?gh_api: a route is required}; shift || true
  _gh_read "$SG_GHREAD" "$route" "$@"
}

# gh_pages <route> [-q <jq filter>]   A list read, every page of it (#437). The route names `per_page=N`; the door asks `&page=1`,
# `&page=2`, ... through `gh_api` (so each page keeps the three outcomes and the call log) and stops at the first page shorter
# than N. The pages are one JSON array to the filter. A page that is refused or fails ends the read with that page's outcome.
gh_pages() {
  local route=${1:?gh_pages: a route is required}; shift || true
  local q="" size p=1 o rc all="[]" n
  [ "${1:-}" = "-q" ] && q=${2:-}
  size=$(printf '%s' "$route" | sed -n 's/.*per_page=\([0-9][0-9]*\).*/\1/p'); size=${size:-30}
  while :; do
    o=$(gh_api "$route&page=$p"); rc=$?
    [ "$rc" -eq 0 ] || { printf '%s\n' "$o"; return "$rc"; }
    all=$(printf '%s\n%s' "$all" "$o" | jq -s 'add') || return 1
    n=$(printf '%s' "$o" | jq 'length') || return 1
    [ "$n" -ge "$size" ] || break
    p=$((p+1))
  done
  if [ -n "$q" ]; then printf '%s' "$all" | jq -r "$q"; else printf '%s' "$all"; fi
}

# gh_snapshot <route> [-q <jq filter>]   A read that may repeat: the same three outcomes as gh_api, and a repeat inside
# the window is answered from the snapshot. Never for a read that decides a write (see above).
gh_snapshot() {
  local route=${1:?gh_snapshot: a route is required}; shift || true
  _gh_read "$SG_GHSNAP" "$route" "$@"
}

# gh_aggregate <route> [-q <jq filter>]   The open-PR or open-issue list, from the snapshot while it is younger than
# `SG_GH_AGGREGATE_WINDOW` (60 to 120 s, default 90): the same three outcomes as `gh_api`, and a repeat inside the window
# sends no request (#45). It is the caller's explicit choice, for a pass that reads the list ONCE for its whole set and
# tolerates an answer a window old -- the announcement of a PR, the landing pass's "an open PR names it" check. A read that
# decides one write about one issue stays `gh_api`.
gh_aggregate() {
  local route=${1:?gh_aggregate: a route is required}; shift || true
  _gh_read "$SG_GHSNAP --aggregate" "$route" "$@"
}

# gh_stretch <base seconds>   The extra seconds a loop cycle of that length waits, 0 while the quota is healthy (#45): once
# less than 20 percent of a pool is left, the cycle gets longer. Never fails: a reading that cannot be had is no stretch.
gh_stretch() { local o; o=$($SG_GHSNAP --stretch "${1:?gh_stretch: a base is required}" 2>/dev/null) && [ "$o" -ge 0 ] 2>/dev/null && echo "$o" || echo 0; }

_gh_read() {   # _gh_read <reader command> <route> [-q <jq filter>]
  local reader=$1 route=$2; shift 2
  local q=""
  [ "${1:-}" = "-q" ] && q=${2:-}
  local o rc
  o=$($reader "$route"); rc=$?
  if [ "$rc" -eq 0 ]; then
    if [ -n "$q" ]; then printf '%s' "$o" | jq -r "$q"; else printf '%s' "$o"; fi
    return 0
  fi
  printf '%s\n' "$o"
  return "$rc"
}

# gh_get <gh api args…>   A read GitHub answers with something other than JSON (`-H "Accept: …diff"`). The body is
# passed through verbatim, and the three outcomes are the same: a caller can tell an empty diff from a refusal.
gh_get() {
  local o rc
  if _gh_child_merge "$@"; then _gh_log refused "$@"; _gh_child_merge_line; return 2; fi
  o=$(gh api "$@" 2>/dev/null); rc=$?
  if [ "$rc" -eq 0 ] && ! _gh_refused "$o"; then _gh_log answer "$@"; printf '%s' "$o"; return 0; fi
  if _gh_refused "$o"; then _gh_log refused "$@"; _gh_refusal_line "$o"; return 2; fi
  _gh_log failed "$@"
  printf 'failed: no verdict (gh exited %s)\n' "$rc"
  return 1
}

# gh_send <gh api args…> [-q <jq filter>]   A write. Returns 0 only when GitHub accepted it; a refusal is exit 2 with
# GitHub's own message, a local failure exit 1. `-q` projects the answer the same way `gh_api` does.
gh_send() {
  local q="" args=()
  while [ $# -gt 0 ]; do
    case "$1" in
      -q|--jq) q=${2:-}; shift 2;;
      *) args+=("$1"); shift;;
    esac
  done
  local o rc
  if _gh_child_merge "${args[@]}"; then _gh_log refused "${args[@]}"; _gh_child_merge_line; return 2; fi
  o=$(gh api "${args[@]}" 2>/dev/null); rc=$?
  if _gh_refused "$o"; then _gh_log refused "${args[@]}"; _gh_refusal_line "$o"; return 2; fi
  if [ "$rc" -ne 0 ]; then _gh_log failed "${args[@]}"; printf 'failed: the write reached no verdict (gh exited %s)\n' "$rc"; return 1; fi
  _gh_log done "${args[@]}"
  if [ -n "$q" ]; then printf '%s' "$o" | jq -r "$q" || return 1; else printf '%s' "$o"; fi
  return 0
}
