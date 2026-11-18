#!/bin/bash
# usage: wave.sh start <run>          the run cuts its wave branch wave/<run> from origin/$SG_BASE, on the remote
#        wave.sh enter <run> <issue>  from the child's worktree: the local gates, the rebase on the wave tip, the --ff-only entry
#        wave.sh land <run>           when the run ends: the rebase on the moved base, the one push, and the closes
#        wave.sh expired <run>        exit 0 when the run's wave reached its age cap (SG_WAVE_MAX_AGE_SECONDS)
#        wave.sh requeue <issue> <why> [cap]  the child never enters: its issue goes back to the queue and the loop builds it again
#        wave.sh retire <run>         end the run an age-cap requeue waits for, then requeue its issues once (#1970)
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
# and `enter` says why on one `WAVE REQUEUE` line. A child that entered moves its card to Landing: Landing means
# the child is in the wave branch, Closed means the wave is in `dev`. The wave branch is rebased on `dev` only before its own
# final push (the wave's landing pass), never between entries: between them the tip moves only by a child that entered.
# A child whose own commits and a sibling's commits in the wave change one file never enters (#1434): the entry reads the two
# diffs, not the issue bodies, so a file neither body names is a conflict before the wave, not a conflict at the landing.
# A child's gate can fail on a red the base carries, not on the child's change: the run cut its wave from a base that has moved
# since, and the fix for that red may be in the current base. When the entry gate fails, the entry rebases the child on the
# current $SG_BASE once and runs the gate again on that tree (#2272): a pass lets the child enter, and a red on the rebased tree
# requeues it as before. The entry record, the requeue line and the hold line name each failed test and say whether the base
# tip fails it too; the base answer comes from the gate's own interpreter, run through gate.sh under the same gate lock. A
# rebase that conflicts holds the child where it stands with its commits kept, and requeues nothing.
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
# A wave with nothing ahead of the base landed empty: its ref and marker go and `land` exits 0. A wave whose tip the base already
# carries, with a child that entered it, has landed in an earlier cycle: its books are written, its ref and markers go as a kept
# landing branch's do (#1826), and a child that never entered it goes back to the queue (#1833). A child commit of a wave the base
# does not carry, on a local branch only, still refuses the landing. A landing that fails on a rebase
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
# commit changes), then its Python test delta through the gate lock and slots (`SG_WAVE_CHILD_PYTEST`, #1409). The first child whose suite fails leaves the wave -- its commits are dropped from the tip, its issue goes
# back to the queue -- and the wave without it lands. A red tip no child's suite names is a landing that failed, as before.
# Exit: 0 entered (or the wave branch was cut, or the wave landed), 1 not entered (requeued after a failed gate, a conflict or a
# refused push; held where it stands when the review did not approve the child), 2 a bad call or a wave branch that is not there.
# Guards: `test-wave-entry.sh`, `test-wave-land.sh`, `test-wave-retry.sh`, `test-wave-aged.sh`, `test-wave-obligation.sh`,
# `test-wave-issue.sh`, `test-wave-tipcheck.sh`, `test-wave-conflict.sh`, `test-wave-eviction.sh`.
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
# removed `wave-<run>`, that a child of this run never gets the direct Landing move.
mark() { mkdir -p "$SG_STATE/auto" && printf '%s\n' "$2" > "$SG_STATE/auto/wave-$1" && printf '%s\n' "$2" > "$SG_STATE/auto/waved-$1" \
  && { [ -s "$SG_STATE/auto/wave-start-$1" ] || date +%s > "$SG_STATE/auto/wave-start-$1"; }; }

# run_dir <run>: the run folder, its line in active-runs.txt, else $SG_CHECKOUT/runs/<run>.
run_dir() { local rd; rd=$(/usr/bin/grep "/$1\$" "$SG_STATE/active-runs.txt" 2>/dev/null | tail -1); printf '%s\n' "${rd:-${SG_CHECKOUT:-.}/runs/$1}"; }

# obligation_of <tree sha>: the newest proof obligation the gate wrote for that tree, its path, "" when it wrote none (spec 1163).
# gate.sh is its one writer; the entry reads it to carry the job the unproven tree owes into the run's entry record.
obligation_of() {
  local f osh t best="" bt=-1
  for f in "$SG_STATE"/obligations/*; do
    [ -f "$f" ] || continue
    osh=$(cut -f2 "$f" 2>/dev/null); [ "$osh" = "$1" ] || continue
    if stat --version >/dev/null 2>&1; then t=$(stat -c %Y "$f" 2>/dev/null || echo 0); else t=$(stat -f %m "$f" 2>/dev/null || echo 0); fi
    [ "$t" -gt "$bt" ] 2>/dev/null && { bt=$t; best=$f; }
  done
  [ -n "$best" ] && printf '%s\n' "$best"
}

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

# refs_in_message: the issue numbers a commit message names (#1491), read from stdin, one per line, in order, repeats kept. A
# `Refs #N` inside an inline backtick span, or between ``` fence lines, is text and names no issue; on a line with an odd backtick
# count, the text after the last backtick is text. This is the one parser: every reader of a message calls it.
refs_in_message() {
  awk '
    /^[ \t]*```/ { fence = !fence; next }
    fence { next }
    {
      line = $0; n = gsub(/`/, "`", line)
      if (n % 2 == 1) { i = length(line); while (substr(line, i, 1) != "`") i--; line = substr(line, 1, i - 1) }
      gsub(/`[^`]*`/, "", line)
      while (match(line, /Refs #[0-9]+/)) { print substr(line, RSTART + 6, RLENGTH - 6); line = substr(line, RSTART + RLENGTH) }
    }'
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
      refs=" $(printf '%s\n' "$msg" | refs_in_message | tr '\n' ' ') "
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

reset_sha() {   # <run>: the sha a reset names on its issue: the run's branch tip, or the checkout's HEAD when no run names one (#1904)
  local tip=""
  [ -n "$1" ] && tip=$($G -C "$SG_CHECKOUT" rev-parse -q --verify "refs/heads/squad/run/$1" 2>/dev/null)
  printf '%s' "${tip:-$($G -C "$SG_CHECKOUT" rev-parse HEAD 2>/dev/null)}"
}
board_write() {   # <column> <issue>: a refused card write is named, so the card's observed column stays clear.
  local col=$1 n=$2 out rc
  out=$("$BOARD" "$SG_OWNER" "$SG_PROJECT" "$col" "$n" 2>&1); rc=$?
  [ "$rc" -eq 0 ] || printf 'WAVE-BOARD #%s %s refused: %s\n' "$n" "$col" "$out"
  return 0
}

# gating <clone> <range> <run>...: before a wave's tip gate starts, the card of each child in the gate moves to Gating (ADR-0170).
# A card moves when its child entered the wave of one of the runs (the ledger's entered fact) and the gated range carries
# the child's commit. So a child the tip check dropped from the tree stays out of the column. board.py sends no write when the card
# already carries Gating (ADR-0144), so a repeat pass after a red or unproven attempt moves nothing: the attempt is a marker.
# The legacy vocabulary has no Gating column, so it writes nothing.
gating() {
  [ "${SG_BOARD_VOCAB:-}" = shipped ] || return 0
  local gs=$1 range=$2 c n ent seen=" "; shift 2
  ent=$(python3 "$here/issue_ledger.py" entered "$SG_REPO" 2>/dev/null | awk -F '\t' -v rs=" $* " 'index(rs, " " $1 " ") { print $2 }' | tr '\n' ' ')
  while read -r c; do
    n=$(child_of "$gs" "$c"); [ -n "$n" ] || continue
    case "$seen" in *" $n "*) continue;; esac
    case " $ent " in *" $n "*) ;; *) continue;; esac
    seen="$seen$n "; board_write Gating "$n"
  done < <($G -C "$gs" log --format=%H "$range" 2>/dev/null)
}

# proof_card <issue> <obligation path|"">: the card's proof fact (#1176, spec 1163). An unproven entry records the
# obligation key under $SG_STATE/proof-card/<issue>/ and puts `unproven` on the card, so the nightly settlement can
# answer it. An entry that proved its work answers no obligation, so it writes nothing: a later pass proves the new
# tree, never the tree the open obligation names, and only the settlement clears the fact.
# The fact is written by the entry and by the wave landing here, and the nightly consumes the map; gate.sh stays the
# obligation's one writer, and a refused card write is named, never dropped.
proof_card() {
  local n=$1 ob=$2 d=$SG_STATE/proof-card/$1 out rc
  [ -n "$ob" ] || return 0
  mkdir -p "$d" 2>/dev/null && : > "$d/$(basename "$ob")"
  out=$("$BOARD" --proof "$SG_OWNER" "$SG_PROJECT" "$n" unproven 2>&1); rc=$?
  [ "$rc" -eq 0 ] || printf 'WAVE-BOARD #%s proof refused: %s\n' "$n" "$(printf '%s\n' "$out" | /usr/bin/grep -v '^issue_state\.py: items read ' | tail -1)"
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
  local c
  for c in $($G -C "${SG_CHECKOUT:-/nonexistent}" log -n 500 --format=%H -E --grep="Refs #$1([^0-9]|\$)" "origin/$base" 2>/dev/null); do
    local msg; msg=$($G -C "$SG_CHECKOUT" log -1 --format=%B "$c" 2>/dev/null)
    case "$(printf '%s\n' "$msg" | awk 'NF { print; exit }')" in "TDD RED"*) continue;; esac
    printf '%s\n' "$msg" | refs_in_message | /usr/bin/grep -qx "$1" && { printf '%s\n' "$c"; return 0; }
  done
  return 0
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

# freeze_stand <issue> <class> <why>: 0 when the issue already carries the freeze this class writes for this reason (#2217).
# The freeze stands only with both its labels and a comment that quotes the reason: owner-only needs frozen:owner and
# needs-ratification; does-not-reproduce needs frozen:not-startable and a marker that names this build. A label with no
# comment, the state a refused comment queue leaves, is not standing, so the next pass writes the comment again. A read
# that does not answer is no proof of a freeze: the caller writes, and GitHub dedupes a label it already holds.
freeze_stand() {
  local n=$1 cls=$2 why=$3 labels cur body
  labels=$(gh_api "repos/$SG_REPO/issues/$n/labels" -q '[.[]?.name] | join(" ")' 2>/dev/null) || return 1
  if [ "$cls" = owner-only ]; then
    case " $labels " in *" frozen:owner "*) ;; *) return 1;; esac
    case " $labels " in *" needs-ratification "*) ;; *) return 1;; esac
  else
    case " $labels " in *" frozen:not-startable "*) ;; *) return 1;; esac
    cur=$(sg_loop_build)
    [ -n "$cur" ] || return 1
  fi
  body=$(gh_api "repos/$SG_REPO/issues/$n/comments?per_page=100" -q '.[].body' 2>/dev/null) || return 1
  printf '%s\n' "$body" | /usr/bin/grep -qF "Reason: $why" || return 1
  [ "$cls" = owner-only ] && return 0
  printf '%s\n' "$body" | /usr/bin/grep -qF "SQUAD-NOT-STARTABLE: build=${cur} "
}

# freeze_class <issue> <run> <why> <class>: a failed run whose artifact named its end class freezes its issue in one comment that
# quotes the reason (#2217). owner-only takes frozen:owner and needs-ratification in one label request, held until the owner
# frees it; a request GitHub refuses freezes nothing, and the caller reports the partial state. does-not-reproduce writes the
# not-startable marker at this build, so a later build dispatches the issue again. Sets froze=1 when it wrote the freeze and
# froze=0 when the freeze already stood.
freeze_class() {
  local n=$1 run=$2 why=$3 cls=$4 f out
  froze=0
  freeze_stand "$n" "$cls" "$why" && return 0
  if [ "$cls" = owner-only ]; then
    out=$(gh_send -X POST "repos/$SG_REPO/issues/$n/labels" -f "labels[]=frozen:owner" -f "labels[]=needs-ratification" 2>&1) || {
      printf 'WAVE-FREEZE #%s frozen:owner and needs-ratification refused: %s\n' "$n" "$out"; return 1; }
    f=$(sg_cq_file "$n-endclass.md" 2>/dev/null) || { printf 'WAVE-FREEZE #%s the freeze comment could not be queued\n' "$n"; return 1; }
    printf 'Loop freeze: run %s ended failed with an owner-only reason. The loop does not dispatch this issue again; the owner decides it.\n\nReason: %s\n' "$run" "$why" > "$f"
  else
    out=$(gh_send -X POST "repos/$SG_REPO/issues/$n/labels" -f "labels[]=frozen:not-startable" 2>&1) || {
      printf 'WAVE-FREEZE #%s frozen:not-startable refused: %s\n' "$n" "$out"; return 1; }
    f=$(sg_cq_file "$n-endclass.md" 2>/dev/null) || { printf 'WAVE-FREEZE #%s the freeze comment could not be queued\n' "$n"; return 1; }
    printf 'SQUAD-NOT-STARTABLE: build=%s run %s ended failed: the defect does not reproduce at the base tip. A later build dispatches it again.\n\nReason: %s\n' "$(sg_loop_build)" "$run" "$why" > "$f"
  fi
  froze=1
}

# hand <issue> <why>: the issue's own count reached SG_HAND_AT (2 by default) below SG_REQUEUE_CAP, so the loop hands it to
# HAL9K, the hand agent, instead of a third dispatch (#2053). The hand hold (squad.handhold) writes ready-for-HAL9K, which no
# dispatch pass takes, and one comment lists the runs counted. The route is bounded: with SG_HAND_MAX (2) live hal9k holds open,
# the issue stays in the queue and reaches the stop at the cap. A hold that expires frees the issue, and its next failure
# reaches the cap. SG_HAND_AT=off turns the route off. Returns 0 when it handed the issue over, else 1.
hand() {
  local n=$1 why=$2 at=${SG_HAND_AT:-2} py open out f runs hold_nums closed h
  [ "$at" = off ] && return 1
  [ "$c" -ge "$at" ] && [ "$c" -lt "${SG_REQUEUE_CAP:-3}" ] || return 1
  py=${SG_SQUAD:+$SG_SQUAD/.venv/bin/python}; [ -n "$py" ] && [ -x "$py" ] || py=python3
  hold_nums=$(PYTHONPATH="${SG_SQUAD:-.}${PYTHONPATH:+:$PYTHONPATH}" "$py" -m squad.handhold holds 2>/dev/null | /usr/bin/grep ' hal9k ' | /usr/bin/sed 's/^#\([0-9]*\) .*/\1/')
  closed=""
  for h in $hold_nums; do [ "$(gh_api "repos/$SG_REPO/issues/$h" -q '.state' 2>/dev/null)" = closed ] && closed="$closed $h"; done
  open=$(PYTHONPATH="${SG_SQUAD:-.}${PYTHONPATH:+:$PYTHONPATH}" "$py" -m squad.handhold holds --live --closed $closed 2>/dev/null | /usr/bin/grep -c ' hal9k ')
  [ "${open:-0}" -lt "${SG_HAND_MAX:-2}" ] || { echo "WAVE HAND-FULL #$n: $open hal9k holds are open, so it stays in the queue"; return 1; }
  out=$(PYTHONPATH="${SG_SQUAD:-.}${PYTHONPATH:+:$PYTHONPATH}" "$py" -m squad.handhold hold --by hal9k "$n" 2>&1) \
    || { echo "WAVE HAND-REFUSED #$n: $(printf '%s' "$out" | tail -1)"; return 1; }
  runs=$(ledger read "$n" own 2>/dev/null | python3 -c 'import json,sys
for x in json.load(sys.stdin).get("runs", []): print("- %s: %s" % (x.get("run") or "(no run named)", x.get("why", "")))' 2>/dev/null)
  f=$(sg_cq_file "$n-wavehand$$.md" 2>/dev/null) && {
    printf 'SQUAD-HAND: %s failed dispatches with no change in the work the base holds for it, so HAL9K takes the issue by hand (#2053).\n\nRuns:\n' "$c"
    printf '%s\n' "$runs"; } > "$f"
  echo "WAVE HAND #$n: $c failed dispatches with no change, so HAL9K takes it by hand: $why"
}

# loop_caused <why> [own|loop]: the loop caused this requeue, not the issue's work (#621): a child a held gate lock or the
# wave's age cap kept out of the wave (#682; a wave that landed without a child for a cause wave.sh did not record is no loop
# cause, so the janitor's "unknown" counts as the issue's own), an adapter error, a merge conflict with a sibling (the rebase on the wave tip), a disk stop, a review chain that
# wrote no ruling (every model failed, or a hold with no verdict), a red tip a child that broke it cannot leave (#663), a
# run that completed with nothing landed, a prerequisite the loop could not resolve (no profile, a sandbox denial), and a
# network failure, and a finished run that cut no wave (#1349). A full gate budget is one of them wherever it is named: the
# take prints "caps.gates is full", a wait past its bound "runs with 1 worker and no slot", and a gate that gave the lock back
# "the gate slots are full" (#1684). Each is a whole phrase of the message, never a test's text that mentions the budget, which
# is a real red. The caller's kind wins over the text: the janitor reads the run's own review verdict (`own`: CHANGES or
# BLOCKED on the diff), which a reason like "review_held" hides.
loop_caused() {
  case "${2:-}" in own) return 1;; loop) return 0;; esac
  case "$1" in *"the gate lock is held"*|*"the gate slots"*"are full"*|*"caps.gates is full"*|*"caps.gates full"*|*"runs with 1 worker and no slot"*|*"reached its age cap"*|*adapter_error*|*merge_conflict*|*"the rebase on "*" conflicts"*|*"DISK STOP"*|*"No space left"*|\
    *"every review model failed"*|*"head moved during the review"*|*"with no verdict"*|\
    *"no child's own suite names the cause"*|*"is inconclusive, so the wave does not land"*|*"completed"*"nothing landed"*|*"no profile"*|*"sandbox"*"denied"*|\
    *"Could not resolve host"*|*"Failed to connect"*|*"Connection timed out"*|*"Operation timed out"*|*"Network is unreachable"*|\
    *"run cut no wave"*) return 0;; esac
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
  local n=$1 why=$2 f h st
  # A closed issue never goes back to the queue (#1335): the owner closed it, its card stays where it is, and no marker or count moves.
  # The state is read live (gh_api: a read that decides a write is never a snapshot). A read that does not answer keeps the requeue, named.
  st=$(gh_api "repos/$SG_REPO/issues/$n" -q '.state' 2>/dev/null) || st=""
  case "$st" in
    closed) echo "WAVE KEEP #$n: the issue is closed, so $why does not put it back in the queue"; return 0;;
    open) ;;
    *) echo "WAVE NOTE #$n: its state could not be read, so the requeue goes on";;
  esac
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
    [ "$fresh" = 1 ] && hand "$n" "$why"   # the hold keeps every dispatch pass off; the card still goes back to Todo below
  elif h=$(held_out "$n"); then   # a cap requeue counts nothing, so it reads the stop itself (#682): a stopped issue stays put
    echo "WAVE STOP #$n: $h, so the age cap does not put it back in the queue: $why"; return 0
  elif [ -n "${SG_REQUEUE_RUN:-}" ] && [ -e "$SG_STATE/auto/empty-requeued-$SG_REQUEUE_RUN-$n" ]; then
    # An empty landing lands once per (run, issue) (#2245): the first one puts the issue back in the queue, so a second
    # landing of the same ended wave starts no new run.
    echo "WAVE KEEP #$n: run $SG_REQUEUE_RUN already sent it back to the queue once, so this landing starts no new run: $why"; return 0
  fi
  [ "${3:-}" = cap ] && [ -n "${SG_REQUEUE_RUN:-}" ] && { mkdir -p "$SG_STATE/auto" && : > "$SG_STATE/auto/empty-requeued-$SG_REQUEUE_RUN-$n"; }
  rm -f "$SG_STATE/auto/disp-$n" "$SG_STATE/auto/bug-$n"
  SG_MOVE_REASON=no-landing SG_MOVE_EVENT="${SG_REQUEUE_RUN:-requeue-$n}" SG_MOVE_SHA="$(reset_sha "${SG_REQUEUE_RUN:-}")" board_write "${SG_TODO:-Todo}" "$n"   # a requeue landed nothing: the card leaves Landing as a no-landing move (#1340)
  f=$(sg_cq_file "$n-wave$$.md" 2>/dev/null) && printf '%s\n' "$why" > "$f"
  echo "WAVE REQUEUE #$n: $why"
}

