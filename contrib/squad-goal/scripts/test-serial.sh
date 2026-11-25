#!/bin/bash
# seconds: 36.68
# Regression test for serial.sh: two commands under the same lock never overlap, a stale lock is taken over, exit codes pass through,
# a waiter prints one line naming the holder and runs as soon as the holder ends, and a command already inside the lock does not wait.
# usage: bash test-serial.sh [path/to/serial.sh]
unset SG_SERIAL_HELD   # a gate inside a gate hands the lock name down; this suite takes its own locks from a clean slate (re-entry is proved inline)
src=${1:-$(dirname "$0")/serial.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export SG_STATE=$T/st; mkdir -p "$SG_STATE"
job() { bash "$src" gui sh -c "echo start >> $T/log; sleep 3; echo end >> $T/log"; }
job & job & wait
[ "$(tr '\n' ' ' < "$T/log")" = "start end start end " ] && ok "two jobs on one lock run one after the other" || ko "overlap: $(tr '\n' ' ' < "$T/log")"
mkdir -p "$SG_STATE/locks/gui"; echo 99999999 > "$SG_STATE/locks/gui/pid"
bash "$src" gui true && ok "stale lock (dead pid) is taken over" || ko "stale lock blocked the job"
bash "$src" gui sh -c 'exit 7'; [ $? = 7 ] && ok "exit code passes through" || ko "exit code lost"
[ -d "$SG_STATE/locks/gui" ] && ko "lock left behind" || ok "lock released after the command"

# (#1057) A holder that died as a zombie is dead: its parent has not reaped it, so kill -0 still answers, and the lock must not
# survive it. The reap writes one line naming the lock, the dead pid and the lock's age. A live holder is left alone, no reap line.
agefile() { python3 -c 'import os,sys,time; t=time.time()-float(sys.argv[2]); os.utime(sys.argv[1],(t,t))' "$1" "$2"; }
sh -c 'sleep 0 & echo $! > "$1"; exec sleep 300' sh "$T/zombie" >/dev/null 2>&1 & zpar=$!
for i in $(seq 1 50); do [ -s "$T/zombie" ] && [ "$(/bin/ps -o stat= -p "$(cat "$T/zombie")" 2>/dev/null | cut -c1)" = Z ] && break; sleep 0.1; done
zp=$(cat "$T/zombie"); [ "$(/bin/ps -o stat= -p "$zp" 2>/dev/null | cut -c1)" = Z ] && ok "the fixture holder is a zombie" || ko "no zombie fixture (pid ${zp:-none})"
mkdir -p "$SG_STATE/locks/gui"; echo "$zp" > "$SG_STATE/locks/gui/pid"; agefile "$SG_STATE/locks/gui/pid" 120
out=$(SERIAL_WAIT_SECONDS=4 bash "$src" gui sh -c 'echo ran' 2>"$T/zombie.err"); rc=$?
[ "$rc" = 0 ] && [ "$out" = ran ] && ok "a lock held by a zombie is reaped and the command runs" || ko "a zombie holder blocked the command (rc=$rc out=$out)"
[ "$(/usr/bin/grep -c "^serial: reaping stale lock gui: holder pid $zp .* age [0-9]*s\$" "$T/zombie.err")" = 1 ] && ok "the reap writes one line naming the lock, the dead pid and the age" || ko "reap line: $(cat "$T/zombie.err")"
age=$(sed -nE 's/.*age ([0-9]+)s$/\1/p' "$T/zombie.err"); [ "${age:-0}" -ge 100 ] && ok "the reap line reports the lock's age" || ko "reap age: ${age:-none}"
[ "$(/usr/bin/grep -c 'waiting' "$T/zombie.err")" = 0 ] && ok "the command ran with no wait after the reap" || ko "a wait line after the reap"
kill -9 "$zpar" 2>/dev/null; wait "$zpar" 2>/dev/null
mkdir -p "$SG_STATE/locks/gui"; echo $$ > "$SG_STATE/locks/gui/pid"
out=$(SERIAL_TRY=1 SERIAL_WAIT_SECONDS=4 bash "$src" gui true 2>"$T/live.err"); rc=$?
[ "$rc" = 73 ] && [ "$(cat "$SG_STATE/locks/gui/pid")" = "$$" ] && ok "a live holder is left alone and the try skips it" || ko "a live holder was touched or not skipped (rc=$rc)"
/usr/bin/grep -q "reaping" "$T/live.err" && ko "a reap line for a live holder" || ok "a live holder writes no reap line"
rm -rf "$SG_STATE/locks/gui"
mkdir -p "$SG_STATE/locks/gui"; echo $$ > "$SG_STATE/locks/gui/pid"
# (#81) The hold is visible and the give-up names the holder: an operator reading a stuck gate learns who it waits for, and the
# command already inside the lock (SG_SERIAL_HELD) runs at once -- a gate inside a gate holder waits for nobody.
out=$(SERIAL_WAIT_SECONDS=4 bash "$src" gui true 2>&1); rc=$?
[ "$rc" = 75 ] && ok "wait budget exhausted: exit 75" || ko "no timeout exit (got $rc)"
printf '%s\n' "$out" | /usr/bin/grep -qx "serial: waited 4s for lock gui held by pid $$" && ok "the give-up names the lock, the budget and the holder" || ko "give-up text: $out"
[ "$(printf '%s\n' "$out" | /usr/bin/grep -c "^serial: waiting for lock gui held by pid $$")" = 1 ] && ok "the waiter prints one wait line, naming the holder" || ko "wait lines: $out"
# (spec 1163) A waiter records that it is queued, in $SG_STATE/locks/waiting/<lock>-<pid>: `<epoch> TAB <lock> TAB <pid>`. The
# live check reads that marker to tell a child starved by a queued slot holder from a child that waits its turn (#1178). The
# marker exists only while the wait lasts: the waiter removes it when it gives up, and the lock holder's own trap removes it too.
SERIAL_WAIT_SECONDS=3 SERIAL_POLL_SECONDS=0.2 bash "$src" gui sh -c 'echo ran' >"$T/wmark.out" 2>"$T/wmark.err" & wp=$!
for i in $(seq 1 30); do [ -e "$SG_STATE/locks/waiting/gui-$wp" ] && break; sleep 0.1; done
mark=$(cat "$SG_STATE/locks/waiting/gui-$wp" 2>/dev/null)
[ "$(printf '%s' "$mark" | cut -f2,3)" = "gui	$wp" ] && [ "$(printf '%s' "$mark" | cut -f1)" -gt 0 ] 2>/dev/null \
  && ok "a waiter records that it is queued for the lock, with the lock, its pid and the epoch it began" || ko "queue marker: '$mark'"
wait "$wp"; wrc=$?
[ "$wrc" = 75 ] && [ ! -e "$SG_STATE/locks/waiting/gui-$wp" ] && ok "the queue marker is gone once the wait gives up" || ko "queue marker after exit $wrc: '$(/bin/ls "$SG_STATE/locks/waiting" 2>&1)'"
[ "$(cat "$SG_STATE/locks/gui/pid" 2>/dev/null)" = "$$" ] && ok "the live holder is untouched by the queue marker" || ko "the queue marker touched the lock"
[ "$(SG_SERIAL_HELD=gui SERIAL_WAIT_SECONDS=4 bash "$src" gui sh -c 'echo inner')" = inner ] && ok "a command inside the holder runs without waiting" || ko "re-entry did not run inside the holder"
# (#459) Try mode: SERIAL_TRY=1 skips a held lock at once with exit 73 and one line, never queues; a free lock runs the command,
# and the command does not inherit the mode, so a lock it takes inside waits as usual.
s0=$(date +%s); out=$(SERIAL_TRY=1 SERIAL_WAIT_SECONDS=20 bash "$src" gui sh -c 'echo ran' 2>&1); rc=$?
[ "$rc" = 73 ] && [ $(( $(date +%s) - s0 )) -lt 3 ] && ok "try mode: a held lock skips at once with exit 73" || ko "try mode on a held lock: rc=$rc after $(( $(date +%s) - s0 ))s"
case "$out" in *ran*) ko "try mode ran the command beside the holder";; *) ok "try mode does not run the command beside the holder";; esac
[ "$(printf '%s\n' "$out" | /usr/bin/grep -c "^serial: lock gui held by pid $$: skipped")" = 1 ] && ok "try mode says the skip on one line, naming the holder" || ko "try skip line: $out"
rm -rf "$SG_STATE/locks/gui"
[ "$(SERIAL_TRY=1 bash "$src" gui sh -c 'echo "${SERIAL_TRY:-unset}"')" = unset ] && ok "try mode on a free lock runs the command, which does not inherit the mode" || ko "try mode leaked into the command or did not run it"
mkdir -p "$SG_STATE/locks/gui"; echo $$ > "$SG_STATE/locks/gui/pid"

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

