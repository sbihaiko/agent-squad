#!/bin/bash
# usage: promote-stg.sh      promote the dev tip into stg by one pull request when the promotion proof is green on that tip
#                            (ADR-0145, #743), and never otherwise. No script pushes refs/heads/stg.
#
# SG_PROMOTE_PROOF names the proof.
#   ci (the default): the CI of the dev tip. Green promotes; red, running, unproven, unknown or a missing run promotes nothing.
#     The fast tier, which every event starts, is the whole CI proof of the dev tip (#1723). The complete tier belongs to the
#     stg-to-main pull request: this script never asks for it and never waits on it.
#     A red check is red only when a job of its workflow run failed after it ran a step (#1629, ADR-0164): a job GitHub could not
#     start measured nothing, so it never holds the promotion, as the base check reads it.
#   local: the promotion gate's full-result line, $SG_STATE/gate-status-full. While the account's Actions billing keeps CI from
#     starting, the full suite in both modes is the proof (owner decision on #439). The run that earns it writes the line through
#     the existing seam, with the kind gate.sh names for a promotion to stg:
#         SG_GATE_KIND=full bash gate.sh bash -c '<the full suite>'
#     stg moves only when the line says `full`, `pass`, and names the dev tip exactly. A missing proof, a failed proof and a
#     proof for another sha each refuse in their own words.
#   fast: the fast proof word, $SG_STATE/gate-status-promote (#1723): `<epoch> TAB fast TAB pass TAB <sha>` for the dev tip. A
#     record for another tip waits (exit 75); any other word or result refuses.
# The script prints `promote-stg: tip <sha>` for the tip it evaluated, so a caller keys a refusal to that tip, never to a tip it
# proved earlier (#1495). Under the ci proof a tip whose checks have not reported prints `promote-stg: pending <sha>` and exits 75
# (EX_TEMPFAIL): a wait, no refusal, no marker. Any other non-zero exit is a refusal.
# Exit 0: the dev tip is in stg (promoted, or already there). Exit 1: nothing promoted; the one reason goes to $SG_STATE/promote.log
# once per tip and reason (the dedupe list keeps its last 20 lines), so a pass that repeats says nothing new, and to stderr.
# A later green tip carries the earlier work with it. The consolidation is one pull request (#743, owner rule for every project):
# the pass opens or reuses the open `dev` -> `stg` pull request and merges it with a merge commit, so a later tip is measured by
# ancestry, never by identity. The proof is read before the merge, so a red tip leaves the pull request open and promotes nothing.
# This script never writes main: main moves by promote-main.sh alone, a fast-forward, and the merge path refuses a base that is
# the main branch (SG_MAIN_BRANCH). The work happens in a throwaway clone.
# $SG_PROMOTE_REMOTE overrides the remote; $SG_CI_STATE_CMD <sha> prints green, red, running, unproven or unknown (the CI seam);
# SG_PROMOTE_PROOF selects the proof (ci, the default).
d=$(cd "$(dirname "$0")" && pwd); remote=${SG_PROMOTE_REMOTE:-https://github.com/$SG_REPO.git}
dev=${SG_BASE:-dev}; stg=${SG_STG_BRANCH:-stg}   # the work branch and the promotion branch, both named by the profile (#1646)
[ "${SG_PROMOTE:-on}" = off ] && exit 0           # a profile that delivers by another path promotes nothing: no fetch, no line
export GIT_TERMINAL_PROMPT=0
# SG_PROMOTE_OPEN_ONLY opens or reuses one pull request and merges nothing: no lock, no proof, no merge (ADR-0164, #1051).
# It is the one pull request route of the loop, so both promotions come through it:
#   dev -> stg (the default pair): promote-pass.sh calls it so the fast tier starts on the tip while the quiet window passes.
#     It asks for nothing: no label is sent, and the fast tier every event starts is the whole CI proof of the dev tip.
#   stg -> main: promote-main-pass.sh names the pair (SG_PROMOTE_OPEN_HEAD, SG_PROMOTE_OPEN_BASE), the request label
#     (SG_PROMOTE_OPEN_LABEL) and the record (SG_PROMOTE_OPEN_RECORD, a file name in $SG_STATE). The label is the request for
#     the complete tier (ADR-0173). It is removed first (a label that is not there is fine) and then sent, so a second
#     request on the same pull request is a new label event. The record is `<head sha> TAB <epoch>`, the head the pull
#     request answered, and it is written only after GitHub accepted the label. A head that is SG_PROMOTE_OPEN_SKIP, the
#     sha of the last red verdict, is not requested. That pull request is never merged: main moves by a fast-forward.
if [ -n "${SG_PROMOTE_OPEN_ONLY:-}" ]; then
  . "$d/ghcall.sh"
  oh=${SG_PROMOTE_OPEN_HEAD:-$dev}; ob=${SG_PROMOTE_OPEN_BASE:-$stg}; label=${SG_PROMOTE_OPEN_LABEL:-}
  if [ -n "$label" ]; then say_body="The loop opened this pull request to run the complete CI tier on the $oh tip. It is never merged by hand: the loop moves $ob by a fast-forward when the complete tier is green."
  else say_body="The $oh tip is under CI. Merge this to move $ob."; fi
  # The lookup names the head and the base, and answers the number and the head sha in one read.
  row=$(gh_api "repos/$SG_REPO/pulls?state=open" -q '[.[] | select(.head.ref == "'"$oh"'" and .base.ref == "'"$ob"'")][0] | "\(.number // "") \(.head.sha // "")"' 2>/dev/null) \
    || { [ -z "$label" ] && exit 0; echo "promote-stg: the open $oh -> $ob pull request could not be read" >&2; exit 1; }
  read -r pr hsha <<< "$row"
  if [ -z "$pr" ]; then
    row=$(gh_send -X POST "repos/$SG_REPO/pulls" -f head="$oh" -f base="$ob" -f "title=Promote $oh into $ob" \
      -f "body=$say_body" -q '"\(.number // "") \(.head.sha // "")"' 2>/dev/null) || row=
    read -r pr hsha <<< "$row"
  fi
  [ -n "$label" ] || exit 0   # the default pair asks for nothing
  { [ -n "$pr" ] && [ -n "$hsha" ]; } || { echo "promote-stg: the $oh -> $ob pull request could not be opened, or it names no head" >&2; exit 1; }
  if [ -n "${SG_PROMOTE_OPEN_SKIP:-}" ] && [ "$hsha" = "$SG_PROMOTE_OPEN_SKIP" ]; then
    echo "promote-stg: not requested: $hsha is the head of the last red verdict, and a new tip is needed"; exit 0
  fi
  gh_send -X DELETE "repos/$SG_REPO/issues/$pr/labels/$label" >/dev/null 2>&1   # a label that is not there answers 404: fine
  o=$(gh_send -X POST "repos/$SG_REPO/issues/$pr/labels" -f "labels[]=$label" 2>&1) \
    || { echo "promote-stg: the $label label was refused on pull request #$pr: $(printf '%s' "$o" | tail -1)" >&2; exit 1; }
  if [ -n "${SG_PROMOTE_OPEN_RECORD:-}" ]; then
    rec=${SG_STATE:-/tmp/sg-state}/$SG_PROMOTE_OPEN_RECORD; mkdir -p "${rec%/*}"
    printf '%s\t%s\n' "$hsha" "$(date +%s)" > "$rec.$$" && mv -f "$rec.$$" "$rec" || exit 1
  fi
  echo "promote-stg: requested $hsha on pull request #$pr"
  exit 0
fi
# The merge route never serves main (ADR-0173): main takes no merge commit, so a profile whose promotion branch is the main
# branch is refused here, before any lock or proof.
if [ "$stg" = "${SG_MAIN_BRANCH:-main}" ]; then
  echo "promote-stg: refused: the base is $stg, the main branch; it moves by promote-main.sh, a fast-forward, never by a merge" >&2; exit 1
fi
# The local proof reads the machine-wide gate lock (#392), so the proof cannot change under it; a pass already inside the lock
# just runs. A ci or fast proof runs no local suite, so it takes no gate lock and no gate slot (#2061): its own `promote-stg`
# lock keeps two promotions from overlapping, and the promotion never waits behind the test gates.
if [ "${SG_PROMOTE_PROOF:-ci}" = local ]; then
  case ":${SG_SERIAL_HELD:-}:" in *":gate:"*) ;; *) SG_GATE_STATUS=self exec bash "$d/gate.sh" bash "$0" "$@";; esac
