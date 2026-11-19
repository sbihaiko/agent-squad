#!/bin/bash
# usage: fast-gate.sh      the fast gate of a work-branch tip or of a child that enters a wave (ADR-0145), run in that tip's worktree.
#
# It is the base battery (ruff, mypy and the fast pytest, SG_FAST_CMD) at the `fast` stage, so the pytest runs the delta the
# tip reaches in one pass; a child's own gate narrows a delta the selector cannot place instead of widening it (#2085, the
# `narrow` battery argument below), while the wave entry, the wave tip and the landing check keep the full-battery fallback
# (ADR-0169 rules 4 and 8): the parallel run records every unmarked call at or over the project's 2 s line (the line the CI
# serial tier enforces), and only those calls run once more, serially, against it (#1726), then the shell suite test-<script>.sh of every .sh or .py
# script under contrib/squad-goal/scripts that the tip changes, and every fixture suite whose `# mirrors:` line names a changed
# file; a changed test-<script>.sh runs itself. The changed files are the diff of HEAD
# against its merge-base with SG_FAST_BASE (default origin/$SG_BASE, else origin/dev), so work that landed on the base after the
# branch point adds no suite. A base that does not resolve fails the gate, never skips the suites. Each suite runs hermetic, as
# shell-suites.sh runs it: no SG_* variable, its own HOME. The promotion gate is the full suite (promote-stg.sh), not this.
# The gate runs under the machine-wide gate lock (gate.sh) and leaves the fast status line the hub reads. Exit 0 when all pass,
# 1 on a defect, 2 when the base does not resolve, 4 when the verdict is unproven: the battery is inconclusive twice (#699), a
# tool is absent (spec 1163), or the delta selected no test, so no pytest ran (#1249). Exit 0 is pass and 1 is fail; 2 and 4 are
# unproven: the entry gate and the landing measure the tree again and carry its obligation (ADR-0164), so a child caller gets one
# bounded or inconclusive attempt per key (#1527): a second run at the same key exits 4 at once, before the lock, and runs no
# battery; an entry or tip caller always measures. SG_FAST_MEMO=0 turns both memos off. The verdict, one of pass, fail
# or unproven with its reason and the checks that ran, goes to the history line through GATE_OUTCOME_FILE.
# A pass is kept under its key (#955), so a second gate at the same key runs no battery; a red is never kept. SG_FAST_MEMO=0 turns it off.
# `--memo-hit` asks that key alone, with no lock, no slots and no battery: exit 0 when a pass answers it, else 1 with the reason,
# so a wave entry whose child finished on its own green gate enters without waiting behind another gate (ADR-0168, #1718).
# One deadline covers the battery and its retry (#1171): gate.sh hands the clock down, and a direct run starts its own. Past it the
# battery's tree is stopped and the log decides: a class it already named is the gate's answer, so a failing test printed before
# the hang ends fail, and an attempt that reported nothing is bounded and unproven, never a pass. A retry spends the same clock,
# and a retry the deadline stops never clears the red the battery named (#1170).
# The gate carries a ceiling of its own (#1839, ADR-0051): whatever clock the caller hands down -- a proof's bound, a run's call
# deadline, or a long one -- a gate never runs past SG_GATE_MAX_SECONDS (900 by default), so a caller that never answers cannot
# park the gate and hold its worker slots. The gate takes the smaller of the two clocks and never widens the caller's, so a bounded
# proof stays bounded where it was and a bounded gate never widens a proof (ADR-0163, the same rule at two layers). Past the
# ceiling the battery's or a suite's whole tree is killed, one line names the bound and that tree, the outcome is unproven, and
# the lease goes back as on any other exit.
here="$(cd "$(dirname "$0")" && pwd)"
base=${SG_FAST_BASE:-origin/${SG_BASE:-dev}}
# The base battery is one definition (base-battery.sh), the one branchland.sh's landing check and wave.sh's tip check run
# over the same fast gate. Its pytest takes a bounded worker count, SG_GATE_WORKERS, never `auto`.
. "$here/base-battery.sh"
. "$here/caps.sh"   # sg_gate_python: the one interpreter lookup (#1086), never a second copy of it here
sg_git_env_scrub   # (#2306) no suite or battery writes a GIT_DIR its caller exported
py=$(sg_gate_python)
# (#1872, #2085) A child's count is one, whatever the selector answers: its battery is a delta, and a change the delta cannot
# place runs no pytest at all (the `narrow` battery below), so no child gate is ever a full battery that would need the whole
# count to fit its bound (ADR-0163, ADR-0018). The full-battery wait belongs to the delivery's own gates, which hold the lock.
child_count=${SG_GATE_WORKERS_CHILD:-1}
# (#1436, #1865, #1527) A child's own gate takes one gate slot, so three children land at once on a three-slot budget; an entry
# or a tip gate keeps the full count. The count never rides on SG_GATE_WORKERS: env.sh exports it for the whole loop, so a child
# would inherit six and hold the whole budget, and `:-` substitutes only an unset name. A caller that wants another count names
# SG_GATE_WORKERS_CHILD. It is set before the battery command is built, so the memo key read before the lock names the same
# count as the key stored inside it.
case ":${SG_SERIAL_HELD:-}:" in *":gate:"*) ;;
  *) [ "${SG_GATE_CALLER:-child}" = child ] && export SG_GATE_WORKERS=$child_count;; esac