# (#805) A holder past its deadline whose watchdog is gone is reaped even when its parent is not pid 1: the run that owns it may
# still be alive, but nothing will stop its command. The stub holder is a sleep of this shell (its parent is not pid 1), its
# recorded deadline is past, and the watchdog it records is a pid already gone.
alive() { kill -0 "$1" 2>/dev/null && [ "$(/bin/ps -o stat= -p "$1" 2>/dev/null | cut -c1)" != Z ]; }
sleep 300 >/dev/null 2>&1 & sp=$!
true & dw=$!; wait "$dw"
mkdir -p "$SG_STATE/locks/gate"; echo "$sp" > "$SG_STATE/locks/gate/pid"; echo sleep > "$SG_STATE/locks/gate/cmd"
echo $(( $(date +%s) - 30 )) > "$SG_STATE/locks/gate/due"; echo "$dw" > "$SG_STATE/locks/gate/wd"
[ "$(/bin/ps -o ppid= -p "$sp" 2>/dev/null | tr -d ' ')" != 1 ] && ok "the stub holder's parent is not pid 1" || ko "the stub holder is an orphan"
out=$(SERIAL_WAIT_SECONDS=4 bash "$src" gate sh -c 'echo ran' 2>"$T/reap805.err"); rc=$?
[ "$rc" = 0 ] && [ "$out" = ran ] && ok "a gate reaps a holder past its deadline whose watchdog is gone, parent not pid 1" || ko "a dead watchdog did not free the gate (rc=$rc out=$out)"
/usr/bin/grep -q "reaping orphaned lock gate" "$T/reap805.err" && ok "the reaping names the holder with the dead watchdog" || ko "no reaping line: $(cat "$T/reap805.err")"
alive "$sp" && ko "the holder with a dead watchdog was left alive" || ok "the holder with a dead watchdog is reaped"
kill -9 "$sp" 2>/dev/null; wait "$sp" 2>/dev/null
[ -d "$SG_STATE/locks/gate" ] && ko "the lock stayed after the reaping gate" || ok "the lock is released when the reaping gate ends"
rm -rf "$SG_STATE/locks/gate"

