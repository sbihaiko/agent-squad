#!/bin/bash
# usage: wave.sh start <run>          the run cuts its wave branch wave/<run> from origin/$SG_BASE, on the remote
#        wave.sh enter <run> <issue>  from the child's worktree: the local gates, the rebase on the wave tip, the --ff-only entry
#        wave.sh land <run>           when the run ends: the rebase on the moved base, the one push, and the closes
#        wave.sh expired <run>        exit 0 when the run's wave reached its age cap (SG_WAVE_MAX_AGE_SECONDS)
#        wave.sh requeue <issue> <why> [cap]  the child never enters: its issue goes back to the queue and the loop builds it again
#        wave.sh fail <issue> <run> <why>     the run ended with nothing delivered for the issue: one failed dispatch (#609)
#        wave.sh probe-retry <issue>  the janitor sends again a probe's refutation that failed for a transient cause (#888)
#        (a `probe` ticket whose run left its acceptance command unmet refutes its premise instead, in fail and requeue: #888)
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
# A review that asked for CHANGES puts its issue back in the queue (`requeue`) instead of holding it, live run or not
# (childcc.py): a child's branch is pushed nowhere, so nothing behind a hold could fix it.
# A wave with nothing ahead of the base landed empty: its ref and marker go and `land` exits 0. A landing that fails on a rebase
# conflict, a push refused twice, or a start it cannot make (the base or the wave branch is gone, the checkout fails: exit 2)
# is counted per run (SG_WAVE_LAND_TRIES, default 3); at the cap each issue the wave carries
# gets the label frozen:owner and one queued comment, and the run's wave marker is cleared so the cycle stops retrying.
# `waved-<run>` stays after the landing: the durable record that the run cut a wave.
#
# A wave has an age cap (#378, ADR-0145 rule 13): SG_WAVE_MAX_AGE_SECONDS, 7200 s by default, set by a profile (env.sh holds
# the default). `start` records the epoch it cut the wave in `auto/wave-start-<run>`, and the age is read from that marker,
# never from a second clock. The time a child of the wave spends in a review round or a fix entry, in the run or in a childcc
# fix run, pauses the cap (#637): `expired` subtracts it (wavepause.py). A wave past its cap lands what it holds and the children of the run that never entered go back
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
. "$here/basecheck.sh"   # a red base check holds the landing (#786)
G=/usr/bin/git; remote=${SG_LAND_REMOTE:-https://github.com/$SG_REPO.git}; base=${SG_BASE:-dev}
export GIT_TERMINAL_PROMPT=0
wave_of() { case "${1:-}" in wave/*) printf '%s\n' "$1";; *) printf 'wave/%s\n' "$1";; esac; }
# mark <run> <wave>: the run has a wave, and its completion has one to land. $SG_STATE/auto is the loop's marker directory;
# `land` clears the marker when the wave is in $SG_BASE.
# `waved-<run>` is the durable record that the run cut a wave: nothing clears it, so childcc.py still knows, after the landing
# removed `wave-<run>`, that a child of this run never gets the direct Code Complete move.
mark() { mkdir -p "$SG_STATE/auto" && printf '%s\n' "$2" > "$SG_STATE/auto/wave-$1" && printf '%s\n' "$2" > "$SG_STATE/auto/waved-$1" \
  && { [ -s "$SG_STATE/auto/wave-start-$1" ] || date +%s > "$SG_STATE/auto/wave-start-$1"; }; }

# run_dir <run>: the run folder, its line in active-runs.txt, else $SG_CHECKOUT/runs/<run>.
run_dir() { local rd; rd=$(/usr/bin/grep "/$1\$" "$SG_STATE/active-runs.txt" 2>/dev/null | tail -1); printf '%s\n' "${rd:-${SG_CHECKOUT:-.}/runs/$1}"; }

# gate_failed <run>: 0 when the run's verdict failed on its final gate (#694), read as the dashboard reads it: the last verdict
# after the last run_start or resume, `failed`, with a reason that starts `ac-verify:`. `ac-verify:no_ac_commands` ran no
# command, so it is no failed gate (watch.sh reads it as landed). A run with no verdict yet, or no record, is not failed.
gate_failed() {
  python3 -c 'import json,sys
v=None
try:
    for l in open(sys.argv[1]):
        try: d=json.loads(l)
        except Exception: continue
        e=d.get("event") if isinstance(d,dict) else None
        if e in ("run_start","resume"): v=None
        elif e=="verdict": v=d
except OSError: pass
rs=[r for r in (v or {}).get("reasons") or [] if isinstance(r,str)]
sys.exit(0 if v and v.get("verdict")=="failed" and any(r.startswith("ac-verify:") and r!="ac-verify:no_ac_commands" for r in rs) else 1)' "$(run_dir "$1")/record.jsonl" 2>/dev/null
}

# ac_unmet <run>: 0 when the run's verdict failed on ac-verify and every ac-verify finding is `ac_failed` (#888): a command ran
# and its result was red. squad/gates.py also writes `malformed:<cmd>` (prose in a command: a bad check) and
# `child_tree_missing:<cmd>` (the gate found no child tree: the false red of #862). Neither is an answer from a probe, so a run
# with one of them, alone or beside ac_failed, is no refutation: it takes the normal fail or requeue path.
ac_unmet() {
  gate_failed "$1" || return 1
  python3 -c 'import json,sys
v=None
try:
    for l in open(sys.argv[1]):
        try: d=json.loads(l)
        except Exception: continue
        e=d.get("event") if isinstance(d,dict) else None
        if e in ("run_start","resume"): v=None
        elif e=="verdict": v=d
except OSError: pass
fs=[r[len("ac-verify:"):] for r in (v or {}).get("reasons") or [] if isinstance(r,str) and r.startswith("ac-verify:")]
fs=[f for f in fs if f!="no_ac_commands"]
sys.exit(0 if fs and all(f=="ac_failed" or f.startswith("ac_failed:") for f in fs) else 1)' "$(run_dir "$1")/record.jsonl" 2>/dev/null
}

# run_verdict <run>: the run's last verdict name (completed, failed, needs-human, stopped, conflict), read as gate_failed reads it:
# the last verdict after the last run_start or resume. It prints nothing for a run with no verdict yet, or no record (#845).
run_verdict() {
  python3 -c 'import json,sys
v=None
try:
    for l in open(sys.argv[1]):
        try: d=json.loads(l)
        except Exception: continue
        e=d.get("event") if isinstance(d,dict) else None
        if e in ("run_start","resume"): v=None
        elif e=="verdict": v=d
except OSError: pass
print((v or {}).get("verdict") or "")' "$(run_dir "$1")/record.jsonl" 2>/dev/null
}

# expired <run>: 0 when the run's wave reached its age cap (SG_WAVE_MAX_AGE_SECONDS, 7200 s by default, a profile setting).
# The age is the epoch `mark` wrote into the run's wave start marker, never a second clock. A wave with no marker, or one
# that is not cut, is never expired: the cap is a fact about a wave that exists.
# The age does not count the pause (#637, wavepause.py): the time a child of the wave is in a review round or a fix entry,
# read from the run's own record, and the time of each childcc fix run that auto/wave-pause-<run> names. The run folder is
# its line in active-runs.txt, else $SG_CHECKOUT/runs/<run>. A pause that cannot be read is no pause.
expired() {
  local run=$1 t now cap p rd l fx=(); [ -e "$SG_STATE/auto/wave-$run" ] || return 1
  t=$(cat "$SG_STATE/auto/wave-start-$run" 2>/dev/null); case "$t" in ''|*[!0-9]*) return 1;; esac
  now=$(date +%s); cap=${SG_WAVE_MAX_AGE_SECONDS:-7200}
  [ $(( now - t )) -ge "$cap" ] || return 1   # under the cap with no pause: no record is read
  rd=$(run_dir "$run")
  if [ -f "$SG_STATE/auto/wave-pause-$run" ]; then while IFS= read -r l; do [ -n "$l" ] && fx+=("$l"); done < "$SG_STATE/auto/wave-pause-$run"; fi
  p=$(python3 "$here/wavepause.py" "$t" "$now" "$rd" ${fx[@]+"${fx[@]}"} 2>/dev/null); case "$p" in ''|*[!0-9]*) p=0;; esac
  [ $(( now - t - p )) -ge "$cap" ]
}

# run_issues <run-dir>: every issue the run dispatched, each named once: first the request's CHILD headers (request.txt, copied at
# launch: "GitHub issue(s) N, M", as autobug.sh writes them and janitor.sh reads them, #836), then the run's record as childcc.py
# reads it (spawn_accepted and its definition prompt). A run with no CHILD header falls back to its record; a run whose directory
# carries neither names none.
run_issues() {
  local req="$1/request.txt" out
  if [ -s "$req" ]; then
    out=$(python3 -c 'import re,sys
found=set()
for l in open(sys.argv[1], errors="replace"):
    if not l.startswith("CHILD"): continue
    for m in re.finditer(r"GitHub issue(?:\(s\))? #?([0-9][0-9, ]*)", l):
        found.update(x.strip() for x in m.group(1).split(",") if x.strip().isdigit())
for n in sorted(found, key=int): print(n)' "$req" 2>/dev/null)
    [ -n "$out" ] && { printf '%s\n' "$out"; return 0; }
  fi
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

# orphan_child <run> <rd> <S> <wave> <issue>...: the first commit of the run's own work that names one of the issues, lives on a
# branch or a child tree of the run (squad/run/<run>, a tree under the run folder) or of a fix run the run's children started (#866),
# and is in neither the wave nor the base (#836). It prints "<sha> <issue>" and returns 0; it returns 1 when there is none. A leftover
# squad/ branch of another run holds nothing here. A commit a remote ref already reaches (a fix/ branch that salvaged it) is no orphan,
# as `branch -r --contains` reads it from the checkout. A RED commit proves nothing and is no work. Each tip is fetched into the
# scratch clone $S, so the commits are read from that clone, and the wave and the base are the reach that counts.
# (#866) A fix run cuts its own worktree of the child's branch and commits there (childfix.sh), so its reviewed commit lives in that
# run's tree and its squad/<fix run>/ branches. Those folders come from the run's own markers: auto/wave-pause-<run> and auto/ccfix-<run>-*.
# A run whose verdict is not completed holds a commit only when a review read that commit's head (auto/rvd-<run>-<child>): the
# unreviewed work of a failed, needs-human, stopped or verdict-less run never holds its wave (#845), and a reviewed one never goes
# away with it.
orphan_child() {
  local run=$1 rd=$2 S=$3 w=$4 tip c msg n refs rda tips dir dirs reviewed verdict f; shift 4
  verdict=$(run_verdict "$run")
  dirs=$( { printf '%s\n' "$rd"; for f in "$SG_STATE/auto/wave-pause-$run" "$SG_STATE/auto/ccfix-$run"-*; do [ -f "$f" ] && { cat "$f"; echo; }; done; } | sort -u)
  reviewed=$(for f in "$SG_STATE/auto/rvd-$run"-*; do [ -f "$f" ] && awk '{print $1}' "$f"; done | tr '\n' ' ')
  tips=$($G -C "$SG_CHECKOUT" rev-parse -q --verify "refs/heads/squad/run/$run" 2>/dev/null)
  while IFS= read -r dir; do
    [ -n "$dir" ] || continue
    if rda=$(cd "$dir" 2>/dev/null && pwd -P); then
      tips="$tips
$($G -C "$SG_CHECKOUT" for-each-ref --format='%(objectname)' "refs/heads/squad/$(basename "$rda")/" 2>/dev/null)"
      tips="$tips
$($G -C "$SG_CHECKOUT" worktree list --porcelain 2>/dev/null | awk -v d="$rda/" '/^worktree /{w=substr($0,10); if (index(w,d)==1) print w}' | while read -r t; do $G -C "$t" rev-parse HEAD 2>/dev/null; done)"
    fi
  done <<< "$dirs"
  git_sc() { $G -C "$S" "$@"; }
  for tip in $(printf '%s\n' "$tips" | sort -u); do
    [ -n "$tip" ] || continue
    git_sc fetch -q "$SG_CHECKOUT" "$tip" 2>/dev/null || continue
    for c in $(git_sc rev-list "$tip" --not "origin/$base" "origin/$w" 2>/dev/null); do
      msg=$(git_sc log -1 --format=%B "$c" 2>/dev/null)
      case "$msg" in *"TDD RED"*) continue;; esac
      if [ "$verdict" != completed ]; then case " $reviewed " in *" $c "*) ;; *) continue;; esac; fi
      refs=" $(printf '%s\n' "$msg" | /usr/bin/grep -oE 'Refs #[0-9]+' | /usr/bin/grep -oE '[0-9]+' | tr '\n' ' ') "
      for n in "$@"; do case "$refs" in *" $n "*)
          [ -z "$($G -C "$SG_CHECKOUT" branch -r --contains "$c" 2>/dev/null)" ] || continue 2   # a remote ref reaches it: salvaged
          printf '%s %s\n' "$c" "$n"; return 0;; esac; done
    done
  done
  return 1
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

# landed_key <issue>: the newest non-RED `Refs #N` commit in origin/$SG_BASE of the checkout, the evolution key of the count
# (#609, ADR-0134): the work the base holds for the issue. A RED commit carries the same line and proves nothing.
landed_key() {
  $G -C "${SG_CHECKOUT:-/nonexistent}" log -n 500 --format='%H%x1f%B%x1e' -E --grep="Refs #$1([^0-9]|\$)" "origin/$base" 2>/dev/null \
    | awk -v RS='\036' -F'\037' 'NF > 1 && $2 !~ /^\n*TDD RED/ { sub(/^\n/, "", $1); print $1; exit }'
}

# ledger <op> <issue> ...: the issue's one ledger file (#673, issue_ledger.py), keyed <owner>/<repo>#<n> (ADR-0138). It holds
# what auto/requeues-<n> and its .runs held, under a lock per issue and with an atomic write (ADR-0030). A missing file beside
# those old files holds the issue until the owner's `migrate` (ADR-0149 rule 4).
ledger() { local op=$1 n=$2; shift 2; python3 "$here/issue_ledger.py" "$op" "$SG_REPO#$n" "$@"; }

# count <issue> <run> <why> [own|loop]: one failed dispatch of the issue (#609). The count is the issue's (#532), in its
# ledger file, which lists each one: the run, the evolution key, the reason. The run is the key: one run counts once,
# whether a requeue or the janitor's `fail` reports it. Evolution starts the count again at one: a new key (a non-RED commit
# in the base names the issue), or a new run after the freeze, which only the owner lets start. A different reason is no
# evolution: the reasons change while nothing reaches the base. Sets c (the count), fresh (1: this call counted the run)
# and stopped (1: the ledger is stopped). The 4th argument names the ledger: `loop` keeps the loop-caused requeues apart
# (#621). A ledger that does not answer counts nothing, and the line says so.
count() {
  local out; c=0; fresh=0; stopped=0
  out=$(ledger fail "$1" "$2" "$(landed_key "$1")" "$3" "${4:-own}" 2>&1) || { echo "WAVE-LEDGER #$1 refused: $out"; return 0; }
  read -r c fresh stopped <<< "$out"
}

# stop <issue> <why>: the count reached SG_REQUEUE_CAP (3 by default). The card keeps the column it reached with an owner
# freeze (ADR-0029), and one SQUAD-NOT-STARTABLE comment lists every run counted and its reason; both dispatch passes read
# the label and the comment. The owner frees the issue by removing both, or by `issue_ledger.py rearm`.
stop() {
  local n=$1 why=$2 f runs
  freeze_owner "$n"
  runs=$(ledger stop "$n" "$why" own 2>&1)
  f=$(sg_cq_file "$n-wavestop$$.md" 2>/dev/null) && {
    printf 'SQUAD-NOT-STARTABLE: build=%s %s failed dispatches with no change in the work the base holds for it; the loop stops building it (ADR-0134).\n\nRuns:\n' "$(sg_loop_build)" "$c"
    printf '%s\n' "$runs"; } > "$f"
  echo "WAVE STOP #$n: $c failed dispatches with no change, so it is not dispatched again: $why"
}

# loop_caused <why> [own|loop]: the loop caused this requeue, not the issue's work (#621): a child a held gate lock or the
# wave's age cap kept out of the wave (#682; a wave that landed without a child for a cause wave.sh did not record is no loop
# cause, so the janitor's "unknown" counts as the issue's own), an adapter error, a merge conflict with a sibling (the rebase on the wave tip), a disk stop, a review chain that
# wrote no ruling (every model failed, or a hold with no verdict), a red base battery no child's own suite names (#663), a
# run that completed with nothing landed, a prerequisite the loop could not resolve (no profile, a sandbox denial), and a
# network failure. The caller's kind wins over the text: the janitor reads the run's own review verdict (`own`: CHANGES or
# BLOCKED on the diff), which a reason like "review_held" hides.
loop_caused() {
  case "${2:-}" in own) return 1;; loop) return 0;; esac
  case "$1" in *"the gate lock is held"*|*"the gate slots"*"are full"*|*"reached its age cap"*|*adapter_error*|*merge_conflict*|*"the rebase on "*" conflicts"*|*"DISK STOP"*|*"No space left"*|\
    *"every review model failed"*|*"head moved during the review"*|*"with no verdict"*|\
    *"no child's own suite names the cause"*|*"is inconclusive, so the wave does not land"*|*"completed"*"nothing landed"*|*"no profile"*|*"sandbox"*"denied"*|\
    *"Could not resolve host"*|*"Failed to connect"*|*"Connection timed out"*|*"Operation timed out"*|*"Network is unreachable"*) return 0;; esac
  return 1
}

# loop_stop <issue> <why>: SG_LOOP_REQUEUE_CAP (6) loop-caused requeues of one issue (#621). A child held for good is not
# redispatched forever: the issue ends needs-human (ADR-0029). It takes needs-owner, which no dispatch pass takes
# (SG_FRONTIER_SKIP_LABELS), with one comment that names the loop cause. No card write here (#630): Blocked is no column of
# the shipped board, it is the owner freeze over the column the work reached (board_columns.py), so the card keeps its
# column and janitor rule R turns needs-owner into frozen:owner (Blocked on the old vocabulary). The owner frees it by
# removing the labels.
loop_stop() {
  local n=$1 why=$2 f out runs
  out=$(gh_send -X POST "repos/$SG_REPO/issues/$n/labels" -f "labels[]=needs-owner" 2>&1) || printf 'WAVE-LABEL #%s needs-owner refused: %s\n' "$n" "$out"
  runs=$(ledger stop "$n" "$why" loop 2>&1)
  f=$(sg_cq_file "$n-loopstop$$.md" 2>/dev/null) && {
    printf 'SQUAD-NEEDS-HUMAN: %s requeues the loop itself caused, none a failure of this issue'"'"'s work; the loop stops building it (#621). Last cause: %s\n\nRuns:\n' "$c" "$(printf '%s' "$why" | head -1)"
    printf '%s\n' "$runs"; } > "$f"
  echo "WAVE NEEDS-HUMAN #$n: $c loop-caused requeues, so it is not dispatched again: $why"
}

# wave_fact <run> <issue> <state> <cause> [keep]: one wave event of the issue (ADR-0149) -- entered, held, refused, stuck or
# delivered -- written with its cause into the issue's ledger through its one transition function (issue_ledger.py wave). The
# janitor reads the cause there when the wave lands without the child (#682), and names "unknown" when there is none. A later
# event of the run replaces the fact; `keep` leaves a fact the run already has; a duplicate writes nothing; a delivered run is
# terminal; a run from before a rearm counts nothing. A ledger that refuses the event (a corrupt file holds the issue) is named.
wave_fact() {
  local out
  [ -n "$1" ] && [ -n "$2" ] || return 0
  out=$(ledger wave "$2" "$1" "$3" "$4" ${5:+"$5"} 2>&1) || echo "WAVE-LEDGER #$2 refused: $out"
  return 0
}

# held_out <issue>: why the issue must stay where it is, and exit 0, when its ledger stopped it (own or loop), its ledger is
# corrupt or missing beside its old counters (held, ADR-0149), or it carries frozen:owner or needs-owner (#682). A label read that does not answer holds it too:
# a freeze is never lifted on a guess.
held_out() {
  local why labels
  why=$(ledger read "$1" state 2>/dev/null)
  case "$why" in frozen|needs-human) printf 'its ledger is stopped (%s)\n' "$why"; return 0;;
    held) printf '%s\n' "$(ledger read "$1" held 2>/dev/null)"; return 0;; esac
  labels=$(gh_api "repos/$SG_REPO/issues/$1" -q '[.labels[]?.name] | map(select(. == "frozen:owner" or . == "needs-owner")) | join(" ")' 2>/dev/null) \
    || { echo "its labels could not be read"; return 0; }
  [ -n "$labels" ] && { printf 'it carries %s\n' "$labels"; return 0; }
  return 1
}

# premise_check <args>: `squad premise-check <args>` from the plugin's checkout (SG_SQUAD), on the plugin's interpreter, never
# the host's gate interpreter (SG_GATE_PY): a host venv may be an older Python without squad's dependencies (#888).
# SG_PREMISE_CHECK names a stand-in.
premise_check() {
  if [ -n "${SG_PREMISE_CHECK:-}" ]; then "$SG_PREMISE_CHECK" "$@"; return; fi
  local py=${SG_SQUAD:+$SG_SQUAD/.venv/bin/python}; [ -n "$py" ] && [ -x "$py" ] || py=python3
  PYTHONPATH="${SG_SQUAD:-.}${PYTHONPATH:+:$PYTHONPATH}" "$py" -m squad.cli premise-check "$@"
}

# probe_refuted <issue> <run>: 0 when the issue is a probe ticket whose run left its acceptance command unmet (#888, ac_unmet). Its
# premise is refuted on its spec (`squad premise-check <spec> --refute <premise>`: the spec is the ticket's `## Parent`, the
# premise its `Premise:` line), and the probe stays open, frozen to the owner, so the blocker guard holds its dependents. It
# is not requeued and not counted: a refutation is the probe's answer, not a failed dispatch. premise-check exits 75 when gh
# or I/O failed for a cause a later call may not repeat (a 5xx, a network failure, a timeout, a rate limit; a 4xx exits 2): the line then carries `WAVE PROBE-RETRY`, which the janitor reads to leave the run uncounted, so its next
# pass calls `fail` again and the refutation is sent again (premise-check answers a premise it already records refuted as a
# no-op). The failure also leaves auto/probe-retry-<issue> (spec, premise, run): rule O never reads a child that entered its
# wave, so a `requeue` failure is sent again by the janitor's probe sweep (`probe-retry`) instead. Any other refusal (the premise was never recorded as needs a probe) cannot change, so it is named to the owner on
# the probe, as `WAVE PROBE-REFUSED`, and never retried (ADR-0134). The probe is not frozen twice. Any other ticket, a run
# that met its gate, or an issue read that does not answer returns 1.
probe_refuted() {
  local n=$1 run=$2 j t state spec premise
  { [ -n "$run" ] && ac_unmet "$run"; } || return 1
  j=$(gh_api "repos/$SG_REPO/issues/$n") || return 1
  t=$(printf '%s' "$j" | python3 -c 'import json,re,sys
d=json.load(sys.stdin); d=d[0] if isinstance(d,list) and d else d
names=[l.get("name") if isinstance(l,dict) else l for l in (d.get("labels") or [])]
if "probe" not in names: sys.exit(1)
body=d.get("body") or ""
p=re.search(r"^## Parent\s*\n+\s*#(\d+)", body, re.M); q=re.search(r"^Premise:[ \t]*(\S.*?)\s*$", body, re.M)
print("frozen" if "frozen:owner" in names else "open"); print(p.group(1) if p else ""); print(q.group(1) if q else "")' 2>/dev/null) || return 1
  { read -r state; read -r spec; read -r premise; } <<< "$t"
  if [ -z "$spec" ] || [ -z "$premise" ]; then
    [ "$state" = frozen ] || freeze_owner "$n"; echo "WAVE PROBE #$n: its acceptance command is unmet, but it names no Premise line or no Parent spec: frozen for the owner (run $run)"; return 0
  fi
  refute "$n" "$spec" "$premise" "$run"
  [ "$state" = frozen ] || freeze_owner "$n"; return 0
}

# refute <issue> <spec> <premise> <run>: one `premise-check <spec> --refute <premise>` and its one line. Exit 75 (gh or I/O)
# leaves auto/probe-retry-<issue>, which the janitor's probe sweep sends again; a refutation recorded, or refused for good,
# drops it. A refusal is named to the owner on the probe once.
refute() {
  local n=$1 spec=$2 premise=$3 run=$4 out rc why f m="$SG_STATE/auto/probe-retry-$1"
  out=$(premise_check "$spec" --refute "$premise" --repo "$SG_REPO" 2>&1); rc=$?
  why=$(printf '%s' "$out" | tail -1)
  if [ "$rc" -eq 75 ]; then
    mkdir -p "$SG_STATE/auto" && printf '%s\n%s\n%s\n' "$spec" "$premise" "$run" > "$m.tmp" && mv "$m.tmp" "$m"
    echo "WAVE PROBE-RETRY #$n: the refutation of '$premise' on spec #$spec failed for a transient cause: $why; frozen for the owner, and the next janitor pass sends it again (run $run)"
    return 0
  fi
  rm -f "$m"
  if [ "$rc" -eq 0 ]; then
    echo "WAVE PROBE #$n: refuted '$premise' on spec #$spec, which goes back to the check; the probe stays open, frozen for the owner (run $run)"
  else
    echo "WAVE PROBE-REFUSED #$n: premise-check refused the refutation of '$premise' on spec #$spec: $why; frozen for the owner, never retried (run $run)"
    f=$(sg_cq_file "$n-probe$$.md" 2>/dev/null) && printf 'This probe ended with its acceptance command unmet (run %s), but `squad premise-check %s --refute` refused its premise "%s": %s\n\nThe same refusal cannot change, so the loop does not retry it. The probe stays open, frozen for the owner, who settles the premise on spec #%s.\n' \
      "$run" "$spec" "$premise" "$why" "$spec" > "$f"
  fi
}

# probe_retry <issue>: the janitor's probe sweep sends a refutation auto/probe-retry-<issue> names again (#888). The janitor
# calls it only for an open probe frozen for the owner; a marker with no spec or premise is dropped.
probe_retry() {
  local n=${1:?probe-retry: an issue is required} spec premise run m
  m="$SG_STATE/auto/probe-retry-$n"; [ -f "$m" ] || return 0
  { read -r spec; read -r premise; read -r run; } < "$m"
  { [ -n "$spec" ] && [ -n "$premise" ]; } || { rm -f "$m"; return 0; }
  refute "$n" "$spec" "$premise" "$run"
}

requeue() {   # <issue> <why> [cap]: the child never entered, so its issue goes back to the queue and the loop can build it again.
  local n=$1 why=$2 f h
  [ "${3:-}" != cap ] && probe_refuted "$n" "${SG_REQUEUE_RUN:-}" && return 0   # a refuting probe is held, never requeued (#888)
  # The run is SG_REQUEUE_RUN (enter and land set it; childcc and runreview pass it). A requeue the wave's age cap made
  # (`cap`, #594) is the loop's delay, not a failure of the child: it neither counts nor stops the issue. The run behind it
  # is counted once at its end, by the janitor's `fail`, when it delivered nothing. A loop-caused one (#621) counts only
  # toward its own, larger bound.
  if [ "${3:-}" != cap ] && loop_caused "$why"; then
    count "$n" "${SG_REQUEUE_RUN:-}" "$why" loop
    if [ "$fresh" = 1 ] && [ "$c" -ge "${SG_LOOP_REQUEUE_CAP:-6}" ]; then loop_stop "$n" "$why"; return 0; fi
    if [ "$fresh" = 0 ] && [ "$stopped" = 1 ]; then
      echo "WAVE NEEDS-HUMAN #$n: already stopped, so it is not dispatched again: $why"; return 0; fi
  elif [ "${3:-}" != cap ]; then
    count "$n" "${SG_REQUEUE_RUN:-}" "$why"
    if [ "$fresh" = 1 ] && [ "$c" -ge "${SG_REQUEUE_CAP:-3}" ]; then stop "$n" "$why"; return 0; fi
    if [ "$fresh" = 0 ] && [ "$stopped" = 1 ]; then
      echo "WAVE STOP #$n: already frozen, so it is not dispatched again: $why"; return 0; fi
  elif h=$(held_out "$n"); then   # a cap requeue counts nothing, so it reads the stop itself (#682): a stopped issue stays put
    echo "WAVE STOP #$n: $h, so the age cap does not put it back in the queue: $why"; return 0
  fi
  rm -f "$SG_STATE/auto/disp-$n" "$SG_STATE/auto/bug-$n"
  board_write "${SG_TODO:-Todo}" "$n"
  f=$(sg_cq_file "$n-wave$$.md" 2>/dev/null) && printf '%s\n' "$why" > "$f"
  echo "WAVE REQUEUE #$n: $why"
}

fail() {   # <issue> <run> <why> [own|loop]: the run ended with nothing delivered for the issue (janitor rule O, #609): one failed dispatch.
  local n=$1 run=$2 why=$3
  { [ -n "$n" ] && [ -n "$run" ]; } || { echo "WAVE-ERROR wave.sh fail <issue> <run> <why>"; return 2; }
  probe_refuted "$n" "$run" && return 0   # a probe whose acceptance command is unmet refutes its premise (#888)
  if loop_caused "$why" "${4:-}"; then   # the loop's failure, not the issue's (#621): its own ledger and bound
    count "$n" "$run" "$why" loop
    [ "$fresh" = 1 ] || { echo "WAVE FAILED #$n: run $run is already counted"; return 0; }
    [ "$c" -ge "${SG_LOOP_REQUEUE_CAP:-6}" ] && { loop_stop "$n" "$why"; return 0; }
    echo "WAVE FAILED #$n: loop-caused, $c of ${SG_LOOP_REQUEUE_CAP:-6} (run $run), not counted toward the cap: $why"; return 0
  fi
  count "$n" "$run" "$why"
  [ "$fresh" = 1 ] || { echo "WAVE FAILED #$n: run $run is already counted"; return 0; }
  [ "$c" -ge "${SG_REQUEUE_CAP:-3}" ] && { stop "$n" "$why"; return 0; }
  echo "WAVE FAILED #$n: $c of ${SG_REQUEUE_CAP:-3} failed dispatches with no change (run $run): $why"
}

enter() {   # <run> <issue>: from the child's worktree, the gates, the rebase on the wave tip, and the --ff-only entry.
  local run=$1 iss=$2 w br sha files out SG_REQUEUE_RUN=$1; w=$(wave_of "$run")
  { [ -n "$run" ] && [ -n "$iss" ]; } || { echo "WAVE-ERROR wave.sh enter <run> <issue>"; return 2; }
  br=$($G symbolic-ref --quiet --short HEAD) || { echo "WAVE-ERROR $PWD is on no branch (#$iss)"; return 2; }
  [ -n "${SG_WAVE_GATES:-}" ] || { echo "WAVE-ERROR no gates are set for this repository (SG_WAVE_GATES): a child never enters a wave ungated (#$iss)"; return 2; }
  local rk="$SG_STATE/review-$(printf '%s' "$br" | tr / _)" rsha
  rsha=$($G rev-parse HEAD)
  # hold <why> and refuse <why> [cap]: the child stays out, held or requeued, and the cause is recorded for the janitor (#682).
  hold() { wave_fact "$run" "$iss" held "$1"; echo "$(date -u '+%F %T') WAVE HOLD #$iss: $1"; return 1; }
  refuse() { wave_fact "$run" "$iss" refused "$1"; requeue "$iss" "$@"; return 1; }
  # A run whose final gate failed takes no child into its wave (#694): what its gate read failed, so the wave would carry a
  # failed tree and conflict on the base. The child stays out, its issue goes back to the queue, and the wave lands empty.
  gate_failed "$run" && { refuse "the run's final gate failed: run $run failed ac-verify, so its child never enters $w"; return 1; }
  # A wave past its age cap takes no new child (#378, ADR-0145 rule 13): the child's issue goes back to the queue, so the
  # loop builds it into a later wave, and that requeue is not counted (#594). A child whose review already approved its
  # current head against this wave still enters (#594): its wait was the loop's queue of reviews, not its own work. Any
  # other child -- no review yet, one in flight, one of another head or base, or one that did not approve -- is refused
  # before the gates: the wave is closing.
  if expired "$run" && ! { [ "$(cat "$rk.base" 2>/dev/null)" = "$w" ] && [ "$(cat "$rk.sha" 2>/dev/null)" = "$rsha" ] \
      && head -1 "$rk.md" 2>/dev/null | /usr/bin/grep -q 'VERDICT: APPROVE'; }; then
    refuse "the wave reached its age cap (${SG_WAVE_MAX_AGE_SECONDS:-7200} s): the child's issue goes back to the queue" cap; return 1
  fi
  # The review of the child reads only its own commits, against its merge-base with the wave branch (#376), and a verdict that
  # did not approve it keeps it out of the wave. The gate reads three things: the verdict, the base it was read against (the
  # wave branch, never `dev`, which would be the whole wave; the diff starts at the newer merge-base with the wave or with
  # `dev`, #655), and the sha it read. The sha is read before the rebase below:
  # the entry rewrites the child's shas while its own commits stay the same, so what was approved is what enters, rebased.
  # A child that cannot enter is held where it stands -- its branch and its review stay for the fix round -- so nothing is
  # requeued here, unlike a failed gate, a conflict or a refused push.
  [ -s "$rk.md" ] || { hold "no review of $br yet: a child never enters a wave unreviewed"; return 1; }
  [ "$(cat "$rk.base" 2>/dev/null)" = "$w" ] || { hold "the review of $br was read against '$(cat "$rk.base" 2>/dev/null)', not $w: it read the whole wave, not this child's own commits"; return 1; }
  [ "$(cat "$rk.sha" 2>/dev/null)" = "$rsha" ] || { hold "the review of $br read $(cut -c1-9 "$rk.sha" 2>/dev/null), not HEAD ${rsha:0:9}"; return 1; }
  head -1 "$rk.md" | /usr/bin/grep -q 'VERDICT: APPROVE' || { hold "the review of $br did not approve it: $(head -1 "$rk.md")"; return 1; }
  # The child is rebased on the wave tip first, and the gates read that tree: what they pass is what enters (#514). A child
  # cut from a base behind the wave (a stale main, #469) was gated on that base and failed a check the wave tip passes.
  # A push that is not a fast-forward is refused by git itself. The wave tip can move between the rebase and the push (a
  # sibling entered first): the push is then refused, and one repeat from the fetch is what a rejected push gets, as the
  # landing pass does (ADR-0145 rule 2).
  rebase_on_tip() {
    $G fetch -q origin "+refs/heads/$w:refs/remotes/origin/$w" 2>/dev/null || {
      echo "WAVE-ERROR $w is not on $remote (the run cuts it from $base at its start): #$iss enters no wave"; return 2; }
    if ! out=$($G rebase "origin/$w" 2>&1); then
      files=$($G diff --name-only --diff-filter=U | tr '\n' ' '); $G rebase --abort >/dev/null 2>&1
      [ -n "$files" ] || files="git said: $(printf '%s\n' "$out" | /usr/bin/grep -v '^[[:space:]]*$' | tail -1)"
      refuse "the rebase on $w conflicts: ${files% }"; return 1
    fi
  }
  # The gates run in the child's own worktree, and only they open the entry (ADR-0145 rule 9). A child that fails them never
  # enters, and the wave CI is not what finds it: that one runs once, when the whole wave lands in `dev`.
  # They run under the machine-wide gate lock (#392), in try mode (#575): the entry runs inline in the fast lane, and a wait
  # there stops every dispatch, so a lock held by another gate holds the child out until a later pass, with no requeue.
  # The landing waits a bounded time instead (#955): a bounded wait in a detached pass never stops dispatch, because dispatch rides fastloop.
  gates() {
    out=$(SERIAL_TRY=1 SG_GATE_KIND=fast bash "$here/gate.sh" bash -c "$SG_WAVE_GATES" 2>&1); grc=$?
    if [ $grc = 73 ] && h=$(held_by "$out"); then
      case "$h" in slots:*) hold "the gate slots (caps.gates) are full, held by ${h#slots:}; the child enters on a later pass";;
        *) hold "the gate lock is held by pid $h; the child enters on a later pass";; esac; return 1; fi
    # Exit 4 is an inconclusive gate (#699): the machine gave no answer, so the child is held, never requeued.
    [ $grc = 4 ] && { hold "the local gates were inconclusive: $(printf '%s' "$out" | /usr/bin/grep -v '^$' | tail -1); the child enters on a later pass"; return 1; }
    [ $grc = 0 ] || { refuse "the local gates failed: $(printf '%s' "$out" | /usr/bin/grep -v '^$' | tail -1)"; return 1; }
  }
  rebase_on_tip || return $?
  gates || return 1
  local try=1
  while :; do
    sha=$($G rev-parse HEAD)
    if $G push -q origin "HEAD:refs/heads/$w" 2>/dev/null; then
      board_write "Code Complete" "$iss"   # a refused write is named, never dropped (#593)
      wave_fact "$run" "$iss" entered "entered $w at $sha"   # the durable record of the entry (#467, ADR-0149): the janitor's Code Complete reads it, never the run's own land
      echo "WAVE $br $sha entered $w (#$iss)"; return 0
    fi
    [ "$try" = 1 ] || break
    try=2; rebase_on_tip || return $?
    gates || return 1   # the rebase made a new tree, and only the gates open the entry (#682): what enters is what they passed
  done
  refuse "the push to $w was refused: HEAD is not a fast-forward of its tip"; return 1
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

# held_by <output>: the pid named by serial.sh's try-mode skip line, and exit 0, when the output carries one (#575); a try-mode skip of
# the gate slots (gate.sh, caps.gates) is a hold as well and prints `slots:` and the holders it names (#567).
held_by() { printf '%s\n' "$1" | sed -n -e 's/^serial: lock gate held by pid \([0-9?]*\): skipped, not queued.*/\1/p' -e 's/^caps: caps\.gates is full ([0-9]* slots), held by \(.*\): .* skipped, not queued (try)$/slots:\1/p' | head -1 | /usr/bin/grep .; }
# waited_out <output>: the bounded wait for the gate lock or the gate slots ran out (serial.sh and caps.py both exit 75 then, and
# name it). A 75 without that line is the check's own hold deadline, which is a failed check, not a hold (#955).
waited_out() { printf '%s\n' "$1" | /usr/bin/grep -qE '^serial: waited [0-9]+s for lock gate |^caps: .* waited [0-9.]+s for caps\.gates'; }

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
# the tip's check is green (after any child left); 1: it stayed red, so the landing fails as before; 3: the gate lock stayed
# held past its bounded wait; 4: the check was inconclusive (#699), so no child is blamed.
wave_check() {
  local S=$1 w=$2 rc found cx clog
  [ -n "$tipgate" ] || return 0
  while :; do
    clog="$SG_STATE/wave-check-$(printf '%s' "$w" | tr / _).log"   # the check's own output, kept: a red that names no cause has to name its log (#447)
    # The check waits for the gate lock a bounded time (SERIAL_WAIT_SECONDS, 900 s): a try that gave up on its first look lost the
    # cycle, and the wave waited a whole cycle for each retry (#946 lost three cycles). The landing runs in its own detached pass
    # (watch.sh), so this wait holds no watch cycle, and dispatch rides fastloop, which never runs a landing (#955).
    (cd "$S" && export SERIAL_WAIT_SECONDS=${SERIAL_WAIT_SECONDS:-900} && eval "$tipgate") >"$clog" 2>&1; rc=$?
    [ $rc = 0 ] && { rm -f "$clog"; return 0; }
    [ $rc = 73 ] && held_by "$(cat "$clog")" >/dev/null && { rm -f "$clog"; return 3; }   # 3: the gate lock is held, so the check did not run
    [ $rc = 75 ] && waited_out "$(cat "$clog")" && { rm -f "$clog"; return 3; }   # the bounded wait ran out: the check did not run either
    [ $rc = 4 ] && return 4   # 4: the check was inconclusive (#699): no child is blamed, and the wave does not land
    found=""; while read -r sha; do
      [ -n "$sha" ] || continue
      child_gate "$S" "$sha^" "$sha" "$(child_of "$S" "$sha")" || { found=$sha; break; }
    done < <($G -C "$S" log --format=%H "origin/$base..HEAD" 2>/dev/null)   # newest first: the last entrant is tried first
    [ -n "$found" ] || { echo "WAVE CHECK RED $w: the wave tip's check is red and no child's own suite names the cause (the check's own output is $clog)"; return 1; }
    cx=$(child_of "$S" "$found")
    $G -C "$S" rebase --onto "$found^" "$found" >/dev/null 2>&1 || { $G -C "$S" rebase --abort >/dev/null 2>&1
      echo "WAVE CHECK RED $w: #$cx broke the wave tip and cannot leave it (a rebase conflict): the wave does not land"; return 1; }
    wave_fact "$run" "$cx" refused "the check at the wave tip was red: #$cx broke it, so the child left the wave"   # land's run (#682)
    requeue "$cx" "the check at the wave tip was red: #$cx broke it, so the child left the wave"
    echo "WAVE CHECK RED $w: #$cx broke the wave tip; it leaves the wave and its issue is back in the queue"
  done
}

# refs_of <clone> <range>: every `Refs #N` in the whole message of each commit of the range, each named once.
refs_of() { $G -C "$1" log --format=%B "$2" 2>/dev/null | /usr/bin/grep -oE 'Refs #[0-9]+' | /usr/bin/grep -oE '[0-9]+' | sort -un | tr '\n' ' '; }

# commit_refs <clone> <range> (#869): one line per non-RED commit of the range that names an issue. `D <sha> <n>`: the commit
# names exactly one issue (each `Refs #N` counted once), so it delivers that issue. `A <sha> <n> ...`: it names several, and a
# cluster child that wrote one commit for its block did not say which issue the commit fixed, so it delivers none of them.
commit_refs() {
  $G -C "$1" log --format='%H%x1f%B%x1e' "$2" 2>/dev/null | awk -v RS='\036' -F'\037' '
    NF > 1 && $2 !~ /^\n*TDD RED/ {
      sha = $1; sub(/^\n+/, "", sha)
      k = 0; split("", seen); s = $2
      while (match(s, /Refs #[0-9]+/)) {
        n = substr(s, RSTART + 6, RLENGTH - 6)
        if (!(n in seen)) { seen[n] = 1; list[++k] = n }
        s = substr(s, RSTART + RLENGTH)
      }
      if (k == 1) print "D " sha " " list[1]
      else if (k > 1) { out = "A " sha; for (i = 1; i <= k; i++) out = out " " list[i]; print out }
    }'
}

# netfail <what> <stderr>: a fetch or a push that failed on the network (DNS, connect, timeout, a 5xx answer, a TLS
# handshake) is no cause a repeat does not change: the next cycle can land (#656). It prints one WAVE-NETWORK line with the
# last line that names the network error and returns 0; the caller then exits 2 with no call to stuck, so no try is counted
# and nothing is frozen. Any other failure returns 1, and the caller calls stuck as before.
WAVE_NET_RE='Could not resolve host|Failed to connect|Connection (timed out|refused|reset)|Operation timed out|Network is unreachable|The requested URL returned error: 5[0-9][0-9]|SSL_|SSL connect|TLS handshake|gnutls_handshake'
netfail() {
  local l; l=$(printf '%s\n' "$2" | /usr/bin/grep -E "$WAVE_NET_RE" | tail -1)
  [ -n "$l" ] || return 1
  echo "WAVE-NETWORK $1: $l; it lands on a later cycle"
}

# stuck <run> <wave> <clone> <why>: a landing that failed on a cause a repeat does not change. The attempts are ledger facts
# (ADR-0149): each issue the wave carries (its `Refs #N` ahead of the base) or the run was dispatched for (`run_issues` of the
# caller's run folder $rd) counts the try in its ledger (`issue_ledger.py landtry`), and the run's count is the largest. At
# SG_WAVE_LAND_TRIES (default 3) the pass stops retrying: each issue the wave carries gets the label frozen:owner, its card
# goes to Todo (#657), one comment is queued, and the run's wave marker goes, so the cycle stops. A wave that names no issue
# has no ledger to count in and holds no issue, so its first failure clears the marker. The wave branch stays on the remote
# with the work, when it is there. A cause the loop itself caused (#663) is no work failure: each issue is named once as
# WAVE-LOOP-CAUSED and counts no try, the wave stays, and the normal cap handles the issue. Returns 1 either way: the
# landing did not happen.
stuck() {
  local run=$1 w=$2 S=$3 why=$4 t=0 k n named="" all
  if loop_caused "$why"; then
    for n in $(refs_of "$S" "origin/$base..origin/$w"); do
      wave_fact "$run" "$n" stuck "$why; loop-caused, not counted"
      echo "WAVE-LOOP-CAUSED #$n: $why; not counted"; named=1
    done
    [ -n "$named" ] || echo "WAVE-LOOP-CAUSED $w: $why; not counted"
    return 1
  fi
  echo "WAVE-ERROR $why"
  # Only the run's dispatch set is acted on (#776), as the close does (#775): a `Refs #N` outside it is reported here and in
  # wave-outside.tsv, and gets no try, label, card move or comment. A run whose record names no issue keeps every `Refs #N`.
  local refs dset kept="" n2; refs=$(refs_of "$S" "origin/$base..origin/$w"); dset=" $(run_issues "${rd:-/nonexistent}" | tr '\n' ' ') "
  if [ -n "${dset// /}" ]; then
    for n2 in $refs; do case "$dset" in *" $n2 "*) kept="${kept:+$kept }$n2";; *)
      echo "WAVE-NOTE #$n2: $w names it (Refs #$n2) outside the dispatch set of run $run; reported, not frozen"
      printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$run" "$n2" "$w" >> "$SG_STATE/wave-outside.tsv";; esac; done
    refs=$kept
  fi
  all=$(printf '%s\n' $refs $dset | sort -un | tr '\n' ' ')
  for n in $all; do
    k=$(ledger landtry "$n" "$run" "$why" 2>&1) || { echo "WAVE-LEDGER #$n refused: $k"; continue; }
    [ "$k" -gt "$t" ] 2>/dev/null && t=$k
  done
  [ -n "${all// /}" ] || { t=${SG_WAVE_LAND_TRIES:-3}; echo "WAVE-NOTE $w names no issue (no Refs ahead of $base, no run record): no ledger counts its tries"; }
  [ "$t" -ge "${SG_WAVE_LAND_TRIES:-3}" ] || return 1
  export SG_GH_PASS=wave
  for n in $refs; do
    gh_send -X POST "repos/$SG_REPO/issues/$n/labels" -f "labels[]=frozen:owner" >/dev/null 2>&1
    board_write "${SG_TODO:-Todo}" "$n"   # nothing landed, so the card leaves Code Complete (#657); frozen:owner holds it out of the frontier
    mkdir -p "$SG_STATE/cq"
    printf 'Needs the owner: the landing of %s in %s failed %s times and stopped (%s). The work stays on %s. Refs #%s\n' "$w" "$base" "$t" "$why" "$w" "$n" > "$(sg_cq_file "$n-wavestuck$$.md")"
  done
  rm -f "$SG_STATE/auto/wave-$run" "$SG_STATE/auto/wave-start-$run" "$SG_STATE/auto/wave-pause-$run"
  if [ -n "${all// /}" ]; then echo "WAVE NEEDS-HUMAN $w: the landing failed $t times; the run's wave marker is cleared and $w stays on the remote"
  else echo "WAVE NEEDS-HUMAN $w: the landing failed and the wave names no issue; the run's wave marker is cleared and $w stays on the remote"; fi
  return 1
}

# deliver <run> <wave> <sha> <issues>: the closes of what a landed wave carries, each with its comment, ledger row and card.
# An issue that does not close stays owed (#475): its ledger fact is `owed`, with the wave and the sha (ADR-0149), the run
# keeps its wave marker, and the pass exits 1, so the run is not read as done and the next landing pass tries the close again.
deliver() {
  local run=$1 w=$2 sha=$3 issues=$4 n owed="" out
  export SG_GH_PASS=wave
  local col=Done; [ "${SG_BOARD_VOCAB:-}" = shipped ] && col=Closed
  for n in $issues; do
    # The landing is the issue's `landed` fact (ADR-0149, #695): rule R reads it instead of the run's land heads. It is
    # written before the close, because the work is in the base whether or not the API accepts the close.
    out=$(ledger landed "$n" "$run" run "$sha" 2>&1) || echo "WAVE-LEDGER #$n refused: $out"
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
    wave_fact "$run" "$n" delivered "landed $w in $base as $sha"   # terminal for the run (ADR-0149)
    "$BOARD" "$SG_OWNER" "$SG_PROJECT" "$col" "$n" 2>&1 >/dev/null | grep -v '^issue_state\.py: items read ' | sed "s/^/WAVE-BOARD #$n $col refused: /"   # the seam's read note is no refusal (#526)
  done
  if [ -n "$owed" ]; then
    for n in $owed; do out=$(ledger owe "$n" "$run" "$w" "$sha" 2>&1) || echo "WAVE-LEDGER #$n refused: $out"; done
    echo "WAVE-ERROR $w landed in $base, but ${owed// /, } stays owed: the landing exits 1 and the next pass closes it"
    return 1
  fi
  rm -f "$SG_STATE/auto/wave-$run"
  return 0
}

# wave_conflict <run> <wave> <run folder> <clone> <rebase output> (#844): a rebase of the wave on the base that conflicts is terminal
# for this wave head. The same rebase on the next cycle cannot succeed, so the landing is not retried: the issues the run was
# dispatched for (the Refs of the wave when the run names none) go back to the queue through requeue, so its loop cap and the
# needs-human path apply as they do to any requeue. The run's wave marker goes, so the loop stops calling the landing. The wave
# branch stays on the remote with its commits: they may exist on no other ref. One line names the commit that conflicts. Returns 1.
wave_conflict() {
  local run=$1 w=$2 rd=$3 S=$4 out=$5 at refs dset list n n2 named="" outs=""
  at=$(printf '%s\n' "$out" | /usr/bin/grep -oE 'Could not apply [0-9a-f]+' | head -1 | awk '{print $4}')
  at=${at:-unknown}
  refs=$(refs_of "$S" "origin/$base..origin/$w")
  dset=$(run_issues "${rd:-/nonexistent}" | tr '\n' ' ')
  # Only the run's dispatch set is acted on (#776, as stuck and the close do): a `Refs #N` outside it is noted and recorded,
  # and gets no requeue, label, card move, comment or try.
  if [ -n "${dset// /}" ]; then list=$dset; for n2 in $refs; do case " $dset " in *" $n2 "*) ;; *) outs="${outs:+$outs }$n2";; esac; done
  else list=$refs; fi
  for n in $list; do named="${named:+$named, }#$n"; done
  echo "WAVE CONFLICT $w: the rebase on $base conflicts at $at; ${named:-no issue} requeued on the current base"
  for n2 in $outs; do
    echo "WAVE-NOTE #$n2: $w names it (Refs #$n2) outside the dispatch set of run $run; reported, not frozen"
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$run" "$n2" "$w" >> "$SG_STATE/wave-outside.tsv"
  done
  for n in $list; do
    wave_fact "$run" "$n" refused "the rebase on $base conflicts at $at"
    requeue "$n" "the rebase on $base conflicts at $at: $w cannot land on the current base"
  done
  rm -f "$SG_STATE/auto/wave-$run" "$SG_STATE/auto/wave-start-$run" "$SG_STATE/auto/wave-pause-$run"
  return 1
}

