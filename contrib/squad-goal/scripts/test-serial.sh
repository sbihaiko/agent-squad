#!/bin/bash
# Regression test for serial.sh: two commands under the same lock never overlap, a stale lock is taken over, exit codes pass through,
# a waiter prints one line naming the holder and runs as soon as the holder ends, and a command already inside the lock does not wait.
# usage: bash test-serial.sh [path/to/serial.sh]
src=${1:-$(dirname "$0")/serial.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export SG_STATE=$T/st; mkdir -p "$SG_STATE"
job() { bash "$src" gui sh -c "echo start >> $T/log; sleep 3; echo end >> $T/log"; }
job & job & wait
[ "$(tr '\n' ' ' < "$T/log")" = "start end start end " ] && ok "two jobs on one lock run one after the other" || ko "overlap: $(tr '\n' ' ' < "$T/log")"
mkdir -p "$SG_STATE/locks/gui"; echo 99999999 > "$SG_STATE/locks/gui/pid"
bash "$src" gui true && ok "stale lock (dead pid) is taken over" || ko "stale lock blocked the job"
bash "$src" gui sh -c 'exit 7'; [ $? = 7 ] && ok "exit code passes through" || ko "exit code lost"
[ -d "$SG_STATE/locks/gui" ] && ko "lock left behind" || ok "lock released after the command"
mkdir -p "$SG_STATE/locks/gui"; echo $$ > "$SG_STATE/locks/gui/pid"
# (#81) The hold is visible and the give-up names the holder: an operator reading a stuck gate learns who it waits for, and the
# command already inside the lock (SG_SERIAL_HELD) runs at once -- a gate inside a gate holder waits for nobody.
out=$(SERIAL_WAIT_SECONDS=4 bash "$src" gui true 2>&1); rc=$?
[ "$rc" = 75 ] && ok "wait budget exhausted: exit 75" || ko "no timeout exit (got $rc)"
printf '%s\n' "$out" | /usr/bin/grep -qx "serial: waited 4s for lock gui held by pid $$" && ok "the give-up names the lock, the budget and the holder" || ko "give-up text: $out"
[ "$(printf '%s\n' "$out" | /usr/bin/grep -c "^serial: waiting for lock gui held by pid $$")" = 1 ] && ok "the waiter prints one wait line, naming the holder" || ko "wait lines: $out"
[ "$(SG_SERIAL_HELD=gui SERIAL_WAIT_SECONDS=4 bash "$src" gui sh -c 'echo inner')" = inner ] && ok "a command inside the holder runs without waiting" || ko "re-entry did not run inside the holder"
rm -rf "$T"; exit $fail
