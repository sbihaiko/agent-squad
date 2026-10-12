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
# `-q`/`--jq` is the caller's projection and is applied HERE, on the answer, with `jq -r`, so the transport always
# speaks raw JSON: a filtering transport cannot tell a refusal apart from an answer, and `gh` cannot either.

ghcall_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${SG_GHREAD:=python3 $ghcall_here/ghread.py}"   # the interface module: one place to point at another build of it

# _gh_refused <body>: 0 when the body is GitHub's own refusal shape (a message beside a status). Any body that is not
# JSON, or is JSON without that shape, is not a refusal -- `jq -e` exits non-zero for both.
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
  local q=""
  [ "${1:-}" = "-q" ] && q=${2:-}
  local o rc
  o=$($SG_GHREAD "$route"); rc=$?
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
  if [ "$rc" -eq 0 ] && ! _gh_refused "$o"; then printf '%s' "$o"; return 0; fi
  if _gh_refused "$o"; then _gh_refusal_line "$o"; return 2; fi
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
  if _gh_refused "$o"; then _gh_refusal_line "$o"; return 2; fi
  if [ "$rc" -ne 0 ]; then printf 'failed: the write reached no verdict (gh exited %s)\n' "$rc"; return 1; fi
  if [ -n "$q" ]; then printf '%s' "$o" | jq -r "$q" || return 1; else printf '%s' "$o"; fi
  return 0
}