# (#2085, ADR-0169 rule 4) Only the child's own gate narrows: it is bounded (ADR-0163) and waits behind its siblings' gates, so
# a change its delta cannot place runs no pytest there and the gate ends unproven with that reason, while the landing gate runs
# the full battery over the branch. A caller that brings its own battery names its scope (the wave tip and the landing check),
# and the wave entry keeps the fallback that proves the tree before it enters (rule 8). The narrowed and the widened batteries
# carry different text, so a pass won by one never answers the other (ADR-0168 rule 1).
narrow=no
if [ -z "${SG_FAST_CMD:-}" ] && [ "${SG_TEST_SELECT:-yes}" != no ] && [ "${SG_GATE_CALLER:-child}" = child ]; then narrow=yes; fi
fastcmd=${SG_FAST_CMD:-"$(sg_base_battery "$py" "$base" fast "$narrow")"}
# (#1557) The retry keeps the serial 2 s line: it lifts no cap, so it cannot clear the slow unmarked call the serial pass found.
retrycmd=${SG_FAST_RETRY_CMD:-"$py -m pytest -p no:deepeval -p no:xdist"}
# The memo key names what the suite reads: the tree's content (HEAD's tree object, never its commit), its uncommitted state,
# the base ref, the merge-base the delta starts from, and the battery text. The base commit is no part of the key (#1408): a
# landing moves it at every turn, and the merge-base already names what the delta reads, so a pass still answers after the base
# moves. The base must still resolve, else there is no key. A change to the merge-base, the tree or the battery is a new key. The battery runs over the tree, so
# a pass won on a dirty tree must never answer for the commit. A rebase that keeps every file rewrites the commit sha and the
# tree object stays, so the pass still answers (#1205); a commit that changes a file is a new tree and runs the battery. It is
# read before the machine lock, so a memo hit waits for no gate, and again inside the lock, so two gates that miss together
# share one battery.
st=${SG_STATE:-/tmp/sg-state}
memokey=
if [ "${SG_FAST_MEMO:-1}" != 0 ]; then
  bsha=$(git rev-parse --verify -q "$base^{commit}" 2>/dev/null); hsha=$(git rev-parse --verify -q HEAD 2>/dev/null)
  tsha=$(git rev-parse --verify -q "HEAD^{tree}" 2>/dev/null); mb=$(git merge-base HEAD "$base" 2>/dev/null)
  dirt=$( { git status --porcelain; git diff HEAD; git ls-files -o --exclude-standard -z | while IFS= read -r -d '' f; do printf '%s\n' "$f"; cat -- "$f" 2>/dev/null; done; } 2>/dev/null | cksum | cut -d' ' -f1)
  [ -n "$bsha" ] && [ -n "$hsha" ] && [ -n "$tsha" ] && [ -n "$mb" ] \
    && memokey="$tsha ${dirt:-x} $base $mb $(printf '%s\n%s' "$fastcmd" "$retrycmd" | cksum | cut -d' ' -f1)"