# cap_hold <run> <run dir> <issue> <why>: an issue the age cap sends back to the queue waits for the end of its run (#1970).
# The run that worked it may still run: it holds a run slot and spend, and the loop could dispatch the issue a second time.
# auto/retire-<run> keeps the run folder on its first line and one "<issue> TAB <why>" line per issue; retire_run reads it.
cap_hold() {
  local m="$SG_STATE/auto/retire-$1" rd=$2
  case "$rd" in /*) ;; *) rd="$PWD/$rd";; esac
  mkdir -p "$SG_STATE/auto"
  [ -s "$m" ] || printf '%s\n' "$rd" > "$m"
  tail -n +2 "$m" | cut -f1 | /usr/bin/grep -qx "$3" || printf '%s\t%s\n' "$3" "$4" >> "$m"
}

# retire_run <run>: end the run its retire marker names, then requeue each issue the marker holds, once (#1970). The end goes
# through squad/retire.py (SG_RETIRE_CMD replaces it in a test): the engine the record names is stopped by its pid and start
# time, the end is verified, the work is kept and the killed verdict is written, which frees the run slot (ADR-0167). A run
# that is not proven ended keeps its claim: no issue is requeued, the marker stays, and the watch cycle tries again
# (`wave.sh retire <run>`). A requeue already done is listed in <marker>.done, so a pass that stopped midway repeats none.
retire_run() {
  local run=${1:?retire: a run is required} m rd out rc n why py
  m="$SG_STATE/auto/retire-$run"; [ -s "$m" ] || return 0
  rd=$(head -1 "$m")
  if [ -n "${SG_RETIRE_CMD:-}" ]; then
    out=$("$SG_RETIRE_CMD" "$rd" "killed/age-cap-requeue" 2>&1); rc=$?
  else
    py=${SG_SQUAD:+$SG_SQUAD/.venv/bin/python}; [ -n "$py" ] && [ -x "$py" ] || py=python3
    out=$(PYTHONPATH="${SG_SQUAD:-${SG_CHECKOUT:-.}}${PYTHONPATH:+:$PYTHONPATH}" "$py" -m squad.retire "$rd" "killed/age-cap-requeue" --checkout "${SG_CHECKOUT:-.}" 2>&1); rc=$?
  fi
  [ -n "$out" ] && printf '%s\n' "$out"
  if [ "$rc" != 0 ]; then
    echo "WAVE RETIRE-HOLD $run: the run is not proven ended (exit $rc), so its issues stay claimed and none is requeued: $(tail -n +2 "$m" | cut -f1 | tr '\n' ' ')"
    return 1
  fi
  while IFS=$'\t' read -r n why <&3; do
    [ -n "$n" ] || continue
    /usr/bin/grep -qx "$n" "$m.done" 2>/dev/null && continue
    requeue "$n" "$why" cap
    echo "$n" >> "$m.done"
  done 3< <(tail -n +2 "$m")
  rm -f "$m" "$m.done"
}

fail() {   # <issue> <run> <why> [own|loop|nochange] [end class]: the run ended with nothing delivered for the issue (janitor rule O, #609): one failed dispatch.
  local n=$1 run=$2 why=$3 kind=${4:-} cls=${5:-} loopf=0
  { [ -n "$n" ] && [ -n "$run" ]; } || { echo "WAVE-ERROR wave.sh fail <issue> <run> <why>"; return 2; }
  probe_refuted "$n" "$run" && return 0   # a probe whose acceptance command is unmet refutes its premise (#888)
  if [ "$kind" = nochange ]; then   # a run that changed no file is never tried again: one such run marks the issue for the owner (#2245)
    why="no change landed (review_nochange): $why"   # the card reads the no-change cause, never the unproven text alone
    loop_caused "$why" "$kind" && loopf=1
    if [ "$loopf" = 1 ]; then count "$n" "$run" "$why" loop; else count "$n" "$run" "$why" own; fi
    [ "$fresh" = 1 ] || { echo "WAVE FAILED #$n: run $run is already counted"; return 0; }
    stop "$n" "$why"; return 0
  fi
  loop_caused "$why" "$kind" && loopf=1
  # The count decision: one failed dispatch, which a run counts once, under its own ledger when the loop caused it (#621).
  if [ "$loopf" = 1 ]; then count "$n" "$run" "$why" loop; else count "$n" "$run" "$why" own; fi
  # The freeze decision, apart from the count (#2217): an artifact that named its end class freezes the issue even when a
  # loop-caused reason would never reach the count's cap. The freeze is its own idempotence marker, so a later run of the
  # same class writes nothing again.
  if [ -n "$cls" ]; then
    freeze_class "$n" "$run" "$why" "$cls" \
      || { echo "WAVE FREEZE PARTIAL #$n: run $run ended $cls and the freeze did not complete: $why"; return 1; }
    [ "$froze" = 1 ] && echo "WAVE FROZEN #$n: run $run ended $cls, so it is not dispatched again: $why" \
                     || echo "WAVE FROZEN #$n: run $run ended $cls; its freeze already stands: $why"
    return 0
  fi
  [ "$fresh" = 1 ] || { echo "WAVE FAILED #$n: run $run is already counted"; return 0; }
  if [ "$loopf" = 1 ]; then
    [ "$c" -ge "${SG_LOOP_REQUEUE_CAP:-6}" ] && { loop_stop "$n" "$why"; return 0; }
    echo "WAVE FAILED #$n: loop-caused, $c of ${SG_LOOP_REQUEUE_CAP:-6} (run $run), not counted toward the cap: $why"; return 0
  fi
  [ "$c" -ge "${SG_REQUEUE_CAP:-3}" ] && { stop "$n" "$why"; return 0; }
  hand "$n" "$why" && return 0
  echo "WAVE FAILED #$n: $c of ${SG_REQUEUE_CAP:-3} failed dispatches with no change (run $run): $why"
}

enter() {   # <run> <issue>: from the child's worktree, the gates, the rebase on the wave tip, and the --ff-only entry.
  # obl is the obligation of an unproven entry and gwhy/gnot the word and the checks the gate named: gates() sets them, and the
  # entry carries them into the run's entry record. grc is the gate's exit code.
  # base is read here, not left to the caller's shell: the sibling check below compares a diff against origin/$base, and an
  # unset base would silently disable it (#1434 review).
  local run=$1 iss=$2 w br sha files out SG_REQUEUE_RUN=$1 base=${SG_BASE:-dev}; w=$(wave_of "$run")
  # firstrs and redtxt are set together, by note_first_red only, and read as a pair: nothing else assigns firstrs (#2272 review).
  local obl="" gwhy="" gnot="" grc=0 reuse="" skipped="" prior="" baseretried=0 rebsha="" firstrs redtxt gsha0
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
  # keep_conflict <files>: a rebase conflict with the wave tip keeps the child's own commits (#1344). The rebase is aborted, so HEAD
  # is the reviewed commits: they go to fix/<n>-rebase-<run> on the remote, and a CHANGES verdict on that branch asks one rebase
  # round (branchfixpass.sh). The child is held, never requeued: its worktree is not reset and no fresh build is dispatched.
  keep_conflict() { local kb=fix/$iss-rebase-$run kkey=fix_$iss-rebase-$run khead rhead
    khead=$($G rev-parse HEAD)
    # A later pass enters the same child again while its round is still open: the kept state is written, the round owns the branch,
    # and the review is the round's. So the child holds on the same wording, and nothing is pushed and no review is rewritten (#1344).
    if [ -s "$SG_STATE/auto/kept-$kkey" ]; then
      hold "the rebase on $w conflicts: $1; its commits are kept on $kb for one rebase round, not requeued"; return 1; fi
    # A later pass enters the same child again on the same head, or on a round that already pushed on top of it: a branch that
    # already holds this commit is the kept state, never a failure, so the push's refusal is read against the remote, not its exit code.
    $G push -q origin "HEAD:refs/heads/$kb" >/dev/null 2>&1
    $G fetch -q origin "+refs/heads/$kb:refs/remotes/origin/$kb" 2>/dev/null
    rhead=$($G rev-parse -q --verify "refs/remotes/origin/$kb" 2>/dev/null)
    { [ -n "$rhead" ] && $G merge-base --is-ancestor "$khead" "$rhead" 2>/dev/null; } || {
      hold "the rebase on $w conflicts: $1; its commits could not be kept on $kb (the push failed): the child stays held"; return 1; }
    # The review names the head it was written for, read after the push, and the ref the round rebases on: the wave tip it
    # actually conflicted with, never dev, which the held child keeps the wave from reaching (#1344).
    khead=$rhead
    printf 'origin/%s\n' "$w" > "$SG_STATE/auto/kept-$kkey"
    python3 "$here/revformat.py" --round-review "$w" "origin/$w" "$1" > "$SG_STATE/review-$kkey.md"   # the one writer of the round review (#1960)
    printf '%s\n' "$khead" > "$SG_STATE/review-$kkey.sha"; rm -f "$SG_STATE/review-$kkey.base"
    hold "the rebase on $w conflicts: $1; its commits are kept on $kb for one rebase round, not requeued"; return 1
  }
  # sibling_diff (#1434, ADR-0150 rule 2 derived): the files the child's own commits change and a sibling's commits in $w
  # change too, one path per line, exit 0; exit 1 when the two diffs are disjoint; exit 2 when either diff cannot be read, so
  # the entry reports a WAVE-ERROR instead of reading an unreadable diff as a clean pass. The body rule (siblings.py) reads
  # what an issue names; this reads what each commit touches, so a file no body names is a conflict before the wave instead
  # of at the landing. A sibling's commits are what $w carries above $base (the wave is cut from $base and only children
  # enter it); the child's own are what HEAD carries above its merge-base with $w, the range its review reads. Both ends of a
  # renamed path are read, so a sibling that renames a file the child edits is still seen as a shared path.
  diff_names() { $G diff --name-only --no-renames "$@" 2>/dev/null || return 1
                 $G diff --name-only --find-renames "$@" 2>/dev/null || return 1; }
  sibling_diff() {
    local mine theirs shared
    [ -n "$base" ] || return 2
    $G rev-parse -q --verify "origin/$w" >/dev/null 2>&1 || return 2
    $G rev-parse -q --verify "origin/$base" >/dev/null 2>&1 || return 2
    mine=$(diff_names "origin/$w...HEAD") || return 2
    theirs=$(diff_names "origin/$base...origin/$w") || return 2
    shared=$(comm -12 <(printf '%s\n' "$mine" | sed '/^$/d' | LC_ALL=C sort -u) \
                      <(printf '%s\n' "$theirs" | sed '/^$/d' | LC_ALL=C sort -u))
    [ -n "$shared" ] || return 1
    printf '%s\n' "$shared"
  }
  # record_shared_pairs (stdin: the shared paths, #1941): each child of the run that entered the wave is recorded with $iss, so the
  # frontier never pairs the two again in one run (siblings.py record). The entered children are the wave's siblings; a pair
  # is recorded with each, since the wave does not tell which sibling holds the path.
  record_shared_pairs() {
    local p s paths=()
    while IFS= read -r p; do [ -n "$p" ] && paths+=("$p"); done
    [ ${#paths[@]} -gt 0 ] || return 0
    for s in $(python3 "$here/issue_ledger.py" entered "$SG_REPO" 2>/dev/null | awk -F '\t' -v r="$run" '$1 == r { print $2 }'); do
      [ "$s" = "$iss" ] && continue
      python3 "$here/siblings.py" record "$iss" "$s" "${paths[@]}" >/dev/null 2>&1 || echo "WAVE-NOTE the shared-path pair of #$iss and #$s is not recorded (#1941)"
    done
  }
  rebase_on_tip() {
    $G fetch -q origin "+refs/heads/$w:refs/remotes/origin/$w" 2>/dev/null || {
      echo "WAVE-ERROR $w is not on $remote (the run cuts it from $base at its start): #$iss enters no wave"; return 2; }
    if ! out=$($G rebase "origin/$w" 2>&1); then
      files=$($G diff --name-only --diff-filter=U | tr '\n' ' '); $G rebase --abort >/dev/null 2>&1
      # Only a conflict with unmerged files keeps the commits (#1344). A rebase that stops with none (a dirty worktree) is a refusal.
      [ -n "$files" ] || { refuse "the rebase on $w conflicts: git said: $(printf '%s\n' "$out" | /usr/bin/grep -v '^[[:space:]]*$' | tail -1)"; return 1; }
      keep_conflict "${files% }"; return 1
    fi
    # A sibling's diff on one of the child's files is refused once the rebase is clean (#1434): the pair costs the wave a
    # landing conflict, and git alone names no cause for it. The child's issue goes back to the queue, and a later run
    # builds it alone. A diff the entry cannot read is a WAVE-ERROR, never a clean pass (#1434 review): exit 2 from
    # sibling_diff is reported and the child enters no wave. A rebase that conflicts is the case above: it keeps the
    # child's commits for one fix round (#1344).
    local shared src=0 list="" p
    shared=$(sibling_diff) || src=$?
    case "$src" in
      2) echo "WAVE-ERROR the sibling diff of $w over $base cannot be read: #$iss enters no wave"; return 2;;
      0) while IFS= read -r p; do [ -n "$p" ] && list="${list:+$list, }\"$p\""; done <<< "$shared"
         record_shared_pairs <<< "$shared"
         refuse "the diff of a sibling in $w changes the same file ($list): a merge_conflict of one wave, so the child waits for a run that builds it alone"; return 1;;
    esac
  }
  # failed_ids <reason>: the test ids the fast gate's pytest clause names, one per line, in pytest's `path::case` shape:
  # `pytest the base battery failed at <id> <id> ...`. Anchoring to that clause keeps a test name that prose merely mentions
  # out of the record. A reason with no such clause (a lint, a type, a shell suite, a collection failure) names no test, so
  # no base answer is asked for it.
  failed_ids() {
    printf '%s\n' "$1" \
      | sed -n 's/^.*pytest the base battery failed at //p' \
      | tr ' \t' '\n\n' \
      | sed -n 's/^\([^ ]*\.py::[^ ]*\)$/\1/p' \
      | LC_ALL=C sort -u
  }
  # base_fails <id>...: the named tests the base tip fails, one per line. Exit 0: every named test passes at the base tip;
  # 2: the base tip was measured, and the ids it named are the ones it fails (none named: it was told apart by the caller);
  # 1: the base tip was not measured (its ref does not resolve, the worktree cannot be made, the interpreter is not there, or
  # the probe ran no test).
  # The run happens in a detached worktree of the child's own repository at origin/<base>. SG_WAVE_BASE_PYTEST replaces the
  # command, which runs in that worktree and reads the ids in SG_WAVE_BASE_IDS. The default probe runs the gate's own
  # interpreter ($py: SG_GATE_PY, else the checkout's venv, else python3) through gate.sh, so it takes the same gate lock and
  # the same environment the child's gate ran under; a probe the lock, the slots or the bound keeps from running measured
  # nothing, and no base answer is claimed for it (#2272 review).
  base_fails() {
    local d lg rc
    [ -n "$py" ] || return 1
    d=$(mktemp -d "${TMPDIR:-/tmp}/wave-base.XXXXXX" 2>/dev/null) || return 1
    if ! $G worktree add -q --detach "$d" "origin/$base" >/dev/null 2>&1; then rm -rf "$d"; return 1; fi
    if [ -n "${SG_WAVE_BASE_PYTEST:-}" ]; then
      lg=$(cd "$d" && SG_WAVE_BASE_IDS="$*" bash -c "$SG_WAVE_BASE_PYTEST" 2>&1); rc=$?
    else
      lg=$(cd "$d" && SG_WAVE_BASE_PY=$py SG_WAVE_BASE_IDS="$*" SERIAL_TRY=1 SG_GATE_KIND=fast SG_GATE_STATUS=self \
             bash "$here/gate.sh" bash -c 'set -f; "$SG_WAVE_BASE_PY" -m pytest -p no:deepeval -q $SG_WAVE_BASE_IDS' 2>&1); rc=$?
    fi
    $G worktree remove --force "$d" >/dev/null 2>&1; rm -rf "$d"
    case "$rc" in 73|75) return 1;; esac   # the gate lock or its slots were held, or the bounded wait ran out
    if [ "$rc" = 4 ] && printf '%s\n' "$lg" | /usr/bin/grep -q '^gate: bounded: '; then return 1; fi
    [ "$rc" = 0 ] && return 0
    printf '%s\n' "$lg" | sed -n -e 's/^FAILED \([^ ]*\).*/\1/p' -e 's/^ERROR \([^ ]*::[^ ]*\).*/\1/p' | LC_ALL=C sort -u
    return 2
  }
  # base_verdict <id>...: one clause per named test, joined by "; ", saying whether the base tip fails it. The base runs once,
  # with every named test together, so the record answers each test without one run per test.
  base_verdict() {
    local reds src=0 id out=""
    [ $# -gt 0 ] || return 0
    reds=$(base_fails "$@") || src=$?
    case "$src" in
      1) printf 'the base tip was not measured'; return 0;;
      2) [ -n "$reds" ] || { printf 'the base tip was not measured'; return 0; };;
    esac
    for id in "$@"; do
      if printf '%s\n' "$reds" | /usr/bin/grep -qxF -- "$id"; then out="$out${out:+; }$id: the base tip fails it"
      else out="$out${out:+; }$id: the base tip passes it"; fi
    done
    printf '%s' "$out"
  }
  # rebase_on_base <red-clause> (#2272): one rebase of the child on the current base, so a gate that failed on a base the base
  # itself has fixed since is measured again on the newer tree (ADR-0164: a pass on a descendant that fixed the red releases it).
  # <red-clause> is the red_note text of the gate that failed, so a hold this rebase produces still names each failed test and
  # the base tip's own answer for it (AC5, AC6): a conflicting rebase holds the child, and the record says what the red was.
  # Exit 0: the child is on the current base. Exit 1: the rebase conflicts or fails, so the child is held where it stands
  # with its commits kept, and no gate runs on a tree the rebase did not settle.
  rebase_on_base() {
    local red=${1:-}
    $G fetch -q origin "+refs/heads/$base:refs/remotes/origin/$base" 2>/dev/null || {
      hold "the base $base is not on $remote, so the child cannot be rebased on it after a red gate${red:+; $red}"; return 1; }
    $G rev-parse -q --verify "origin/$base" >/dev/null 2>&1 || {
      hold "the base $base does not resolve after the fetch, so the child cannot be rebased on it after a red gate${red:+; $red}"; return 1; }
    if ! out=$($G rebase "origin/$base" 2>&1); then
      files=$($G diff --name-only --diff-filter=U | tr '\n' ' '); $G rebase --abort >/dev/null 2>&1
      if [ -n "$files" ]; then hold "the rebase on $base conflicts: ${files% }; its commits are kept, not requeued${red:+; $red}"
      else hold "the rebase on $base failed: $(printf '%s\n' "$out" | /usr/bin/grep -v '^[[:space:]]*$' | tail -1)${red:+; $red}"; fi
      return 1
    fi
    return 0
  }
  # red_note <reason>: the clause the entry record carries for a gate that failed: each failed test named, and the base tip's
  # own answer for it (#2272). At most SG_WAVE_RED_IDS_MAX ids are measured and named (20 by default), so one red record never
  # grows without bound; the ids past the cap are counted, never dropped silently. Prints nothing when the reason names no test.
  red_note() {
    local ids all n max vd more=""
    all=$(failed_ids "$1")
    [ -n "$all" ] || return 0
    max=${SG_WAVE_RED_IDS_MAX:-20}; case "$max" in ''|*[!0-9]*|0) max=20;; esac
    n=$(printf '%s\n' "$all" | /usr/bin/grep -c .)
    ids=$(printf '%s\n' "$all" | head -n "$max")
    [ "$n" -le "$max" ] || more=" and $((n - max)) more"
    vd=$(base_verdict $ids)
    printf 'failed tests: %s%s; %s' "$(printf '%s' "$ids" | tr '\n' ' ' | sed 's/ $//')" "$more" "$vd"
  }
  # note_first_red <reason> (#2272 review): the first red the entry sees, recorded in one place as its reason and the base
  # tip's answer for the tests it names. Both are set together, so a later branch that reads `firstrs` beside `redtxt` never
  # finds the reason without the base answer. Only the first red sets them; a red after the rebase never overwrites the
  # measurement of the tree the first gate ran on.
  note_first_red() {
    [ -z "$firstrs" ] || return 0
    firstrs=$1; redtxt=$(red_note "$1")
  }
  # The gates run in the child's own worktree, and only they open the entry (ADR-0145 rule 9). A child that fails them never
  # enters, and the wave CI is not what finds it: that one runs once, when the whole wave lands in `dev`.
  # They run under the machine-wide gate lock (#392), in try mode (#575): the fast lane runs the childcc pass, which makes the entry, in the
  # background, and a wait there would still hold the childcc lock, so a lock held by another gate holds the child out until a later pass, with no requeue.
  # The landing waits a bounded time instead (#955): a bounded wait in a detached pass never stops dispatch, because dispatch rides fastloop.
  gates() {
    # The gate's word decides (spec 1163, #1174, ADR-0164): pass admits the child, fail refuses it and requeues it, and unproven
    # admits it with its obligation. The word is read from the caller's copy gate.sh writes inside the lock; the exit code alone
    # never admits or refuses. A hold (the lock or its slots are taken, or a bounded wait ran out) is no outcome at all, so the
    # child is held, never requeued: the machine, not the child, gave no answer.
    local of oc rs ran notran fgm ph prc
    reuse="" skipped=""
    # gsha0 is the tree the first gate invocation measures. It is pinned here, before the gate loop, and never reassigned:
    # a later iteration runs after rebase_on_base rewrites the head, so a read inside the loop would name a tree the first
    # gate never ran on. The reviewed head (rsha) is read before rebase_on_tip, which also rewrites it, so it is no label
    # either (#2272 review). Both the first-gate label and the obligation read this same sha.
    gsha0=$($G rev-parse HEAD)
    # (ADR-0168, #1718) Before the machine lock: a green pass already kept for the tree the child enters with -- the same tree,
    # uncommitted state, base, merge-base and suite its final gate proved -- answers the entry as it stands. The child enters
    # with no second battery and, above all, with no wait behind another gate: a held lock holds no child whose final gate
    # proved this tree. Only the shipped fast gate answers the probe, and it answers only a green pass, so a red, a different
    # tree or suite, a tree the key cannot read, or a memo turned off answers no hit and the gate runs below as before.
    case "$SG_WAVE_GATES" in *"$here/fast-gate.sh"*) fgm=$here/fast-gate.sh;; *) fgm=;; esac
    # The entry's mode is on its gate-history line (#1798): `reused` when this probe answers, `gated` when the entry runs the
    # gate below, and `skipped` is the word of an entry that runs no battery at all. SG_GATE_ENTRY_MODE carries it into
    # gate.sh, the history file's one writer.
    if [ -n "$fgm" ]; then
      ph=$(SG_GATE_CALLER=entry SG_GATE_ENTRY_MODE=reused SG_GATE_CARD="$iss" SG_GATE_WAVE="$run" bash "$fgm" --memo-hit 2>&1); prc=$?
      [ $prc = 0 ] && { reuse=$(printf '%s\n' "$ph" | tail -1); return 0; }
    fi
    # (#1800, ADR-0166) With the entry gate off, a miss enters after the clean rebase with no battery, and the entry is recorded
    # as skipped: gate.sh writes the one history line, mode `skipped`, and the wave's landing gate proves every child as before.
    # `off` is the default (#1802); only the value `on` runs the battery, and the reuse probe above already answered a hit.
    if [ "${SG_WAVE_ENTRY_GATE:-off}" = off ]; then
      SG_GATE_CALLER=entry bash "$here/gate.sh" --skip fast "$($G rev-parse HEAD)" >/dev/null 2>&1
      skipped=1; return 0
    fi
    while :; do
      of=$(mktemp "${TMPDIR:-/tmp}/wave-outcome.XXXXXX" 2>/dev/null) || of=
      [ -z "$of" ] || : > "$of"
      # SG_GATE_CARD names the card this gate answers for: its attempt is journaled and projected to Gate Tries (#1255).
      # SG_GATE_WAVE names the wave the gate is for, so the wave's Gating fact reads it (#1257).
      out=$(GATE_OUTCOME_READ_FILE="$of" SERIAL_TRY=1 SG_GATE_CALLER=entry SG_GATE_ENTRY_MODE=gated SG_GATE_KIND=fast SG_GATE_CARD="$iss" SG_GATE_WAVE="$run" bash "$here/gate.sh" bash -c "$SG_WAVE_GATES" 2>&1); grc=$?
      oc= rs= ran= notran=
      [ -z "$of" ] || IFS=$'\t' read -r oc rs ran notran < "$of" 2>/dev/null
      [ -z "$of" ] || rm -f "$of"
      obl=""; gwhy=""; gnot=""
      if [ $grc = 73 ] && h=$(held_by "$out"); then
        case "$h" in slots:*) hold "the gate slots (caps.gates) are full, held by ${h#slots:}; the child enters on a later pass";;
          landing) hold "the landing's gate is queued ahead of this one (#1858); the child enters on a later pass";;
          *) hold "the gate lock is held by pid $h; the child enters on a later pass";; esac; return 1; fi
      [ $grc = 75 ] && waited_out "$out" && { hold "the local gates waited for the gate lock past their bound; the child enters on a later pass"; return 1; }
      case "$oc" in
        pass)
          # (#2272) A pass on the tree the rebase made follows a red the wave tip carried: the entry record names that red,
          # each test it reported, and the base tip's own answer for each, so a reader sees the base's red was not the child's.
          if [ -z "$prior" ] && [ -n "$firstrs" ]; then
            prior="the entry gate failed on the wave tip at ${gsha0:0:9}: $firstrs"
            [ -z "$redtxt" ] || prior="$prior; $redtxt"
            prior="$prior; the child was rebased on $base at ${rebsha:0:9} once, and the gate passed on the rebased tree"
          fi
          return 0;;
        unproven)
          if [ -z "$prior" ] && [ -n "$firstrs" ]; then
            prior="the entry gate failed on the wave tip at ${gsha0:0:9}: $firstrs"
            [ -z "$redtxt" ] || prior="$prior; $redtxt"
            prior="$prior; the child was rebased on $base at ${rebsha:0:9} once, and the gate carried its obligation from that tree"
          fi
          obl=$(obligation_of "$gsha0"); gwhy=$rs; gnot=$notran; return 0;;
        fail)
          # (#2272) A red at the entry may be the base's red, not the child's: the wave was cut from a base that has moved
          # since, and the fix for this red may be on the current base. One rebase on origin/<base> and one re-measure decide
          # it (ADR-0164: a pass on a descendant that fixed the red releases it). The record names each failed test and
          # whether the base tip fails it. A red on the rebased tip requeues the child, as before.
          local why rn line
          # The red of the first gate is measured once, with the base tip's answer for each test it named, before any rebase:
          # every later path (a pass, an unproven tree, a red again, or a rebase that conflicts and holds the child) carries
          # that same clause, so AC5 and AC6 hold whichever way the retry goes.
          note_first_red "$rs"
          if [ "$baseretried" = 0 ] && [ "${SG_WAVE_ENTRY_BASE_RETRY:-1}" != 0 ]; then
            baseretried=1; reuse=""; skipped=""
            rebase_on_base "$redtxt" || return 1
            rebsha=$($G rev-parse HEAD)
            continue
          fi
          # The gate's own word carries the failed tests (fast-gate names them in its reason); a bare `exit N` carries
          # nothing, so the gate's last line is read instead, as before.
          case "$rs" in ''|'-'|exit\ [0-9]*) line=$(printf '%s' "$out" | /usr/bin/grep -v '^$' | tail -1)
            why="the local gates failed: ${line:-${rs:-no answer}}";;
            *) why="the local gates failed: $rs";; esac
          # The rebased gate's red may name other tests than the first one did: its own answer is measured, and a reason
          # identical to the first reuses the clause already measured instead of probing the base twice.
          if [ "$rs" = "$firstrs" ]; then rn=$redtxt; else rn=$(red_note "$rs"); fi
          [ -z "$rn" ] || why="$why; $rn"
          [ -z "$firstrs" ] || [ "$firstrs" = "$rs" ] || why="$why; the first gate failed on the wave tip at ${gsha0:0:9}: $firstrs${redtxt:+; $redtxt}"
          [ -z "$rebsha" ] || why="$why; the child was rebased on $base at ${rebsha:0:9} once, and the gate ran again on that tree: red again"
          refuse "$why"; return 1;;
      esac
      # No word: a memo hit writes none and is always a pass (#955); fast-gate's 2 and 4 are its unproven codes (spec 1163).
      [ $grc = 0 ] && return 0
      case "$grc" in 2|4) obl=$(obligation_of "$gsha0"); gwhy=$rs; gnot=$notran; return 0;; esac
      hold "the local gates reported no outcome (exit $grc); the child enters on a later pass"; return 1
    done
  }
  rebase_on_tip || return $?
  gates || return 1
  local try=1 perr pline
  while :; do
    sha=$($G rev-parse HEAD)
    # The push's stderr is kept: it tells a rejection git names from a failure of the network, auth or server (#1348).
    if perr=$($G push -q origin "HEAD:refs/heads/$w" 2>&1); then
      board_write "Landing" "$iss"   # a refused write is named, never dropped (#593); the definition's column name (#2155)
      proof_card "$iss" "$obl"   # an unproven entry carries its fact on the card until the settlement answers it (#1176)
      # The run's entry record is the ledger's entered fact (#467, ADR-0149): the janitor's Landing reads it, never the
      # run's own land. An unproven entry carries its obligation there, so the job that settles it is attached to the run that
      # admitted the child (spec 1163, #1174). A pass carries none; gate.sh is the obligation's one writer.
      local cause="entered $w at $sha"
      # A reused gate is no silent skip (ADR-0168 rule 5): the entry's own record names the pass it read, so a reader sees the
      # child entered on its final gate and not on a battery this wave ran.
      [ -z "$reuse" ] || cause="$cause; the entry gate reused the child's final green verdict (tree, base, merge-base and suite: $reuse), so no battery ran"
      # An entry that ran no battery is no silent skip either (#1800): its record says the entry gate is off, and the wave's
      # landing gate is what proves the child.
      [ -z "$skipped" ] || cause="$cause; skipped: the entry gate is off (SG_WAVE_ENTRY_GATE=off), so no battery ran at the entry"
      # (#2272) An entry that passed only after the rebase carries the red it first met and the base tip's answer for each
      # test, so no reader takes the entry for a clean pass on the wave tip.
      [ -z "$prior" ] || cause="$cause; $prior"
      if [ -n "$obl" ]; then
        local oba och osc odl
        IFS=$'\t' read -r _ _ oba och osc odl < "$obl" 2>/dev/null
        cause="$cause; unproven: ${gwhy:--}; not run: ${gnot:--}; obligation $(basename "$obl") ($och at $osc, base $oba, deadline $odl)"
      fi
      wave_fact "$run" "$iss" entered "$cause"
      echo "WAVE $br $sha entered $w (#$iss)$([ -n "$obl" ] && printf ', unproven with obligation %s' "$(basename "$obl")")"; return 0
    fi
    pline=$(printf '%s\n' "$perr" | /usr/bin/grep -v '^[[:space:]]*$' | tail -1)
    # A rejection (the wave tip moved: git's non-fast-forward or fetch-first, or the remote's refusal of the ref) is the one refusal,
    # and it gets one repeat. Any other failure is a hold: the child keeps its commits and enters on a later pass, no requeue (#1348).
    printf '%s' "$perr" | /usr/bin/grep -q 'rejected\|non-fast-forward\|fetch first' \
      || { hold "the push to $w failed (git: ${pline:--}); the child enters on a later pass"; return 1; }
    [ "$try" = 1 ] || break
    try=2; rebase_on_tip || return $?
    gates || return 1   # the rebase made a new tree, and only the gates open the entry (#682): what enters is what they passed
  done
  refuse "the push to $w was refused: HEAD is not a fast-forward of its tip (git: ${pline:--})"; return 1
}

