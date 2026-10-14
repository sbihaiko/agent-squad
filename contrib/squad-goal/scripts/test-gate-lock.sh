#!/bin/bash
# Regression test for gate.sh (#392): one command runs a hand gate under the machine-wide `gate` lock, the lock serial.sh owns.
# Two gates started at once run one after the other and never together; the second waits and then runs, and its wait shows on one
# log line; a gate whose holder died takes the lock; the exit code passes through. Offline, a throwaway SG_STATE.
# usage: bash test-gate-lock.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s"; cp "$d/gate.sh" "$d/serial.sh" "$T/s/" 2>/dev/null
export SG_STATE=$T/st; mkdir -p "$SG_STATE"
job() { bash "$T/s/gate.sh" sh -c "echo start >> $T/log; sleep 2; echo end >> $T/log" 2>>"$T/err"; }
job & sleep 0.5; job & wait
[ "$(tr '\n' ' ' < "$T/log" 2>/dev/null)" = "start end start end " ] && ok "two gates started at once run one after the other" || ko "overlap or drop: $(tr '\n' ' ' < "$T/log" 2>&1)"
[ "$(/usr/bin/grep -c '^serial: waiting for lock gate held by pid' "$T/err" 2>/dev/null)" = 1 ] && ok "the wait shows on one log line" || ko "wait lines: $(cat "$T/err" 2>&1)"
mkdir -p "$SG_STATE/locks/gate"; echo 99999999 > "$SG_STATE/locks/gate/pid"
bash "$T/s/gate.sh" true && ok "a gate whose holder died takes the lock" || ko "a dead holder blocked the gate"
bash "$T/s/gate.sh" sh -c 'exit 7'; [ $? = 7 ] && ok "the gate's exit code passes through" || ko "exit code lost"
[ -d "$SG_STATE/locks/gate" ] && ko "the lock is left behind" || ok "the lock is released after the gate"
bash "$T/s/gate.sh" >/dev/null 2>&1; [ $? = 2 ] && ok "no command: usage error, exit 2" || ko "no command did not exit 2"
rm -rf "$T"; exit $fail