else
  case ":${SG_SERIAL_HELD:-}:" in *":promote-stg:"*|*":gate:"*) ;; *) exec bash "$d/serial.sh" promote-stg bash "$0" "$@";; esac
fi
state=${SG_STATE:-/tmp/sg-state};   # the machine-wide default serial.sh uses: never TMPDIR, which is per user
mkdir -p "$state"; log=$state/promote.log
W=$(mktemp -d) || exit 1; trap 'rm -rf "$W"' EXIT
g() { /usr/bin/git -C "$W" "$@"; }
seen() { echo "$1" >> "$state/promote.seen"; tail -n 20 "$state/promote.seen" > "$state/promote.seen.tmp" && mv "$state/promote.seen.tmp" "$state/promote.seen"; }   # the dedupe list keeps the last 20 lines, never grows (#438)
say() {  # sha, reason: one line per tip and reason
  grep -qxF "$1 $2" "$state/promote.seen" 2>/dev/null && return; seen "$1 $2"
  echo "$(date -u +%FT%TZ) promote-stg: $1 $2" >> "$log"; }
refuse() { say "$1" "$2"; echo "promote-stg: $2" >&2; exit 1; }   # the one reason, on the log line and stderr
# (#2702) The promotion pass emits one release event when stg moves: `stg_release`, which a subscribed channel announces. The
# record holds the event id and the items the release carries, and `voiced` reads it. A record that cannot be written costs the
# announcement alone: the promotion already happened, so it never changes this exit code.
emit_release() {  # <event> <key> <from> <to>
  local event=$1 key=$2 args=() l out
  while IFS= read -r l; do [ -n "$l" ] && args+=(--item "$l"); done <<< "$(g log --no-merges --format='%h %s' "$3..$4" 2>/dev/null)"
  [ ${#args[@]} -gt 0 ] || args=(--item "$4")
  out=$(python3 "$d/../../voice/release_events.py" emit --event "$event" --key "$key" "${args[@]}" 2>&1) \
    || { echo "release: $event not emitted: $(printf '%s' "$out" | tail -1)" >&2; return 0; }
  echo "release: $out"
}
proof=${SG_PROMOTE_PROOF:-ci}
ci_state() {  # sha -> green | red | running | unproven | unknown, from the check runs of the commit, a red refined by its run's jobs
  if [ -n "$SG_CI_STATE_CMD" ]; then "$SG_CI_STATE_CMD" "$1"; return; fi
  . "$d/ghcall.sh"; . "$d/basecheck.sh"   # the classifier is the base check's own (#786): the two readers cannot disagree
  local cr c; cr=$(gh_api "repos/$SG_REPO/commits/$1/check-runs?per_page=100&filter=latest") || return 1
  c=$(printf '%s' "$cr" | sg_ci_class) || return 1
  if [ "$c" = red ]; then   # sg_ci_class's red, refined by the jobs of its failed checks: a job that never started measured nothing (#1629)
    c=$(sg_ci_refine "$cr") || return 1
    c=${c%% *}
  fi
  [ "$c" = none ] && c=running   # zero check runs is not yet green: promotion waits for them
  printf '%s\n' "$c"
}
prove() {  # <tip>: refuse unless the proof for this tip is green, each refusal in its own words
  if [ "$proof" = local ]; then
    IFS=$'\t' read -r _ kind res sha < "$state/gate-status-full" 2>/dev/null
    [ "$kind" = full ] || refuse "$1" "not promoted: no local proof: $state/gate-status-full holds no full-gate result (run the promotion gate: SG_GATE_KIND=full bash gate.sh bash -c '<the full suite>')"
    [ "$sha" = "$1" ] || refuse "$1" "not promoted: the local proof is for $sha, not the dev tip $1"
    # The gate's own word (spec 1163, #1174): `pass` promotes, and a line that says `unproven` is named as such, never read
    # as a pass or misnamed a fail.
    [ "$res" = pass ] || { local w; case "${res:-}" in fail) w="a fail";; '') w=none;; *) w=$res;; esac
      refuse "$1" "not promoted: the local proof is $w, not a pass"; }
  elif [ "$proof" = fast ]; then
    # #1723: the fast proof word is one record for the exact tip. A record for another tip is a wait (#1495); any other word refuses.
    IFS=$'\t' read -r _ fk fres fsha < "$state/gate-status-promote" 2>/dev/null
    [ "$fk" = fast ] || refuse "$1" "not promoted: the fast record holds ${fk:-no word}, not the fast proof"
    [ "$fsha" = "$1" ] || { echo "promote-stg: pending $1: the fast proof is for ${fsha:-no tip}, not this tip"; exit 75; }
    [ "$fres" = pass ] || refuse "$1" "not promoted: the fast proof is ${fres:-none}, not a pass"
  else
    st=$(ci_state "$1" 2>/dev/null)
    [ "$st" = running ] && { echo "promote-stg: pending $1: CI is running"; exit 75; }   # the checks have not reported: a wait, never a refusal (#1495)
    # A cancelled or timed-out check never reports again, so unproven is a refusal, never a wait: the ci proof promotes only a
    # green tip (#1532).
    [ "$st" = green ] || refuse "$1" "not promoted: CI is ${st:-unknown} (green needed)"
  fi; }