# A red check at the wave tip names the child that broke it (#377, ADR-0145 rule 11). The wave's check is its CI in
# production, which no run starts while the account's Actions billing is blocked (#439); the local check at the tip is the
# same rule and the one that runs today. `SG_WAVE_TIP_GATES` is that check, run in the wave's throwaway clone; the default is
# the fast gate over the one base battery (base-battery.sh) at the `wave-tip` stage, so its pytest is the delta the wave's
# commits reach, then the shell suites of what the wave changes, with the checkout's python, as branchland.sh runs it, the
# changed scripts read against origin/$SG_BASE. An empty value turns the
# local check off and leaves the wave's CI as its only check.
py=${SG_GATE_PY:-$SG_CHECKOUT/.venv/bin/python}; [ -x "$py" ] || py=python3
. "$here/base-battery.sh"
tipgate=${SG_WAVE_TIP_GATES-"$(sg_base_gate "origin/$base" "$py" wave-tip)"}

# child_of <clone> <sha>: the issue a commit names, its last `Refs #N`, empty when it names none.
child_of() { $G -C "$1" log -1 --format=%B "$2" 2>/dev/null | refs_in_message | tail -1; }

# held_by <output>: the pid named by serial.sh's try-mode skip line, and exit 0, when the output carries one (#575); a try-mode skip of
# the gate slots (gate.sh, caps.gates) is a hold as well and prints `slots:` and the holders it names (#567).
held_by() { printf '%s\n' "$1" | sed -n -e 's/^serial: lock gate held by pid \([0-9?]*\): skipped, not queued.*/\1/p' -e 's/^serial: lock gate: the landing.s gate is queued ahead: skipped, not queued.*/landing/p' -e 's/^caps: caps\.gates is full ([0-9]* slots), held by \(.*\): .* skipped, not queued (try)$/slots:\1/p' | head -1 | /usr/bin/grep .; }
# waited_out <output>: the bounded wait for the gate lock or the gate slots ran out (serial.sh and caps.py both exit 75 then, and
# name it). A 75 without that line is the check's own hold deadline, which is a failed check, not a hold (#955).
waited_out() { printf '%s\n' "$1" | /usr/bin/grep -qE '^serial: waited [0-9]+s for lock gate |^caps: .* waited [0-9.]+s for caps\.gates'; }

