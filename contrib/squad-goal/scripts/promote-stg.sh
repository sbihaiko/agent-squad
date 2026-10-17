#!/bin/bash
# usage: promote-stg.sh      fast-forward stg to the dev tip when the promotion proof is green on that tip (ADR-0145), and never otherwise.
#
# SG_PROMOTE_PROOF names the proof.
#   ci (the default): the CI of the dev tip. Green promotes; red, running or a missing run promotes nothing.
#   local: the promotion gate's full-result line, $SG_STATE/gate-status-full. While the account's Actions billing keeps CI from
#     starting, the full suite in both modes is the proof (owner decision on #439). The run that earns it writes the line through
#     the existing seam, with the kind gate.sh names for a promotion to stg:
#         SG_GATE_KIND=full bash gate.sh bash -c '<the full suite>'
#     stg moves only when the line says `full`, `pass`, and names the dev tip exactly. A missing proof, a failed proof and a
#     proof for another sha each refuse in their own words.
# Exit 0: stg is at the dev tip (promoted, or already there). Exit 1: nothing promoted; the one reason goes to $SG_STATE/promote.log
# once per tip and reason (the dedupe list keeps its last 20 lines), so a pass that repeats says nothing new, and to stderr.
# A later green tip carries the earlier work with it. The push is plain, never forced, so stg moves by a fast-forward or not at all.
# The loop never writes main: the owner does, with promote-main.sh. The work happens in a throwaway clone.
# $SG_PROMOTE_REMOTE overrides the remote; $SG_CI_STATE_CMD <sha> prints green, red or running (the CI seam); SG_PROMOTE_PROOF
# selects the proof (ci, the default).
d=$(cd "$(dirname "$0")" && pwd); remote=${SG_PROMOTE_REMOTE:-https://github.com/$SG_REPO.git}
export GIT_TERMINAL_PROMPT=0
# The promotion reads the machine-wide gate lock (#392), so the proof cannot change under it and two promotions never overlap;
# a pass already inside the lock just runs.
case ":${SG_SERIAL_HELD:-}:" in *":gate:"*) ;; *) SG_GATE_STATUS=self exec bash "$d/gate.sh" bash "$0" "$@";; esac
state=${SG_STATE:-/tmp/sg-state};   # the machine-wide default serial.sh uses: never TMPDIR, which is per user
mkdir -p "$state"; log=$state/promote.log
W=$(mktemp -d) || exit 1; trap 'rm -rf "$W"' EXIT
g() { /usr/bin/git -C "$W" "$@"; }
seen() { echo "$1" >> "$state/promote.seen"; tail -n 20 "$state/promote.seen" > "$state/promote.seen.tmp" && mv "$state/promote.seen.tmp" "$state/promote.seen"; }   # the dedupe list keeps the last 20 lines, never grows (#438)
say() {  # sha, reason: one line per tip and reason
  grep -qxF "$1 $2" "$state/promote.seen" 2>/dev/null && return; seen "$1 $2"
  echo "$(date -u +%FT%TZ) promote-stg: $1 $2" >> "$log"; }
refuse() { say "$1" "$2"; echo "promote-stg: $2" >&2; exit 1; }   # the one reason, on the log line and stderr
proof=${SG_PROMOTE_PROOF:-ci}
ci_state() {  # sha -> green | red | running, from the check runs of the commit
  if [ -n "$SG_CI_STATE_CMD" ]; then "$SG_CI_STATE_CMD" "$1"; return; fi
  . "$d/ghcall.sh"
  gh_api "repos/$SG_REPO/commits/$1/check-runs" -q 'if .total_count == 0 or ([.check_runs[] | select(.status != "completed")] | length) > 0 then "running" elif ([.check_runs[] | select(.conclusion | IN("success","skipped","neutral") | not)] | length) > 0 then "red" else "green" end'
}
prove() {  # <tip>: refuse unless the proof for this tip is green, each refusal in its own words
  if [ "$proof" = local ]; then
    IFS=$'\t' read -r _ kind res sha < "$state/gate-status-full" 2>/dev/null
    [ "$kind" = full ] || refuse "$1" "not promoted: no local proof: $state/gate-status-full holds no full-gate result (run the promotion gate: SG_GATE_KIND=full bash gate.sh bash -c '<the full suite>')"
    [ "$sha" = "$1" ] || refuse "$1" "not promoted: the local proof is for $sha, not the dev tip $1"
    [ "$res" = pass ] || refuse "$1" "not promoted: the local proof is a fail, not a pass"
  else
    st=$(ci_state "$1" 2>/dev/null)
    [ "$st" = green ] || refuse "$1" "not promoted: CI is ${st:-unknown} (green needed)"
  fi; }
g init -q . 2>/dev/null; g remote add origin "$remote"
o=$(g fetch -q origin "+refs/heads/dev:refs/remotes/origin/dev" "+refs/heads/stg:refs/remotes/origin/stg" 2>&1) || { echo "promote-stg: fetch failed: $(printf '%s' "$o" | tail -1)" >&2; exit 1; }
tip=$(g rev-parse origin/dev) || exit 1
[ "$(g rev-parse origin/stg)" = "$tip" ] && exit 0
g merge-base --is-ancestor origin/stg "$tip" || refuse "$tip" "not promoted: stg is not an ancestor of dev, no fast-forward"
prove "$tip"
o=$(g push -q origin "$tip:refs/heads/stg" 2>&1) || refuse "$tip" "not promoted: push refused: $(printf '%s' "$o" | tail -1)"
echo "PROMOTED stg $tip"