fi
memo_file() {  # <key>: the file that holds a key; the name is a checksum, the key inside it is compared whole
  echo "$st/gate-memo/$(printf '%s' "$1" | cksum | cut -d' ' -f1)"
}
memo_store() {  # <key> <log>: a temp file and a mv, so no reader sees half of it
  local f; mkdir -p "$st/gate-memo" 2>/dev/null || return 0
  f=$(memo_file "$1"); printf '%s\t%s\n' "$1" "$2" > "$f.$$" && mv -f "$f.$$" "$f"
}
memo_reuse() {  # <key> <sha> <log>: a green pass answers this key; write what a memo hit writes, and nothing else
  # The hit writes the pass line itself. The line names the current commit, not the tree object: the pass answers for the tip
  # the gate stands on. (#1856, #1798, ADR-0168) The gate is cancelled, and it is still one row of the history, mode `reused`:
  # inside a gate the command marks the gate's own line as a reuse, so the reader counts the saved gate; asked before any gate
  # (--memo-hit, the wave entry's probe), no other line is coming, so the hit writes one here through gate.sh, the file's one
  # writer, with wait, wall and cpu of 0 and the reuse named. The entry's reuse is never a silent skip (ADR-0168 rule 5).
  SG_GATE_STATUS=self bash "$here/gate.sh" --status fast pass "$2"
  if [ -n "${GATE_REUSE_FILE:-}" ]; then printf 'yes\n' > "$GATE_REUSE_FILE" 2>/dev/null || :
  else bash "$here/gate.sh" --reuse fast "$2" >/dev/null 2>&1 || :; fi
  # A memo reuse is no attempt (#1255): it is recorded as a reuse marker, and Gate Tries rises by none.
  local fgj=${SG_GATE_JOURNAL:-}
  if [ -n "${SG_GATE_CARD:-}" ]; then
    if [ -z "$fgj" ] && [ -n "${SG_BOARD_CACHE:-}" ] && [ -n "${SG_REPO:-}" ]; then
      fgj=$(python3 "$here/gate_attempts.py" journal "$SG_BOARD_CACHE" "$SG_REPO" 2>/dev/null) || fgj=
    fi
    [ -z "$fgj" ] || python3 "$here/gate_attempts.py" reuse "$fgj" "$SG_GATE_CARD" "$2" \
      "memo-${2:0:9}-$(printf '%s' "$1" | cksum | cut -d' ' -f1)" >/dev/null 2>&1 || :
  fi
}
# (ADR-0168, #1718) --memo-hit asks the memo before a caller takes the machine gate lock: a wave entry whose child finished on
# its own green gate must not wait behind another gate for a battery that already ran. A hit writes the pass line and the reuse
# marker and exits 0 with no lock, no slots and no battery; a memo turned off, a tree or base the key cannot name, or no stored
# pass exits 1 with the reason, so the caller runs the gate as before. A red is never stored, so it never answers here.
if [ "${1:-}" = --memo-hit ]; then
  [ "${SG_FAST_MEMO:-1}" = 0 ] && { echo "fast-gate: no memo: SG_FAST_MEMO is 0, so no pass answers this tree"; exit 1; }
  [ -n "$memokey" ] || { echo "fast-gate: no memo key: the base does not resolve or the tree cannot be read, so no pass is kept for it"; exit 1; }
  phsha=$(git rev-parse --verify -q HEAD 2>/dev/null)
  if [ -n "$phsha" ] && [ -r "$(memo_file "$memokey")" ]; then
    IFS=$'\t' read -r mk ml < "$(memo_file "$memokey")"
    if [ "$mk" = "$memokey" ]; then
      memo_reuse "$memokey" "$phsha" "$ml"
      echo "fast-gate: memo hit ${phsha:0:9} (log $ml): the same tree, merge-base and battery passed, so no battery runs"
      exit 0
    fi
  fi
  echo "fast-gate: memo miss: no pass answers this tree, its uncommitted state, its base, its merge-base and its battery"
  exit 1
fi
if [ -n "$memokey" ] && [ -r "$(memo_file "$memokey")" ]; then
  IFS=$'\t' read -r mk ml < "$(memo_file "$memokey")"
  if [ "$mk" = "$memokey" ]; then
    memo_reuse "$memokey" "$hsha" "$ml"
    echo "fast-gate: pass ${hsha:0:9} (log $ml) memo: the same tree, merge-base and battery passed, so no battery ran"; exit 0
  fi
