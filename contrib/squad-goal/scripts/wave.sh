#!/bin/bash
# usage: wave.sh start <run>          the run cuts its wave branch wave/<run> from origin/$SG_BASE, on the remote
#        wave.sh enter <run> <issue>  from the child's worktree: the local gates, the rebase on the wave tip, the --ff-only entry
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
# The gates are the profile's SG_WAVE_GATES: ruff, mypy and the fast battery for this repository. `start` works in a
# throwaway clone, never in a checkout of the owner's, as ffland.sh does. `enter` runs where the child works, so it reads
# the branch from that worktree's HEAD, and the gates run there too. Every line the script prints is one `WAVE ...` line.
# A child enters only after the review of its own diff approved it (#376): `enter` reads review-<branch>.md beside the base
# it was read against and the sha, and holds the child out (one `WAVE HOLD` line, nothing requeued) when the review did not
# approve it, was read against another base, or read a different head.
# Exit: 0 entered (or the wave branch was cut), 1 not entered (requeued after a failed gate, a conflict or a refused push;
# held where it stands when the review did not approve the child), 2 a bad call or a wave branch that is not there.
# Guards: `test-wave.sh`.
. "$(dirname "$0")/env.sh" 2>/dev/null || exit 0
G=/usr/bin/git; remote=${SG_LAND_REMOTE:-https://github.com/$SG_REPO.git}; base=${SG_BASE:-dev}
export GIT_TERMINAL_PROMPT=0
wave_of() { case "${1:-}" in wave/*) printf '%s\n' "$1";; *) printf 'wave/%s\n' "$1";; esac; }

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
    echo "WAVE $w $(sc rev-parse "refs/heads/$w") exists"; rm -rf "$S"; return 0
  fi
  sc rev-parse -q --verify "refs/heads/$base" >/dev/null || { echo "WAVE-ERROR $base is not on the remote"; rm -rf "$S"; return 2; }
  if sc push -q origin "refs/heads/$base:refs/heads/$w" 2>/dev/null; then
    echo "WAVE $w $(sc rev-parse "refs/heads/$base") cut from $base"
  else echo "WAVE-ERROR the push of $w was refused"; rm -rf "$S"; return 1; fi
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
  out=$(bash -c "$SG_WAVE_GATES" 2>&1) || {
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

case "${1:-}" in
  start) shift; start "$@";;
  enter) shift; enter "$@";;
  *) echo "usage: wave.sh start <run> | wave.sh enter <run> <issue>" >&2; exit 2;;
esac
