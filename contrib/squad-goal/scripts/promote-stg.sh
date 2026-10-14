#!/bin/bash
# usage: promote-stg.sh      fast-forward stg to the dev tip when the CI of that tip is green (ADR-0145), and never otherwise.
#
# Exit 0: stg is at the dev tip (promoted, or already there). Exit 1: nothing promoted; the reason goes to $SG_STATE/promote.log
# once per tip and reason, so a pass that repeats says nothing new. A red, running or missing CI result promotes nothing, and a
# later green tip carries the earlier work with it. The push is plain, never forced, so stg moves by a fast-forward or not at all.
# The loop never writes main: the owner does, with promote-main.sh. The work happens in a throwaway clone.
# $SG_PROMOTE_REMOTE overrides the remote; $SG_CI_STATE_CMD <sha> prints green, red or running (the test seam).
d=$(cd "$(dirname "$0")" && pwd); remote=${SG_PROMOTE_REMOTE:-https://github.com/$SG_REPO.git}
export GIT_TERMINAL_PROMPT=0
# The promotion gate takes the machine-wide gate lock (#392); a pass already inside it just runs.
case ":${SG_SERIAL_HELD:-}:" in *":gate:"*) ;; *) exec bash "$d/gate.sh" bash "$0" "$@";; esac
state=${SG_STATE:-${TMPDIR:-/tmp}/sg-state}; mkdir -p "$state"; log=$state/promote.log
W=$(mktemp -d) || exit 1; trap 'rm -rf "$W"' EXIT
g() { /usr/bin/git -C "$W" "$@"; }
say() {  # sha, reason: one line per tip and reason
  grep -qxF "$1 $2" "$state/promote.seen" 2>/dev/null && return; echo "$1 $2" >> "$state/promote.seen"
  echo "$(date -u +%FT%TZ) promote-stg: $1 $2" >> "$log"; }
ci_state() {  # sha -> green | red | running, from the check runs of the commit
  if [ -n "$SG_CI_STATE_CMD" ]; then "$SG_CI_STATE_CMD" "$1"; return; fi
  . "$d/ghcall.sh"
  gh_api "repos/$SG_REPO/commits/$1/check-runs" -q 'if .total_count == 0 or ([.check_runs[] | select(.status != "completed")] | length) > 0 then "running" elif ([.check_runs[] | select(.conclusion | IN("success","skipped","neutral") | not)] | length) > 0 then "red" else "green" end'
}
g init -q . 2>/dev/null; g remote add origin "$remote"
o=$(g fetch -q origin "+refs/heads/dev:refs/remotes/origin/dev" "+refs/heads/stg:refs/remotes/origin/stg" 2>&1) || { echo "promote-stg: fetch failed: $(printf '%s' "$o" | tail -1)" >&2; exit 1; }
tip=$(g rev-parse origin/dev) || exit 1
[ "$(g rev-parse origin/stg)" = "$tip" ] && exit 0
g merge-base --is-ancestor origin/stg "$tip" || { say "$tip" "not promoted: stg is not an ancestor of dev, no fast-forward"; exit 1; }
st=$(ci_state "$tip" 2>/dev/null)
[ "$st" = green ] || { say "$tip" "not promoted: CI is ${st:-unknown} (green needed)"; exit 1; }
o=$(g push -q origin "$tip:refs/heads/stg" 2>&1) || { say "$tip" "not promoted: push refused: $(printf '%s' "$o" | tail -1)"; exit 1; }
echo "PROMOTED stg $tip"
