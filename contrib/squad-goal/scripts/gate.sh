#!/bin/bash
# usage: gate.sh <command...>   Runs a gate under the machine-wide `gate` lock (#392), so two gates never run together.
# The branchland local gate, the wave entry gate, the promotion gate and a hand-started gate all come through here. A second gate
# waits for the first and then runs; it is never dropped, so the wait is long (SERIAL_WAIT_SECONDS, default 86400) and says so on one
# line naming the holder. A gate holder that died, or that outlived its run, loses the lock to the next gate (serial.sh).
# SERIAL_HOLD_SECONDS (default 7200) is the longest a gate may hold the lock. The exit code of the command is passed through.
# The hub reads the latest result from $SG_STATE/gate-status: `<epoch> TAB <kind> TAB <pass|fail> TAB <sha>`, and, when the
# gate ran the test battery, a fifth field, its selection (`selected N files` or `all: <reason>`; GATE_SELECT_FILE carries it
# out), and a sixth, the scope that selection names, `delta` or `full` (GATE_SCOPE_FILE carries it out). The reason in an
# `all:` line is why a delta refused.
# SG_GATE_KIND is fast for a tip or wave entry, full for a promotion to stg, and gate by default. A later gate replaces this
# hub line. A full result also writes $SG_STATE/gate-status-full, so a later fast result cannot replace promotion proof.
# The sha is the HEAD the gate ran at, read from the working directory. The lock lives in $SG_STATE/locks/gate. With SG_STATE
# unset it is the one machine-wide /tmp/sg-state, never a TMPDIR path: TMPDIR is per user on macOS, so two users or a launchd
# job would resolve two lock directories and run two gates together.
# A gate takes SG_GATE_WORKERS gate slots (#566) of the machine-wide budget (caps.gates, squad/caps.py) BEFORE it takes the lock, in
# one locked step, so the lock holder never spends its hold deadline waiting for slots. It takes all it asks for when they are free,
# else the free slots when they number two or more (#734), so a third gate runs with 2 to 4 workers instead of giving up; fewer than
# two free is no take, so a gate never holds part of its slots while it waits for the rest. SQUAD_GATE_WORKERS is the count the gate
# actually took, and the battery's pytest runs with it. Slots are re-entrant as the lock is: a gate inside a gate holder
# (SG_SERIAL_HELD names gate) takes none and keeps the holder's SQUAD_GATE_WORKERS. A project file that lowers
# caps.gates below SG_GATE_WORKERS gives the gate min(workers, ceiling) slots, never a wait for slots that cannot exist. Try mode (SERIAL_TRY) skips a full budget at once (73); a wait is
# bound by SERIAL_WAIT_SECONDS and by the call deadline of a run (SG_CALL_DEADLINE_SECONDS), says so on one line, and ends in 75.
# `gate.sh --status <kind> <pass|fail> <sha>` writes status for a gate whose sha is not the working directory's HEAD: a command
# that calls it sets SG_GATE_STATUS=self, so the gate does not overwrite it with the HEAD it was started in. Each line is written
# by a temp file and a mv, so no reader sees half of it. SG_STATE unset: /tmp/sg-state, never TMPDIR.
# Each finished gate also appends one line to $SG_STATE/gate-history: `<finish epoch> TAB <kind> TAB <result> TAB <sha> TAB <start epoch>
# TAB <selection> TAB <scope> TAB <outcome> TAB <reason> TAB <ran> TAB <not run> TAB <slots>`. The selection and the scope are empty
# when the gate ran no battery or names none. The outcome is pass, fail or unproven (spec 1163): the gate's own verdict, written by
# the command to GATE_OUTCOME_FILE as `<outcome> TAB <reason> TAB <ran> TAB <not run>`; with none, pass or fail by the exit code,
# and a gate stopped before any check answered is unproven. The result stays pass or fail, as the hub reads it. The reason is one
# line, `-` when none; ran and not run list the checks, `-` when none; slots is the count of gate slots the gate took. The duration
# is finish minus start, and start is read inside the lock, after the wait: it is the gate's run time, never its queue time. A gate
# that sets SG_GATE_STATUS=self writes no history line, as it writes no status line. The file only grows: the throughput report
# reads it, and nothing rewrites it.
here="$(cd "$(dirname "$0")" && pwd)"; st=${SG_STATE:-/tmp/sg-state}
write_status() {  # <path> <line>
  printf '%s\n' "$2" > "$1.$$" && mv -f "$1.$$" "$1"
}
status() {  # <kind> <result> <sha> [<test selection>] [<test scope>]
  mkdir -p "$st" 2>/dev/null || return 0
  printf -v line '%s\t%s\t%s\t%s' "$(date +%s)" "$1" "$2" "$3"
  [ -z "${4:-}" ] || line="$line"$'\t'"$4"
  [ -z "${5:-}" ] || line="$line"$'\t'"$5"
  write_status "$st/gate-status" "$line" || return
  [ "$1" != full ] || write_status "$st/gate-status-full" "$line"
}
append_line() {  # <path> <line>: one short write at the end of the file, never a rewrite
  printf '%s\n' "$2" >> "$1"
}
gate_history() {  # <kind> <result> <sha> <start epoch> <selection> <scope> <outcome> <reason> <ran> <not run> <slots>
  mkdir -p "$st" 2>/dev/null || return 0
  printf -v line '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' "$(date +%s)" "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9" "${10}" "${11}"
  append_line "$st/gate-history" "$line"
}
case "${1:-}" in
  --status) [ $# = 4 ] || { echo "usage: gate.sh --status <kind> <pass|fail> <sha>" >&2; exit 2; }; status "$2" "$3" "$4"; exit 0;;
  --run)    # inside the lock: run the command, then write the line. A command the holder stops at its deadline (TERM) is a fail.
    shift; export GATE_SELECT_FILE GATE_SCOPE_FILE GATE_OUTCOME_FILE
    GATE_SELECT_FILE=$(mktemp "${TMPDIR:-/tmp}/gate-select.XXXXXX" 2>/dev/null) || GATE_SELECT_FILE=
    GATE_SCOPE_FILE=$(mktemp "${TMPDIR:-/tmp}/gate-scope.XXXXXX" 2>/dev/null) || GATE_SCOPE_FILE=
    GATE_OUTCOME_FILE=$(mktemp "${TMPDIR:-/tmp}/gate-outcome.XXXXXX" 2>/dev/null) || GATE_OUTCOME_FILE=
    start=$(date +%s)
    note() {  # <result> <outcome if the command wrote none> <reason if none>
      if [ "${SG_GATE_STATUS:-}" != self ]; then
        sha=$(git rev-parse HEAD 2>/dev/null); sel=$(cat "$GATE_SELECT_FILE" 2>/dev/null); scope=$(cat "$GATE_SCOPE_FILE" 2>/dev/null)
        oc= rs= ran= notran=
        [ -z "$GATE_OUTCOME_FILE" ] || IFS=$'\t' read -r oc rs ran notran < "$GATE_OUTCOME_FILE" 2>/dev/null
        [ -n "$oc" ] || { oc=$2; rs=$3; ran=-; notran=-; }
        status "${SG_GATE_KIND:-gate}" "$1" "$sha" "$sel" "$scope"
        gate_history "${SG_GATE_KIND:-gate}" "$1" "$sha" "$start" "$sel" "$scope" "$oc" "$rs" "${ran:--}" "${notran:--}" "${SQUAD_GATE_WORKERS:-0}"
      fi
      [ -z "$GATE_SELECT_FILE" ] || rm -f "$GATE_SELECT_FILE"
      [ -z "$GATE_SCOPE_FILE" ] || rm -f "$GATE_SCOPE_FILE"
      [ -z "$GATE_OUTCOME_FILE" ] || rm -f "$GATE_OUTCOME_FILE"
    }
    trap 'note fail unproven "the gate was stopped before any check answered"; exit 143' TERM
    "$@"; rc=$?; if [ $rc = 0 ]; then note pass pass -; else note fail fail "exit $rc"; fi; exit $rc;;