g init -q . 2>/dev/null; g remote add origin "$remote"
o=$(g fetch -q origin "+refs/heads/$dev:refs/remotes/origin/$dev" "+refs/heads/$stg:refs/remotes/origin/$stg" 2>&1) || { echo "promote-stg: fetch failed: $(printf '%s' "$o" | tail -1)" >&2; exit 1; }
tip=$(g rev-parse "origin/$dev") || exit 1
prev=$(g rev-parse "origin/$stg" 2>/dev/null) || prev=   # the stg this merge moves, for the release event's items (#2702)
echo "promote-stg: tip $tip"
g merge-base --is-ancestor "$tip" "origin/$stg" && exit 0   # the tip is already in the promotion branch: no pull request is opened
# A promotion leaves a merge commit in stg, so stg is never an ancestor of the next dev tip. The work stg holds and dev lacks
# must be those merge commits alone; any plain commit is the owner's to resolve before a merge.
o=$(g rev-list --no-merges "$tip..origin/$stg" 2>/dev/null) || o=
[ -z "$o" ] || refuse "$tip" "not promoted: $stg holds $(printf '%s\n' "$o" | /usr/bin/grep -c .) commit(s) the $dev tip lacks; the owner resolves it"
prove "$tip"
if [ -n "$(bash "$d/red-tier.sh" files "$state")" ]; then   # stg is red: the files of its failing tier run on the tip first (#2203)
  g checkout -q --detach "$tip" 2>/dev/null || refuse "$tip" "not promoted: the $dev tip could not be checked out for the red tier's files"
  bash "$d/red-tier.sh" run "$state" "$W"; rc=$?
  if [ $rc != 0 ]; then
    bash "$d/red-tier.sh" alert "$state" "$tip"
    refuse "$tip" "not promoted: the tests of the red complete tier fail on the $dev tip: $(bash "$d/red-tier.sh" failed "$state")"
  fi
