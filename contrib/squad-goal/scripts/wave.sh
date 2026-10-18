#!/bin/bash
# usage: wave.sh start <run>          the run cuts its wave branch wave/<run> from origin/$SG_BASE, on the remote
#        wave.sh enter <run> <issue>  from the child's worktree: the local gates, the rebase on the wave tip, the --ff-only entry
#        wave.sh land <run>           when the run ends: the rebase on the moved base, the one push, and the closes
#        wave.sh expired <run>        exit 0 when the run's wave reached its age cap (SG_WAVE_MAX_AGE_SECONDS)
#        wave.sh requeue <issue> <why> [cap]  the child never enters: its issue goes back to the queue and the loop builds it again
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
# card: a delivery the API did not accept is never claimed. That issue stays owed (#475): the landing exits 1 and keeps the wave marker, so a later pass closes it. A landing that cannot rebase, or whose push is refused, lands
# nothing and closes nothing.
#
# The run's completion finds its wave through the marker `start` leaves in $SG_STATE/auto/wave-<run>, and `land` clears it
# when the wave is in $SG_BASE: the loop's cycle runs `land` for a finished run that has a wave, and never for a run that cut
# none (#375). That completion runs the run's child-completion pass first (in the childcc single-flight lock, waited for a bounded time), then lands under a lock of its own, so the wave
# `land` is handed is one the run's finished children are in: a landing attempted before that entry finds an empty wave,
# refuses it, and the run -- read once -- would leave the work of its children on wave/<run> for good.
#
# A child enters only after the review of its own diff approved it (#376): `enter` reads review-<branch>.md beside the base
# it was read against and the sha, and holds the child out (one `WAVE HOLD` line, nothing requeued) when the review did not
# approve it, was read against another base, or read a different head. The review of a run child is read in the child's own
# worktree against the wave branch (childreview.sh, #448): a child's branch reaches no remote, so the review lane that lists
# pushed `fix/` and `gui/` branches never sees it, and the entry held every child for good.
# A review that asked for CHANGES on a run that already ended puts its issue back in the queue (`requeue`) instead of holding
# it: the child of a live run stays where it stands, because that run may still fix it.
# A wave with nothing ahead of the base landed empty: its ref and marker go and `land` exits 0. A landing that fails on a rebase
# conflict, a push refused twice, or a start it cannot make (the base or the wave branch is gone, the checkout fails: exit 2)
# is counted per run (SG_WAVE_LAND_TRIES, default 3); at the cap each issue the wave carries
# gets the label frozen:owner and one queued comment, and the run's wave marker is cleared so the cycle stops retrying.
# `waved-<run>` stays after the landing: the durable record that the run cut a wave.
#
# A wave has an age cap (#378, ADR-0145 rule 13): SG_WAVE_MAX_AGE_SECONDS, 7200 s by default, set by a profile (env.sh holds
# the default). `start` records the epoch it cut the wave in `auto/wave-start-<run>`, and the age is read from that marker,
# never from a second clock. A wave past its cap lands what it holds and the children of the run that never entered go back
# to the queue, an empty wave included: `land` reads the run's own record (as childcc.py does) and requeues every dispatched
# issue that is not ahead of the base. `enter` refuses a new child into an expired wave (its issue goes back to the queue),
# unless the review already approved the child's current head against the wave (#594): that child waited on the loop's own
# queue, not on its own work. The loop lands an expired wave without waiting for the run's verdict (watch.sh). `wave.sh
# expired <run>` is the predicate it reads. A requeue the cap makes is no failure of the child, so it never counts toward
# SG_REQUEUE_CAP (#594).
#
# A red check at the wave tip names the child that broke it (#377, ADR-0145 rule 11). The wave's check is its CI in
# production, which no run can produce while the account's GitHub Actions billing is blocked (issue #439); the local check
# at the tip, `SG_WAVE_TIP_GATES` (the fast gate by default, run in the wave's throwaway clone with the checkout's python),
# carries the same rule and is the one that runs today. When that check is red, `land` runs each child's own suite again
# against the tip, the last entrant first (`SG_WAVE_CHILD_GATES`; the default is the shell suite of each script that child's
# commit changes). The first child whose suite fails leaves the wave -- its commits are dropped from the tip, its issue goes
# back to the queue -- and the wave without it lands. A red tip no child's suite names is a landing that failed, as before.
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
mark() { mkdir -p "$SG_STATE/auto" && printf '%s\n' "$2" > "$SG_STATE/auto/wave-$1" && printf '%s\n' "$2" > "$SG_STATE/auto/waved-$1" \
  && { [ -s "$SG_STATE/auto/wave-start-$1" ] || date +%s > "$SG_STATE/auto/wave-start-$1"; }; }