# child_pytest <clone> <from> <to> <issue> <log> (#1409): the Python test delta of one child's change, run against the wave tip in the
# clone, through gate.sh: under the gate lock and holding gate slots, never beside them. `SG_GATE_STATUS=self` keeps it out of the tip's red,
# status, history and wave journal: it only passes the command's exit code through. `SG_WAVE_CHILD_PYTEST` replaces the
# command (SG_WAVE_CHILD, _FROM and _TO are set); the default selects the files the child's commit reaches (squad.testselect, as
# the fast gate does), read in a detached tree at the child's commit, and runs them in the clone. An empty selection passes: the
# child changed nothing pytest covers. Exit 0: passed; 1: failed; 9: no verdict: the gate lock or slots were not free, so nothing
# ran, or the run was bounded and proved nothing (gate.sh exit 4, #1687). An unproven run is never the child's red, and its own
# output goes to <log> with the cause in `cpwhy`, so the landing that waits keeps the trace of why it waited (#1700).
child_pytest() {
  local S=$1 from=$2 to=$3 iss=$4 log=${5:-} cmd out grc
  cpwhy=
  if [ -n "${SG_WAVE_CHILD_PYTEST:-}" ]; then cmd=$SG_WAVE_CHILD_PYTEST
  else
    cmd='sel=$(d=$(mktemp -d) && /usr/bin/git worktree add -q --detach "$d" "$SG_WAVE_CHILD_TO" && (cd "$d" && "$SG_WAVE_PY" -m squad.testselect --stage wave-tip --base "$SG_WAVE_CHILD_FROM"); r=$?; /usr/bin/git worktree remove --force "$d" 2>/dev/null; exit $r) || sel="all: the selector failed"
case $sel in none:*|"") exit 0;; all:*) set --;; *) set -f; set -- $sel; set +f;; esac
"$SG_WAVE_PY" -m pytest -p no:deepeval -o heavy_cap_seconds=20 -n "${SQUAD_GATE_WORKERS:-2}" -m "not heavy" "$@"'
  fi
  out=$(cd "$S" && SG_WAVE_PY=$py SG_WAVE_CHILD=$iss SG_WAVE_CHILD_FROM=$from SG_WAVE_CHILD_TO=$to SERIAL_TRY=1 SG_GATE_KIND=fast SG_GATE_STATUS=self SERIAL_WAIT_SECONDS=${SERIAL_WAIT_SECONDS:-900} bash "$here/gate.sh" bash -c "$cmd" 2>&1); grc=$?
  # Each cause of a run that measured nothing names itself in `cpwhy`, so the landing that waits says the one it met (#1700).
  if [ $grc = 73 ] && held_by "$out" >/dev/null; then
    cpwhy="the gate lock or the gate slots were held by another gate"
    [ -z "$log" ] || printf '%s\n' "$out" >> "$log" 2>/dev/null; return 9; fi
  if [ $grc = 75 ] && waited_out "$out"; then
    cpwhy="the bounded wait for the gate lock ran out"
    [ -z "$log" ] || printf '%s\n' "$out" >> "$log" 2>/dev/null; return 9; fi
  if [ $grc = 4 ] && printf '%s\n' "$out" | /usr/bin/grep -q '^gate: bounded: '; then   # bounded: it measured no red (#1687); a bare exit 4 is the command's own red (#1699)
    cpwhy="the run was bounded and proved nothing"
    [ -z "$log" ] || printf '%s\n' "$out" >> "$log" 2>/dev/null; return 9; fi
  [ $grc = 0 ] && return 0
  printf '%s\n' "$out" | tail -5 >&2; return 1
}

# child_gate <clone> <from> <to> <issue> <log>: one child's own suite against the wave tip, run in the clone. `SG_WAVE_CHILD_GATES`
# replaces it (with SG_WAVE_CHILD the issue, SG_WAVE_CHILD_FROM/_TO the child's commit range); the default is the shell suite
# test-<script>.sh of every script the child's commit changes, as fast-gate.sh reads a changed script. Exit 0: it passed.
child_gate() {
  local S=$1 from=$2 to=$3 iss=$4 log=${5:-} n s hm rc=0
  if [ -n "${SG_WAVE_CHILD_GATES:-}" ]; then (cd "$S" && SG_WAVE_CHILD=$iss SG_WAVE_CHILD_FROM=$from SG_WAVE_CHILD_TO=$to bash -c "$SG_WAVE_CHILD_GATES"); return $?; fi
  for n in $($G -C "$S" diff --name-only --diff-filter=d "$from" "$to" 2>/dev/null | sed -n 's|^contrib/squad-goal/scripts/\(.*\)\.sh$|\1|p' | /usr/bin/grep -v / | sed 's|^test-||' | sort -u); do
    s="$S/contrib/squad-goal/scripts/test-$n.sh"; [ -f "$s" ] || continue
    hm=$(mktemp -d); (cd "$S" && env $(env | sed -n 's/^\(SG_[A-Za-z0-9_]*\)=.*/-u \1/p' | tr '\n' ' ') HOME="$hm" bash "$s") || rc=1; rm -rf "$hm"
  done
  [ $rc = 0 ] || return $rc
  child_pytest "$S" "$from" "$to" "$iss" "$log"
}

# wave_check <clone> <w>: the wave tip's check, and, when it is red, the child that broke it. Each child's own suite runs
# again against the tip, the last entrant first; the first child whose suite fails leaves the wave -- its commits are dropped
# from the tip and its issue goes back to the queue -- and the check runs again, so the wave without it can land. Exit 0:
# the tip's check is green (after any child left); 1: it stayed red, so the landing fails as before; 3: the check ran no
# verdict -- the gate lock or the gate slots are held, the bounded wait for them ran out, or a child's own check measured
# nothing -- and `waitwhy` names which, so the landing says the one cause it met and keeps the check's log (#1700); 5: the
# check is unproven (spec 1163, #1174), so the wave lands with its obligation and no child is blamed. The gate's word
# decides: pass and unproven admit, fail is the red path. 6 (#1687): the tip is red on a tree a gate passed before with the
# same suite, so the red may be the machine's and no child is blamed on it: the check runs again on a later pass, once per
# (wave, tip). A second red on that same tip takes the red path. 7 (#1961): the tip is red, no child names the cause, and the
# red is held: it was red twice on the same tree with the same failing tests, or the rerun named another set; a first red
# runs once more on the same tree in the same pass, and a green rerun of the same suite releases it (flake_release).
# 8 (#2209): the check evicted every child, so nothing is ahead of the base and no check runs on that tip: the landing
# closes the wave as an empty landing.
# passed_before <sha> <kind> <selection> <scope>: the gate history holds a pass for this tree and this suite (#1687, #1700):
# a full battery (scope full, an `all: <reason>` selection) whatever its kind, or a delta of the tip check's own kind over
# the tip check's own selection. A row that named no selection ran no battery, so it covers nothing.
passed_before() {
  [ -n "$3" ] && [ -n "$4" ] || return 1
  awk -F'\t' -v s="$1" -v k="$2" -v sel="$3" -v sc="$4" \
    '$3 == "pass" && $8 == "pass" && $4 == s && $6 != "" && (($7 == "full" && $6 ~ /^all: /) || ($2 == k && $7 == sc && $6 == sel)) { f = 1 } END { exit !f }' \
    "$SG_STATE/gate-history" 2>/dev/null
}
# tip_suite <sha> <rows before>: the kind, the selection and the scope of the tip check's own row -- the rows the check itself
# appended since <rows before> -- TAB separated; empty when the check wrote no row, which covers nothing (#1700).
tip_suite() {
  tail -n "+$(( ${2:-0} + 1 ))" "$SG_STATE/gate-history" 2>/dev/null \
    | awk -F'\t' -v s="$1" '$4 == s && $6 != "" { k = $2; sel = $6; sc = $7 } END { if (k != "") printf "%s\t%s\t%s", k, sel, sc }'
}
# tipred_file <wave>: the second-look marker (#1687, #1700). It names the one tip that spent the wave's second look, and it
# goes when the check settles and the wave lands or stops, so no marker outlives its tip or its wave.
tipred_file() { printf '%s/auto/tipred-%s\n' "$SG_STATE" "$(printf '%s' "$1" | tr / _)"; }
# The measured-red marker (#1961) sits beside the second-look marker: <marker>.red holds, one a line, the tip sha, the key of
# its failing-test set ("-" when the log names no test), the reds measured on that sha and set, and the kind, selection and
# scope of the check that measured them. <marker>.red.tests is the set and <marker>.red.lines the first red's failure lines,
# for the bug a green rerun files. A red that names no child runs once more on the same tree; a second red on the same tree
# and set runs no third check until the tip moves, and the landing counts it as a try (stuck), so the cap and the owner's
# alert apply. A green rerun of the same suite releases the red; a narrower or unproven rerun never does (ADR-0164).
tipred_clear() { local m; m=$(tipred_file "$1"); rm -f "$m" "$m.red" "$m.red.tests" "$m.red.lines"; }
# red_read <wave>: the measured-red marker into r_sha r_key r_n r_k r_sel r_sc; exit 1 when there is none.
red_read() {
  r_sha=; r_key=; r_n=0; r_k=; r_sel=; r_sc=
  local f; f="$(tipred_file "$1").red"; [ -f "$f" ] || return 1
  { IFS= read -r r_sha; IFS= read -r r_key; IFS= read -r r_n; IFS= read -r r_k; IFS= read -r r_sel; IFS= read -r r_sc; } 2>/dev/null < "$f"
  case $r_n in ''|*[!0-9]*) r_n=0;; esac
  [ -n "$r_sha" ]
}
# red_write <wave> <sha> <key> <reds> <kind> <selection> <scope>: the marker's one write; exit 1 when it did not land.
red_write() { local f; f="$(tipred_file "$1").red"; mkdir -p "$SG_STATE/auto" 2>/dev/null && printf '%s\n%s\n%s\n%s\n%s\n%s\n' "$2" "$3" "$4" "$5" "$6" "$7" > "$f" 2>/dev/null; }
# red_set <log>: the failing tests a check log names, sorted, one a line: pytest's FAILED and ERROR node ids, a shell suite's FAIL lines.
red_set() { awk '/^(FAILED|ERROR) /{ print $2; next } /^FAIL /{ sub(/^FAIL /, ""); print }' "$1" 2>/dev/null | sort -u; }
# set_key <set>: the key of a failing-test set; "-" for an empty set, which counts as the same set as any other.
set_key() { [ -n "$1" ] || { echo -; return 0; }; printf '%s\n' "$1" | cksum | awk '{ print $1 }'; }
# flake_release <wave> <sha>: a green rerun of the same suite released the red on this tree. The release is named and kept
# in wave-flake.tsv, and the flaky check is filed as one bug through the loop's one filing door (autohunt.sh --file), once
# per failing-test set: a set already filed is named, never filed again.
flake_release() {
  local w=$1 sha=$2 m fp tests lines body out first more
  m=$(tipred_file "$w")
  tests=$(cat "$m.red.tests" 2>/dev/null); fp=$r_key; [ "$fp" != - ] || fp="tree-$sha"
  echo "WAVE $w: the tip's check was red on $sha and green on its rerun: released by the green run on the same tree"
  printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$w" "$sha" "$fp" >> "$SG_STATE/wave-flake.tsv" 2>/dev/null
  if [ -e "$SG_STATE/auto/flake-filed-$fp" ]; then echo "WAVE-FLAKE $w: the flaky check (${tests:-no test named}) is already filed as a bug"; return 0; fi
  first=$(printf '%s\n' "$tests" | head -1); more=$(printf '%s\n' "$tests" | /usr/bin/grep -c .)
  [ "$more" -gt 1 ] 2>/dev/null && first="$first and $((more - 1)) more"
  body=$(mktemp) || return 0
  lines=$(cat "$m.red.lines" 2>/dev/null)
  printf 'The check at the tip of %s was red on tree %s and green when it ran again on the same tree with the same suite, so the landing released the red and the wave landed. The failing tests are flaky.\n\nFailing tests:\n```\n%s\n```\n\nFailure lines of the red run:\n```\n%s\n```\n' \
    "$w" "$sha" "${tests:-none named by the check log}" "${lines:-none kept}" > "$body"
  out=$(SG_FILED_BY=wave SG_FILED_LABELS="${SG_FRONTIER_LABEL:-ready-for-agent}" bash "$here/autohunt.sh" --file "flaky test: ${first:-the tip check of a wave} is red, then green on the same tree" "$body" --fingerprint "$fp" 2>&1)
  rm -f "$body"
  case "$out" in *"HUNT filed"*|*"HUNT dup"*) : > "$SG_STATE/auto/flake-filed-$fp" 2>/dev/null; echo "WAVE-FLAKE $w: the flaky check is filed as a bug: $out";;
    *) echo "WAVE-FLAKE $w: the bug for the flaky check was not filed: $out";; esac
  return 0
}
# evicted_before <issue> <run> <sha>: the run already evicted the issue's child at this same commit. The run's marker holds each
# commit it evicted (#2216), so a ledger that answers floor or refuses the requeue fact repeats nothing. The requeue fact
# names the last head (#2209). A child that entered again carries another commit, so its eviction is not yet written.
evicted_before() {
  [ -n "$3" ] || return 1
  /usr/bin/grep -qx "$3" "$SG_STATE/auto/evicted-$2-$1" 2>/dev/null && return 0
  ledger read "$1" 2>/dev/null | python3 -c 'import json, sys
d = json.load(sys.stdin); run, sha = sys.argv[1], sys.argv[2]
q = (d.get("requeued") or {}).get(run) or {}
sys.exit(0 if sha and q.get("head") == sha else 1)' "$2" "$3" 2>/dev/null
}
# evicted_mark <issue> <run> <sha>: the run's marker of one eviction, written whatever the ledger answers (#2216).
evicted_mark() { mkdir -p "$SG_STATE/auto" 2>/dev/null && printf '%s\n' "$3" >> "$SG_STATE/auto/evicted-$2-$1"; }
# drop_evicted <clone> <run>: removes from the clone's tip each child commit the run already evicted (#2216), newest first, and
# prints the issues it removed. A tip with no child left reads as an empty landing, so no tip check runs on it.
drop_evicted() {
  local S=$1 run=$2 sha cx
  while read -r sha; do
    [ -n "$sha" ] || continue
    cx=$(child_of "$S" "$sha")
    evicted_before "$cx" "$run" "$sha" || continue
    $G -C "$S" rebase --onto "$sha^" "$sha" >/dev/null 2>&1 || { $G -C "$S" rebase --abort >/dev/null 2>&1; return 1; }
    printf '%s ' "$cx"
  done < <($G -C "$S" log --format=%H "origin/$base..HEAD" 2>/dev/null)
}
wave_check() {
  local S=$1 w=$2 rc crc found cx clog oc of hsha mk look=1 tk tsel tsc hb hid known rset rkey
  waitwhy=   # the one cause of exit 3, read by the landing (#1700)
  [ -n "$tipgate" ] || return 0
  mk=$(tipred_file "$w")
  # Two reds on this tree and set (#1961): no third check until the tip moves, and the landing counts the hold.
  hsha=$($G -C "$S" rev-parse HEAD 2>/dev/null)
  if [ -n "$hsha" ] && red_read "$w" && [ "$r_sha" = "$hsha" ] && [ "$r_n" -ge 2 ]; then
    echo "WAVE CHECK RED $w: the tip's check was red twice on the same tree with the same failing tests ($(tr '\n' ' ' < "$mk.red.tests" 2>/dev/null)); held until the tip moves: no third check runs on this tree"
    return 7
  fi
  gating "$S" "origin/$base..HEAD" "$run"   # the gate waits for a slot or runs from here, so its cards stand in Gating (ADR-0170)
  of=$(mktemp "${TMPDIR:-/tmp}/wave-tip-outcome.XXXXXX" 2>/dev/null) || of=
  while :; do
    clog="$SG_STATE/wave-check-$(printf '%s' "$w" | tr / _).log"   # the check's own output, kept: a red that names no cause has to name its log (#447)
    hsha=$($G -C "$S" rev-parse HEAD 2>/dev/null)
    known=; red_read "$w" && [ -n "$hsha" ] && [ "$r_sha" = "$hsha" ] && [ "$r_n" -ge 1 ] && known=1   # a red already measured on this tree
    [ -z "$of" ] || : > "$of"
    # The check waits for the gate lock a bounded time (SERIAL_WAIT_SECONDS, 900 s): a try that gave up on its first look lost the
    # cycle, and the wave waited a whole cycle for each retry (#946 lost three cycles). The landing runs in its own detached pass
    # (watch.sh), so this wait holds no watch cycle, and dispatch rides fastloop, which never runs a landing (#955).
    # The check writes its word to $of: GATE_OUTCOME_READ_FILE is the copy gate.sh makes inside the lock, and GATE_OUTCOME_FILE
    # is the file a raw tip check writes, as the entry's gate does.
    # Each gate round marks the held landing lock (#1290): health.sh reads the last mark as the landing's progress, so a landing
    # that runs several rounds (a red tip, the child that broke it leaves, the tip again) is busy, not held past its bounds.
    touch "$SG_STATE/locks/single-wland/round" 2>/dev/null
    hb=0; [ ! -f "$SG_STATE/gate-history" ] || hb=$(wc -l < "$SG_STATE/gate-history" 2>/dev/null); hb=${hb// /}
    # SG_SERIAL_PRIORITY=1 (#1858): the tip check is the landing's gate, the delivery that ends the run, so the gate lock queues
    # it ahead of every child's fast gate that waits for the same lock (ADR-0164). The wait it still spends in the queue is gate
    # wait, named in its log, and it never counts as run time: the gate's own clock starts when it holds the lock and its slots.
    (cd "$S" && export SERIAL_WAIT_SECONDS=${SERIAL_WAIT_SECONDS:-900} SG_SERIAL_PRIORITY=1 GATE_OUTCOME_FILE="$of" GATE_OUTCOME_READ_FILE="$of" SG_GATE_WAVE="${w#wave/}" SG_GATE_CALLER=tip && eval "$tipgate") >"$clog" 2>&1; rc=$?
    oc=
    [ -z "$of" ] || IFS=$'\t' read -r oc _ _ _ < "$of" 2>/dev/null
    # 3: the check ran no verdict, and the log stays: the landing that waits has to leave the cause and its trace for the
    # next pass (#1700).
    if [ $rc = 73 ] && hid=$(held_by "$(cat "$clog")"); then
      case "$hid" in
        slots:*) waitwhy="the tip's check did not run: the gate slots are held by ${hid#slots:}";;
        *) waitwhy="the tip's check did not run: the gate lock is held by pid $hid";; esac
      rm -f "$of"; return 3
    fi
    [ $rc = 75 ] && waited_out "$(cat "$clog")" && { waitwhy="the tip's check did not run: the bounded wait for the gate lock ran out"; rm -f "$of"; return 3; }
    case "$oc" in pass|unproven|fail) ;; *) oc=; [ $rc = 0 ] && oc=pass; [ $rc = 4 ] && oc=unproven;; esac   # a raw check that names no word: exit 0 passes, 4 keeps the old inconclusive code (#699)
    # A red already measured on this tree (#1961) is released only by a green run of the same suite: an unproven run never
    # releases it, and neither does a narrower one (another selection or scope that is not a full battery). Either waits.
    if [ -n "$known" ]; then
      case "$oc" in
        unproven) waitwhy="the rerun of the tip's check on the tree that was red is unproven, and an unproven run never releases a red"; rm -f "$of"; return 3;;
        pass) IFS=$'\t' read -r tk tsel tsc < <(tip_suite "$hsha" "$hb")
          if [ -n "$r_k$r_sel$r_sc" ] && [ -n "$tk$tsel$tsc" ] && [ "$tk|$tsel|$tsc" != "$r_k|$r_sel|$r_sc" ] && { [ "$tsc" != full ] || [ "${tsel#all: }" = "$tsel" ]; }; then
            waitwhy="the rerun of the tip's check ran another suite ($tk, ${tsel:-no selection}, ${tsc:-no scope}) than the red ($r_k, ${r_sel:-no selection}, ${r_sc:-no scope}), and a narrower run never releases a red"; rm -f "$of"; return 3
          fi
          flake_release "$w" "$hsha"; rm -f "$clog" "$of" "$mk"; tipred_clear "$w"; return 0;;
      esac
    fi
    case "$oc" in
      pass) rm -f "$clog" "$of" "$mk"; return 0;;
      unproven) rm -f "$of" "$mk"; return 5;;   # the wave waits and the check runs again (#1802); the log stays, as it does for a red
      *) : ;;                             # fail, and any other exit: the red path below
    esac
    # Only before a child left in this pass: one marker cannot follow two tips (#1699). The pass row must name the tip check's
    # own kind, selection and scope, and the marker's write must land, or the next pass answers the same second look again and
    # the wave never lands (#1700).
    if [ -z "$known" ] && [ $look = 1 ] && [ -n "$hsha" ] && [ "$(cat "$mk" 2>/dev/null)" != "$hsha" ]; then
      IFS=$'\t' read -r tk tsel tsc < <(tip_suite "$hsha" "$hb")
      if passed_before "$hsha" "$tk" "$tsel" "$tsc"; then
        if mkdir -p "$SG_STATE/auto" 2>/dev/null && printf '%s\n' "$hsha" > "$mk" 2>/dev/null; then rm -f "$of"; return 6; fi
        echo "WAVE-NOTE $w: the second-look marker $mk cannot be written, so the red takes the usual path"
      fi
    fi
    found=""; [ -n "$known" ] || while read -r sha; do
      [ -n "$sha" ] || continue
      touch "$SG_STATE/locks/single-wland/round" 2>/dev/null   # each child's own suite is a gate round too (#1290)
      child_gate "$S" "$sha^" "$sha" "$(child_of "$S" "$sha")" "$clog"; crc=$?
      # The child's own check ran no verdict -- the gate lock or slots were busy, or it was bounded: nothing measured the tip,
      # no child is blamed (#1409, #1687), and its own output stays in the check's log (#1700).
      [ $crc = 9 ] && { waitwhy="the tip's check has no verdict: the own check of #$(child_of "$S" "$sha") measured nothing (${cpwhy:-no verdict})"; rm -f "$of"; return 3; }
      [ $crc = 0 ] || { found=$sha; break; }
    done < <($G -C "$S" log --format=%H "origin/$base..HEAD" 2>/dev/null)   # newest first: the last entrant is tried first
    # No child names the cause: the red is the tree's, measured, and never the loop's (#1961). A second look the tip already
    # spent (#1687) is a red on this tree that named no test, which counts as the same set.
    if [ -z "$found" ]; then
      rm -f "$of"
      [ -n "$known" ] || { [ -n "$hsha" ] && [ "$(cat "$mk" 2>/dev/null)" = "$hsha" ] && { known=1; r_key=-; r_n=1; r_k=; r_sel=; r_sc=; }; }
      rset=$(red_set "$clog"); rkey=$(set_key "$rset")
      IFS=$'\t' read -r tk tsel tsc < <(tip_suite "$hsha" "$hb")
      if [ -n "$known" ] && { [ "$rkey" = "$r_key" ] || [ "$rkey" = - ] || [ "$r_key" = - ]; }; then
        [ "$rkey" != - ] || rkey=$r_key
        red_write "$w" "$hsha" "$rkey" 2 "${r_k:-$tk}" "${r_sel:-$tsel}" "${r_sc:-$tsc}" || echo "WAVE-NOTE $w: the measured-red marker cannot be written, so the next pass runs the check again"
        [ -z "$rset" ] || printf '%s\n' "$rset" > "$mk.red.tests" 2>/dev/null
        echo "WAVE CHECK RED $w: the tip's check was red twice on the same tree with the same failing tests ($(printf '%s' "${rset:-$(cat "$mk.red.tests" 2>/dev/null)}" | tr '\n' ' ')); held until the tip moves: no third check runs on this tree (the check's own output is $clog)"
        return 7
      fi
      printf '%s\n' "$rset" > "$mk.red.tests" 2>/dev/null
      { /usr/bin/grep -E '^(FAILED|ERROR|FAIL) |^E   ' "$clog" | head -60 | /usr/bin/grep . || tail -40 "$clog"; } > "$mk.red.lines" 2>/dev/null
      if ! red_write "$w" "$hsha" "$rkey" 1 "$tk" "$tsel" "$tsc"; then
        echo "WAVE CHECK RED $w: the tip's check is red, no child names the cause, and the measured-red marker cannot be written, so the red is held (the check's own output is $clog)"
        return 7
      fi
      if [ -n "$known" ]; then   # the same tree, another failing set: one more run, on a later pass
        echo "WAVE CHECK RED $w: the tip's check is red again on the same tree with other failing tests ($(printf '%s' "$rset" | tr '\n' ' ')); held, and the next pass runs it once more (the check's own output is $clog)"
        return 7
      fi
      echo "WAVE CHECK RED $w: the tip's check is red ($(printf '%s' "${rset:-no test named}" | tr '\n' ' ')) and no child's own check names the cause: it runs once more on the same tree"
      continue
    fi
    cx=$(child_of "$S" "$found")
    $G -C "$S" rebase --onto "$found^" "$found" >/dev/null 2>&1 || { $G -C "$S" rebase --abort >/dev/null 2>&1; rm -f "$of"
      echo "WAVE CHECK RED $w: #$cx broke the wave tip and cannot leave it (a rebase conflict): the wave does not land"; return 1; }
    # (#2209) One eviction of a child is one refused fact and one requeue per run. The eviction lives in the scratch clone
    # only, so a pass that pushes nothing leaves the commit on the remote wave and the next pass evicts it again: the
    # requeue fact of the issue's ledger, the read the empty landing uses, says the run already sent it back. The commit
    # is dropped and named each time; the fact, the card move and the comment are written once. A child that entered the
    # wave again carries another commit, so it is another eviction, and it is requeued.
    if evicted_before "$cx" "$run" "$found"; then
      echo "WAVE CHECK RED $w: #$cx broke the wave tip; it leaves the wave, and this run already put its issue back in the queue"
    else
      wave_fact "$run" "$cx" refused "the check at the wave tip was red: #$cx broke it, so the child left the wave"   # land's run (#682)
      requeue "$cx" "the check at the wave tip was red: #$cx broke it, so the child left the wave"
      ledger requeue "$cx" "$run" "$found" >/dev/null 2>&1
      evicted_mark "$cx" "$run" "$found"
      echo "WAVE CHECK RED $w: #$cx broke the wave tip; it leaves the wave and its issue is back in the queue"
    fi
    dropped="${dropped:+$dropped }$cx"; look=0   # land keeps the set: the tree it pushes lacks this child's commits (#1416)
    # (#2209) Nothing is left ahead of the base: no gate runs on a tip that equals the base. 8 sends the landing back to
    # its empty-landing close, which deletes the wave branch, so the evicted commit does not return on the next pass.
    [ "$($G -C "$S" rev-list --count "origin/$base..HEAD" 2>/dev/null)" != 0 ] || { rm -f "$of"; return 8; }
  done
}