fi
# (#1527) A bounded or inconclusive attempt is kept under its key, apart from the pass memo. A child caller that gates the same tree
# again runs no battery: its one attempt at this head already ran, and the entry gate and the landing measure the tree again.
bmemo_file() { echo "$st/gate-memo-bounded/$(printf '%s' "$1" | cksum | cut -d' ' -f1)"; }
bmemo_store() {  # <key> <log>: the same temp file and mv as memo_store
  local f; mkdir -p "$st/gate-memo-bounded" 2>/dev/null || return 0
  f=$(bmemo_file "$1"); printf '%s\t%s\n' "$1" "$2" > "$f.$$" && mv -f "$f.$$" "$f"
}
if [ -n "$memokey" ] && [ "${SG_GATE_CALLER:-child}" = child ] && [ -r "$(bmemo_file "$memokey")" ] \
  && [ "$(cut -f1 "$(bmemo_file "$memokey")")" = "$memokey" ]; then
  echo "fast-gate: bounded already at ${hsha:0:9}: a child spends one bounded attempt per head (#1527); the entry gate and the landing own the obligation (ADR-0164), so commit and report done (log $(cut -f2 "$(bmemo_file "$memokey")"))"
  exit 4
fi
# (#1436, #1865) A child's own gate asks for one slot and an entry or tip gate for the full count: three children fit a
# three-slot budget, and a landing is never starved by the children that feed it, because a queued landing keeps the lock
# (ADR-0174) and the children stop taking slots while it waits. A count the caller names as SG_GATE_WORKERS_CHILD wins over the
# child's own; the count env.sh exports for the loop never does.
case ":${SG_SERIAL_HELD:-}:" in *":gate:"*) ;;
  *) export SG_GATE_CALLER=${SG_GATE_CALLER:-child}
     if [ "$SG_GATE_CALLER" = child ]; then export SG_GATE_WORKERS=$child_count; fi
     # (ADR-0166) A child waits for a free slot inside its deadline; the wait is gate wait, never run time, because the gate
     # starts its clock only once it holds the lock and its slots.
     slot_by=$(( $(date +%s) + ${SG_CALL_DEADLINE_SECONDS:-1800} )); waited=0
     while :; do
       SG_GATE_KIND=fast bash "$here/gate.sh" bash "$0" "$@"; fgrc=$?
       [ "$fgrc" = 73 ] && [ "$SG_GATE_CALLER" = child ] && [ "$(date +%s)" -lt "$slot_by" ] || break
       [ "$waited" = 1 ] || { waited=1; echo "fast-gate: gate wait: no gate slot is free (caps.gates full); waiting for one, not run time"; }
       sleep 1
     done
     # (#1684) Exit 73 is the machine's budget, never this tree's: the gate took no slot, so it proved nothing and it broke
     # nothing. It answers unproven (exit 4), the answer every caller already reads as the machine's: branchland's
     # inconclusive path opens no fix round and gates the tip again on a later cycle, so a child keeps the work it committed
     # instead of losing it to a budget another gate held.
     # (#1715) The line carries what the child does next: a line that said the gate does not pass read as a failed run, and
     # the loop started a new run that repeated the work.
     # (#2330) A 75 is a tip or landing check whose bounded slot wait ran out, and gate.sh named it on the budget's line. It
     # passes through unchanged: the landing reads it as a wait and keeps its queue, where a 4 would land each run alone.
     [ "$fgrc" = 73 ] && { echo "fast-gate: inconclusive: the machine's gate budget is full, so no battery ran and nothing is red; unproven (exit 4): commit and report done, the entry gate and the landing own the proof (ADR-0164)"; exit 4; }
     exit "$fgrc";; esac