# expired <run>: 0 when the run's wave reached its age cap (SG_WAVE_MAX_AGE_SECONDS, 7200 s by default, a profile setting).
# The age is the epoch `mark` wrote into the run's wave start marker, never a second clock. A wave with no marker, or one
# that is not cut, is never expired: the cap is a fact about a wave that exists.
expired() {
  local run=$1 t; [ -e "$SG_STATE/auto/wave-$run" ] || return 1
  t=$(cat "$SG_STATE/auto/wave-start-$run" 2>/dev/null); case "$t" in ''|*[!0-9]*) return 1;; esac
  [ $(( $(date +%s) - t )) -ge "${SG_WAVE_MAX_AGE_SECONDS:-7200}" ]
}

# run_issues <run-dir>: every issue the run dispatched, read from its own record as childcc.py reads it (spawn_accepted and
# its definition prompt), each named once. A run whose directory carries no record names none.
run_issues() {
  local f="$1/record.jsonl"; [ -f "$f" ] || return 0
  python3 -c 'import json,sys
sys.path.insert(0, sys.argv[2]); from child_issues import first   # the one reader childcc.py shares (#555)
for l in open(sys.argv[1]):
    try: d=json.loads(l)
    except Exception: continue
    if d.get("event") != "spawn_accepted": continue
    n = first(json.dumps(d.get("definition")))
    if n: print(n)' "$f" "$here" 2>/dev/null | sort -un
}

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

board_write() {   # <column> <issue>: a refused card write is named, so the card's observed column stays clear.
  local col=$1 n=$2 out rc
  out=$("$BOARD" "$SG_OWNER" "$SG_PROJECT" "$col" "$n" 2>&1); rc=$?
  [ "$rc" -eq 0 ] || printf 'WAVE-BOARD #%s %s refused: %s\n' "$n" "$col" "$out"
  return 0
}

freeze_owner() {   # <issue>: the freeze is a marker over the column reached, never a column of its own.
  local n=$1 out rc
  out=$(gh_send -X POST "repos/$SG_REPO/issues/$n/labels" -f "labels[]=frozen:owner" 2>&1); rc=$?
  [ "$rc" -eq 0 ] || printf 'WAVE-FREEZE #%s frozen:owner refused: %s\n' "$n" "$out"
  return 0
}

requeue() {   # <issue> <why> [cap]: the child never entered, so its issue goes back to the queue and the loop can build it again.
  local n=$1 why=$2 f c cf; cf="$SG_STATE/auto/requeues-$n"
  # The count is the issue's, not the run's: each new run started afresh, and one issue was dispatched 19 times (#532). The
  # SG_REQUEUE_CAP-th requeue (3 by default) stops it: the marker stays, the card keeps the column it reached with an owner
  # freeze, and a SQUAD-NOT-STARTABLE comment is queued, which both dispatch passes read. Removing the counter file and the
  # comment frees the issue. A requeue the wave's age cap made (`cap`, #594) is the loop's delay, not a failure of the child:
  # it neither counts nor stops the issue, and the count real failures left stays as it was.
  if [ "${3:-}" = cap ]; then c=0; else c=$(( $(cat "$cf" 2>/dev/null || echo 0) + 1 )); echo "$c" > "$cf"; fi
  if [ "$c" -ge 1 ] && [ "$c" -ge "${SG_REQUEUE_CAP:-3}" ]; then
    freeze_owner "$n"
    f=$(sg_cq_file "$n-wavestop$$.md" 2>/dev/null) && printf 'SQUAD-NOT-STARTABLE: requeued %s times without a child that entered its wave; the loop stops building it. Last: %s\n' "$c" "$why" > "$f"
    echo "WAVE STOP #$n: requeued $c times, so it is not dispatched again: $why"; return 0
  fi
  rm -f "$SG_STATE/auto/disp-$n" "$SG_STATE/auto/bug-$n"
  board_write "${SG_TODO:-Todo}" "$n"
  f=$(sg_cq_file "$n-wave$$.md" 2>/dev/null) && printf '%s\n' "$why" > "$f"
  echo "WAVE REQUEUE #$n: $why"
}

