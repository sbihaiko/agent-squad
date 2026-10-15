#!/bin/bash
# usage: wave.sh start <run>          the run cuts its wave branch wave/<run> from origin/$SG_BASE, on the remote
#        wave.sh enter <run> <issue>  from the child's worktree: the local gates, the rebase on the wave tip, the --ff-only entry
#        wave.sh land <run>           when the run ends: the rebase on the moved base, the one push, and the closes
#
# One branch per wave (ADR-0145). A wave is the set of children that land in $SG_BASE (`dev`) together, and its branch is the
# only branch that lands there for them, in one fast-forward push with one CI run. A run cuts the branch from `dev` when it
# starts, named for the run; each child cuts its worktree branch from the wave branch, runs its local gates in its own
# worktree, is rebased on the wave tip, and enters the branch by a fast-forward: git refuses a push that is not one, so no
# merge commit can enter. A child that fails its gates, conflicts with the wave tip, or whose push is refused never enters:
# its issue goes back to the queue (its dispatch marker is dropped and its card returns to the column the dispatch reads),
# and `enter` says why on one `WAVE REQUEUE` line. A child that entered moves its card to Code Complete: Code Complete means
# the child is in the wave branch, Done means the wave is in `dev`. The wave branch is rebased on `dev` only before its own
# final push (the wave's landing pass), never between entries: between them the tip moves only by a child that entered.
#
# The gates are the profile's SG_WAVE_GATES: by default the fast gate (fast-gate.sh): ruff, mypy, the fast battery and the shell suites of the scripts the child changes. `start` works in a
# throwaway clone, never in a checkout of the owner's, as ffland.sh does. `enter` runs where the child works, so it reads
# the branch from that worktree's HEAD, and the gates run there too. Every line the script prints is one `WAVE ...` line.
# A child pushes no branch of its own: its commit reaches the remote only through the wave branch, by the entry above. So one
# branch of a run is pushed at all, and when the run ends `land` rebases that branch on $SG_BASE -- the base moved under the
# wave by other work, and this is the only rebase on it -- and pushes its tip there in one fast-forward: one update, one CI
# run. A push the base's move refuses repeats from the fetch, rebased on the moved base (ADR-0145 rule 2), and so does a
# close the API refuses. The wave branch then goes, its work being in $SG_BASE. Every issue the wave carries is read, after the
# rebase, from the whole message of each commit the base still lacks (every `Refs #N`, subject or body, named once), and each is closed through the API, has its one delivery comment queued
# (cqflush.sh posts it), and moves to Done (#375). A close that fails queues no comment, writes no ledger row and moves no
# card: a delivery the API did not accept is never claimed. A landing that cannot rebase, or whose push is refused, lands
# nothing and closes nothing.
#
# The run's completion finds its wave through the marker `start` leaves in $SG_STATE/auto/wave-<run>, and `land` clears it
# when the wave is in $SG_BASE: the loop's cycle runs `land` for a finished run that has a wave, and never for a run that cut
# none (#375). That completion runs the run's child-completion pass first, inside the same single-flight lock, so the wave
# `land` is handed is one the run's finished children are in: a landing attempted before that entry finds an empty wave,
# refuses it, and the run -- read once -- would leave the work of its children on wave/<run> for good.
#
# A child enters only after the review of its own diff approved it (#376): `enter` reads review-<branch>.md beside the base
# it was read against and the sha, and holds the child out (one `WAVE HOLD` line, nothing requeued) when the review did not
# approve it, was read against another base, or read a different head.
# A wave with nothing ahead of the base landed empty: its ref and marker go and `land` exits 0. A landing that fails on a rebase
# conflict or a push refused twice is counted per run (SG_WAVE_LAND_TRIES, default 3); at the cap each issue the wave carries
# gets the label frozen:owner and one queued comment, and the run's wave marker is cleared so the cycle stops retrying.
# `waved-<run>` stays after the landing: the durable record that the run cut a wave.
# Exit: 0 entered (or the wave branch was cut, or the wave landed), 1 not entered (requeued after a failed gate, a conflict or a
# refused push; held where it stands when the review did not approve the child), 2 a bad call or a wave branch that is not there.
# Guards: `test-wave.sh`.
here="$(cd "$(dirname "$0")" && pwd)"   # fixed at the start: a relative $0 means nothing once the work moves to a child's worktree
. "$here/env.sh" 2>/dev/null || exit 0
. "$here/ghcall.sh"   # the close and the board write go through the doors beside this script
G=/usr/bin/git; remote=${SG_LAND_REMOTE:-https://github.com/$SG_REPO.git}; base=${SG_BASE:-dev}
export GIT_TERMINAL_PROMPT=0
wave_of() { case "${1:-}" in wave/*) printf '%s\n' "$1";; *) printf 'wave/%s\n' "$1";; esac; }
# mark <run> <wave>: the run has a wave, and its completion has one to land. $SG_STATE/auto is the loop's marker directory;
# `land` clears the marker when the wave is in $SG_BASE.
# `waved-<run>` is the durable record that the run cut a wave: nothing clears it, so childcc.py still knows, after the landing
# removed `wave-<run>`, that a child of this run never gets the direct Code Complete move.
mark() { mkdir -p "$SG_STATE/auto" && printf '%s\n' "$2" > "$SG_STATE/auto/wave-$1" && printf '%s\n' "$2" > "$SG_STATE/auto/waved-$1"; }

start() {   # <run>: cut wave/<run> from origin/$SG_BASE, unless it is already there. A scratch clone: the owner's checkout is never touched.
  local run=$1 w; w=$(wave_of "$run")
  [ -n "$run" ] || { echo "WAVE-ERROR wave.sh start wants the run's name"; return 2; }
  local S; S=$(mktemp -d) || { echo "WAVE-ERROR no scratch dir"; return 2; }
  sc() { $G -C "$S" "$@"; }
  sc init -q . || { echo "WAVE-ERROR cannot make a scratch clone"; rm -rf "$S"; return 2; }
  sc remote add origin "$remote" 2>/dev/null
  # A branch that is already there is left where it is: a second start must never move a wave a child already entered.
  # One fetch per ref: `git fetch` takes a refspec that names a missing ref as a failure of the whole call, so the wave
  # branch, absent until the first cut, gets its own.
  sc fetch -q origin "+refs/heads/$base:refs/heads/$base" 2>/dev/null
  sc fetch -q origin "+refs/heads/$w:refs/heads/$w" 2>/dev/null || true
  if sc rev-parse -q --verify "refs/heads/$w" >/dev/null; then
    echo "WAVE $w $(sc rev-parse "refs/heads/$w") exists"; mark "$run" "$w"; rm -rf "$S"; return 0
  fi
  sc rev-parse -q --verify "refs/heads/$base" >/dev/null || { echo "WAVE-ERROR $base is not on the remote"; rm -rf "$S"; return 2; }
  if sc push -q origin "refs/heads/$base:refs/heads/$w" 2>/dev/null; then
    echo "WAVE $w $(sc rev-parse "refs/heads/$base") cut from $base"
  else echo "WAVE-ERROR the push of $w was refused"; rm -rf "$S"; return 1; fi
  mark "$run" "$w"
  rm -rf "$S"; return 0
}

requeue() {   # <issue> <why>: the child never entered, so its issue goes back to the queue and the loop can build it again.
  local n=$1 why=$2 f
  rm -f "$SG_STATE/auto/disp-$n" "$SG_STATE/auto/bug-$n"
  "$BOARD" "$SG_OWNER" "$SG_PROJECT" "${SG_TODO:-Todo}" "$n" >/dev/null 2>&1
  f=$(sg_cq_file "$n-wave$$.md" 2>/dev/null) && printf '%s\n' "$why" > "$f"
  echo "WAVE REQUEUE #$n: $why"
}

enter() {   # <run> <issue>: from the child's worktree, the gates, the rebase on the wave tip, and the --ff-only entry.
  local run=$1 iss=$2 w br sha files out; w=$(wave_of "$run")
  { [ -n "$run" ] && [ -n "$iss" ]; } || { echo "WAVE-ERROR wave.sh enter <run> <issue>"; return 2; }
  br=$($G symbolic-ref --quiet --short HEAD) || { echo "WAVE-ERROR $PWD is on no branch (#$iss)"; return 2; }
  [ -n "${SG_WAVE_GATES:-}" ] || { echo "WAVE-ERROR no gates are set for this repository (SG_WAVE_GATES): a child never enters a wave ungated (#$iss)"; return 2; }
  # The review of the child reads only its own commits, against its merge-base with the wave branch (#376), and a verdict that
  # did not approve it keeps it out of the wave. The gate reads three things: the verdict, the base it was read against (the
  # wave branch, never `dev`, which would be the whole wave), and the sha it read. The sha is read before the rebase below:
  # the entry rewrites the child's shas while its own commits stay the same, so what was approved is what enters, rebased.
  # A child that cannot enter is held where it stands -- its branch and its review stay for the fix round -- so nothing is
  # requeued here, unlike a failed gate, a conflict or a refused push.
  local rk="$SG_STATE/review-$(printf '%s' "$br" | tr / _)" rsha
  rsha=$($G rev-parse HEAD)
  [ -s "$rk.md" ] || { echo "WAVE HOLD #$iss: no review of $br yet: a child never enters a wave unreviewed"; return 1; }
  [ "$(cat "$rk.base" 2>/dev/null)" = "$w" ] || { echo "WAVE HOLD #$iss: the review of $br was read against '$(cat "$rk.base" 2>/dev/null)', not $w: it read the whole wave, not this child's own commits"; return 1; }
  [ "$(cat "$rk.sha" 2>/dev/null)" = "$rsha" ] || { echo "WAVE HOLD #$iss: the review of $br read $(cut -c1-9 "$rk.sha" 2>/dev/null), not HEAD ${rsha:0:9}"; return 1; }
  head -1 "$rk.md" | /usr/bin/grep -q 'VERDICT: APPROVE' || { echo "WAVE HOLD #$iss: the review of $br did not approve it: $(head -1 "$rk.md")"; return 1; }
  # The gates run in the child's own worktree, and only they open the entry (ADR-0145 rule 9). A child that fails them never
  # enters, and the wave CI is not what finds it: that one runs once, when the whole wave lands in `dev`.
  # They run under the machine-wide gate lock (#392): a second gate waits, and its one wait line is passed on.
  out=$(SG_GATE_KIND=fast bash "$here/gate.sh" bash -c "$SG_WAVE_GATES" 2>&1); grc=$?; printf '%s\n' "$out" | /usr/bin/grep '^serial: waiting for lock' >&2
  [ $grc = 0 ] || {
    requeue "$iss" "the local gates failed: $(printf '%s' "$out" | /usr/bin/grep -v '^$' | tail -1)"; return 1; }
  # A push that is not a fast-forward is refused by git itself, so the entry is the rebase below and nothing else. The wave
  # tip can move between that rebase and the push (a sibling entered first): the push is then refused, and one repeat from
  # the fetch is what a rejected push gets, as the landing pass does (ADR-0145 rule 2).
  local try=1
  while :; do
    $G fetch -q origin "+refs/heads/$w:refs/remotes/origin/$w" 2>/dev/null || {
      echo "WAVE-ERROR $w is not on $remote (the run cuts it from $base at its start): #$iss enters no wave"; return 2; }
    if ! $G rebase "origin/$w" >/dev/null 2>&1; then
      files=$($G diff --name-only --diff-filter=U | tr '\n' ' '); $G rebase --abort >/dev/null 2>&1
      requeue "$iss" "the rebase on $w conflicts: ${files% }"; return 1
    fi
    sha=$($G rev-parse HEAD)
    if $G push -q origin "HEAD:refs/heads/$w" 2>/dev/null; then
      "$BOARD" "$SG_OWNER" "$SG_PROJECT" "Code Complete" "$iss" >/dev/null 2>&1
      echo "WAVE $br $sha entered $w (#$iss)"; return 0
    fi
    [ "$try" = 1 ] || break
    try=2
  done
  requeue "$iss" "the push to $w was refused: HEAD is not a fast-forward of its tip"; return 1
}

# refs_of <clone> <range>: every `Refs #N` in the whole message of each commit of the range, each named once.
refs_of() { $G -C "$1" log --format=%B "$2" 2>/dev/null | /usr/bin/grep -oE 'Refs #[0-9]+' | /usr/bin/grep -oE '[0-9]+' | sort -un | tr '\n' ' '; }

# stuck <run> <wave> <clone> <why>: a landing that failed on a cause a repeat does not change. The attempts are counted in
# auto/wland-tries-<run>; at SG_WAVE_LAND_TRIES (default 3) the pass stops retrying: each issue the wave carries gets the label
# frozen:owner and one queued comment, and the run's wave marker goes, so the loop's cycle reads the run and stops. The wave
# branch stays on the remote with the work. Returns 1 either way: the landing did not happen.
stuck() {
  local run=$1 w=$2 S=$3 why=$4 f="$SG_STATE/auto/wland-tries-$1" t n
  mkdir -p "$SG_STATE/auto"; t=$(( $(cat "$f" 2>/dev/null || echo 0) + 1 )); printf '%s\n' "$t" > "$f"
  echo "WAVE-ERROR $why"
  [ "$t" -ge "${SG_WAVE_LAND_TRIES:-3}" ] || return 1
  export SG_GH_PASS=wave
  for n in $(refs_of "$S" "origin/$base..origin/$w"); do
    gh_send -X POST "repos/$SG_REPO/issues/$n/labels" -f "labels[]=frozen:owner" >/dev/null 2>&1
    mkdir -p "$SG_STATE/cq"
    printf 'Needs the owner: the landing of %s in %s failed %s times and stopped (%s). The work stays on %s. Refs #%s\n' "$w" "$base" "$t" "$why" "$w" "$n" > "$(sg_cq_file "$n-wavestuck$$.md")"
  done
  rm -f "$SG_STATE/auto/wave-$run" "$f"
  echo "WAVE NEEDS-HUMAN $w: the landing failed $t times; the run's wave marker is cleared and $w stays on the remote"
  return 1
}

land() {   # <run>: the wave's landing pass -- the rebase on the base, the one push, and the closes of what it carries.
  local run=$1 w; w=$(wave_of "$run")
  [ -n "$run" ] || { echo "WAVE-ERROR wave.sh land wants the run's name"; return 2; }
  local S; S=$(mktemp -d) || { echo "WAVE-ERROR no scratch dir"; return 2; }
  sc() { $G -C "$S" "$@"; }
  sc init -q . || { echo "WAVE-ERROR cannot make a scratch clone"; rm -rf "$S"; return 2; }
  sc remote add origin "$remote" 2>/dev/null
  [ -n "$(sc config user.name)" ] || sc config user.name "${SG_LAND_NAME:-squad-goal}"
  [ -n "$(sc config user.email)" ] || sc config user.email "${SG_LAND_EMAIL:-squad-goal@localhost}"
  # One fetch per ref, as `start` does: a refspec that names a missing ref fails the whole call, and the wave branch is the one
  # that may be gone (a wave already landed).
  sc fetch -q origin "+refs/heads/$base:refs/remotes/origin/$base" 2>/dev/null || {
    echo "WAVE-ERROR $base is not on $remote"; rm -rf "$S"; return 2; }
  sc fetch -q origin "+refs/heads/$w:refs/remotes/origin/$w" 2>/dev/null || {
    echo "WAVE-ERROR $w is not on $remote (the run cuts it from $base at its start)"; rm -rf "$S"; return 2; }
  sc checkout -q -B land "origin/$w" 2>/dev/null || { echo "WAVE-ERROR the checkout of $w failed"; rm -rf "$S"; return 2; }
  # The rebase on the base happens here and nowhere else (ADR-0145 rule 14): between entries the tip moves only by a child that
  # entered, so a base that moved under the wave is rebased onto now, before the one push. A push the remote refuses -- the
  # base moved once more between this rebase and the push -- repeats from the fetch: the moved base is fetched, the wave is
  # rebased on it, and the push goes again (ADR-0145 rule 2). One repeat, as the entry does.
  local out sha try=1 issues n
  while :; do
    out=$(sc rebase "origin/$base" 2>&1) || { sc rebase --abort >/dev/null 2>&1
      stuck "$run" "$w" "$S" "the rebase of $w on $base conflicts: $(printf '%s' "$out" | tail -1)"; rm -rf "$S"; return 1; }
    # Nothing ahead of the base is a wave that landed empty (a run with no dispatch, every child failed its gates, no child):
    # there is nothing to push or close, so the wave ref goes, the marker goes, and the run is done, exit 0.
    [ "$(sc rev-list --count "origin/$base..HEAD")" -gt 0 ] || {
      sc push -q origin ":refs/heads/$w" >/dev/null 2>&1 || echo "WAVE-NOTE branch $w was not deleted" >&2
      rm -rf "$S"; rm -f "$SG_STATE/auto/wave-$run" "$SG_STATE/auto/wland-tries-$run"
      echo "WAVE $w landed empty in $base (nothing ahead of it; the wave branch is deleted)"; return 0; }
    sha=$(sc rev-parse HEAD)
    # The issues the wave carries: every `Refs #N` in a commit message, subject or body (a child never writes Closes), each
    # named once. They are read here, after the rebase, from what is ahead of the base: a commit the base already holds is
    # dropped by the rebase, and its issue was not delivered by this landing. A repeat from a refused push reads them again.
    issues=""; while read -r n; do [ -n "$n" ] && issues="${issues:+$issues }$n"; done < <(
      sc log --format=%B "origin/$base..HEAD" 2>/dev/null | /usr/bin/grep -oE 'Refs #[0-9]+' | /usr/bin/grep -oE '[0-9]+' | sort -un)
    # ONE push of the wave's tip to the base: one ref update, one CI run, and a push git refuses unless it is a fast-forward,
    # so no merge commit can land. Nothing else is pushed -- no child branch, and the wave branch goes once its work is in.
    out=$(sc push -q origin "HEAD:refs/heads/$base" 2>&1) && break
    [ "$try" = 1 ] || { stuck "$run" "$w" "$S" "the push of $w to $base was refused: $(printf '%s' "$out" | tail -1)"; rm -rf "$S"; return 1; }
    try=2
    sc fetch -q origin "+refs/heads/$base:refs/remotes/origin/$base" 2>/dev/null || {
      echo "WAVE-ERROR $base is not on $remote"; rm -rf "$S"; return 2; }
  done
  sc push -q origin ":refs/heads/$w" >/dev/null 2>&1 || echo "WAVE-NOTE branch $w was not deleted" >&2
  rm -rf "$S"
  rm -f "$SG_STATE/auto/wave-$run" "$SG_STATE/auto/wland-tries-$run"   # the wave is in $base: the run's completion has no wave left to land
  # The wave is in $base: each issue it carries closes through the API, and its one delivery comment is queued, never posted
  # (cqflush.sh keeps one per issue, edited in place, #328). Each card reads Done, which is what the landing means. Only a
  # close the API accepted queues the comment, writes the ledger row and moves the card: a close that fails leaves the issue
  # open, and every one of those three would claim a delivery that did not happen. The close repeats once, as the push does.
  export SG_GH_PASS=wave
  for n in $issues; do
    local closed= tries=1
    while :; do
      gh_send -X PATCH "repos/$SG_REPO/issues/$n" -f state=closed -f state_reason=completed >/dev/null && { closed=1; break; }
      [ "$tries" = 1 ] || break
      tries=2
    done
    [ -n "$closed" ] || { echo "WAVE-ERROR #$n did not close after the landing of $w: no delivery comment is queued and its card keeps its column"; continue; }
    mkdir -p "$SG_STATE/cq"
    printf 'Closed #%s after landing %s in %s as %s (one fast-forward push, %s deleted). Refs #%s\n' "$n" "$w" "$base" "$sha" "$w" "$n" > "$(sg_cq_delivery "$n" "wave$$")"
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$w" "$sha" "$n" >> "$SG_STATE/landed.tsv"   # the ledger the hub's Landed branches lane reads
    "$BOARD" "$SG_OWNER" "$SG_PROJECT" "Done" "$n" >/dev/null 2>&1
  done
  local named=""; for n in $issues; do named="${named:+$named, }#$n"; done
  echo "WAVE $w $sha landed in $base (${named:-no issue named: the wave carries no Refs})"
  return 0
}

case "${1:-}" in
  start) shift; start "$@";;
  enter) shift; enter "$@";;
  land) shift; land "$@";;
  *) echo "usage: wave.sh start <run> | wave.sh enter <run> <issue> | wave.sh land <run>" >&2; exit 2;;
esac
