#!/bin/bash
# Regression test for serial.sh: two commands under the same lock never overlap, a stale lock is taken over, exit codes pass through. usage: bash test-serial.sh [path/to/serial.sh]
src=${1:-$(dirname "$0")/serial.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export SG_STATE=$T/st; mkdir -p "$SG_STATE"
job() { bash "$src" gui sh -c "echo start >> $T/log; sleep 3; echo end >> $T/log"; }
job & job & wait
[ "$(tr '\n' ' ' < "$T/log")" = "start end start end " ] && ok "two jobs on one lock run one after the other" || ko "overlap: $(tr '\n' ' ' < "$T/log")"
mkdir -p "$SG_STATE/locks/gui"; echo 99999999 > "$SG_STATE/locks/gui/pid"
bash "$src" gui true && ok "stale lock (dead pid) is taken over" || ko "stale lock blocked the job"
bash "$src" gui sh -c 'exit 7'; [ $? = 7 ] && ok "exit code passes through" || ko "exit code lost"
[ -d "$SG_STATE/locks/gui" ] && ko "lock left behind" || ok "lock released after the command"
mkdir -p "$SG_STATE/locks/gui"; echo $$ > "$SG_STATE/locks/gui/pid"; SERIAL_WAIT_SECONDS=4 bash "$src" gui true 2>/dev/null; [ $? = 75 ] && ok "wait budget exhausted: exit 75" || ko "no timeout exit"
rm -rf "$T"; exit $fail