# (#392) The holder's own cleanup TERMs its watchdog's process group when the command ends, and the watchdog can be the process
# that dies. A watchdog cut short is not a deadline: the holder keeps its command's exit code. SERIAL_HOLD_SECONDS=4242 puts the
# deadline hours away, so the cut is the only thing that can end it. The watchdog is found by the pid the holder writes for it
# ($L/wd), never by a sleep's argument: the watchdog sleeps its hold in short steps so it can notice a holder that died without
# its traps (#1660), so its children are short sleeps and name no hold. A reading is not the behavior: the probe runs the holder
# again when it found nothing to cut in time.
rc=1; cut=0
for attempt in 1 2 3; do
  SERIAL_HOLD_SECONDS=4242 bash "$src" gate sh -c 'sleep 3; exit 7' 2>/dev/null & hp=$!
  wp=""; for i in $(seq 1 30); do wp=$(cat "$SG_STATE/locks/gate/wd" 2>/dev/null); [ -n "$wp" ] && kill -0 "$wp" 2>/dev/null && break; wp=""; sleep 0.1; done
  [ -n "$wp" ] && { cut=1; kill -TERM "$wp" 2>/dev/null; }
  wait "$hp"; rc=$?
  [ "$cut" = 1 ] && break
done
[ "$cut" = 1 ] && ok "the holder's watchdog is named by the holder and its sleep is cut" || ko "no watchdog sleep to cut short"
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