# The base the gate read the tree against goes out beside the scope (GATE_BASE_FILE), so an unproven entry's obligation
# names it (gate.sh, spec 1163): the commit when the base resolves, else the ref as it was named.
gbase=$(git rev-parse --verify -q "$base^{commit}" 2>/dev/null)
[ -z "${GATE_BASE_FILE:-}" ] || printf '%s\n' "${gbase:-$base}" > "$GATE_BASE_FILE"
# verdict <outcome> <reason> <ran> <not run>: the verdict the history line carries (spec 1163). gate.sh hands the file over and reads it
# inside the lock; a field is never empty, so a dash stands for none.
verdict() {
  [ -z "${GATE_OUTCOME_FILE:-}" ] || printf '%s\t%s\t%s\t%s\n' "$1" "$(printf '%s' "$2" | tr '\t' ' ')" "$3" "$4" > "$GATE_OUTCOME_FILE"
}
mb=$(git merge-base HEAD "$base" 2>/dev/null) || { verdict unproven "base $base does not resolve, so the changed scripts are unknown" - "ruff mypy pytest"; echo "fast-gate: base $base does not resolve: the changed scripts are unknown, so the gate does not pass"; exit 2; }
top=$(git rev-parse --show-toplevel) || exit 2
# classify <log> <rc>: the class of a red battery (#699), one word, then its detail: lint (ruff's code and path, #670, #671), type
# (mypy's line), collection (the files pytest could not collect), pytest (the failed or errored test ids), absent (ruff, mypy or
# pytest is not installed in the battery's interpreter, spec 1163), saturation (a worker crashed, or a signal killed the battery)
# or unknown (none of these). A red that names no failed test is never read as one cause: it is a lint, type or collection
# defect, or it is inconclusive.
ids_of() { sed -n -e 's/^FAILED \([^ ]*\).*/\1/p' -e 's/^ERROR \([^ ]*::[^ ]*\).*/\1/p' "$1" | sort -u; }
classify() {
  local log=$1 rc=$2 p f
  p=$(sed -n -e 's/^\([A-Z][A-Z]*[0-9][0-9][0-9]*\) \[[^]]*\] \(.*\)$/\1 \2/p' -e 's/^\([A-Z][A-Z]*[0-9][0-9][0-9]*\) \([^[].*\)$/\1 \2/p' "$log" | head -n 1)   # with or without the fix marker (#671)
  if [ -n "$p" ]; then f=$(sed -n 's/^ *--> \(.*\)$/\1/p' "$log" | head -n 1); echo "lint the base battery failed at ruff: ${f:+$f }$p"; return; fi
  p=$(sed -n 's/^\([^ ]*:[0-9][0-9]*\): error: \(.*\)$/\1: \2/p' "$log" | head -n 1)
  [ -n "$p" ] && { echo "type the base battery failed at mypy: $p"; return; }
  p=$(sed -n -e 's/^ERROR collecting \([^ ]*\).*/\1/p' -e 's/^ERROR \([^ :]*\.py\)\( .*\)*$/\1/p' "$log" | sort -u | tr '\n' ' ' | sed 's/ $//')
  if [ -n "$p" ] || /usr/bin/grep -qE 'errors? during collection' "$log"; then echo "collection pytest could not collect ${p:-its tests}"; return; fi
  p=$(ids_of "$log" | tr '\n' ' ' | sed 's/ $//')
  [ -n "$p" ] && { echo "pytest the base battery failed at $p"; return; }
  p=$(/usr/bin/grep -oE 'No module named [^a-z]*(ruff|mypy|pytest)([^A-Za-z0-9_.]|$)' "$log" | head -n 1 | sed -E 's/.*named [^a-z]*([a-z]+).*/\1/')
  [ -n "$p" ] && { echo "absent $p is absent from the battery's interpreter, so its check did not run"; return; }
  if [ "$rc" -ge 128 ] || /usr/bin/grep -qE 'node down|crashed worker|worker .*crashed|^Killed|MemoryError|Cannot allocate memory|Too many open files|Resource temporarily unavailable' "$log"; then
    echo "saturation the battery exited $rc with a crashed worker or a kill, and no test id"; return; fi
  echo "unknown the battery exited $rc and named no lint, type, collection or test failure"
}
# A test that fails under the parallel battery and passes alone is saturation, not a defect (#520): the failed tests the log
# names run once more, serially (pyproject turns the pytest cache off, so --lf would rerun the whole battery). A red there too
# fails the gate. A saturation or unknown red is inconclusive: the battery runs once more, and only once. A pass there goes
# on; a defect there fails as that defect; inconclusive again exits 4, and the landing holds. The battery output stays in a
# log that the failure line names. Exit 1 is a defect, 4 is inconclusive; neither grants a landing.
logdir=${SG_FAST_LOG_DIR:-${TMPDIR:-/tmp}}   # never SG_STATE (#945): the child that ran the gate may not open a file there
mkdir -p "$logdir" 2>/dev/null
gcd=$(git rev-parse --git-common-dir 2>/dev/null); [ -n "$gcd" ] && gcd=$(cd "$top" && cd "$gcd" 2>/dev/null && pwd -P)   # the real repository, never the worktree
# The name is the real repository, the tip and a pid: every child runs in ws/target-<child>, so a worktree basename collides
# across runs, and two runs at one short sha would write this log at the same time (#945).
log=$logdir/fast-gate-$(printf '%s' "${gcd:-$top}" | cksum | cut -d' ' -f1)-$(git rev-parse --short HEAD 2>/dev/null || echo x)-$$.log
# (#1171) One clock covers the battery and its retry. gate.sh starts it when it takes the lock and hands the deadline down;
# a gate run without that wrapper starts its own. Past the deadline the battery's whole tree is stopped, and the log it wrote
# decides: a class it already named is the gate's answer (a red a check reported survives the kill), and an attempt that
# reported nothing is bounded and unproven, never a pass.
bound=${GATE_BOUND_SECONDS:-${SG_GATE_BOUND_SECONDS:-900}}; case $bound in ''|*[!0-9]*|0) bound=900;; esac
# (#1839) The gate's own ceiling (ADR-0051), taken beside the caller's clock and never in place of a shorter one: SG_GATE_MAX_SECONDS,
# 900 by default. A gate_max attempt is one the ceiling ended, and its line names that bound and the killed tree.
now0=$(date +%s)
maxb=${SG_GATE_MAX_SECONDS:-900}; case $maxb in ''|*[!0-9]*|0) maxb=900;; esac
deadline=${GATE_DEADLINE_EPOCH:-$(( now0 + bound ))}
gate_max=0
if [ "$deadline" -gt $(( now0 + maxb )) ]; then deadline=$(( now0 + maxb )); gate_max=1; fi
[ "$bound" -gt "$maxb" ] && bound=$maxb
left() { local l=$(( deadline - $(date +%s) )); [ "$l" -lt 1 ] && l=1; echo "$l"; }
tree_pids() {  # <pid>: the pid and every descendant it started, children first
  local c; for c in $(pgrep -P "$1" 2>/dev/null); do tree_pids "$c"; done; printf '%s\n' "$1"
}
stop_tree() {  # <pid>: the tree as it stands now, TERM then KILL, so a child that ignores TERM still goes
  local list x; list=$(mktemp "${TMPDIR:-/tmp}/fg-stop.XXXXXX" 2>/dev/null)
  if [ -z "$list" ]; then kill -TERM "$1" 2>/dev/null; sleep 2; kill -KILL "$1" 2>/dev/null; return 0; fi
  tree_pids "$1" > "$list"
  while read -r x; do [ -n "$x" ] && kill -TERM "$x" 2>/dev/null; done < "$list"; sleep 2
  while read -r x; do [ -n "$x" ] && kill -KILL "$x" 2>/dev/null; done < "$list"
  rm -f "$list"
}
# reap_wd <pid>: the watchdog's own group when it leads one, its children and its pid when it does not, so the sleep it
# forked is never reparented and left to run out its bound (#1441).
reap_wd() { local p; [ "$($SG_PS -o pgid= -p "$1" 2>/dev/null | tr -d ' ')" = "$1" ] && kill -- -"$1" 2>/dev/null
  for p in $(pgrep -P "$1" 2>/dev/null); do kill "$p" 2>/dev/null; done; kill "$1" 2>/dev/null; wait "$1" 2>/dev/null; }