fi
. "$d/ghcall.sh"
# The owner's consolidation rule (#743): one pull request, never a push. The merge is a merge commit, so the next tip is read
# by ancestry and a tip already in stg is no work.
pr=$(gh_api "repos/$SG_REPO/pulls?state=open" -q '[.[] | select(.head.ref == "'"$dev"'" and .base.ref == "'"$stg"'")][0].number // empty' 2>/dev/null) \
  || { echo "promote-stg: pending $tip: the open $dev -> $stg pull request could not be read" >&2; exit 75; }   # a transport failure is unproven, never a verdict (ADR-0164, #1490)
if [ -z "$pr" ]; then
  o=$(gh_send -X POST "repos/$SG_REPO/pulls" -f head="$dev" -f base="$stg" -f "title=Promote $dev into $stg" \
      -f "body=The $dev tip $tip carries its proof. Merge this to move $stg." 2>&1) \
    || refuse "$tip" "not promoted: the $dev -> $stg pull request could not be opened: $(printf '%s' "$o" | tail -1)"
  pr=$(printf '%s' "$o" | jq -r '.number // empty' 2>/dev/null)
  [ -n "$pr" ] || refuse "$tip" "not promoted: the pull request was opened but its number was not read"
fi
# The merge names the proven head: GitHub answers 409 when the head moved past it. The pull request then names the new head, and
# the next pass reads that tip (#1723), so a moved head is a wait, never a refusal of the old one.
o=$(gh_send -X PUT "repos/$SG_REPO/pulls/$pr/merge" -f merge_method=merge -f sha="$tip" 2>&1) || {
  hd=$(gh_api "repos/$SG_REPO/pulls/$pr" -q '.head.sha' 2>/dev/null) || hd=
  [ -n "$hd" ] && [ "$hd" != "$tip" ] && { echo "promote-stg: pending $tip: the head moved to $hd before the merge (409)" >&2; exit 75; }
  refuse "$tip" "not promoted: pull request #$pr could not be merged: $(printf '%s' "$o" | tail -1)"; }
printf '%s' "$o" | jq -e '.merged == true' >/dev/null 2>&1 \
  || refuse "$tip" "not promoted: pull request #$pr did not merge: $(printf '%s' "$o" | jq -r '.message // "no reason"' 2>/dev/null)"
echo "PROMOTED $stg $tip via pull request #$pr"
emit_release stg_release "stg_release:$tip" "${prev:-$tip}" "$tip"
