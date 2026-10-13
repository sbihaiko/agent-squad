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

# (#134, ADR-0051) A gate that outlives its run is found without a caller: the holder records the deadline it keeps,
# and a holder left alive past that deadline with no owner (its parent is pid 1: its run is gone) is reaped by name.
mkorphan() { local f=$1; bash -c 'sleep 300 >/dev/null 2>&1 & echo $! > "'"$f"'"'; for i in $(seq 1 50); do [ -s "$f" ] && break; sleep 0.1; done; cat "$f"; }
op=$(mkorphan "$T/orphan"); [ -n "$op" ] && [ "$(/bin/ps -o ppid= -p "$op" 2>/dev/null | tr -d ' ')" = 1 ] && ok "the fixture holds an orphan (parent is pid 1)" || ko "no orphan to reap (pid ${op:-none})"
mkdir -p "$SG_STATE/locks/gate"; echo "$op" > "$SG_STATE/locks/gate/pid"; echo "sleep" > "$SG_STATE/locks/gate/cmd"
echo $(( $(date +%s) - 30 )) > "$SG_STATE/locks/gate/due"
out=$(SERIAL_WAIT_SECONDS=20 bash "$src" gate sh -c 'echo ran' 2>"$T/reap.err"); rc=$?
[ "$rc" = 0 ] && [ "$out" = ran ] && ok "the gate runs after the orphaned holder is reaped" || ko "gate blocked by the orphan: rc=$rc out=$out"
/usr/bin/grep -q "reaping orphaned lock gate" "$T/reap.err" && ok "the reaping names the orphaned holder" || ko "no reaping line: $(cat "$T/reap.err")"
kill -0 "$op" 2>/dev/null && { kill -9 "$op" 2>/dev/null; ko "the orphaned holder was left alive"; } || ok "the orphaned holder is reaped"
[ -d "$SG_STATE/locks/gate" ] && ko "the lock stayed after the reaping gate" || ok "the lock is released when the reaping gate ends"
rm -rf "$SG_STATE/locks/gate"
bash "$src" gate sh -c 'sleep 2' & hp=$!
for i in $(seq 1 30); do [ -f "$SG_STATE/locks/gate/due" ] && break; sleep 0.1; done
[ -f "$SG_STATE/locks/gate/due" ] && [ -f "$SG_STATE/locks/gate/cmd" ] && ok "the holder records its deadline and its name while it runs" || ko "no deadline/name recorded (a waiter cannot find a dead holder)"
wait "$hp"
hold=$(SERIAL_HOLD_SECONDS=1 bash "$src" gate sh -c 'sleep 4; echo late' 2>&1); rc=$?
[ "$rc" = 75 ] && ok "a holder past its deadline stops: exit 75" || ko "the deadline did not reach the gate process (rc=$rc out=$hold)"
# A live holder that is past its deadline but still owned (this shell is its parent) is never killed on a guess.
mkdir -p "$SG_STATE/locks/gate"; echo $$ > "$SG_STATE/locks/gate/pid"; echo bash > "$SG_STATE/locks/gate/cmd"
echo $(( $(date +%s) - 300 )) > "$SG_STATE/locks/gate/due"
out=$(SERIAL_WAIT_SECONDS=4 bash "$src" gate true 2>&1); rc=$?
[ "$rc" = 75 ] && ok "a held lock whose owner is alive is waited for, never reaped" || ko "an owned holder was reaped (rc=$rc)"
rm -rf "$SG_STATE/locks/gate"
rm -rf "$T"; exit $fail