land() {   # <run> [folder]: the wave's landing pass -- the rebase on the base, the one push, and the closes of what it carries.
  local run=$1 w SG_REQUEUE_RUN=$1; w=$(wave_of "$run")
  [ -n "$run" ] || { echo "WAVE-ERROR wave.sh land wants the run's name"; return 2; }
  # The run's own folder, as active-runs.txt records it: an absolute path (a loop run lives under its issue worktree) or a bare
  # name under the checkout's runs/. The caller passes it; without one the name is read as a bare name.
  local rd=${2:-$run}; case "$rd" in /*) ;; *) rd="runs/${rd##*/}";; esac
  # A run with no record is named, never read as a run with no issues (#474).
  [ -f "$rd/record.jsonl" ] || echo "WAVE-NOTE the record of run $run is missing: $rd/record.jsonl is not there, so no issue of the run is read from it" >&2
  # The age cap is read before the landing clears the markers it is read from (#378): a wave that reached it requeues the
  # children of the run that never entered it, once the wave it holds is in the base.
  # An issue an earlier landing could not close is owed (#475): the wave is already in the base, so this pass only closes it.
  local owed osha ow oiss
  owed=$(python3 "$here/issue_ledger.py" owed "$SG_REPO" "$run" 2>/dev/null)
  if [ -n "$owed" ]; then
    IFS=$'\t' read -r osha ow _ <<< "$owed"; oiss=$(printf '%s\n' "$owed" | cut -f3 | tr '\n' ' '); oiss=${oiss% }
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
  # A fetch that failed on the network is not counted (netfail, #656): it exits 2 and the wave lands on a later cycle.
  local fo
  fo=$(sc fetch -q origin "+refs/heads/$base:refs/remotes/origin/$base" 2>&1) || {
    netfail "the fetch of $base from $remote" "$fo" || stuck "$run" "$w" "$S" "$base is not on $remote"; rm -rf "$S"; return 2; }
  fo=$(sc fetch -q origin "+refs/heads/$w:refs/remotes/origin/$w" 2>&1) || {
    netfail "the fetch of $w from $remote" "$fo" || stuck "$run" "$w" "$S" "$w is not on $remote (the run cuts it from $base at its start)"; rm -rf "$S"; return 2; }
  sc checkout -q -B land "origin/$w" 2>/dev/null || { stuck "$run" "$w" "$S" "the checkout of $w failed"; rm -rf "$S"; return 2; }
  # The rebase on the base happens here and nowhere else (ADR-0145 rule 14): between entries the tip moves only by a child that
  # entered, so a base that moved under the wave is rebased onto now, before the one push. A push the remote refuses -- the
  # base moved once more between this rebase and the push -- repeats from the fetch: the moved base is fetched, the wave is
  # rebased on it, and the push goes again (ADR-0145 rule 2). One repeat, as the entry does.
  local out sha try=1 issues n anyref=0
  while :; do
    out=$(sc rebase "origin/$base" 2>&1) || { sc rebase --abort >/dev/null 2>&1
      wave_conflict "$run" "$w" "$rd" "$S" "$out"; rm -rf "$S"; return 1; }
    # Nothing ahead of the base is a wave that landed empty (a run with no dispatch, every child failed its gates, no child):
    # there is nothing to push or close, so the wave ref goes, the marker goes, and the run is done, exit 0. It is read
    # BEFORE the tip's check: an empty wave has no child commit to blame for a red check, so that check can only fail the
    # landing of a wave that carries nothing (#449: it ran anyway, and four runs went needs-human on it).
    [ "$(sc rev-list --count "origin/$base..HEAD")" -gt 0 ] || {
      # A child's fix run in flight holds the wave (#667): the childcc pass started it because the review asked for changes,
      # and it may still put the child in the wave. Landing empty here would delete the wave branch, clean the child's
      # worktree and send the issue back to the queue while that work runs. The age cap is what frees the wave (the capped
      # branch below), so a marker whose fix run never verdicts cannot hold it for good.
      if [ "$capped" != 1 ]; then for fx in "$SG_STATE/auto/ccfix-$run"-*; do
        [ -e "$fx" ] || continue
        f=$(cat "$fx" 2>/dev/null)
        [ -n "$f" ] && /usr/bin/grep -q '"event": "verdict"' "$f/record.jsonl" 2>/dev/null && continue
        echo "WAVE WAIT $w: the fix run of one of its children has no verdict yet, so it does not land empty"
        rm -rf "$S"; return 1
      done; fi
      # The run's work may have reached the base outside this wave (#533): the run's children merged into its target and
      # their trees are gone, so no entry exists. The issues the run was dispatched for are named, and each one whose
      # non-RED `Refs #N` commit the base holds (an ancestor of origin/$base by construction) is delivered. A RED commit
      # carries the same line and proves nothing, so it never counts.
      # Only a commit that reached the base after the wave was cut is this run's (#822): an issue landed once, then reopened and
      # dispatched again, carries its old `Refs #N` commit in the base, and that commit delivers nothing of this run. The cut is
      # the fork point of the wave branch, read before the rebase moved it.
      local dispatched="" delivered="" ent cut rows
      cut=$(sc merge-base "origin/$w" "origin/$base" 2>/dev/null)
      ent=$(python3 "$here/issue_ledger.py" entered "$SG_REPO" 2>/dev/null | awk -F '\t' -v r="$run" '$1 == r { print $2 }' | tr '\n' ' ')
      # (#869) The commits since the cut deliver by commit_refs: a commit that names several issues delivers none of them.
      rows=$(commit_refs "$S" "${cut:+$cut..}origin/$base")
      printf '%s\n' "$rows" | while read -r k sha ns; do
        [ "$k" = A ] && echo "WAVE-NOTE $w: commit ${sha:0:9} names several issues ($ns) and closes none of them"; done
      for n in $(run_issues "$rd"); do
        dispatched="${dispatched:+$dispatched, }#$n"
        # A child that entered this wave (the entered fact of its ledger, ADR-0149) and still leaves it empty reached the base
        # by another route, whatever its message says: the entry is the proof, so it is delivered, never requeued (#620).
        case " $ent " in *" $n "*) delivered="${delivered:+$delivered }$n"; continue;; esac
        printf '%s\n' "$rows" | awk -v n="$n" '$1 == "D" && $3 == n { f = 1 } END { exit !f }' && delivered="${delivered:+$delivered }$n"
      done
      # (#836) A child commit of this run that is in neither the wave nor the base, and lives on a local branch of the checkout only,
      # keeps the wave: deleting the wave branch would leave that commit nowhere but on the local branch. One WAVE REFUSE line
      # names it, and nothing is pushed. A commit of an issue the base already delivered is no refusal. A capped wave lands as
      # it is (ADR-0145 rule 13), so the cap frees it here as it does the #667 hold above: no refusal once the cap is reached.
      # (#845) Only a completed run's child commit holds the wave: a failed, needs-human, stopped or verdict-less run's commit never
      # does, so its wave takes the normal path. The refusal is said once per wave and commit: the marker names the commit last said.
      local undel="" orph refused="$SG_STATE/auto/orphan-refused-$run"
      for n in $(run_issues "$rd"); do case " $delivered " in *" $n "*) ;; *) undel="${undel:+$undel }$n";; esac; done
      if [ "$capped" != 1 ] && [ -n "$undel" ] && orph=$(orphan_child "$run" "$rd" "$S" "$w" $undel); then
        if [ "$(cat "$refused" 2>/dev/null)" != "${orph% *}" ]; then
          mkdir -p "$SG_STATE/auto" && printf '%s\n' "${orph% *}" > "$refused"
          echo "WAVE REFUSE $w: child commit ${orph% *} names #${orph#* }; it is on a local branch only and in neither $w nor $base, so the wave is kept"
        fi
        rm -rf "$S"; return 1
      fi
      sc push -q origin ":refs/heads/$w" >/dev/null 2>&1 || echo "WAVE-NOTE branch $w was not deleted" >&2
      sha=$(sc rev-parse "origin/$base"); rm -rf "$S"; rm -f "$SG_STATE/auto/wave-start-$run" "$SG_STATE/auto/wave-pause-$run" "$refused"
      echo "WAVE $w landed empty in $base (nothing ahead of it; the wave branch is deleted${dispatched:+; the run was dispatched for $dispatched})"
      # An empty wave past its age cap follows the capped landing's rule too (#591): every issue of the run that `dev` does
      # not hold goes back to the queue, so a child that never entered is built into a later wave instead of staying in Doing.
      # An issue whose non-RED commit the base holds is delivered below, never requeued.
      if [ "$capped" = 1 ]; then
        for n in $(run_issues "$rd"); do case " $delivered " in *" $n "*) ;; *)
          wave_fact "$run" "$n" refused "the wave reached its age cap (${SG_WAVE_MAX_AGE_SECONDS:-7200} s) and landed empty without this child" keep
          requeue "$n" "the wave reached its age cap (${SG_WAVE_MAX_AGE_SECONDS:-7200} s) and landed empty without this child: it is back in the queue" cap;; esac; done
      fi
      if [ -n "$delivered" ]; then deliver "$run" "$w" "$sha" "$delivered" || return 1; else rm -f "$SG_STATE/auto/wave-$run"; fi
      return 0; }
    # The wave tip's check (#377, ADR-0145 rule 11): a red tip names the child that broke it, and that child leaves the wave
    # before it lands. A tip that stays red with no child named is a landing that failed, as any other cause is.
    # The base's own check (#786): a red completed check on $base holds the landing until its head is green.
    red=$(sg_base_red "$base") && { echo "WAVE WAIT $w: $red"; rm -rf "$S"; return 1; }
    wave_check "$S" "$w"; rc=$?
    [ $rc = 3 ] && { echo "WAVE WAIT $w: the gate lock or the gate slots are held by another gate, so the tip's check did not run; the wave lands on a later pass"; rm -rf "$S"; return 1; }
    [ $rc = 4 ] && { stuck "$run" "$w" "$S" "the check at the tip of $w is inconclusive, so the wave does not land: $(/usr/bin/grep -v '^$' "$SG_STATE/wave-check-$(printf '%s' "$w" | tr / _).log" 2>/dev/null | tail -1)"; rm -rf "$S"; return 1; }
    [ $rc = 0 ] || { stuck "$run" "$w" "$S" "the check at the tip of $w is red and no child's own suite names the cause"; rm -rf "$S"; return 1; }
    sha=$(sc rev-parse HEAD)
    # The issues the wave delivers, read here, after the rebase, from what is ahead of the base: a commit the base already holds
    # is dropped by the rebase, and its issue was not delivered by this landing. A repeat from a refused push reads them again.
    # (#869) A commit that names exactly one issue delivers it (commit_refs). A commit that names several delivers none of them:
    # the cluster child did not say which it fixed, so those issues are named and requeued below, never closed.
    rows=$(commit_refs "$S" "origin/$base..HEAD")
    anyref=$(sc log --format=%B "origin/$base..HEAD" 2>/dev/null | /usr/bin/grep -cE 'Refs #[0-9]+')   # a RED commit's Refs counts here (#1003)
    issues=$(printf '%s\n' "$rows" | awk '$1 == "D" { print $3 }' | sort -un | tr '\n' ' ')
    amb=$(printf '%s\n' "$rows" | awk '$1 == "A" { for (i = 3; i <= NF; i++) print $i }' | sort -un | tr '\n' ' ')
    # ONE push of the wave's tip to the base: one ref update, one CI run, and a push git refuses unless it is a fast-forward,
    # so no merge commit can land. Nothing else is pushed -- no child branch, and the wave branch goes once its work is in.
    out=$(sc push -q origin "HEAD:refs/heads/$base" 2>&1) && break
    netfail "the push of $w to $base" "$out" && { rm -rf "$S"; return 2; }
    [ "$try" = 1 ] || { stuck "$run" "$w" "$S" "the push of $w to $base was refused: $(printf '%s' "$out" | tail -1)"; rm -rf "$S"; return 1; }
    try=2
    fo=$(sc fetch -q origin "+refs/heads/$base:refs/remotes/origin/$base" 2>&1) || {
      netfail "the fetch of $base from $remote" "$fo" || stuck "$run" "$w" "$S" "$base is not on $remote"; rm -rf "$S"; return 2; }
  done
  sc push -q origin ":refs/heads/$w" >/dev/null 2>&1 || echo "WAVE-NOTE branch $w was not deleted" >&2
  rm -rf "$S"
  rm -f "$SG_STATE/auto/wave-start-$run" "$SG_STATE/auto/wave-pause-$run"   # the wave is in $base; wave-$run goes once every issue is delivered (deliver)
  sg_base_red "$base" >/dev/null   # the base right after the push (#786): a red head the wave lands on is reported now
  printf '%s\n' "$rows" | while read -r k sha ns; do
    [ "$k" = A ] && echo "WAVE-NOTE $w: commit ${sha:0:9} names several issues ($ns) and closes none of them"; done
  # (#869) The run's issues a multi-issue commit names, and the wave did not deliver, go back to the queue with that reason. Their
  # work is in $base, so the cap: a requeue for it counts no failed dispatch.
  ent=$(python3 "$here/issue_ledger.py" entered "$SG_REPO" 2>/dev/null | awk -F '\t' -v r="$run" '$1 == r { print $2 }' | tr '\n' ' ')
  for n in $(run_issues "$rd"); do case " $amb " in *" $n "*) ;; *) continue;; esac
    case " $issues " in *" $n "*) continue;; esac
    why="a commit names several issues, so it closes none of them: one commit per issue closes each one"
    wave_fact "$run" "$n" refused "$why" keep
    requeue "$n" "$why" cap
  done
  # A wave that reached its age cap landed as it is (#378, ADR-0145 rule 13): the children the run dispatched that never
  # entered it go back to the queue, so the loop builds them into a later wave. They are read from the run's own record
  # (run_issues); an issue the wave carried just closed above, and is never requeued.
  local rq=""   # the issues the capped block below sends back to the queue: the no-Refs fallback never closes one (#1003)
  if [ "$capped" = 1 ]; then
    # (#869) A child that entered the wave is not missing from it, even when its commit is not ahead of the base any more (the
    # rebase dropped it), and an issue a multi-issue commit named was requeued above with its own reason.
    for n in $(run_issues "$rd"); do case " $issues $amb $ent " in *" $n "*) ;; *)
      wave_fact "$run" "$n" refused "the wave reached its age cap (${SG_WAVE_MAX_AGE_SECONDS:-7200} s) and landed without this child" keep
      rq="$rq $n"
      requeue "$n" "the wave reached its age cap (${SG_WAVE_MAX_AGE_SECONDS:-7200} s) and landed without this child: it is back in the queue" cap;; esac; done
  fi
  # The wave is in $base: each issue it carries closes through the API, and its one delivery comment is queued, never posted
  # (cqflush.sh keeps one per issue, edited in place, #328). Each card reads the delivered column, which is what the landing
  # means: Closed on the shipped vocabulary, which has no Done (#468), Done otherwise. A refused write is named, never dropped. Only a
  # close the API accepted queues the comment, writes the ledger row and moves the card: a close that fails leaves the issue
  # open, and every one of those three would claim a delivery that did not happen. The close repeats once, as the push does.
  # A wave closes only the issues its run was dispatched for (#775). A `Refs #N` outside the run's dispatch set (an engine
  # commit that took the first #N of the issue's prose) is reported here and in wave-outside.tsv, which the spec cascade
  # reads, and that issue is never closed. A run whose record names no issue keeps the old rule: every `Refs #N` closes.
  local dset kept="" n2; dset=" $(run_issues "$rd" | tr '\n' ' ') "
  if [ -n "${dset// /}" ]; then
    for n2 in $issues; do case "$dset" in *" $n2 "*) kept="${kept:+$kept }$n2";; *)
      echo "WAVE-NOTE #$n2: $w names it (Refs #$n2) outside the dispatch set of run $run; reported, not closed"
      printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$run" "$n2" "$w" >> "$SG_STATE/wave-outside.tsv";; esac; done
    issues=$kept
  fi
  # (#1003) A wave whose commits name no issue at all (no `Refs #N` in any commit, a RED one included) closes the issues its run
  # was dispatched for, read from the run's own request (run_issues, one source), and clears their dispatch markers: the run
  # ended and its work is in $base, so nothing else would free them. Only an issue whose child entered the wave closes (`ent`,
  # the ledger's entered fact): a child that failed its gates has no work in $base. A one-issue run with no entered fact
  # closes its one issue. An issue the capped block requeued above never closes. A wave with any Refs keeps the per-commit
  # rule above. A run whose issues cannot be read, or a multi-issue run with no entered child, closes nothing, and one line names the run.
  local fb="" cand="" n3
  if [ -z "$issues" ] && [ "${anyref:-0}" = 0 ]; then
    for n3 in $dset; do case " $rq " in *" $n3 "*) ;; *) cand="${cand:+$cand }$n3";; esac; done
    if [ -z "${dset// /}" ]; then
      echo "WAVE-NOTE $w: no commit names an issue (no Refs), and the issues of run $run cannot be read ($rd/request.txt, $rd/record.jsonl), so the landing closes nothing"
    elif [ -n "${ent// /}" ]; then
      for n3 in $cand; do case " $ent " in *" $n3 "*) issues="${issues:+$issues }$n3";; esac; done
    elif [ "$(printf '%s' "$dset" | wc -w | tr -d ' ')" = 1 ]; then
      issues=$cand
    fi
    if [ -n "$issues" ]; then
      fb=1
      echo "WAVE-NOTE $w: no commit names an issue (no Refs), so the landing closes the issues run $run was dispatched for whose child entered: #${issues// /, #}"
    elif [ -n "${dset// /}" ]; then
      echo "WAVE-NOTE $w: no commit names an issue (no Refs), and no issue of run $run has an entered child it did not requeue, so the landing closes nothing"
    fi
  fi
  deliver "$run" "$w" "$sha" "$issues" || return 1
  [ -n "$fb" ] && for n in $issues; do rm -f "$SG_STATE/auto/disp-$n"; done
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
  fail) shift; fail "$@";;
  probe-retry) shift; probe_retry "$@";;
  *) echo "usage: wave.sh start <run> | wave.sh enter <run> <issue> | wave.sh land <run> | wave.sh expired <run> | wave.sh requeue <issue> <why> [cap] | wave.sh fail <issue> <run> <why> | wave.sh probe-retry <issue>" >&2; exit 2;;
esac