enter() {   # <run> <issue>: from the child's worktree, the gates, the rebase on the wave tip, and the --ff-only entry.
  local run=$1 iss=$2 w br sha files out; w=$(wave_of "$run")
  { [ -n "$run" ] && [ -n "$iss" ]; } || { echo "WAVE-ERROR wave.sh enter <run> <issue>"; return 2; }
  br=$($G symbolic-ref --quiet --short HEAD) || { echo "WAVE-ERROR $PWD is on no branch (#$iss)"; return 2; }
  [ -n "${SG_WAVE_GATES:-}" ] || { echo "WAVE-ERROR no gates are set for this repository (SG_WAVE_GATES): a child never enters a wave ungated (#$iss)"; return 2; }
  local rk="$SG_STATE/review-$(printf '%s' "$br" | tr / _)" rsha
  rsha=$($G rev-parse HEAD)
  # A wave past its age cap takes no new child (#378, ADR-0145 rule 13): the child's issue goes back to the queue, so the
  # loop builds it into a later wave, and that requeue is not counted (#594). A child whose review already approved its
  # current head against this wave still enters (#594): its wait was the loop's queue of reviews, not its own work. Any
  # other child -- no review yet, one in flight, one of another head or base, or one that did not approve -- is refused
  # before the gates: the wave is closing.
  if expired "$run" && ! { [ "$(cat "$rk.base" 2>/dev/null)" = "$w" ] && [ "$(cat "$rk.sha" 2>/dev/null)" = "$rsha" ] \
      && head -1 "$rk.md" 2>/dev/null | /usr/bin/grep -q 'VERDICT: APPROVE'; }; then
    requeue "$iss" "the wave reached its age cap (${SG_WAVE_MAX_AGE_SECONDS:-7200} s): the child's issue goes back to the queue" cap; return 1
  fi
  # The review of the child reads only its own commits, against its merge-base with the wave branch (#376), and a verdict that
  # did not approve it keeps it out of the wave. The gate reads three things: the verdict, the base it was read against (the
  # wave branch, never `dev`, which would be the whole wave), and the sha it read. The sha is read before the rebase below:
  # the entry rewrites the child's shas while its own commits stay the same, so what was approved is what enters, rebased.
  # A child that cannot enter is held where it stands -- its branch and its review stay for the fix round -- so nothing is
  # requeued here, unlike a failed gate, a conflict or a refused push.
  [ -s "$rk.md" ] || { echo "WAVE HOLD #$iss: no review of $br yet: a child never enters a wave unreviewed"; return 1; }
  [ "$(cat "$rk.base" 2>/dev/null)" = "$w" ] || { echo "WAVE HOLD #$iss: the review of $br was read against '$(cat "$rk.base" 2>/dev/null)', not $w: it read the whole wave, not this child's own commits"; return 1; }
  [ "$(cat "$rk.sha" 2>/dev/null)" = "$rsha" ] || { echo "WAVE HOLD #$iss: the review of $br read $(cut -c1-9 "$rk.sha" 2>/dev/null), not HEAD ${rsha:0:9}"; return 1; }
  head -1 "$rk.md" | /usr/bin/grep -q 'VERDICT: APPROVE' || { echo "WAVE HOLD #$iss: the review of $br did not approve it: $(head -1 "$rk.md")"; return 1; }
  # The child is rebased on the wave tip first, and the gates read that tree: what they pass is what enters (#514). A child
  # cut from a base behind the wave (a stale main, #469) was gated on that base and failed a check the wave tip passes.
  # A push that is not a fast-forward is refused by git itself. The wave tip can move between the rebase and the push (a
  # sibling entered first): the push is then refused, and one repeat from the fetch is what a rejected push gets, as the
  # landing pass does (ADR-0145 rule 2).
  rebase_on_tip() {
    $G fetch -q origin "+refs/heads/$w:refs/remotes/origin/$w" 2>/dev/null || {
      echo "WAVE-ERROR $w is not on $remote (the run cuts it from $base at its start): #$iss enters no wave"; return 2; }
    if ! $G rebase "origin/$w" >/dev/null 2>&1; then
      files=$($G diff --name-only --diff-filter=U | tr '\n' ' '); $G rebase --abort >/dev/null 2>&1
      requeue "$iss" "the rebase on $w conflicts: ${files% }"; return 1
    fi
  }
  rebase_on_tip || return $?
  # The gates run in the child's own worktree, and only they open the entry (ADR-0145 rule 9). A child that fails them never
  # enters, and the wave CI is not what finds it: that one runs once, when the whole wave lands in `dev`.
  # They run under the machine-wide gate lock (#392), in try mode (#575): the entry runs inline in the fast lane, and a wait
  # there stops every dispatch, so a lock held by another gate holds the child out until a later pass, with no requeue.
  out=$(SERIAL_TRY=1 SG_GATE_KIND=fast bash "$here/gate.sh" bash -c "$SG_WAVE_GATES" 2>&1); grc=$?
  if [ $grc = 73 ] && h=$(held_by "$out"); then echo "WAVE HOLD #$iss: the gate lock is held by pid $h; the child enters on a later pass"; return 1; fi
  [ $grc = 0 ] || {
    requeue "$iss" "the local gates failed: $(printf '%s' "$out" | /usr/bin/grep -v '^$' | tail -1)"; return 1; }
  local try=1
  while :; do
    sha=$($G rev-parse HEAD)
    if $G push -q origin "HEAD:refs/heads/$w" 2>/dev/null; then
      board_write "Code Complete" "$iss"   # a refused write is named, never dropped (#593)
      : > "$SG_STATE/auto/entered-$run-$iss"   # the durable record of the entry (#467): the janitor's Code Complete reads it, never the run's own land
      echo "WAVE $br $sha entered $w (#$iss)"; return 0
    fi
    [ "$try" = 1 ] || break
    try=2; rebase_on_tip || return $?
  done
  requeue "$iss" "the push to $w was refused: HEAD is not a fast-forward of its tip"; return 1
}

