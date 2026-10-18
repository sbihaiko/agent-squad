#!/bin/bash
# usage: gate.sh <command...>   Runs a gate under the machine-wide `gate` lock (#392), so two gates never run together.
# The branchland local gate, the wave entry gate, the promotion gate and a hand-started gate all come through here. A second gate
# waits for the first and then runs; it is never dropped, so the wait is long (SERIAL_WAIT_SECONDS, default 86400) and says so on one
# line naming the holder. A gate holder that died, or that outlived its run, loses the lock to the next gate (serial.sh).
# SERIAL_HOLD_SECONDS (default 7200) is the longest a gate may hold the lock. The exit code of the command is passed through.
# The hub reads the latest result from $SG_STATE/gate-status: `<epoch> TAB <kind> TAB <pass|fail> TAB <sha>`.
# SG_GATE_KIND is fast for a tip or wave entry, full for a promotion to stg, and gate by default. A later gate replaces this
# hub line. A full result also writes $SG_STATE/gate-status-full, so a later fast result cannot replace promotion proof.
# The sha is the HEAD the gate ran at, read from the working directory. The lock lives in $SG_STATE/locks/gate. With SG_STATE
# unset it is the one machine-wide /tmp/sg-state, never a TMPDIR path: TMPDIR is per user on macOS, so two users or a launchd
# job would resolve two lock directories and run two gates together.
# A gate takes SG_GATE_WORKERS gate slots (#566) of the machine-wide budget (caps.gates, squad/caps.py) BEFORE it takes the lock, all
# at once or none, so the lock holder never spends its hold deadline waiting for slots. Slots are re-entrant as the lock is: a gate
# inside a gate holder (SG_SERIAL_HELD names gate) takes none and keeps the holder's SQUAD_GATE_WORKERS. A project file that lowers
# caps.gates below SG_GATE_WORKERS gives the gate min(workers, ceiling) slots, never a wait for slots that cannot exist, and the
# battery's pytest runs with that count (SQUAD_GATE_WORKERS). Try mode (SERIAL_TRY) skips a full budget at once (73); a wait is
# bound by SERIAL_WAIT_SECONDS and by the call deadline of a run (SG_CALL_DEADLINE_SECONDS), says so on one line, and ends in 75.
# `gate.sh --status <kind> <pass|fail> <sha>` writes status for a gate whose sha is not the working directory's HEAD: a command
# that calls it sets SG_GATE_STATUS=self, so the gate does not overwrite it with the HEAD it was started in. Each line is written
# by a temp file and a mv, so no reader sees half of it. SG_STATE unset: /tmp/sg-state, never TMPDIR.
here="$(cd "$(dirname "$0")" && pwd)"; st=${SG_STATE:-/tmp/sg-state}
write_status() {  # <path> <line>
  printf '%s\n' "$2" > "$1.$$" && mv -f "$1.$$" "$1"
}
status() {  # <kind> <result> <sha>
  mkdir -p "$st" 2>/dev/null || return 0
  printf -v line '%s\t%s\t%s\t%s' "$(date +%s)" "$1" "$2" "$3"
  write_status "$st/gate-status" "$line" || return
  [ "$1" != full ] || write_status "$st/gate-status-full" "$line"
}
case "${1:-}" in
  --status) [ $# = 4 ] || { echo "usage: gate.sh --status <kind> <pass|fail> <sha>" >&2; exit 2; }; status "$2" "$3" "$4"; exit 0;;
  --run)    # inside the lock: run the command, then write the line. A command the holder stops at its deadline (TERM) is a fail.
    shift; note() { [ "${SG_GATE_STATUS:-}" = self ] || status "${SG_GATE_KIND:-gate}" "$1" "$(git rev-parse HEAD 2>/dev/null)"; }
    trap 'note fail; exit 143' TERM
    "$@"; rc=$?; if [ $rc = 0 ]; then note pass; else note fail; fi; exit $rc;;
esac
[ $# -gt 0 ] || { echo "usage: gate.sh <command...>   (lock in \$SG_STATE/locks/gate; SG_STATE unset: /tmp/sg-state, never TMPDIR)" >&2; exit 2; }
export SERIAL_WAIT_SECONDS=${SERIAL_WAIT_SECONDS:-86400} SERIAL_HOLD_SECONDS=${SERIAL_HOLD_SECONDS:-7200}
case ":${SG_SERIAL_HELD:-}:" in *":gate:"*) ;; *)
  . "$here/caps.sh"; w=$SERIAL_WAIT_SECONDS
  [ "${SG_CALL_DEADLINE_SECONDS:-0}" -gt 0 ] 2>/dev/null && [ "$SG_CALL_DEADLINE_SECONDS" -lt "$w" ] && w=$SG_CALL_DEADLINE_SECONDS
  SQUAD_GATE_WORKERS=$(sg_caps take gates "gate-$$" --count "${SG_GATE_WORKERS:-6}" --pid $$ --target "$PWD" --wait "$w" ${SERIAL_TRY:+--try}) || exit $?
  export SQUAD_GATE_WORKERS;; esac
exec bash "$here/serial.sh" gate bash "$here/gate.sh" --run "$@"   # exec: the lock holder is this process, and --run writes the line inside the lock
