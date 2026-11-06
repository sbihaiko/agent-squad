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
#     A delta record (ADR-0156, $SG_STATE/gate-status-delta) carries the full pass of an ancestor P across a covered change: stg
#     moves on it when the record says pass for the dev tip, P has a full pass and the changed-file list still hashes to P..tip.
#     Each carried P is appended to $SG_STATE/gate-carried after the merge, and a P already there refuses.
#   bound: the bound-ended proof (ADR-0164). Only for the tip promote-pass.sh recorded in $SG_STATE/promote-indeterminate, and
#     only while neither the full line nor the delta record says fail for that tip. Nothing else measured the tip, so a CI check
#     that reported a defect on it refuses the merge by name (#1196); a check still running or absent holds nothing. The merge
#     carries the marker's meaning: no test reached every part of the tip, and the nightly proves it later.
# The script prints `promote-stg: tip <sha>` for the tip it evaluated, so a caller keys a refusal to that tip, never to a tip it
# proved earlier (#1495). Under the ci proof a tip whose checks have not reported prints `promote-stg: pending <sha>` and exits 75
# (EX_TEMPFAIL): a wait, no refusal, no marker. Any other non-zero exit is a refusal.
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
# SG_PROMOTE_OPEN_ONLY opens or reuses the dev -> stg pull request and nothing else: no lock, no proof, no merge (ADR-0164,
# #1051). promote-pass.sh calls it so the four CI shards start on the tip while the quiet window passes.
if [ -n "${SG_PROMOTE_OPEN_ONLY:-}" ]; then
  . "$d/ghcall.sh"
  pr=$(gh_api "repos/$SG_REPO/pulls?state=open" -q '[.[] | select(.head.ref == "dev" and .base.ref == "stg")][0].number // empty' 2>/dev/null) || exit 0
  [ -n "$pr" ] && exit 0
  gh_send -X POST "repos/$SG_REPO/pulls" -f head=dev -f base=stg -f "title=Promote dev into stg" \
    -f "body=The dev tip is under CI. Merge this to move stg." >/dev/null 2>&1
  exit 0
fi
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
hash_list() { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi | cut -d' ' -f1; }
prove() {  # <tip>: refuse unless the proof for this tip is green, each refusal in its own words
  if [ "$proof" = local ]; then
    IFS=$'\t' read -r _ kind res sha < "$state/gate-status-full" 2>/dev/null
    if [ "$kind" = full ] && [ "$sha" = "$1" ]; then
      # The gate's own word (spec 1163, #1174): `pass` promotes, and a line that says `unproven` is named as such, never read
      # as a pass or misnamed a fail. The bound-ended proof is the one route an unfinished proof takes, with its marker.
      [ "$res" = pass ] || { local w; case "${res:-}" in fail) w="a fail";; '') w=none;; *) w=$res;; esac
        refuse "$1" "not promoted: the local proof is $w, not a pass"; }
      return
    fi
    if [ ! -s "$state/gate-status-delta" ]; then
      [ "$kind" = full ] || refuse "$1" "not promoted: no local proof: no delta record and no full proof; $state/gate-status-full holds no full-gate result (run the promotion gate: SG_GATE_KIND=full bash gate.sh bash -c '<the full suite>')"
      refuse "$1" "not promoted: the local proof is for $sha, not the dev tip $1"
    fi
    # A delta (ADR-0156) carries a full pass across a covered change; every check is repeated here, on the tree this pass reads.
    IFS=$'\t' read -r _ dk dres dP dT dh < "$state/gate-status-delta" 2>/dev/null
    [ "$dk" = delta ] || refuse "$1" "not promoted: $state/gate-status-delta is not a delta record"
    [ "$dres" = pass ] || { local w; case "${dres:-}" in fail) w="a fail";; '') w=none;; *) w=$dres;; esac
      refuse "$1" "not promoted: the delta is $w, not a pass"; }
    if [ "$dT" != "$1" ] && g merge-base --is-ancestor "$dT" "$1" 2>/dev/null; then refuse "$1" "not promoted: the record is stale because the tip moved from $dT to $1"; fi
    [ "$dT" = "$1" ] || refuse "$1" "not promoted: the delta proved $dT, not the dev tip $1"
    [ "$kind" = full ] && [ "$sha" = "$dP" ] && [ "$res" = pass ] || refuse "$1" "not promoted: its P $dP has no full pass"
    g cat-file -e "$dP^{commit}" 2>/dev/null || g fetch -q origin "$dP" 2>/dev/null
    g merge-base --is-ancestor "$dP" "$1" 2>/dev/null || refuse "$1" "not promoted: P $dP is not an ancestor of the dev tip $1"
    grep -qxF "$dP" "$state/gate-carried" 2>/dev/null && refuse "$1" "not promoted: P $dP is already carried by a delta"
    list=$(g diff --name-only "$dP..$1" | LC_ALL=C sort)
    [ "$(printf '%s\n' "$list" | hash_list)" = "$dh" ] || refuse "$1" "not promoted: the proved changed-file list no longer hashes to P..T"
    carried=$dP
  elif [ "$proof" = bound ]; then
    # ADR-0164 (#1046): the bound-ended proof is the tip promote-pass.sh recorded as the bound's, and never a tip a test measured red.
    IFS=$'\t' read -r _ it _ 2>/dev/null < "$state/promote-indeterminate"
    [ "$it" = "$1" ] || refuse "$1" "not promoted: no bound-ended proof: $state/promote-indeterminate holds no record for the dev tip $1"
    IFS=$'\t' read -r _ kind res sha 2>/dev/null < "$state/gate-status-full"
    [ "$kind" = full ] && [ "$sha" = "$1" ] && [ "$res" = fail ] && refuse "$1" "not promoted: the last full proof of the dev tip is a fail"
    IFS=$'\t' read -r _ dk dres _ dT _ 2>/dev/null < "$state/gate-status-delta"
    [ "$dk" = delta ] && [ "$dT" = "$1" ] && [ "$dres" = fail ] && refuse "$1" "not promoted: the delta proof of the dev tip is a fail"
  else
    st=$(ci_state "$1" 2>/dev/null)
    [ "$st" = running ] && { echo "promote-stg: pending $1: CI is running"; exit 75; }   # the checks have not reported: a wait, never a refusal (#1495)
    [ "$st" = green ] || refuse "$1" "not promoted: CI is ${st:-unknown} (green needed)"
  fi; }