esac
[ $# -gt 0 ] || { echo "usage: gate.sh <command...>   (lock in \$SG_STATE/locks/gate; SG_STATE unset: /tmp/sg-state, never TMPDIR)" >&2; exit 2; }
export SERIAL_WAIT_SECONDS=${SERIAL_WAIT_SECONDS:-86400} SERIAL_HOLD_SECONDS=${SERIAL_HOLD_SECONDS:-7200}
case ":${SG_SERIAL_HELD:-}:" in *":gate:"*) ;; *)
  . "$here/caps.sh"; w=$SERIAL_WAIT_SECONDS
  [ "${SG_CALL_DEADLINE_SECONDS:-0}" -gt 0 ] 2>/dev/null && [ "$SG_CALL_DEADLINE_SECONDS" -lt "$w" ] && w=$SG_CALL_DEADLINE_SECONDS
  SQUAD_GATE_WORKERS=$(sg_caps take gates "gate-$$" --count "${SG_GATE_WORKERS:-6}" --pid $$ --target "$PWD" --wait "$w" ${SERIAL_TRY:+--try}) || exit $?
  export SQUAD_GATE_WORKERS;; esac
exec bash "$here/serial.sh" gate bash "$here/gate.sh" --run "$@"   # exec: the lock holder is this process, and --run writes the line inside the lock