# A red check at the wave tip names the child that broke it (#377, ADR-0145 rule 11). The wave's check is its CI in
# production, which no run starts while the account's Actions billing is blocked (#439); the local check at the tip is the
# same rule and the one that runs today. `SG_WAVE_TIP_GATES` is that check, run in the wave's throwaway clone; the default is
# the fast gate over the one base battery (base-battery.sh, then the shell suites of what the wave changes) with the
# checkout's python, as branchland.sh runs it, the changed scripts read against origin/$SG_BASE. An empty value turns the
# local check off and leaves the wave's CI as its only check.
py=${SG_GATE_PY:-$SG_CHECKOUT/.venv/bin/python}; [ -x "$py" ] || py=python3
. "$here/base-battery.sh"
tipgate=${SG_WAVE_TIP_GATES-"$(sg_base_gate "origin/$base" "$py")"}

# child_of <clone> <sha>: the issue a commit names, its last `Refs #N`, empty when it names none.
child_of() { $G -C "$1" log -1 --format=%B "$2" 2>/dev/null | /usr/bin/grep -oE 'Refs #[0-9]+' | /usr/bin/grep -oE '[0-9]+' | tail -1; }

# held_by <output>: the pid named by serial.sh's try-mode skip line, and exit 0, when the output carries one (#575).
held_by() { printf '%s\n' "$1" | sed -n 's/^serial: lock gate held by pid \([0-9?]*\): skipped, not queued.*/\1/p' | head -1 | /usr/bin/grep .; }