# refs_of <clone> <range>: every `Refs #N` in the whole message of each commit of the range, each named once.
refs_of() { $G -C "$1" log --format=%B "$2" 2>/dev/null | refs_in_message | sort -un | tr '\n' ' '; }

# TEST_RE is the test notion of check-tdd-red.sh, with the same default and the same SG_TDD_TEST_RE override, so a RED commit
# is test-only by the paths the TDD guard reads as tests (#1474). Keep it in step with check-tdd-red.sh.
TEST_RE=${SG_TDD_TEST_RE:-'(^|/)tests?/[^/]+\.(py|sh|js|mjs|ts|tsx|cs|rs|go|rb|java|kt|php|c|cc|cpp)$|(^|/)test[_.-][^/]+\.(py|sh|js|mjs|ts|tsx|cs|rs|go|rb|java|kt|php|c|cc|cpp)$|(^|/)[^/]+[_.-]tests?\.(py|sh|js|mjs|ts|tsx|cs|rs|go|rb|java|kt|php|c|cc|cpp)$'}

# is_test_path <path>: 0 when the path is test-only: a path under tests/ at any depth, a path TEST_RE reads as a test, and the
# fixtures/ paths, conftest.py and pytest.ini that the suites carry beside their tests (#1474).
is_test_path() {
  case "$1" in tests/*|*/tests/*|fixtures/*|*/fixtures/*|conftest.py|*/conftest.py|pytest.ini|*/pytest.ini) return 0;; esac
  printf '%s' "$1" | /usr/bin/grep -qE "$TEST_RE" && return 0
  return 1
}

# is_code_path <path>: 0 when the path is code, neither a test (is_test_path) nor a `.md` note, which check-tdd-red.sh skips as
# no code of a squash (#1474).
is_code_path() {
  case "$1" in *.md) return 1;; esac
  is_test_path "$1" && return 1
  return 0
}

# red_test_only <clone> <sha> (#1474): 0 when every path the commit changes is a test or a `.md` note. A `TDD RED` commit of that
# kind is a red test alone, a test and no fix (#533), and delivers nothing. A `TDD RED` commit that also changes code is a squashed
# red and fix.
red_test_only() {
  local f
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    is_code_path "$f" && return 1
  done <<< "$($G -C "$1" diff-tree --no-commit-id --name-only -r --root "$2" 2>/dev/null)"
  return 0
}

# commit_refs <clone> <range> (#869, #1474): one line per commit of the range that names an issue. `D <sha> <n>`: the commit
# names exactly one issue (each `Refs #N` counted once), so it delivers that issue. `A <sha> <n> ...`: it names several, and a
# cluster child that wrote one commit for its block did not say which issue the commit fixed, so it delivers none of them.
# A `TDD RED` commit delivers by the same rule, unless it changes test-only paths (red_test_only): a squashed red and fix is a
# delivery (#1474).
commit_refs() {
  local c msg first refs list count
  for c in $($G -C "$1" rev-list "$2" 2>/dev/null); do
    msg=$($G -C "$1" log -1 --format=%B "$c" 2>/dev/null)
    first=$(printf '%s\n' "$msg" | awk 'NF { print; exit }')
    case "$first" in "TDD RED"*) red_test_only "$1" "$c" && continue;; esac
    list=$(printf '%s\n' "$msg" | refs_in_message | awk '!seen[$0]++' | tr '\n' ' ')
    count=$(printf '%s' "$list" | wc -w | tr -d ' ')
    if [ "$count" = 1 ]; then echo "D $c ${list% }"
    elif [ "$count" -gt 1 ]; then echo "A $c ${list% }"; fi
  done
}

# netfail <what> <stderr>: a fetch or a push that failed on the transport -- the network (DNS, connect, timeout, a 5xx
# answer, a TLS handshake) or a machine that could not start the process doing it (a full process table, no memory, no file
# handles, no disk) -- is no cause a repeat does not change: the next cycle can land (#656, #1710). A local fetch forks
# git-upload-pack, so a loaded machine fails one exactly as a network failure does, and the fixture holds its own repository
# already: no retry here, only no blame on the run. It prints one WAVE-NETWORK line with the last line that names the cause
# and returns 0; the caller then exits 2 with no call to stuck, so no try is counted and nothing is frozen. Any other
# failure returns 1, and the caller calls stuck as before, which now carries git's own line.
# The match and the pick are bash's own (`[[ =~ ]]` and a read loop), never a helper command: the machine that refuses the
# transport's fork refuses `grep` and `tail` the same way, so a reader that forked would drop the cause exactly when it
# happens. Case L did, and the landing took the stuck path with a bare `failed:` (#1710).
WAVE_NET_RE='Could not resolve host|Failed to connect|Connection (timed out|refused|reset)|Operation timed out|Network is unreachable|The requested URL returned error: 5[0-9][0-9]|SSL_|SSL connect|TLS handshake|gnutls_handshake|cannot fork|unable to fork|fork failed|Resource temporarily unavailable|Cannot allocate memory|Too many open files|No space left on device'
netfail() {
  local l="" line
  while IFS= read -r line; do [[ $line =~ $WAVE_NET_RE ]] && l=$line; done <<< "$2"
  [ -n "$l" ] || return 1
  echo "WAVE-NETWORK $1: $l; it lands on a later cycle"
}

# run_holds_slot <run folder>: the run is listed in active-runs.txt and its last verdict follows its last start or resume (ADR-0167).
run_holds_slot() {
  local n=${1##*/} l
  while read -r l; do
    [ "${l##*/}" = "$n" ] || continue
    if awk '/"event": *"(run_start|resume)"/{v=""} /"event": *"verdict"/{v=$0} END{exit (v == "")}' "$1/record.jsonl" 2>/dev/null; then return 1; fi
    return 0
  done < "$SG_STATE/active-runs.txt" 2>/dev/null
  return 1
}

# absent_wave <run> <wave> <clone> <run folder> <fetch output> (#1310): a wave branch that is not on the remote was never pushed,
# so no later cycle can land it. The landing names that cause. A run that holds no slot stops at once; a live run counts the try.
# Returns 1 when the fetch failed for another cause.
absent_wave() {
  local run=$1 w=$2 S=$3 rd=$4 fo=$5 now=""
  [[ $fo == *"couldn't find remote ref"* ]] || return 1   # bash's own match, for netfail's reason (#1710)
  run_holds_slot "$rd" || now=1
  stuck "$run" "$w" "$S" "$w is not on $remote: the run never pushed it, so there is no branch to land (the run cuts it from $base at its start)" "$now"
  return 0
}

# stuck <run> <wave> <clone> <why> [now]: a landing that failed on a cause a repeat does not change. The attempts are ledger facts
# (ADR-0149): each issue the wave carries (its `Refs #N` ahead of the base) or the run was dispatched for (`run_issues` of the
# caller's run folder $rd) counts the try in its ledger (`issue_ledger.py landtry`), and the run's count is the largest. At
# SG_WAVE_LAND_TRIES (default 3) the pass stops retrying: each issue the wave carries gets the label frozen:owner, its card
# goes to Todo (#657), one comment is queued, and the run's wave marker goes, so the cycle stops. A wave that names no issue
# has no ledger to count in and holds no issue, so its first failure clears the marker. The wave branch stays on the remote
# with the work, when it is there. A cause the loop itself caused (#663) is no work failure: each issue is named once as
# WAVE-LOOP-CAUSED and counts no try, the wave stays, and the normal cap handles the issue. Returns 1 either way: the
# landing did not happen.
stuck() {
  local run=$1 w=$2 S=$3 why=$4 now=${5:-} t=0 k n named="" all held
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
  [ -n "$now" ] || [ "$t" -ge "${SG_WAVE_LAND_TRIES:-3}" ] || return 1
  case "$why" in "$w is not on"*) held="$w is not on the remote";; *) held="$w stays on the remote";; esac
  export SG_GH_PASS=wave
  for n in $refs; do
    gh_send -X POST "repos/$SG_REPO/issues/$n/labels" -f "labels[]=frozen:owner" >/dev/null 2>&1
    SG_MOVE_REASON=no-landing SG_MOVE_EVENT="$run" SG_MOVE_SHA="$(reset_sha "$run")" board_write "${SG_TODO:-Todo}" "$n"   # nothing landed, so the card leaves Landing (#657, #1340); frozen:owner holds it out of the frontier
    mkdir -p "$SG_STATE/cq"
    printf 'Needs the owner: the landing of %s in %s failed %s times and stopped (%s). The work stays on %s. Refs #%s\n' "$w" "$base" "$t" "$why" "$w" "$n" > "$(sg_cq_file "$n-wavestuck$$.md")"
  done
  rm -f "$SG_STATE/auto/wave-$run" "$SG_STATE/auto/wave-start-$run" "$SG_STATE/auto/wave-pause-$run"; tipred_clear "$w"   # a wave that stopped for good leaves no tip to check again (#1700)
  if [ -n "${all// /}" ]; then echo "WAVE NEEDS-HUMAN $w: the landing failed $t times; the run's wave marker is cleared and $held"
  else echo "WAVE NEEDS-HUMAN $w: the landing failed and the wave names no issue; the run's wave marker is cleared and $held"; fi
  return 1
}