# (#392) An empty reading of a pid's start time is no evidence of a reused pid: a ps that fails (a pid of another user, another pid
# namespace, a transient error) names nothing. A live holder whose token cannot be read back is waited for, never reaped; only a
# non-empty token that differs from the recorded one is a reused pid. SG_PS names the ps, so the test makes the reading empty.
printf '#!/bin/bash\ncase "$*" in *lstart*) exit 1;; esac\nexec /bin/ps "$@"\n' > "$T/ps-blind"; chmod +x "$T/ps-blind"
rm -rf "$SG_STATE/locks/gui"; mkdir -p "$SG_STATE/locks/gui"; echo $$ > "$SG_STATE/locks/gui/pid"; echo "Mon Jan  1 00:00:00 2001" > "$SG_STATE/locks/gui/inc"
out=$(SG_PS="$T/ps-blind" SERIAL_WAIT_SECONDS=4 bash "$src" gui sh -c 'echo ran' 2>&1); rc=$?
[ "$rc" = 75 ] && [ "$(cat "$SG_STATE/locks/gui/pid" 2>/dev/null)" = "$$" ] && ok "an empty start-time reading does not reap a live holder" || ko "a live holder was reaped on an empty reading (rc=$rc out=$out)"
rm -rf "$SG_STATE/locks/gui"

# (#392) The lock directory is one machine-wide path when SG_STATE is not set: TMPDIR is per user on macOS, so two users or a
# launchd job would otherwise resolve two lock directories and two gates would run together.
mkdir -p "$T/ta" "$T/tb"
a=$(env -u SG_STATE TMPDIR="$T/ta" bash "$src" tmpdir-probe sh -c 'ls -d /tmp/sg-state/locks/tmpdir-probe 2>&1')
b=$(env -u SG_STATE TMPDIR="$T/tb" bash "$src" tmpdir-probe sh -c 'ls -d /tmp/sg-state/locks/tmpdir-probe 2>&1')
[ "$a" = /tmp/sg-state/locks/tmpdir-probe ] && [ "$b" = "$a" ] && [ ! -e "$T/ta/sg-state" ] && [ ! -e "$T/tb/sg-state" ] && ok "the lock directory does not depend on TMPDIR" || ko "the lock directory follows TMPDIR ('$a' / '$b')"
# (#1858) The landing's gate is first out of the queue. A waiter carrying SG_SERIAL_PRIORITY=1 says so in field 4 of its queue
# marker, and a waiter that carries none stands aside while a live priority waiter is queued for the same lock. The order is proved
# without timing the queue: the holder is released only after both waiters have recorded their markers, so the waiter that runs
# first is the one the rule chose. A marker names nobody once its pid is gone or its epoch is past SERIAL_PRIORITY_MAX.
q=$T/q; mkdir -p "$q"; rm -rf "$SG_STATE/locks/gui"
bash "$src" gui sh -c "while [ ! -e '$q/go' ]; do sleep 0.05; done" >/dev/null 2>&1 & qh=$!
for i in $(seq 1 60); do [ -s "$SG_STATE/locks/gui/pid" ] && break; sleep 0.05; done
SERIAL_POLL_SECONDS=0.5 SERIAL_WAIT_SECONDS=30 bash "$src" gui sh -c "echo child >> '$q/order'" 2>"$q/child.err" & qc=$!
for i in $(seq 1 60); do [ -e "$SG_STATE/locks/waiting/gui-$qc" ] && break; sleep 0.05; done
SG_SERIAL_PRIORITY=1 SERIAL_POLL_SECONDS=0.05 SERIAL_WAIT_SECONDS=30 bash "$src" gui sh -c "echo landing >> '$q/order'" 2>"$q/pri.err" & qp=$!
for i in $(seq 1 60); do [ -e "$SG_STATE/locks/waiting/gui-$qp" ] && break; sleep 0.05; done
[ "$(cut -f4 "$SG_STATE/locks/waiting/gui-$qp" 2>/dev/null)" = 1 ] && [ "$(cut -f4 "$SG_STATE/locks/waiting/gui-$qc" 2>/dev/null)" = 0 ] \
  && ok "a priority waiter says so in its queue marker, and a child's marker carries no priority" || ko "markers: '$(cat "$SG_STATE/locks/waiting/gui-$qp" 2>/dev/null)' / '$(cat "$SG_STATE/locks/waiting/gui-$qc" 2>/dev/null)'"