bounded() {  # <seconds> <command...>: 124 says the deadline stopped it and its whole tree; 0 or less runs with no bound
  local b=$1 p w mark s rc; shift; s=$(date +%s); BTREE=
  if [ "$b" -le 0 ] 2>/dev/null; then "$@"; return $?; fi
  mark=$(mktemp "${TMPDIR:-/tmp}/fg-bound.XXXXXX" 2>/dev/null) || { "$@"; return $?; }; rm -f "$mark"
  "$@" & p=$!
  set -m   # the watchdog leads its own process group, so reap_wd reaches the sleep it forks
  # (#1839) The mark carries the tree the deadline kills, so the line the gate answers can name it; its existence alone is the
  # flag that the deadline fired, as before.
  ( sleep "$b"; kill -0 $$ 2>/dev/null || exit 0; [ $(( $(date +%s) - s )) -ge "$b" ] || exit 0; tree_pids "$p" > "$mark"
    stop_tree "$p" ) >/dev/null 2>&1 & w=$!
  set +m
  wait "$p" 2>/dev/null; rc=$?
  reap_wd "$w"
  if [ -e "$mark" ]; then BTREE=$(tr '\n' ' ' < "$mark" 2>/dev/null | sed 's/ *$//'); rm -f "$mark"; return 124; fi
  rm -f "$mark"; return $rc
}
for try in 1 2; do
  lg=$log; [ $try = 1 ] || lg=${log%.log}-retry.log
  bounded "$(left)" bash -c "$fastcmd" > "$lg" 2>&1 && break
  rc=$?; tail -n 5 "$lg"
  c=$(classify "$lg" "$rc"); cls=${c%% *}; detail=${c#* }
  if [ "$rc" = 124 ]; then
    case $cls in
      absent)
        case $detail in ruff*) r=- n="ruff mypy pytest";; mypy*) r=ruff n="mypy pytest";; *) r="ruff mypy" n=pytest;; esac
        verdict unproven "$detail" "$r" "$n"; echo "fast-gate: $cls: $detail; the battery passed its ${bound}s deadline ($lg)"; exit 4;;
      lint|type|collection|pytest)
        case $cls in lint) r=ruff n="mypy pytest";; type) r="ruff mypy" n=pytest;; *) r="ruff mypy pytest" n=-;; esac
        verdict fail "$detail" "$r" "$n"
        echo "fast-gate: $cls: $detail; the battery passed its ${bound}s deadline with the defect already reported ($lg)"; exit 1;;
      *)
        # (#1839) A battery the gate's own ceiling ended answers on the ceiling: the one line names the bound and the tree it killed.
        if [ "$gate_max" = 1 ]; then
          verdict unproven "bounded: the gate passed its ${maxb}s ceiling and killed the battery's tree (${BTREE:-unknown})" unknown unknown
          [ -z "$memokey" ] || bmemo_store "$memokey" "$lg"
          echo "fast-gate: gate max: the gate passed its ${maxb}s ceiling (SG_GATE_MAX_SECONDS) and killed the battery's tree (${BTREE:-unknown}); unproven (exit 4) ($lg)"; exit 4
        fi
        verdict unproven "bounded: the battery passed its ${bound}s deadline and named no defect" unknown unknown
        [ -z "$memokey" ] || bmemo_store "$memokey" "$lg"
        echo "fast-gate: bounded: the battery passed its ${bound}s deadline and named no defect, so the gate does not pass ($lg)"; exit 4;;
    esac
  fi
  case $cls in
    absent)
      case $detail in ruff*) r=- n="ruff mypy pytest";; mypy*) r=ruff n="mypy pytest";; *) r="ruff mypy" n=pytest;; esac
      verdict unproven "$detail" "$r" "$n"; echo "fast-gate: $cls: $detail; not retried ($lg)"; exit 4;;
    saturation|unknown)
      [ $try = 1 ] && { echo "fast-gate: $cls: $detail; inconclusive, so the battery runs once more ($lg)"; continue; }
      verdict unproven "$detail" unknown unknown
      [ -z "$memokey" ] || bmemo_store "$memokey" "$lg"
      echo "fast-gate: $cls: $detail; inconclusive twice, so the gate does not pass and the landing holds ($lg)"; exit 4;;
    pytest)
      # The retry spends the same clock: a retry that outlives it never clears the red the battery named.
      # shellcheck disable=SC2086  # one word per test id; set -f keeps a parametrized id such as t[a.sh] from globbing
      set -f; bounded "$(left)" bash -c "$retrycmd \"\$@\"" retry $(ids_of "$lg") >> "$lg" 2>&1; rrc=$?; set +f
      if [ $rrc = 0 ]; then
        echo "fast-gate: the base battery failed under load and its failed tests passed alone: saturation, not a defect ($lg)"; break; fi
      verdict fail "$detail" "ruff mypy pytest" -
      [ $rrc = 124 ] && { echo "fast-gate: pytest: $detail; the retry outlived the ${bound}s deadline, so the red stands ($lg)"; exit 1; }
      echo "fast-gate: pytest: $detail, and they fail alone too ($lg)"; exit 1;;
    lint|type|collection)
      case $cls in lint) r=ruff n="mypy pytest";; type) r="ruff mypy" n=pytest;; *) r="ruff mypy pytest" n=-;; esac
      verdict fail "$detail" "$r" "$n"; echo "fast-gate: $cls: $detail ($lg)"; exit 1;;
  esac
