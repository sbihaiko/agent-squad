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

# _gh_refused <body>: 0 when the body is GitHub's own refusal shape (a message beside a status). Any body that is not
# JSON, or is JSON without that shape, is not a refusal -- `jq -e` exits non-zero for both.
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

_gh_refused() {
  printf '%s' "$1" | jq -e 'type == "object" and (.message | type == "string") and has("status")' >/dev/null 2>&1
}

_gh_refusal_line() {   # the one line a caller logs when GitHub refused: GitHub's own words, and nothing invented
  printf 'refused: GitHub refused: %s\n' "$(printf '%s' "$1" | jq -r '.message // "no message"' 2>/dev/null)"
}

# gh_api <route> [-q <jq filter>]   A read. Answers on stdout with exit 0; a refusal is exit 2 and its line is not an
# answer; a failure is exit 1. A caller that only ever learns from an answer keeps what it already knew.
gh_api() {
  local route=${1:?gh_api: a route is required}; shift || true
  _gh_read "$SG_GHREAD" "$route" "$@"
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
  o=$(gh api "${args[@]}" 2>/dev/null); rc=$?
  if _gh_refused "$o"; then _gh_log refused "${args[@]}"; _gh_refusal_line "$o"; return 2; fi
  if [ "$rc" -ne 0 ]; then _gh_log failed "${args[@]}"; printf 'failed: the write reached no verdict (gh exited %s)\n' "$rc"; return 1; fi
  _gh_log done "${args[@]}"
  if [ -n "$q" ]; then printf '%s' "$o" | jq -r "$q" || return 1; else printf '%s' "$o"; fi
  return 0
}