# deliver <run> <wave> <sha> <issues> [<tip obligation>]: the closes of what a landed wave carries, each with its comment,
# ledger row and card. A wave whose tip check was unproven carries that obligation's fact on every issue it delivers
# (#1176); an issue whose own entry was unproven already carries it, and the settlement answers either.
# An issue that does not close stays owed (#475): its ledger fact is `owed`, with the wave and the sha (ADR-0149), the run
# keeps its wave marker, and the pass exits 1, so the run is not read as done and the next landing pass tries the close again.
deliver() {
  local run=$1 w=$2 sha=$3 issues=$4 tipob=${5:-} n owed="" out
  export SG_GH_PASS=wave
  local col=Done; [ "${SG_BOARD_VOCAB:-}" = shipped ] && col=Closed
  for n in $issues; do
    [ -z "$tipob" ] || proof_card "$n" "$tipob"   # the landing itself was unproven: the card carries its obligation
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

# file_conflict_bug <issue> <run> <wave> <commit> <paths> (#1061): the second identical conflict of an issue files one loop bug. The
# landing path is the defect, so the bug carries `loop-bug` beside `bug` and is worked outside the loop (ADR-0146).
file_conflict_bug() {
  local n=$1 run=$2 w=$3 at=$4 paths=$5 body out rc
  body=$(mktemp) || return 0
  printf 'Wave %s conflicts with %s at %s in %s. Run %s is the second run to hit this conflict on this base, so the loop stopped requeuing #%s.\n\nFiled by wave.sh (ADR-0151 rule 2).\n' \
    "$w" "$base" "$at" "${paths:-no path named by git}" "$run" "$n" > "$body"
  # The loop's one filing door (#1978): the publish hold, the card in Todo and the birth Priority urgent of a loop-bug.
  out=$(SG_FILED_BY=wave SG_FILED_LABELS=loop-bug bash "$here/autohunt.sh" --file \
    "[SQUAD] wave rebase conflict on #$n: ${paths:-no path named by git}" "$body" 2>&1)
  rm -f "$body"
  case "$out" in *"HUNT filed"*) echo "WAVE-BUG #$n: the same rebase conflict on $base twice; filed as a loop bug";;
                 *) echo "WAVE-BUG #$n filing refused: $out";; esac
}

# The landing round (#1500). A landing conflict keeps the wave head on fix/<n>-land-<run>, <n> the lowest issue the wave head carries
# of the run's dispatch set. The marker auto/kept-fix_<n>-land-<run> keeps the bare base ref on line 1 (every #1344 reader stays
# as it is), then one `issue <n>` line for each issue it closes and one `round <k> <base sha>` line for each round that started.
# land_marker <run>: the kept landing marker of the run, or exit 1.
land_marker() { local f; for f in "$SG_STATE"/auto/kept-fix_*-land-"$1"; do [ -s "$f" ] && { printf '%s\n' "$f"; return 0; }; done; return 1; }
# remote_git <git args>: git outside any checkout, for a push that needs no object (a branch delete).
remote_git() { local t rc; t=$(mktemp -d) || return 1; $G -C "$t" init -q . >/dev/null 2>&1; $G -C "$t" push -q "$remote" "$@" >/dev/null 2>&1; rc=$?; rm -rf "$t"; return $rc; }
# round_review <review key> <branch head> <files>: the CHANGES review that asks the one rebase round, read at the kept head.
round_review() {
  python3 "$here/revformat.py" --round-review "$base" "origin/$base" "${3:-}" > "$SG_STATE/review-$1.md"   # the one writer, in the shape the lander reads (#1960)
  printf '%s\n' "$2" > "$SG_STATE/review-$1.sha"; rm -f "$SG_STATE/review-$1.base"
  printf '%s\n' "$2" > "$SG_STATE/landround-$1.head"   # the kept head this round started at: the lander reads a head that moved as a round that pushed
}
# land_round <run> <marker> <base sha> <files> <at>: a conflict on a kept landing branch. Checked in order: the base sha of the last
# round again means the round did not resolve the conflict (the loop bug, whatever the round count); a moved base starts the next
# round; after round two the issue lines are requeued as a fresh build with rework, and the marker and both branches go. Returns 1.
land_round() {
  local run=$1 m=$2 bsha=$3 files=$4 at=${5:-unknown} key kb w issues n rounds last head
  key=$(basename "$m"); key=${key#kept-}; kb=fix/${key#fix_}; w=$(wave_of "$run")
  issues=$(sed -n 's/^issue //p' "$m"); rounds=$(/usr/bin/grep -c '^round ' "$m"); last=$(/usr/bin/grep '^round ' "$m" | tail -1 | awk '{print $3}')
  if [ -n "$last" ] && [ "$bsha" = "$last" ]; then
    echo "WAVE CONFLICT $kb: the rebase on $base conflicts again at the same base ${bsha:0:9}; round $rounds did not resolve it"
    for n in $issues; do
      file_conflict_bug "$n" "$run" "$kb" "$at" "$files"
      c=2; stop "$n" "the rebase round on $kb left the same conflict on $base at ${bsha:0:9}: $kb cannot land"
    done
    return 1
  fi
  if [ "$rounds" -ge 2 ]; then
    echo "WAVE CONFLICT $kb: the rebase on $base conflicts after round $rounds; ${issues//$'\n'/, } requeued as a fresh build"
    for n in $issues; do
      requeue "$n" "the rebase on $base conflicts in ${files:-no path named by git} after $rounds rebase rounds: $kb cannot land"
      gh_send -X POST "repos/$SG_REPO/issues/$n/labels" -f "labels[]=rework" >/dev/null 2>&1 || echo "WAVE-LABEL #$n rework refused"
    done
    rm -f "$m" "$SG_STATE/review-${key}".* "$SG_STATE/landround-$key.head"
    remote_git --delete "$kb" "$w" || echo "WAVE-NOTE the delete of $kb and $w from $remote was refused"
    return 1
  fi
  head=$($G ls-remote "$remote" "refs/heads/$kb" 2>/dev/null | awk '{print $1}')
  printf 'round %s %s\n' "$((rounds + 1))" "$bsha" >> "$m"
  [ -n "$head" ] && round_review "$key" "$head" "$files"
  echo "WAVE CONFLICT $kb: the rebase on $base conflicts at $at${files:+ in $files}; round $((rounds + 1)) of 2 asked on the moved base ${bsha:0:9}, nothing requeued"
  return 1
}
# landempty <kept branch> <why> (#1960): a kept landing round whose fix run ended with no pushed head. No conflict comes back to
# land_round for it, so the round is bounded here: each issue line is requeued as a fresh build (Todo, rework), the wave branch
# that keeps the work is named for salvage and stays on the remote, and the marker, the round review and the kept branch go.
landempty() {
  local br=$1 why=$2 key run m w issues n
  key=$(printf '%s' "$br" | tr / _); run=${br#fix/*-land-}; m="$SG_STATE/auto/kept-$key"; w=$(wave_of "$run")
  [ -s "$m" ] || { echo "WAVE-ERROR landempty: $br has no kept marker"; return 2; }
  issues=$(sed -n 's/^issue //p' "$m")
  echo "WAVE ROUND $br: $why; ${issues//$'\n'/, } requeued as a fresh build; salvage the work from $w"
  for n in $issues; do
    SG_REQUEUE_RUN=$run requeue "$n" "the rebase round on $br pushed no head ($why): requeued as a fresh build; salvage the work from $w"
    gh_send -X POST "repos/$SG_REPO/issues/$n/labels" -f "labels[]=rework" >/dev/null 2>&1 || echo "WAVE-LABEL #$n rework refused"
  done
  rm -f "$m" "$SG_STATE/review-${key}".* "$SG_STATE/landround-$key.head"
  remote_git --delete "$br" || echo "WAVE-NOTE the delete of $br from $remote was refused"
  return 1
}
# keep_landing <run> <wave> <clone> <keep set> <files> <at>: the first landing conflict of a wave keeps the wave head on its work branch.
# Returns 1 when the branch could not be kept, and the caller takes the requeue path.
keep_landing() {
  local run=$1 w=$2 S=$3 set=$4 files=$5 at=$6 low kb key head m
  low=$(printf '%s\n' $set | sort -n | head -1); kb=fix/$low-land-$run; key=fix_$low-land-$run
  $G -C "$S" push -q origin "refs/remotes/origin/$w:refs/heads/$kb" >/dev/null 2>&1
  $G -C "$S" fetch -q origin "+refs/heads/$kb:refs/remotes/origin/$kb" 2>/dev/null
  head=$($G -C "$S" rev-parse -q --verify "refs/remotes/origin/$kb" 2>/dev/null)
  { [ -n "$head" ] && $G -C "$S" merge-base --is-ancestor "origin/$w" "$head" 2>/dev/null; } || return 1
  m="$SG_STATE/auto/kept-$key"
  { printf 'origin/%s\n' "$base"; for n in $(printf '%s\n' $set | sort -un); do printf 'issue %s\n' "$n"; done
    printf 'round 1 %s\n' "$($G -C "$S" rev-parse "origin/$base")"; } > "$m.tmp" && mv "$m.tmp" "$m"
  round_review "$key" "$head" "$files"
  echo "WAVE CONFLICT $w: the rebase on $base conflicts at $at${files:+ in $files}; $(printf '#%s\n' $set | sort -u | paste -sd, - | sed 's/,/, /g') kept on $kb for one rebase round, not requeued"
  return 0
}

# wave_conflict <run> <wave> <run folder> <clone> <rebase output> <conflicting paths> (#844): a rebase of the wave on the base that conflicts is terminal
# for this wave head. The same rebase on the next cycle cannot succeed, so the landing is not retried: the issues the run was
# dispatched for (the Refs of the wave when the run names none) go back to the queue through requeue, so its loop cap and the
# needs-human path apply as they do to any requeue. The run's wave marker goes, so the loop stops calling the landing. The wave
# branch stays on the remote with its commits: they may exist on no other ref. One line names the commit that conflicts. Returns 1.
wave_conflict() {
  local run=$1 w=$2 rd=$3 S=$4 out=$5 files at refs dset list n n2 named="" outs="" sig hit firsts="" repeats="" held=""
  at=$(printf '%s\n' "$out" | /usr/bin/grep -oE 'Could not apply [0-9a-f]+' | head -1 | awk '{print $4}')
  at=${at:-unknown}
  refs=$(refs_of "$S" "origin/$base..origin/$w")
  dset=$(run_issues "${rd:-/nonexistent}" | tr '\n' ' ')
  # Only the run's dispatch set is acted on (#776, as stuck and the close do): a `Refs #N` outside it is noted and recorded,
  # and gets no requeue, label, card move, comment or try.
  if [ -n "${dset// /}" ]; then list=$dset; for n2 in $refs; do case " $dset " in *" $n2 "*) ;; *) outs="${outs:+$outs }$n2";; esac; done
  else list=$refs; fi
  files=${6:-}; files=${files% }
  # The sig keys on the conflicting paths (#1343): the base sha moves with every landing, so a base in the key made every conflict a
  # first. A conflict that names no path keeps the base sha, so an unnamed conflict never matches another one.
  if [ -n "$files" ]; then sig=$(printf '%s\n' $files | sort -u | tr '\n' ' '); sig=${sig% }
  else sig="base $($G -C "$S" rev-parse "origin/$base" 2>/dev/null)"; fi
  for n in $list; do wave_fact "$run" "$n" refused "the rebase on $base conflicts at $at"; done
  local km kset="" bsha
  bsha=$($G -C "$S" rev-parse "origin/$base" 2>/dev/null)
  if km=$(land_marker "$run"); then   # a kept landing branch is in its round: the repeat rule replaces the same-paths rule (#1500)
    land_round "$run" "$km" "$bsha" "$files" "$at"
    rm -f "$SG_STATE/auto/wave-$run" "$SG_STATE/auto/wave-start-$run" "$SG_STATE/auto/wave-pause-$run"; return 1
  fi
  for n in $refs; do case " $list " in *" $n "*) kset="${kset:+$kset }$n";; esac; done
  if [ -n "$kset" ] && keep_landing "$run" "$w" "$S" "$kset" "$files" "$at"; then
    for n2 in $outs; do
      echo "WAVE-NOTE #$n2: $w names it (Refs #$n2) outside the dispatch set of run $run; reported, not frozen"
      printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$run" "$n2" "$w" >> "$SG_STATE/wave-outside.tsv"
    done
    # A dispatched issue the wave head does not carry has no branch to keep: it takes the requeue path below, as it did before the keep.
    local rest=""; for n in $list; do case " $kset " in *" $n "*) ;; *) rest="${rest:+$rest }$n";; esac; done
    if [ -z "$rest" ]; then rm -f "$SG_STATE/auto/wave-$run" "$SG_STATE/auto/wave-start-$run" "$SG_STATE/auto/wave-pause-$run"; return 1; fi
    list=$rest; outs=""
  fi
  for n in $list; do
    hit=$(ledger conflict "$n" "$run" "$sig" 2>&1) || { echo "WAVE-LEDGER #$n refused: $hit"; continue; }
    case $hit in
      first) named="${named:+$named, }#$n"; firsts="$firsts $n";;
      repeat) repeats="$repeats $n";;
      *) held="${held:+$held, }#$n";;
    esac
  done
  echo "WAVE CONFLICT $w: the rebase on $base conflicts at $at${files:+ in $files}; ${named:-no issue} requeued on the current base${held:+; $held not requeued, the same conflict again}"
  for n2 in $outs; do
    echo "WAVE-NOTE #$n2: $w names it (Refs #$n2) outside the dispatch set of run $run; reported, not frozen"
    printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$run" "$n2" "$w" >> "$SG_STATE/wave-outside.tsv"
  done
  for n in $firsts; do
    requeue "$n" "the rebase on $base conflicts at $at in ${files:-no path named by git}: $w cannot land on the current base"
    gh_send -X POST "repos/$SG_REPO/issues/$n/labels" -f "labels[]=rework" >/dev/null 2>&1 || echo "WAVE-LABEL #$n rework refused"
  done
  for n in $repeats; do
    file_conflict_bug "$n" "$run" "$w" "$at" "$files"
    # The repeat stops the issue through the stop path (frozen:owner, ledger stop, one NOT-STARTABLE marker), so no dispatch pass
    # picks it again while the loop rebuilds it (#1343). The count is the two conflicts on the same paths.
    c=2; stop "$n" "the rebase on $base conflicts again at $at in ${files:-no path named by git}, the same paths as its last conflict: $w cannot land on the current base"
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
  # A fetch that failed for a cause no class names still names git's own line (#1710): the landing's words are the only
  # record of a failure on a machine whose processes were gone by the time anyone read it.
  # gitwhy <stderr>: git's own cause, into GITWHY -- the stderr's first non-blank line, the one git prints first and the one
  # a pasted failure starts with, else a named empty, so the WAVE-ERROR line never ends in a dangling `failed: `. A variable
  # and bash's own read loop, never a command substitution or a helper: this runs exactly when the machine may refuse a fork
  # (#1710).
  GITWHY=""
  gitwhy() { local line; GITWHY=""
    while IFS= read -r line; do
      [[ $line =~ ^[[:space:]]*$ ]] && continue
      GITWHY=$line; break
    done <<< "$1"
    GITWHY=${GITWHY:-git printed no line}
  }
  # pushwhy <stderr>: the push's LAST non-blank line, into PUSHWHY -- the text the refused push's WAVE-ERROR carried before
  # (`tail -1`), read the same way for gitwhy's reason: no helper to fork on a machine that refuses one (#1710).
  PUSHWHY=""
  pushwhy() { local line; PUSHWHY=""
    while IFS= read -r line; do [[ $line =~ [^[:space:]] ]] && PUSHWHY=$line; done <<< "$1"
    PUSHWHY=${PUSHWHY:-git printed no line}
  }
  fo=$(sc fetch -q origin "+refs/heads/$base:refs/remotes/origin/$base" 2>&1) || {
    netfail "the fetch of $base from $remote" "$fo" || { gitwhy "$fo"; stuck "$run" "$w" "$S" "the fetch of $base from $remote failed: $GITWHY"; }; rm -rf "$S"; return 2; }
  fo=$(sc fetch -q origin "+refs/heads/$w:refs/remotes/origin/$w" 2>&1) || {
    netfail "the fetch of $w from $remote" "$fo" || absent_wave "$run" "$w" "$S" "$rd" "$fo" || { gitwhy "$fo"; stuck "$run" "$w" "$S" "the fetch of $w from $remote failed: $GITWHY"; }; rm -rf "$S"; return 2; }
  sc checkout -q -B land "origin/$w" 2>/dev/null || { stuck "$run" "$w" "$S" "the checkout of $w failed"; rm -rf "$S"; return 2; }
  # The rebase on the base happens here and nowhere else (ADR-0145 rule 14): between entries the tip moves only by a child that
  # entered, so a base that moved under the wave is rebased onto now, before the one push. A push the remote refuses -- the
  # base moved once more between this rebase and the push -- repeats from the fetch: the moved base is fetched, the wave is
  # rebased on it, and the push goes again (ADR-0145 rule 2). One repeat, as the entry does.
  # `dropped`: the issues whose child the tip check removed from the tree this landing pushes (#1416). Only this pass sets it,
  # so a refusal an earlier pass wrote never excludes an issue. wave_check adds to it; a repeat after a refused push keeps it.
  local out sha try=1 issues n anyref=0 files rc ob tipobl="" dropped="" rfs="" nontest="" br note
  while :; do
    out=$(sc rebase "origin/$base" 2>&1) || { files=$(sc diff --name-only --diff-filter=U | sort -u | tr '\n' ' ')
      sc rebase --abort >/dev/null 2>&1
      wave_conflict "$run" "$w" "$rd" "$S" "$out" "$files"; rm -rf "$S"; return 1; }
    # (#2216) A child this run already evicted is removed before the empty check and the tip check, so a pass that finds the
    # same child red again runs no check on it: the tip check runs on the children the run kept, or on none.
    gone=$(drop_evicted "$S" "$run"); [ -z "$gone" ] || dropped="${dropped:+$dropped }${gone% }"
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
        # (#1416) A child the tip check dropped has no work in $base, even when a refused push moved the base and this retry
        # found nothing ahead: the entered fact below is no proof for it.
        case " $dropped " in *" $n "*) continue;; esac
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
      # (#1106) An issue this run requeued is no orphan. Its child commit stays on the run's branch by design: the loop builds the
      # issue again on a new branch. Without this the wave refused on every later cycle, so the run never landed and the commits
      # of its other children stayed on their branches too.
      # (#2209) A child the tip check dropped in this pass was requeued by it, whatever its ledger could record.
      requeued_here() { case " $dropped " in *" $1 "*) return 0;; esac; [ -n "$(python3 "$here/issue_ledger.py" requeued "$SG_REPO#$1" "$run" 2>/dev/null)" ]; }
      for n in $(run_issues "$rd"); do case " $delivered " in *" $n "*) ;; *) requeued_here "$n" || undel="${undel:+$undel }$n";; esac; done
      # (#1833) A wave whose tip the base already carries has landed: its children's work reached the base in an earlier landing,
      # and only the wave branch stayed behind (its deletion did not reach it). A child that never entered such a wave is no
      # orphan to hold the wave: nothing can change on a later pass, so the pass writes its books and drops its markers as a kept
      # landing branch does (#1826). The entered fact is the proof that the wave carried a child, as the marker is the proof for a
      # kept landing branch: a wave cut from the base that no child ever entered is not this case, and its child commit still
      # refuses the landing (#836). The refusal itself is unchanged.
      local landed=""
      if [ "$capped" != 1 ] && [ -n "$ent" ] && sc merge-base --is-ancestor "origin/$w" "origin/$base"; then landed=1; fi
      # (#1835) A wave past its age cap never refuses on an orphan: `5816aa2b` dropped this guard and a capped wave with a child
      # commit on a local branch only refused on every cycle, so its issues stayed held by the wave forever. The branch below
      # (`capped = 1`) writes the books, drops the markers and sends the child back to the queue without counting a failure.
      if [ "$capped" != 1 ] && [ -z "$landed" ] && [ -n "$undel" ] && orph=$(orphan_child "$run" "$rd" "$S" "$w" $undel); then
        if [ "$(cat "$refused" 2>/dev/null)" != "${orph% *}" ]; then
          mkdir -p "$SG_STATE/auto" && printf '%s\n' "${orph% *}" > "$refused"
          echo "WAVE REFUSE $w: child commit ${orph% *} names #${orph#* }; it is on a local branch only and in neither $w nor $base, so the wave is kept"
        fi
        rm -rf "$S"; return 1
      fi
      sc push -q origin ":refs/heads/$w" >/dev/null 2>&1 || echo "WAVE-NOTE branch $w was not deleted" >&2
      sha=$(sc rev-parse "origin/$base"); rm -rf "$S"; rm -f "$SG_STATE/auto/wave-start-$run" "$SG_STATE/auto/wave-pause-$run" "$refused"; tipred_clear "$w"
      if [ -n "$landed" ]; then
        echo "WAVE $w already landed in $base (its tip is in the base; the wave branch is deleted${dispatched:+; the run was dispatched for $dispatched})"
      else
        echo "WAVE $w landed empty in $base (nothing ahead of it; the wave branch is deleted${dispatched:+; the run was dispatched for $dispatched})"
      fi
      # An empty wave past its age cap follows the capped landing's rule too (#591): every issue of the run that `dev` does
      # not hold goes back to the queue, so a child that never entered is built into a later wave instead of staying in Doing.
      # An issue whose non-RED commit the base holds is delivered below, never requeued.
      if [ "$landed" = 1 ]; then
        # (#1833) A child that never entered a landed wave is a requeue for its own pass: its commit stays on the run's branch by
        # design (#1106), and the landing pass never re-reads it. The requeue is no failure of the child (the landing's delay,
        # `cap`): the wave it was built into landed without it, and the loop builds it into a later wave.
        for n in $undel; do
          wave_fact "$run" "$n" refused "the run's wave landed in $base of an earlier landing and this child never entered it" keep
          requeue "$n" "the run's wave landed in $base of an earlier landing without this child: it is back in the queue" cap
        done
      elif [ "$capped" = 1 ]; then
        for n in $(run_issues "$rd"); do case " $delivered " in *" $n "*) ;; *)
          why="the wave reached its age cap (${SG_WAVE_MAX_AGE_SECONDS:-7200} s) and landed empty without this child: it is back in the queue"
          note=$(salvage_note "$run" "$n")   # the child's commits reach a work branch before the queue sends the issue back (#2030, #2231)
          [ -n "$note" ] && why="$why; $note"
          wave_fact "$run" "$n" refused "the wave reached its age cap (${SG_WAVE_MAX_AGE_SECONDS:-7200} s) and landed empty without this child" keep
          cap_hold "$run" "$rd" "$n" "$why";; esac; done
        retire_run "$run" || true   # the run ends before its issues go back to the queue; a hold retries on a later pass (#1970)
      fi
      if [ -n "$delivered" ]; then deliver "$run" "$w" "$sha" "$delivered" || return 1; else rm -f "$SG_STATE/auto/wave-$run" "$SG_STATE/auto/empty-requeued-$run"-*; fi
      return 0; }
    # The wave tip's check (#377, ADR-0145 rule 11): a red tip names the child that broke it, and that child leaves the wave
    # before it lands. A tip that stays red with no child named is a landing that failed, as any other cause is. An unproven tip
    # holds the wave (#1802): with no entry battery, the tip check is the only proof the children get before the base.
    # The base's own check (#786): a red completed check on $base holds the landing until its head is green.
    red=$(sg_base_red "$base") && { echo "WAVE WAIT $w: $red"; rm -rf "$S"; return 1; }
    wave_check "$S" "$w"; rc=$?
    # 8 (#2209): the check evicted every child. The loop repeats, and the empty-landing block above closes the wave.
    [ $rc = 8 ] && continue
    # The check ran no verdict: the one cause it met is named, with the check's own log, so the next pass reads the trace (#1700).
    [ $rc = 3 ] && { wwhy=${waitwhy:-the tip check ran no verdict}; echo "WAVE WAIT $w: $wwhy; the wave lands on a later pass (the check's own output is $SG_STATE/wave-check-$(printf '%s' "$w" | tr / _).log)"; rm -rf "$S"; return 1; }
    [ $rc = 6 ] && { echo "WAVE WAIT $w: the tip's check is red on a tree a gate passed before, so no child is blamed on this red; the check runs again on a later pass (its output is $SG_STATE/wave-check-$(printf '%s' "$w" | tr / _).log)"; rm -rf "$S"; return 1; }
    # 7 (#1961): the tip is red on this tree and no child names the cause, measured twice, or held for its next run. It is a
    # landing that failed on the tree's own red, never the loop's cause, so each pass counts a try and the cap reaches the owner.
    [ $rc = 7 ] && { stuck "$run" "$w" "$S" "the check at the tip of $w is red on the same tree after its rerun and names no child: it is held until the tip moves (its output is $SG_STATE/wave-check-$(printf '%s' "$w" | tr / _).log)"; rm -rf "$S"; return 1; }
    # 5 (#1802, spec 1797): the tip's check is unproven. The entry runs no battery by default, so this check is the one proof
    # of the wave's children: an unproven one never moves the base. The wave waits, blames no child and counts no try; the
    # check runs again on a later pass, and the age cap bounds the wait (ADR-0164).
    # (#2149) The age cap is what frees the run: past it, a wave that never lands would hold its issues for ever, because no
    # other pass ends a run whose wave waits. The capped pass puts each issue the wave still holds back in the queue and ends
    # the run, as the capped-empty landing does; an unproven tip still never lands.
    [ $rc = 5 ] && { if [ "$capped" = 1 ]; then
        for n in $(run_issues "$rd"); do case " $delivered " in *" $n "*) ;; *)
          why="the tip's check stayed unproven through the wave's age cap (${SG_WAVE_MAX_AGE_SECONDS:-7200} s): it is back in the queue"
          note=$(salvage_note "$run" "$n")   # the child's commits reach a work branch before the queue sends the issue back (#2030, #2231)
          [ -n "$note" ] && why="$why; $note"
          wave_fact "$run" "$n" refused "the tip's check stayed unproven through the wave's age cap (${SG_WAVE_MAX_AGE_SECONDS:-7200} s), so the wave never landed with this child" keep
          cap_hold "$run" "$rd" "$n" "$why";; esac; done
        retire_run "$run" || true   # the run ends before its issues go back to the queue; a hold retries on a later pass (#1970)
      fi
      echo "WAVE WAIT $w: the tip's check is unproven, and an unproven tip never lands; the check runs again on a later pass (its output is $SG_STATE/wave-check-$(printf '%s' "$w" | tr / _).log)"; rm -rf "$S"; return 1; }
    tipobl=
    [ $rc = 0 ] || { stuck "$run" "$w" "$S" "the check at the tip of $w is red and no child's own suite names the cause"; rm -rf "$S"; return 1; }
    sha=$(sc rev-parse HEAD)
    land_read "origin/$base" HEAD
    # ONE push of the wave's tip to the base: one ref update, one CI run, and a push git refuses unless it is a fast-forward,
    # so no merge commit can land. Nothing else is pushed -- no child branch, and the wave branch goes once its work is in.
    out=$(sc push -q origin "HEAD:refs/heads/$base" 2>&1) && break
    netfail "the push of $w to $base" "$out" && { rm -rf "$S"; return 2; }
    [ "$try" = 1 ] || { pushwhy "$out"; stuck "$run" "$w" "$S" "the push of $w to $base was refused: $PUSHWHY"; rm -rf "$S"; return 1; }
    try=2
    fo=$(sc fetch -q origin "+refs/heads/$base:refs/remotes/origin/$base" 2>&1) || {
      netfail "the fetch of $base from $remote" "$fo" || { gitwhy "$fo"; stuck "$run" "$w" "$S" "the fetch of $base from $remote failed: $GITWHY"; }; rm -rf "$S"; return 2; }
  done
  local brc; land_books; brc=$?; rm -rf "$S"; return $brc
}