# child_gate <clone> <from> <to> <issue>: one child's own suite against the wave tip, run in the clone. `SG_WAVE_CHILD_GATES`
# replaces it (with SG_WAVE_CHILD the issue, SG_WAVE_CHILD_FROM/_TO the child's commit range); the default is the shell suite
# test-<script>.sh of every script the child's commit changes, as fast-gate.sh reads a changed script. Exit 0: it passed.
child_gate() {
  local S=$1 from=$2 to=$3 iss=$4 n s hm rc=0
  if [ -n "${SG_WAVE_CHILD_GATES:-}" ]; then (cd "$S" && SG_WAVE_CHILD=$iss SG_WAVE_CHILD_FROM=$from SG_WAVE_CHILD_TO=$to bash -c "$SG_WAVE_CHILD_GATES"); return $?; fi
  for n in $($G -C "$S" diff --name-only --diff-filter=d "$from" "$to" 2>/dev/null | sed -n 's|^contrib/squad-goal/scripts/\(.*\)\.sh$|\1|p' | /usr/bin/grep -v / | sed 's|^test-||' | sort -u); do
    s="$S/contrib/squad-goal/scripts/test-$n.sh"; [ -f "$s" ] || continue
    hm=$(mktemp -d); (cd "$S" && env $(env | sed -n 's/^\(SG_[A-Za-z0-9_]*\)=.*/-u \1/p' | tr '\n' ' ') HOME="$hm" bash "$s") || rc=1; rm -rf "$hm"
  done
  return $rc
}

# wave_check <clone> <w>: the wave tip's check, and, when it is red, the child that broke it. Each child's own suite runs
# again against the tip, the last entrant first; the first child whose suite fails leaves the wave -- its commits are dropped
# from the tip and its issue goes back to the queue -- and the check runs again, so the wave without it can land. Exit 0:
# the tip's check is green (after any child left); 1: it stayed red, so the landing fails as before.
wave_check() {
  local S=$1 w=$2 rc found cx clog
  [ -n "$tipgate" ] || return 0
  while :; do
    clog="$SG_STATE/wave-check-$(printf '%s' "$w" | tr / _).log"   # the check's own output, kept: a red that names no cause has to name its log (#447)
    (cd "$S" && export SERIAL_TRY=1 && eval "$tipgate") >"$clog" 2>&1; rc=$?   # try mode (#575): the landing runs inline in the watch cycle
    [ $rc = 0 ] && { rm -f "$clog"; return 0; }
    [ $rc = 73 ] && held_by "$(cat "$clog")" >/dev/null && { rm -f "$clog"; return 3; }   # 3: the gate lock is held, so the check did not run
    found=""; while read -r sha; do
      [ -n "$sha" ] || continue
      child_gate "$S" "$sha^" "$sha" "$(child_of "$S" "$sha")" || { found=$sha; break; }
    done < <($G -C "$S" log --format=%H "origin/$base..HEAD" 2>/dev/null)   # newest first: the last entrant is tried first
    [ -n "$found" ] || { echo "WAVE CHECK RED $w: the wave tip's check is red and no child's own suite names the cause (the check's own output is $clog)"; return 1; }
    cx=$(child_of "$S" "$found")
    $G -C "$S" rebase --onto "$found^" "$found" >/dev/null 2>&1 || { $G -C "$S" rebase --abort >/dev/null 2>&1
      echo "WAVE CHECK RED $w: #$cx broke the wave tip and cannot leave it (a rebase conflict): the wave does not land"; return 1; }
    requeue "$cx" "the check at the wave tip was red: #$cx broke it, so the child left the wave"
    echo "WAVE CHECK RED $w: #$cx broke the wave tip; it leaves the wave and its issue is back in the queue"
  done
}

# refs_of <clone> <range>: every `Refs #N` in the whole message of each commit of the range, each named once.
refs_of() { $G -C "$1" log --format=%B "$2" 2>/dev/null | /usr/bin/grep -oE 'Refs #[0-9]+' | /usr/bin/grep -oE '[0-9]+' | sort -un | tr '\n' ' '; }

