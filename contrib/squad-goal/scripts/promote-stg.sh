#!/bin/bash
# usage: promote-stg.sh      promote the dev tip into stg by one pull request when the promotion proof is green on that tip
#                            (ADR-0145, #743), and never otherwise. No script pushes refs/heads/stg.
#
# SG_PROMOTE_PROOF names the proof.
#   ci (the default): the CI of the dev tip. Green promotes; red, running or a missing run promotes nothing.
#   local: the promotion gate's full-result line, $SG_STATE/gate-status-full. While the account's Actions billing keeps CI from
#     starting, the full suite in both modes is the proof (owner decision on #439). The run that earns it writes the line through
#     the existing seam, with the kind gate.sh names for a promotion to stg:
#         SG_GATE_KIND=full bash gate.sh bash -c '<the full suite>'
#     stg moves only when the line says `full`, `pass`, and names the dev tip exactly. A missing proof, a failed proof and a
#     proof for another sha each refuse in their own words.
# Exit 0: the dev tip is in stg (promoted, or already there). Exit 1: nothing promoted; the one reason goes to $SG_STATE/promote.log
# once per tip and reason (the dedupe list keeps its last 20 lines), so a pass that repeats says nothing new, and to stderr.
# A later green tip carries the earlier work with it. The consolidation is one pull request (#743, owner rule for every project):
# the pass opens or reuses the open `dev` -> `stg` pull request and merges it with a merge commit, so a later tip is measured by
# ancestry, never by identity. The proof is read before the merge, so a red tip leaves the pull request open and promotes nothing.
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
  . "$d/ghcall.sh"; . "$d/basecheck.sh"   # the classifier is the base check's own (#786): the two readers cannot disagree
  local cr c; cr=$(gh_api "repos/$SG_REPO/commits/$1/check-runs?per_page=100&filter=latest") || return 1
  c=$(printf '%s' "$cr" | sg_ci_class) || return 1
  [ "$c" = none ] && c=running   # zero check runs is not yet green: promotion waits for them
  printf '%s\n' "$c"
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
g merge-base --is-ancestor "$tip" origin/stg && exit 0   # the dev tip is already in stg: no pull request is opened
# A promotion leaves a merge commit in stg, so stg is never an ancestor of the next dev tip. The work stg holds and dev lacks
# must be those merge commits alone; any plain commit is the owner's to resolve before a merge.
o=$(g rev-list --no-merges "$tip..origin/stg" 2>/dev/null) || o=
[ -z "$o" ] || refuse "$tip" "not promoted: stg holds $(printf '%s\n' "$o" | /usr/bin/grep -c .) commit(s) the dev tip lacks; the owner resolves it"
prove "$tip"
# The owner's consolidation rule (#743): one pull request, never a push. The merge is a merge commit, so the next tip is read
# by ancestry and a tip already in stg is no work.
. "$d/ghcall.sh"
pr=$(gh_api "repos/$SG_REPO/pulls?state=open" -q '[.[] | select(.head.ref == "dev" and .base.ref == "stg")][0].number // empty' 2>/dev/null) \
  || refuse "$tip" "not promoted: the open dev -> stg pull request could not be read"
if [ -z "$pr" ]; then
  o=$(gh_send -X POST "repos/$SG_REPO/pulls" -f head=dev -f base=stg -f "title=Promote dev into stg" \
      -f "body=The dev tip $tip carries its proof. Merge this to move stg." 2>&1) \
    || refuse "$tip" "not promoted: the dev -> stg pull request could not be opened: $(printf '%s' "$o" | tail -1)"
  pr=$(printf '%s' "$o" | jq -r '.number // empty' 2>/dev/null)
  [ -n "$pr" ] || refuse "$tip" "not promoted: the pull request was opened but its number was not read"
fi
o=$(gh_send -X PUT "repos/$SG_REPO/pulls/$pr/merge" -f merge_method=merge 2>&1) \
  || refuse "$tip" "not promoted: pull request #$pr could not be merged: $(printf '%s' "$o" | tail -1)"
printf '%s' "$o" | jq -e '.merged == true' >/dev/null 2>&1 \
  || refuse "$tip" "not promoted: pull request #$pr did not merge: $(printf '%s' "$o" | jq -r '.message // "no reason"' 2>/dev/null)"
echo "PROMOTED stg $tip via pull request #$pr"