# land_read <from> <to>: the issues the landed range delivers, into rows, anyref, issues, amb, rfs and nontest. `land` reads
# its wave's range; `land_batch` reads each wave's own range of the stacked tip (#2058).
land_read() {
    # The issues the wave delivers, read here, after the rebase, from what is ahead of the base: a commit the base already holds
    # is dropped by the rebase, and its issue was not delivered by this landing. A repeat from a refused push reads them again.
    # (#869) A commit that names exactly one issue delivers it (commit_refs). A commit that names several delivers none of them:
    # the cluster child did not say which it fixed, so those issues are named and requeued below, never closed.
    rows=$(commit_refs "$S" "$1..$2")
    anyref=$(sc log --format=%B "$1..$2" 2>/dev/null | refs_in_message | wc -l | tr -d ' ')   # a RED commit's Refs counts here (#1003)
    issues=$(printf '%s\n' "$rows" | awk '$1 == "D" { print $3 }' | sort -un | tr '\n' ' ')
    amb=$(printf '%s\n' "$rows" | awk '$1 == "A" { for (i = 3; i <= NF; i++) print $i }' | sort -un | tr '\n' ' ')
    # (#1474) The one issue a wave names closes when its only Refs sit on a test-only RED commit and a fix commit names none: the
    # wave changes code, and its single `Refs #N` is N. A wave that changes test files only delivers nothing (#533), and so does a
    # wave that names two issues (#869).
    rfs=$(refs_of "$S" "$1..$2")
    nontest=$(sc diff --name-only "$1" "$2" 2>/dev/null | while IFS= read -r f; do is_code_path "$f" && { echo "$f"; break; }; done)
    if [ -z "$issues" ] && [ -z "$amb" ] && [ -n "$nontest" ] && [ "$(printf '%s' "$rfs" | wc -w | tr -d ' ')" = 1 ]; then
      issues=$(printf '%s' "$rfs" | tr -d ' ')
      echo "WAVE-NOTE $w: the only Refs of the wave name #$issues, on a test-only RED commit, and its fix commit names none; the landing closes #$issues"
    fi
}

# land_books: what follows a wave's push to the base -- the wave branch goes, and the issues it carries close. It reads the
# caller's run, w, rd, S, sha, rows, issues, amb, anyref, nontest, capped, dropped and tipobl, so `land` and `land_batch`
# (#2058) share one path after the push.
land_books() {
  sc push -q origin ":refs/heads/$w" >/dev/null 2>&1 || echo "WAVE-NOTE branch $w was not deleted" >&2
  rm -f "$SG_STATE/auto/wave-start-$run" "$SG_STATE/auto/wave-pause-$run"; tipred_clear "$w"   # the wave is in $base; wave-$run goes once every issue is delivered (deliver)
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
  local rq="" note   # the issues the capped block below sends back to the queue: the no-Refs fallback never closes one (#1003)
  if [ "$capped" = 1 ]; then
    # (#869) A child that entered the wave is not missing from it, even when its commit is not ahead of the base any more (the
    # rebase dropped it), and an issue a multi-issue commit named was requeued above with its own reason.
    for n in $(run_issues "$rd"); do case " $issues $amb $ent " in *" $n "*) ;; *)
      why="the wave reached its age cap (${SG_WAVE_MAX_AGE_SECONDS:-7200} s) and landed without this child: it is back in the queue"
      note=$(salvage_note "$run" "$n")   # the child's commits reach a branch before the queue sends the issue back (#2030)
      [ -n "$note" ] && why="$why; $note"
      wave_fact "$run" "$n" refused "the wave reached its age cap (${SG_WAVE_MAX_AGE_SECONDS:-7200} s) and landed without this child" keep
      rq="$rq $n"
      cap_hold "$run" "$rd" "$n" "$why";; esac; done
    [ -n "$rq" ] && { retire_run "$run" || true; }   # the run ends before its issues go back to the queue (#1970)
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
  # (#1416) An issue whose child the tip check dropped is decided here, before deliver() writes anything: its work is not in
  # the tree this landing pushed, so no close, landed.tsv row or delivery comment follows, and its requeue stands.
  if [ -n "$dropped" ]; then
    kept=""; for n2 in $issues; do case " $dropped " in *" $n2 "*) echo "WAVE-NOTE $w: the tip check dropped the child of #$n2; the landing closes none of them";; *) kept="${kept:+$kept }$n2";; esac; done
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
    for n3 in $dset; do case " $rq " in *" $n3 "*) continue;; esac
      case " $dropped " in *" $n3 "*) echo "WAVE-NOTE $w: the tip check dropped the child of #$n3; the landing closes none of them";; *) cand="${cand:+$cand }$n3";; esac; done
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
  # (#1474) A wave whose commits name an issue and that closes none says why, in place of "no Refs": the Refs of a test-only RED
  # commit deliver nothing (#533), a commit that names several issues closes none of them (#869), and the rest are Refs outside
  # the dispatch set or dropped by the tip check.
  local reason=""
  if [ -z "$issues" ] && [ "${anyref:-0}" -gt 0 ]; then
    if [ -n "$amb" ]; then reason="a commit names several issues, so it closes none of them"
    elif [ -z "$nontest" ]; then reason="the wave changes test files only, so its Refs deliver nothing (#533)"
    else reason="the Refs of the wave name no issue it delivers"; fi
  fi
  deliver "$run" "$w" "$sha" "$issues" "$tipobl" || return 1
  [ -n "$fb" ] && for n in $issues; do rm -f "$SG_STATE/auto/disp-$n"; done
  local named=""; for n in $issues; do named="${named:+$named, }#$n"; done
  if [ -n "$named" ]; then echo "WAVE $w $sha landed in $base ($named)"
  elif [ -n "$reason" ]; then echo "WAVE-NOTE $w: the landing closes no issue, because $reason"
    echo "WAVE $w $sha landed in $base (no issue closed: $reason)"
  else echo "WAVE $w $sha landed in $base (no issue named: the wave carries no Refs)"; fi
  return 0
}