# stuck <run> <wave> <clone> <why>: a landing that failed on a cause a repeat does not change. The attempts are counted in
# auto/wland-tries-<run>; at SG_WAVE_LAND_TRIES (default 3) the pass stops retrying: each issue the wave carries gets the label
# frozen:owner and one queued comment, and the run's wave marker goes, so the loop's cycle reads the run and stops. The wave
# branch stays on the remote with the work, when it is there. Returns 1 either way: the landing did not happen.
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
  rm -f "$SG_STATE/auto/wave-$run" "$SG_STATE/auto/wave-start-$run" "$f"
  echo "WAVE NEEDS-HUMAN $w: the landing failed $t times; the run's wave marker is cleared and $w stays on the remote"
  return 1
}

# deliver <run> <wave> <sha> <issues>: the closes of what a landed wave carries, each with its comment, ledger row and card.
# An issue that does not close stays owed (#475): its number is kept in $SG_STATE/auto/wowed-<run>, the run keeps its wave
# marker, and the pass exits 1, so the run is not read as done and the next landing pass tries the close again.
deliver() {
  local run=$1 w=$2 sha=$3 issues=$4 n owed=""
  export SG_GH_PASS=wave
  local col=Done; [ "${SG_BOARD_VOCAB:-}" = shipped ] && col=Closed
  for n in $issues; do
    local closed= tries=1
    while :; do
      gh_send -X PATCH "repos/$SG_REPO/issues/$n" -f state=closed -f state_reason=completed >/dev/null && { closed=1; break; }
      [ "$tries" = 1 ] || break
      tries=2
    done
    [ -n "$closed" ] || { echo "WAVE-ERROR #$n did not close after the landing of $w: no delivery comment is queued and its card keeps its column"; owed="${owed:+$owed }$n"; continue; }
    mkdir -p "$SG_STATE/cq"
    printf 'Closed #%s after landing %s in %s as %s (one fast-forward push, %s deleted). Refs #%s\n' "$n" "$w" "$base" "$sha" "$w" "$n" > "$(sg_cq_delivery "$n" "wave$$")"
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$w" "$sha" "$n" >> "$SG_STATE/landed.tsv"   # the ledger the hub's Landed branches lane reads
    "$BOARD" "$SG_OWNER" "$SG_PROJECT" "$col" "$n" 2>&1 >/dev/null | grep -v '^issue_state\.py: items read ' | sed "s/^/WAVE-BOARD #$n $col refused: /"   # the seam's read note is no refusal (#526)
  done
  if [ -n "$owed" ]; then
    printf '%s\t%s\t%s\n' "$sha" "$w" "$owed" > "$SG_STATE/auto/wowed-$run"
    echo "WAVE-ERROR $w landed in $base, but ${owed// /, } stays owed: the landing exits 1 and the next pass closes it"
    return 1
  fi
  rm -f "$SG_STATE/auto/wowed-$run" "$SG_STATE/auto/wave-$run"
  return 0
}