: > "$q/go"; wait "$qh"; wait "$qc"; wait "$qp"
[ "$(tr '\n' ' ' < "$q/order")" = "landing child " ] && ok "the landing's gate runs before a child's gate that queued first" || ko "order: '$(tr '\n' ' ' < "$q/order")'"
rm -rf "$SG_STATE/locks/gui"
# The child names the yield, once, and says the landing held it up: a gate that stands aside silently reads as a hang. The marker
# is written by hand here, so whether the child ever meets a *queued* landing is not a race in this case.
sleep 300 >/dev/null 2>&1 & hp=$!; mkdir -p "$SG_STATE/locks/gui" "$SG_STATE/locks/waiting"; echo $$ > "$SG_STATE/locks/gui/pid"
printf '%s\tgui\t%s\t1\n' "$(date +%s)" "$hp" > "$SG_STATE/locks/waiting/gui-$hp"
out=$(SERIAL_WAIT_SECONDS=2 SERIAL_POLL_SECONDS=0.2 bash "$src" gui sh -c 'echo ran' 2>"$q/yield.err"); rc=$?
[ "$rc" = 75 ] && [ "$out" = "" ] && [ "$(/usr/bin/grep -c "the landing's gate is queued ahead of this one" "$q/yield.err")" = 1 ] \
  && [ "$(/usr/bin/grep -c 'behind the landing.s gate' "$q/yield.err")" = 1 ] \
  && ok "a waiter says once that it stands aside, and gives up naming the landing" || ko "yield lines: $(cat "$q/yield.err")"
rm -f "$SG_STATE/locks/waiting/gui-$hp"; kill -9 "$hp" 2>/dev/null; wait "$hp" 2>/dev/null
rm -rf "$SG_STATE/locks/gui"   # the lock the yield case held; the cases below take it free
# Try mode never queues, so it neither runs ahead of a queued landing nor waits for one: it answers 73 at once and says why.
sleep 300 >/dev/null 2>&1 & hp=$!; mkdir -p "$SG_STATE/locks/waiting"; printf '%s\tgui\t%s\t1\n' "$(date +%s)" "$hp" > "$SG_STATE/locks/waiting/gui-$hp"
out=$(SERIAL_TRY=1 SERIAL_WAIT_SECONDS=4 bash "$src" gui sh -c 'echo ran' 2>"$q/try.err"); rc=$?
[ "$rc" = 73 ] && [ "$out" = "" ] && [ "$(/usr/bin/grep -c 'landing.s gate is queued ahead' "$q/try.err")" = 1 ] \
  && ok "try mode skips at once while the landing's gate is queued, and says why" || ko "try mode ran ahead of a queued landing (rc=$rc out=$out err=$(cat "$q/try.err"))"
# A marker whose pid is gone names nobody, and so does one past SERIAL_PRIORITY_MAX: a killed waiter leaves its marker behind.
kill -9 "$hp" 2>/dev/null; wait "$hp" 2>/dev/null
out=$(SERIAL_WAIT_SECONDS=4 bash "$src" gui sh -c 'echo ran' 2>"$q/dead.err"); rc=$?
[ "$rc" = 0 ] && [ "$out" = ran ] && ok "a priority marker whose pid is gone names nobody" || ko "a dead priority marker blocked the gate (rc=$rc out=$out)"
printf '%s\tgui\t%s\t1\n' "$(( $(date +%s) - 1000 ))" "$$" > "$SG_STATE/locks/waiting/gui-old"
out=$(SERIAL_PRIORITY_MAX=900 SERIAL_WAIT_SECONDS=4 bash "$src" gui sh -c 'echo ran' 2>"$q/old.err"); rc=$?
[ "$rc" = 0 ] && [ "$out" = ran ] && ok "a priority marker older than SERIAL_PRIORITY_MAX names nobody" || ko "a stale priority marker blocked the gate (rc=$rc out=$out)"
rm -f "$SG_STATE/locks/waiting/gui-old"; rm -rf "$SG_STATE/locks/gui"


# (#2883) A fresh arrival never passes a queued waiter. daysuites runs suites back to back, each through a new serial.sh, so it
# re-took the lock in milliseconds while a waiter on the default 3 s poll starved. A waiter W queues behind a holder; a runner
# then takes the lock three times back to back for about 1 s each; W must run before the runner's second take.
f=$T/fifo; mkdir -p "$f"; rm -rf "$SG_STATE/locks/fifo"
bash "$src" fifo sh -c "while [ ! -e '$f/go' ]; do sleep 0.05; done" >/dev/null 2>&1 & fh=$!
for i in $(seq 1 60); do [ -s "$SG_STATE/locks/fifo/pid" ] && break; sleep 0.05; done
SERIAL_WAIT_SECONDS=30 bash "$src" fifo sh -c "echo W >> '$f/order'" 2>/dev/null & fw=$!
for i in $(seq 1 60); do [ -s "$SG_STATE/locks/waiting/fifo-$fw" ] && break; sleep 0.05; done
we=$(cut -f1 "$SG_STATE/locks/waiting/fifo-$fw" 2>/dev/null); while [ "$(date +%s)" -le "${we:-0}" ]; do sleep 0.1; done
( for i in 1 2 3; do SERIAL_POLL_SECONDS=0.05 SERIAL_WAIT_SECONDS=30 bash "$src" fifo sh -c "echo R$i >> '$f/order'; sleep 1" 2>/dev/null; done ) & fr=$!
sleep 0.3; : > "$f/go"; wait "$fh"; wait "$fw"; wait "$fr"
case "$(tr '\n' ' ' < "$f/order")" in "W R1 R2 R3 "|"R1 W R2 R3 ") ok "a queued waiter runs before a back-to-back runner's second take";;
  *) ko "a fresh arrival passed a queued waiter: order '$(tr '\n' ' ' < "$f/order")'";; esac