done
# (#885) The pytest tally and the code-quality reading print together; the pipe's status is tee's, so the reading never changes the exit.
tally=$(/usr/bin/grep -E '[0-9]+ (passed|failed|errors?)' "$lg" | tail -n 1)
{ echo "fast-gate: pytest tally: ${tally:-none: no pytest count in the battery log}"; "$py" "$here/quality-reading.py" "$base"; } 2>&1 | tee -a "$lg"
# A fixture suite that copies a module list names it in its `# mirrors:` line; a change to any listed file runs that suite.
suites=$(sg_shell_suites "$top" "$mb")
bran="ruff mypy pytest"; bnot=-
# (#1249) A battery whose selection was empty ran no pytest, so it proves nothing: the line it logged names why.
empty=$(/usr/bin/grep -m 1 '^no pytest ran' "$lg")
[ -z "$empty" ] || { bran="ruff mypy"; bnot=pytest; }
failed=""; ranS=""; hitmax=
# (#1839) A suite spends the gate's clock too: past it its whole tree is killed, as the battery's, so a suite that parks holds the
# gate slot no longer than the ceiling allows.
run_suite() {  # <script> <home>: the suite in its own home, from the repository top, with no SG_* variable and no GATE_REUSE_FILE
  # (#1927) gate.sh --inner exports GATE_REUSE_FILE for the gate's own verdict; a fixture gate inside a suite that inherited it
  # would mark the outer gate's reuse from its own memo hit, and its own row would never land. The file belongs to this gate.
  ( cd "$top" && env $(compgen -e | /usr/bin/grep '^SG_' | sed 's/^/-u /') -u GATE_REUSE_FILE HOME="$2" bash "$1" )
}
for n in $suites; do s=$top/contrib/squad-goal/scripts/test-$n.sh; [ -f "$s" ] || continue
  echo "=== test-$n.sh"; ranS="$ranS test-$n.sh"
  hm=$(mktemp -d); bounded "$(left)" run_suite "$s" "$hm"; src=$?
  if [ "$src" = 0 ]; then echo "--- ok: test-$n.sh"; else
    [ "$src" != 124 ] || hitmax=test-$n.sh
    echo "--- FAILED: test-$n.sh"; failed="$failed test-$n.sh"; fi; rm -rf "$hm"; done