land() {   # <run> [folder]: the wave's landing pass -- the rebase on the base, the one push, and the closes of what it carries.
  local run=$1 w; w=$(wave_of "$run")
  [ -n "$run" ] || { echo "WAVE-ERROR wave.sh land wants the run's name"; return 2; }
  # The run's own folder, as active-runs.txt records it: an absolute path (a loop run lives under its issue worktree) or a bare
  # name under the checkout's runs/. The caller passes it; without one the name is read as a bare name.
  local rd=${2:-$run}; case "$rd" in /*) ;; *) rd="runs/${rd##*/}";; esac
  # A run with no record is named, never read as a run with no issues (#474).
  [ -f "$rd/record.jsonl" ] || echo "WAVE-NOTE the record of run $run is missing: $rd/record.jsonl is not there, so no issue of the run is read from it" >&2
  # The age cap is read before the landing clears the markers it is read from (#378): a wave that reached it requeues the
  # children of the run that never entered it, once the wave it holds is in the base.
  # An issue an earlier landing could not close is owed (#475): the wave is already in the base, so this pass only closes it.
  if [ -f "$SG_STATE/auto/wowed-$run" ]; then
    local osha ow oiss; IFS=$'\t' read -r osha ow oiss < "$SG_STATE/auto/wowed-$run"
    export SG_GH_PASS=wave
    deliver "$run" "$ow" "$osha" "$oiss" || return 1
    echo "WAVE $ow $osha delivered in $base (owed: ${oiss// /, })"; return 0
  fi
  local capped=0; expired "$run" && capped=1
  local S; S=$(mktemp -d) || { echo "WAVE-ERROR no scratch dir"; return 2; }
  sc() { $G -C "$S" "$@"; }
  sc init -q . || { echo "WAVE-ERROR cannot make a scratch clone"; rm -rf "$S"; return 2; }
  sc remote add origin "$remote" 2>/dev/null
  [ -n "$(sc config user.name)" ] || sc config user.name "${SG_LAND_NAME:-squad-goal}"
  [ -n "$(sc config user.email)" ] || sc config user.email "${SG_LAND_EMAIL:-squad-goal@localhost}"
  # One fetch per ref, as `start` does: a refspec that names a missing ref fails the whole call, and the wave branch is the one
  # that may be gone (a wave already landed).
  # A landing that cannot start -- the base or the wave branch is gone, or the checkout fails -- exits 2, and it is a landing
  # that failed: it counts toward the same cap as a conflict (#477), so its marker never holds the run's trees for good.
  sc fetch -q origin "+refs/heads/$base:refs/remotes/origin/$base" 2>/dev/null || {
    stuck "$run" "$w" "$S" "$base is not on $remote"; rm -rf "$S"; return 2; }
  sc fetch -q origin "+refs/heads/$w:refs/remotes/origin/$w" 2>/dev/null || {
    stuck "$run" "$w" "$S" "$w is not on $remote (the run cuts it from $base at its start)"; rm -rf "$S"; return 2; }
  sc checkout -q -B land "origin/$w" 2>/dev/null || { stuck "$run" "$w" "$S" "the checkout of $w failed"; rm -rf "$S"; return 2; }
  # The rebase on the base happens here and nowhere else (ADR-0145 rule 14): between entries the tip moves only by a child that
  # entered, so a base that moved under the wave is rebased onto now, before the one push. A push the remote refuses -- the
  # base moved once more between this rebase and the push -- repeats from the fetch: the moved base is fetched, the wave is
  # rebased on it, and the push goes again (ADR-0145 rule 2). One repeat, as the entry does.
  local out sha try=1 issues n
  while :; do
    out=$(sc rebase "origin/$base" 2>&1) || { sc rebase --abort >/dev/null 2>&1
      stuck "$run" "$w" "$S" "the rebase of $w on $base conflicts: $(printf '%s' "$out" | tail -1)"; rm -rf "$S"; return 1; }
    # Nothing ahead of the base is a wave that landed empty (a run with no dispatch, every child failed its gates, no child):
    # there is nothing to push or close, so the wave ref goes, the marker goes, and the run is done, exit 0. It is read
    # BEFORE the tip's check: an empty wave has no child commit to blame for a red check, so that check can only fail the
    # landing of a wave that carries nothing (#449: it ran anyway, and four runs went needs-human on it).
    [ "$(sc rev-list --count "origin/$base..HEAD")" -gt 0 ] || {
      # The run's work may have reached the base outside this wave (#533): the run's children merged into its target and
      # their trees are gone, so no entry exists. The issues the run was dispatched for are named, and each one whose
      # non-RED `Refs #N` commit the base holds (an ancestor of origin/$base by construction) is delivered. A RED commit
      # carries the same line and proves nothing, so it never counts.
      local dispatched="" delivered=""
      for n in $(run_issues "$rd"); do
        dispatched="${dispatched:+$dispatched, }#$n"
        sc log --format=%B%x1e -E --grep="Refs #$n([^0-9]|\$)" "origin/$base" 2>/dev/null \
          | awk -v RS='\036' '/Refs #/ && $0 !~ /TDD RED/ {f=1} END {exit !f}' && delivered="${delivered:+$delivered }$n"
      done
      sc push -q origin ":refs/heads/$w" >/dev/null 2>&1 || echo "WAVE-NOTE branch $w was not deleted" >&2
      sha=$(sc rev-parse "origin/$base"); rm -rf "$S"; rm -f "$SG_STATE/auto/wave-start-$run" "$SG_STATE/auto/wland-tries-$run"
      echo "WAVE $w landed empty in $base (nothing ahead of it; the wave branch is deleted${dispatched:+; the run was dispatched for $dispatched})"
      # An empty wave past its age cap follows the capped landing's rule too (#591): every issue of the run that `dev` does
      # not hold goes back to the queue, so a child that never entered is built into a later wave instead of staying in Doing.
      # An issue whose non-RED commit the base holds is delivered below, never requeued.
      if [ "$capped" = 1 ]; then
        for n in $(run_issues "$rd"); do case " $delivered " in *" $n "*) ;; *)
          requeue "$n" "the wave reached its age cap (${SG_WAVE_MAX_AGE_SECONDS:-7200} s) and landed empty without this child: it is back in the queue" cap;; esac; done
      fi
      if [ -n "$delivered" ]; then deliver "$run" "$w" "$sha" "$delivered" || return 1; else rm -f "$SG_STATE/auto/wave-$run"; fi
      return 0; }
    # The wave tip's check (#377, ADR-0145 rule 11): a red tip names the child that broke it, and that child leaves the wave
    # before it lands. A tip that stays red with no child named is a landing that failed, as any other cause is.
    wave_check "$S" "$w"; rc=$?
    [ $rc = 3 ] && { echo "WAVE WAIT $w: the gate lock is held by another gate, so the tip's check did not run; the wave lands on a later pass"; rm -rf "$S"; return 1; }
    [ $rc = 0 ] || { stuck "$run" "$w" "$S" "the check at the tip of $w is red and no child's own suite names the cause"; rm -rf "$S"; return 1; }
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
      stuck "$run" "$w" "$S" "$base is not on $remote"; rm -rf "$S"; return 2; }
  done
  sc push -q origin ":refs/heads/$w" >/dev/null 2>&1 || echo "WAVE-NOTE branch $w was not deleted" >&2
  rm -rf "$S"
  rm -f "$SG_STATE/auto/wave-start-$run" "$SG_STATE/auto/wland-tries-$run"   # the wave is in $base; wave-$run goes once every issue is delivered (deliver)
  # A wave that reached its age cap landed as it is (#378, ADR-0145 rule 13): the children the run dispatched that never
  # entered it go back to the queue, so the loop builds them into a later wave. They are read from the run's own record
  # (run_issues); an issue the wave carried just closed above, and is never requeued.
  if [ "$capped" = 1 ]; then
    for n in $(run_issues "$rd"); do case " $issues " in *" $n "*) ;; *)
      requeue "$n" "the wave reached its age cap (${SG_WAVE_MAX_AGE_SECONDS:-7200} s) and landed without this child: it is back in the queue" cap;; esac; done
  fi
  # The wave is in $base: each issue it carries closes through the API, and its one delivery comment is queued, never posted
  # (cqflush.sh keeps one per issue, edited in place, #328). Each card reads the delivered column, which is what the landing
  # means: Closed on the shipped vocabulary, which has no Done (#468), Done otherwise. A refused write is named, never dropped. Only a
  # close the API accepted queues the comment, writes the ledger row and moves the card: a close that fails leaves the issue
  # open, and every one of those three would claim a delivery that did not happen. The close repeats once, as the push does.
  deliver "$run" "$w" "$sha" "$issues" || return 1
  local named=""; for n in $issues; do named="${named:+$named, }#$n"; done
  echo "WAVE $w $sha landed in $base (${named:-no issue named: the wave carries no Refs})"
  return 0
}

case "${1:-}" in
  start) shift; start "$@";;
  enter) shift; enter "$@";;
  land) shift; land "$@";;
  expired) shift; expired "$@";;
  requeue) shift; requeue "$@";;
  *) echo "usage: wave.sh start <run> | wave.sh enter <run> <issue> | wave.sh land <run> | wave.sh expired <run> | wave.sh requeue <issue> <why> [cap]" >&2; exit 2;;
esac