g init -q . 2>/dev/null; g remote add origin "$remote"
o=$(g fetch -q origin "+refs/heads/dev:refs/remotes/origin/dev" "+refs/heads/stg:refs/remotes/origin/stg" 2>&1) || { echo "promote-stg: fetch failed: $(printf '%s' "$o" | tail -1)" >&2; exit 1; }
tip=$(g rev-parse origin/dev) || exit 1
echo "promote-stg: tip $tip"
g merge-base --is-ancestor "$tip" origin/stg && exit 0   # the dev tip is already in stg: no pull request is opened
# A promotion leaves a merge commit in stg, so stg is never an ancestor of the next dev tip. The work stg holds and dev lacks
# must be those merge commits alone; any plain commit is the owner's to resolve before a merge.
o=$(g rev-list --no-merges "$tip..origin/stg" 2>/dev/null) || o=
[ -z "$o" ] || refuse "$tip" "not promoted: stg holds $(printf '%s\n' "$o" | /usr/bin/grep -c .) commit(s) the dev tip lacks; the owner resolves it"
prove "$tip"
. "$d/ghcall.sh"
# ADR-0164 (#1196): delivery outranks the proof, but only an unfinished proof. A failed one always holds. Under the bound proof
# nothing else measured the tip, so the pull request's own CI check is the only evidence left: a red check on the tip refuses the
# merge by name. Only `failure` is a measured red: a `cancelled` or `timed_out` check is an unfinished proof (#1517), and it
# holds nothing, as a running or absent check does, so a slow or cut-short CI never keeps delivery back. The ci proof already
# reads that check, and the local proof is the owner's own full suite, which outranks a CI run of a narrower scope.
if [ "$proof" = bound ]; then
  red=$(gh_api "repos/$SG_REPO/commits/$tip/check-runs" -q '[.check_runs[] | select(.conclusion == "failure") | .name + " " + .conclusion] | join(", ")' 2>/dev/null) || red=""
  [ -z "$red" ] || refuse "$tip" "not promoted: the CI check on the dev tip is red ($red)"
fi
# The owner's consolidation rule (#743): one pull request, never a push. The merge is a merge commit, so the next tip is read
# by ancestry and a tip already in stg is no work.
pr=$(gh_api "repos/$SG_REPO/pulls?state=open" -q '[.[] | select(.head.ref == "dev" and .base.ref == "stg")][0].number // empty' 2>/dev/null) \
  || { echo "promote-stg: pending $tip: the open dev -> stg pull request could not be read" >&2; exit 75; }   # a transport failure is unproven, never a verdict (ADR-0164, #1490)
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
[ -n "${carried:-}" ] && echo "$carried" >> "$state/gate-carried"
echo "PROMOTED stg $tip via pull request #$pr"