[ -z "$hitmax" ] || {
  if [ "$gate_max" = 1 ]; then
    verdict unproven "bounded: the gate passed its ${maxb}s ceiling and killed ${hitmax}'s tree (${BTREE:-unknown})" "$bran$ranS" "$bnot"
    echo "fast-gate: gate max: the gate passed its ${maxb}s ceiling (SG_GATE_MAX_SECONDS) and killed ${hitmax}'s tree (${BTREE:-unknown}); unproven (exit 4)"; exit 4
  fi
  verdict unproven "bounded: the gate passed its ${bound}s deadline and killed ${hitmax}'s tree (${BTREE:-unknown})" "$bran$ranS" "$bnot"
  echo "fast-gate: bounded: the gate passed its ${bound}s deadline and killed ${hitmax}'s tree (${BTREE:-unknown}), so the gate does not pass"; exit 4; }
[ -z "$failed" ] || { verdict fail "failed suite:$failed" "$bran$ranS" "$bnot"; echo "fast-gate: FAILED:$failed"; exit 1; }
# A gate that ran no test is never a pass: the empty selection is unproven, with the selector's reason, and is never kept.
if [ -n "$empty" ]; then
  verdict unproven "$empty" "$bran$ranS" "$bnot"
  echo "fast-gate: unproven: $empty; the gate does not pass ($lg)"; exit 4
fi
verdict pass - "$bran$ranS" "$bnot"
# The verdict the child reads: an exit 0 is a pass, but the one line names it and the log the child may open, so a session that
# saw a lock wait on stderr still knows the gate passed (#945).
[ -z "$memokey" ] || memo_store "$memokey" "$lg"
sha=$(git rev-parse HEAD 2>/dev/null)
echo "fast-gate: pass ${sha:0:9} (log $lg)"