# A priority waiter still goes first, before an older non-priority waiter, and a fresh non-priority arrival comes after both.
rm -rf "$f" "$SG_STATE/locks/fifo"; mkdir -p "$f"
bash "$src" fifo sh -c "while [ ! -e '$f/go' ]; do sleep 0.05; done" >/dev/null 2>&1 & fh=$!
for i in $(seq 1 60); do [ -s "$SG_STATE/locks/fifo/pid" ] && break; sleep 0.05; done
SERIAL_POLL_SECONDS=0.1 SERIAL_WAIT_SECONDS=30 bash "$src" fifo sh -c "echo W >> '$f/order'" 2>/dev/null & fw=$!
for i in $(seq 1 60); do [ -s "$SG_STATE/locks/waiting/fifo-$fw" ] && break; sleep 0.05; done
we=$(cut -f1 "$SG_STATE/locks/waiting/fifo-$fw" 2>/dev/null); while [ "$(date +%s)" -le "${we:-0}" ]; do sleep 0.1; done
SG_SERIAL_PRIORITY=1 SERIAL_POLL_SECONDS=0.1 SERIAL_WAIT_SECONDS=30 bash "$src" fifo sh -c "echo P >> '$f/order'" 2>/dev/null & fp=$!
for i in $(seq 1 60); do [ -s "$SG_STATE/locks/waiting/fifo-$fp" ] && break; sleep 0.05; done
: > "$f/go"; wait "$fh"; SERIAL_POLL_SECONDS=0.05 SERIAL_WAIT_SECONDS=30 bash "$src" fifo sh -c "echo R >> '$f/order'" 2>/dev/null
wait "$fw"; wait "$fp"
[ "$(tr '\n' ' ' < "$f/order")" = "P W R " ] && ok "a priority waiter goes before an older waiter, and a fresh arrival goes last" || ko "order: '$(tr '\n' ' ' < "$f/order")'"
rm -rf "$SG_STATE/locks/fifo"
rm -rf "$T"; exit $fail