# landconflict <kept branch> <base sha> <ignored> <files>: the lander met a conflict on a kept landing branch (#1500); the repeat rule decides.
landconflict() {
  local br=$1 bsha=$2 files=${4:-} key run m
  key=$(printf '%s' "$br" | tr / _); run=${br#fix/*-land-}; m="$SG_STATE/auto/kept-$key"
  [ -s "$m" ] || { echo "WAVE-ERROR landconflict: $br has no kept marker"; return 2; }
  SG_REQUEUE_RUN=$run land_round "$run" "$m" "$bsha" "$files" unknown
}

# land_queue <run> [folder] (#2058): the landing pass watch.sh starts under the landing lock. With SG_LAND_QUEUE=1 it claims
# the other runs that wait for that lock (watch.sh leaves `wland-<run>.queued`, which names the run's folder), oldest first,
# up to SG_LAND_BATCH runs in all (default 4), and lands their waves behind one tip gate (land_batch). It writes its own pid
# into the busy marker of each claimed run, so watch.sh starts no pass for that run while this one lives, and it writes
# `wland-<run>` for each claimed run that landed; the caller writes the marker of <run>. With no other run waiting, with
# SG_LAND_BATCH=1, or without SG_LAND_QUEUE (a hand call), this is `land` itself, unchanged.
land_queue() {
  local self=${1:-} srd=${2:-${1:-}} lim=${SG_LAND_BATCH:-4} q r h n=1 rc i
  local -a cr=() crd=()
  case $lim in ''|*[!0-9]*) lim=4;; esac
  if [ "${SG_LAND_QUEUE:-}" != 1 ] || [ "$lim" -le 1 ] || [ -z "$self" ]; then land "$@"; return; fi
  for q in $(/bin/ls -tr "$SG_STATE"/auto/wland-*.queued 2>/dev/null); do
    [ "$n" -lt "$lim" ] || break
    r=${q##*/wland-}; r=${r%.queued}
    if [ "$r" = "${self##*/}" ] || [ -e "$SG_STATE/auto/wland-$r" ]; then rm -f "$q"; continue; fi
    [ -s "$q" ] || { rm -f "$q"; continue; }                 # an empty marker names no folder: never claimed (#2068)
    red_read "$(wave_of "$r")" && continue                    # a wave held red lands alone, never in a batch (#1961, #2068)
    h=$(cat "$SG_STATE/auto/wland-$r.busy" 2>/dev/null)
    [ -n "$h" ] && kill -0 "$h" 2>/dev/null && continue   # a pass of its own lives: it lands the run or queues it again
    printf '%s\n' "$$" > "$SG_STATE/auto/wland-$r.busy"; cr+=("$r"); crd+=("$(cat "$q")"); rm -f "$q"; n=$((n + 1))
  done
  [ ${#cr[@]} -gt 0 ] || { land "$@"; return; }
  echo "WAVE BATCH ${self##*/}: it lands with ${cr[*]} behind one tip gate"
  local -a args=("${self##*/}" "$srd"); for i in "${!cr[@]}"; do args+=("${cr[$i]}" "${crd[$i]}"); done
  land_batch "${args[@]}"; rc=$?
  if [ $rc = 10 ]; then
    # The fallback (#2058): no bisection in this version. Each run of the batch takes today's one-run landing, with its own
    # gate and its own red path, so a red batch costs one gate more and changes no red path. The claimed runs go first, so the
    # last lines are those of <run>, which watch.sh reads for its wait line; their own lines carry a `BATCH <run>:` prefix.
    for i in "${!cr[@]}"; do
      land "${cr[$i]}" "${crd[$i]}" 2>&1 | sed "s|^|BATCH ${cr[$i]}: |"
      [ "${PIPESTATUS[0]}" = 0 ] && : > "$SG_STATE/auto/wland-${cr[$i]}"
    done
    land "${self##*/}" "$srd"; rc=$?
  fi
  for r in "${cr[@]}"; do [ "$(cat "$SG_STATE/auto/wland-$r.busy" 2>/dev/null)" = "$$" ] && rm -f "$SG_STATE/auto/wland-$r.busy"; done
  return $rc
}

# land_batch <run> <folder> [<run> <folder>]... (#2058): the waves of the runs, stacked on the base in one scratch clone in
# the order named, one tip gate on that exact tip, and one fast-forward push of it. A push the remote refuses repeats once from
# the fetch, with a new stack and a new gate, as `land` does. After the push each run takes `land`'s own books (land_books)
# over its own range of the stack: the claimed runs first, with the `BATCH <run>:` prefix and their `wland-<run>` marker, and
# the first run last. A wave with nothing ahead of the base leaves the batch before it is sized and lands empty on its own
# (`land`), after the batch's books; a run that owes a close or is past its age cap does the same (#2288). Exit 10: the batch does not fit --
# fewer than two waves fit and carry commits, a wave conflicts on the stack, the gate is red or unproven, or the repeat is refused --
# and the caller lands each run alone. Exit 1: the base is red or the gate ran no verdict, so every run waits for a later pass. Exit 2: the network failed.
land_batch() {
  local -a rs=() rds=() ws=() tips=() ers=() erds=() kr=() krd=() kw=()
  local S i out fo try=1 of clog rc oc bstart ids k run w rd sha capped=0 dropped="" tipobl="" brc=0 self0 selfrd er erd
  local rows issues anyref rfs nontest SG_REQUEUE_RUN
  while [ $# -ge 2 ]; do
    rd=$2; case "$rd" in /*) ;; *) rd="runs/${rd##*/}";; esac
    rs+=("$1"); rds+=("$rd")
    ws+=("$(wave_of "$1")"); shift 2; done
  self0=${rs[0]:-}; selfrd=${rds[0]:-}
  # A red base fails every gate the batch or a lone run would start, so the batch waits before it stacks a wave, as the lone
  # landing waits (#2288).
  red=$(sg_base_red "$base") && { echo "WAVE WAIT batch $(IFS=,; printf '%s' "${rs[*]}"): $red; the batch lands on a later pass"; return 1; }
  # A run that owes a close or is past its age cap takes its own landing path, so it leaves the batch and lands alone after
  # it, as an empty wave does; the other runs stay in one batch (#2288). One such run used to send every run of the batch alone.
  kr=() krd=() kw=()
  for i in "${!rs[@]}"; do
    if [ -n "$(python3 "$here/issue_ledger.py" owed "$SG_REPO" "${rs[$i]}" 2>/dev/null)" ]; then
      echo "WAVE BATCH: ${rs[$i]} owes a close, so it leaves the batch and lands alone"; ers+=("${rs[$i]}"); erds+=("${rds[$i]}")
    elif expired "${rs[$i]}"; then
      echo "WAVE BATCH: ${rs[$i]} is past its age cap, so it leaves the batch and lands alone"; ers+=("${rs[$i]}"); erds+=("${rds[$i]}")
    else kr+=("${rs[$i]}"); krd+=("${rds[$i]}"); kw+=("${ws[$i]}"); fi
  done
  rs=("${kr[@]}"); rds=("${krd[@]}"); ws=("${kw[@]}")
  [ "${#rs[@]}" -ge 2 ] || { echo "WAVE BATCH: ${#rs[@]} wave(s) fit the batch, so each run lands alone"; return 10; }
  S=$(mktemp -d) || return 10
  sc() { $G -C "$S" "$@"; }
  sc init -q . && sc remote add origin "$remote" 2>/dev/null
  [ -n "$(sc config user.name)" ] || sc config user.name "${SG_LAND_NAME:-squad-goal}"
  [ -n "$(sc config user.email)" ] || sc config user.email "${SG_LAND_EMAIL:-squad-goal@localhost}"
  for i in "${!ws[@]}"; do
    sc fetch -q origin "+refs/heads/${ws[$i]}:refs/remotes/origin/${ws[$i]}" >/dev/null 2>&1 || { rm -rf "$S"; return 10; }
  done
  while :; do
    fo=$(sc fetch -q origin "+refs/heads/$base:refs/remotes/origin/$base" 2>&1) || { netfail "the fetch of $base from $remote" "$fo" && { rm -rf "$S"; return 2; }; rm -rf "$S"; return 10; }
    bstart=$(sc rev-parse "origin/$base"); tips=()
    sc checkout -q -B land "origin/$base" 2>/dev/null || { rm -rf "$S"; return 10; }
    kr=() krd=() kw=()
    for i in "${!ws[@]}"; do
      if [ "$(sc rev-list --count "origin/$base..origin/${ws[$i]}")" -gt 0 ]; then kr+=("${rs[$i]}"); krd+=("${rds[$i]}"); kw+=("${ws[$i]}")
      else echo "WAVE BATCH: ${ws[$i]} carries nothing, so it lands empty outside the batch"; ers+=("${rs[$i]}"); erds+=("${rds[$i]}"); fi
    done
    rs=("${kr[@]}"); rds=("${krd[@]}"); ws=("${kw[@]}")
    [ "${#rs[@]}" -ge 2 ] || { echo "WAVE BATCH: ${#rs[@]} wave(s) with commits left, so each run lands alone"; rm -rf "$S"; return 10; }
    ids=$(IFS=,; printf '%s' "${rs[*]}")
    for i in "${!ws[@]}"; do
      k=$(sc rev-parse land)
      sc branch -q -f "b$i" "origin/${ws[$i]}"
      out=$(sc rebase -q --onto land "origin/$base" "b$i" 2>&1) || { sc rebase --abort >/dev/null 2>&1
        echo "WAVE BATCH: ${ws[$i]} conflicts on the stack, so each run lands alone"; rm -rf "$S"; return 10; }
      [ "$(sc rev-parse HEAD)" != "$k" ] || { echo "WAVE BATCH: ${ws[$i]} adds nothing to the stack, so each run lands alone"; rm -rf "$S"; return 10; }
      sc checkout -q -B land HEAD 2>/dev/null; tips+=("$(sc rev-parse HEAD)")
    done
    # One tip gate on the exact stacked tip, as wave_check runs it, with the batch named for the gate history (SG_GATE_BATCH).
    # No child is blamed here: a red goes to the fallback, where each wave's own check keeps its red path.
    if [ -n "$tipgate" ]; then
      of=$(mktemp "${TMPDIR:-/tmp}/wave-tip-outcome.XXXXXX" 2>/dev/null) || of=
      clog="$SG_STATE/wave-check-batch.log"; touch "$SG_STATE/locks/single-wland/round" 2>/dev/null
      gating "$S" "$bstart..HEAD" "${rs[@]}"   # the cards of every wave in the batch stand in Gating while its gate waits or runs (ADR-0170)
      (cd "$S" && export SERIAL_WAIT_SECONDS=${SERIAL_WAIT_SECONDS:-900} SG_SERIAL_PRIORITY=1 GATE_OUTCOME_FILE="$of" GATE_OUTCOME_READ_FILE="$of" SG_GATE_WAVE="${rs[0]}" SG_GATE_CALLER=tip SG_GATE_BATCH="${#rs[@]}:$ids" && eval "$tipgate") >"$clog" 2>&1; rc=$?
      oc=; [ -z "$of" ] || IFS=$'\t' read -r oc _ _ _ < "$of" 2>/dev/null; rm -f "$of"
      if { [ $rc = 73 ] && held_by "$(cat "$clog")" >/dev/null; } || { [ $rc = 75 ] && waited_out "$(cat "$clog")"; }; then
        echo "WAVE WAIT batch $ids: the tip's check did not run (the gate lock or slots are held); the batch lands on a later pass (its output is $clog)"; rm -rf "$S"; return 1
      fi
      case "$oc" in pass|unproven|fail) ;; *) oc=; [ $rc = 0 ] && oc=pass; [ $rc = 4 ] && oc=unproven;; esac
      [ "$oc" = pass ] || { echo "WAVE BATCH: the tip's check of batch $ids is ${oc:-red}, so each run lands alone (its output is $clog)"; rm -rf "$S"; return 10; }
    fi
    out=$(sc push -q origin "HEAD:refs/heads/$base" 2>&1) && break
    netfail "the push of batch $ids to $base" "$out" && { rm -rf "$S"; return 2; }
    [ "$try" = 1 ] || { echo "WAVE BATCH: the push of batch $ids to $base was refused twice, so each run lands alone"; rm -rf "$S"; return 10; }
    try=2
  done
  echo "WAVE BATCH $ids $(sc rev-parse HEAD) landed in $base (${#rs[@]} waves, one tip gate)"
  # Each run's books, in its own range of the stack: run i spans tips[i-1]..tips[i], the first run bstart..tips[0].
  for i in $(seq $(( ${#rs[@]} - 1 )) -1 0); do
    run=${rs[$i]}; w=${ws[$i]}; rd=${rds[$i]}; sha=${tips[$i]}; SG_REQUEUE_RUN=$run
    k=$bstart; [ "$i" = 0 ] || k=${tips[$((i - 1))]}
    if [ "$run" = "$self0" ]; then land_read "$k" "$sha"; land_books; brc=$?
    else
      { land_read "$k" "$sha"; land_books; } 2>&1 | sed "s|^|BATCH $run: |"
      [ "${PIPESTATUS[0]}" = 0 ] && : > "$SG_STATE/auto/wland-$run"
    fi
  done
  for i in "${!ers[@]}"; do
    er=${ers[$i]}; erd=${erds[$i]}
    [ "$er" = "$self0" ] && continue
    ( land "$er" "$erd" ) 2>&1 | sed "s|^|BATCH $er: |"
    [ "${PIPESTATUS[0]}" = 0 ] && : > "$SG_STATE/auto/wland-$er"
  done
  for er in "${ers[@]}"; do [ "$er" = "$self0" ] && { ( land "$self0" "$selfrd" ); brc=$?; }; done
  rm -rf "$S"; return $brc
}

# salvage <run> <issue>: push the head of the child that worked <issue> to fix/<issue>-salvage-<run>, a work branch, so the wave's
# age cap sends the issue back to the queue only after its commits reach a remote branch (#2030, #2231). The branch is the one a
# finished run with no wave already pushes (childcc.py): branchreview reviews it and branchland lands it, and the frontier skips the
# issue while it is open (workbranches.sh). stdout is the branch name, or nothing when
# the record names no child of that issue, the child's worktree is gone, or the child stands on the base. A push it cannot
# make names its reason on stderr and prints nothing on stdout, so the caller adds no branch to its comment and the requeue
# goes on: a saved branch is a help, never a condition of the requeue.
# (#2310) A commit the base already holds by patch is no work of the child's own: `git cherry` marks it `-`, and a head with no
# `+` line would open a branch that the review approves and the landing can never fill. The head is read by patch, never by
# `rev-list --count`: a rebase or a cherry-pick gives the base's copy another sha, and the count reads it as the child's work.
# Such a head pushes nothing, names the reason on stderr, and sets SALVAGE_WHY, so the requeue names why no branch carries
# the work. The stdout contract never changes: it is the branch name or nothing, and SALVAGE_BR carries it for a caller that
# must read both answers without parsing (salvage_note). A `git cherry` that cannot run proves nothing: no reason, no branch.
salvage() {
  local run=${1:?} n=${2:?} rd child ws wt head ahead br err
  SALVAGE_BR=""; SALVAGE_WHY=""   # the two answers of this call: the branch, and why no branch carries the work
  rd=$(run_dir "$run")
  read -r child ws < <(python3 - "$rd/record.jsonl" "$n" <<'PY'
import json,re,sys
rec,n=sys.argv[1],sys.argv[2]
try: f=open(rec,errors="replace")
except OSError: sys.exit(0)
with f:
    for l in f:
        if '"spawn_accepted"' not in l: continue
        try: d=json.loads(l)
        except ValueError: continue
        if not isinstance(d,dict) or d.get("event")!="spawn_accepted": continue
        if not re.search(r"#"+re.escape(n)+r"\b",json.dumps(d.get("definition"))): continue
        w=d.get("definition",{}) if isinstance(d.get("definition"),dict) else {}
        w=w.get("workspace")
        print(d.get("child") or "", w.replace("/","_") if w not in (None,"",".") else "target"); break
PY
)
  [ -n "${child:-}" ] || return 0
  wt="$rd/ws/${ws:-target}-$child"
  [ -d "$wt" ] || { echo "salvage: the worktree of $child is not there: $wt" >&2; return 0; }
  head=$($G -C "$wt" rev-parse -q --verify HEAD 2>/dev/null) || return 0
  $G -C "$wt" rev-parse -q --verify "origin/$base" >/dev/null 2>&1 || return 0
  ahead=$($G -C "$wt" cherry "origin/$base" "$head" 2>/dev/null) || return 0   # a read that fails proves nothing
  case "$ahead" in *'+ '*) ;; *)
    SALVAGE_WHY="its head carries no commit that is not on $base by patch"; echo "salvage: $SALVAGE_WHY" >&2; return 0;; esac
  br="fix/$n-salvage-$run"; err="$SG_STATE/salvage-$n.err"
  $G -C "$wt" push -q origin "HEAD:refs/heads/$br" >/dev/null 2>"$err" || { echo "salvage: the push of ${head:0:9} to $br failed: $(tail -1 "$err" 2>/dev/null)" >&2; return 0; }
  SALVAGE_BR=$br; printf '%s\n' "$br"
}

# salvage_note <run> <issue>: the clause the requeue adds about the salvage: the branch the child's work is on, or the reason
# no branch carries it (#2310). Prints nothing when the salvage names neither. It calls salvage in this shell and reads its two
# answer variables, so no caller parses `WHY:` out of a branch name: salvage's stdout stays the branch or nothing.
salvage_note() {
  SALVAGE_BR=""; SALVAGE_WHY=""
  salvage "$1" "$2" >/dev/null
  case "$SALVAGE_WHY" in
    ?*) printf '%s' "$SALVAGE_WHY";;
    *) [ -n "$SALVAGE_BR" ] && printf 'its commits are on %s, for a review and a landing' "$SALVAGE_BR";;
  esac
}

case "${1:-}" in
  start) shift; start "$@";;
  enter) shift; enter "$@";;
  land) shift; land_queue "$@";;
  expired) shift; expired "$@";;
  landconflict) shift; landconflict "$@";;
  landempty) shift; landempty "$@";;
  requeue) shift; requeue "$@";;
  salvage) shift; salvage "$@";;
  fail) shift; fail "$@";;
  probe-retry) shift; probe_retry "$@";;
  retire) shift; retire_run "$@";;
  *) echo "usage: wave.sh start <run> | wave.sh enter <run> <issue> | wave.sh land <run> | wave.sh expired <run> | wave.sh requeue <issue> <why> [cap] | wave.sh salvage <run> <issue> | wave.sh fail <issue> <run> <why> | wave.sh probe-retry <issue> | wave.sh retire <run>" >&2; exit 2;;
esac
