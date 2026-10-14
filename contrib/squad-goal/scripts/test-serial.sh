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

# (#392) The holder's own cleanup TERMs its watchdog's process group when the command ends, and the sleep of that watchdog can be
# the process that dies. A sleep cut short is not a deadline: the holder keeps its command's exit code. SERIAL_HOLD_SECONDS=4242
# puts the deadline hours away and names that one sleep, so the cut is the only thing that can end it. Finding that sleep reads the
# process table, and a reading is not the behavior: the probe runs the holder again when it found nothing to cut in time.
rc=1; cut=0
for attempt in 1 2 3; do
  SERIAL_HOLD_SECONDS=4242 bash "$src" gate sh -c 'sleep 3; exit 7' 2>/dev/null & hp=$!
  sp=""; for i in $(seq 1 30); do sp=$(/bin/ps -ww -o pid=,command= -ax | awk '$NF=="4242" && $2 ~ /sleep/ {print $1}'); [ -n "$sp" ] && break; sleep 0.1; done
  [ -n "$sp" ] && { cut=1; kill -TERM "$sp" 2>/dev/null; }
  wait "$hp"; rc=$?
  [ "$cut" = 1 ] && break
done
[ "$cut" = 1 ] && ok "the holder's watchdog is found by the hold it sleeps, and its sleep is cut" || ko "no watchdog sleep to cut short"
[ "$rc" = 7 ] && ok "a watchdog sleep cut short is not a deadline: the exit code passes through" || ko "a cut-short sleep failed the holder (rc=$rc)"

# (#392) A pid is not an identity: the system reuses one. The holder records the start time of its own pid beside it, and a lock
# whose token does not name the process alive now is stale -- another process wears that number. The gate takes that lock and
# runs; it never waits for a holder that is gone. The token is read back against the pid the lock names.
rm -rf "$SG_STATE/locks/gui"
bash "$src" gui sh -c 'sleep 2' & hp=$!
for i in $(seq 1 30); do [ -s "$SG_STATE/locks/gui/inc" ] && break; sleep 0.1; done
lp=$(cat "$SG_STATE/locks/gui/pid" 2>/dev/null)
[ -n "$lp" ] && [ "$(cat "$SG_STATE/locks/gui/inc" 2>/dev/null | tr -d ' ')" = "$(/bin/ps -o lstart= -p "$lp" 2>/dev/null | tr -d ' ')" ] && ok "the holder records the incarnation of its pid" || ko "no incarnation recorded for holder pid ${lp:-?}"
wait "$hp"
prior=$(mkorphan "$T/prior"); rec=$(/bin/ps -o lstart= -p "$prior" 2>/dev/null | tr -s ' ')   # the incarnation the lock records
sleep 1.1   # the next process starts in another wall second: its start time is provably another incarnation
reused=$(mkorphan "$T/reused"); now=$(/bin/ps -o lstart= -p "$reused" 2>/dev/null | tr -s ' ')   # the incarnation that wears the number now
[ -n "$rec" ] && [ -n "$now" ] && [ "$rec" != "$now" ] && ok "the fixture holds two incarnations of one pid number" || ko "the fixture holds one incarnation ('$rec' / '$now')"
rm -rf "$SG_STATE/locks/gui"; mkdir -p "$SG_STATE/locks/gui"
echo "$reused" > "$SG_STATE/locks/gui/pid"; printf '%s\n' "$rec" > "$SG_STATE/locks/gui/inc"
out=$(SERIAL_WAIT_SECONDS=4 bash "$src" gui sh -c 'echo ran' 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$out" = ran ] && ok "a lock whose recorded pid was reused is stale and is taken over" || ko "the reused-pid lock blocked the gate (rc=$rc out=$out)"
kill -9 "$prior" "$reused" 2>/dev/null
rm -rf "$T"; exit $fail
