#!/bin/bash
# Regression test for gate.sh (#392): one command runs a hand gate under the machine-wide `gate` lock, the lock serial.sh owns.
# Two gates started at once run one after the other and never together; the second waits and then runs, and its wait shows on one
# log line; a gate whose holder died takes the lock; the exit code passes through. Offline, a throwaway SG_STATE.
# (#1166) The lock comes first and the slots second: a gate queued behind a holder takes no slot while it waits, a gate that
# cannot take its slots gives the lock back at once (exit 73), every exit frees both, and a killed holder loses both to the
# next gate. A throwaway budget (SQUAD_CAPS_DIR) is the only one these slots are taken from.
# usage: bash test-gate-lock.sh [scripts dir]
# mirrors: gate.sh serial.sh caps.sh
unset SG_SERIAL_HELD   # a gate inside a gate hands the lock name down; this suite takes its own gate lock, so the order is proved
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s"; cp "$d/gate.sh" "$d/serial.sh" "$d/caps.sh" "$T/s/" 2>/dev/null
export SG_STATE=$T/st; mkdir -p "$SG_STATE"
cd "$T"   # a target with no project settings file: the budget is the plugin's, so no project value decides the counts
# (#567) A gate takes SG_GATE_WORKERS gate slots of the machine-wide budget (caps.gates) inside the lock: a throwaway budget here.
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps SQUAD_CAPS_POLL=0.2 SG_GATE_WORKERS=2
capsctl() { PYTHONPATH="$SG_SQUAD" python3 -m squad.caps "$@"; }
occ() { capsctl | python3 -c 'import json,sys; print(json.load(sys.stdin)["occupancy"]["'"$1"'"])'; }
job() { bash "$T/s/gate.sh" sh -c "echo start >> $T/log; sleep 2; echo end >> $T/log" 2>>"$T/err"; }
job & sleep 0.5; job & wait
[ "$(tr '\n' ' ' < "$T/log" 2>/dev/null)" = "start end start end " ] && ok "two gates started at once run one after the other" || ko "overlap or drop: $(tr '\n' ' ' < "$T/log" 2>&1)"
[ "$(/usr/bin/grep -c '^serial: waiting for lock gate held by pid' "$T/err" 2>/dev/null)" = 1 ] && ok "the wait shows on one log line" || ko "wait lines: $(cat "$T/err" 2>&1)"
mkdir -p "$SG_STATE/locks/gate"; echo 99999999 > "$SG_STATE/locks/gate/pid"
bash "$T/s/gate.sh" true && ok "a gate whose holder died takes the lock" || ko "a dead holder blocked the gate"
bash "$T/s/gate.sh" sh -c 'exit 7'; [ $? = 7 ] && ok "the gate's exit code passes through" || ko "exit code lost"
[ -d "$SG_STATE/locks/gate" ] && ko "the lock is left behind" || ok "the lock is released after the gate"
bash "$T/s/gate.sh" >/dev/null 2>&1; [ $? = 2 ] && ok "no command: usage error, exit 2" || ko "no command did not exit 2"

# (#1057) The land pass's try-mode gate reaps a gate lock whose holder is dead before it defers on it. A zombie holder is dead:
# the gate runs, one reap line names the lock, the dead pid and the age, and no skip line is printed. A live holder is skipped
# in its own words and its lock is left alone.
agefile() { python3 -c 'import os,sys,time; t=time.time()-float(sys.argv[2]); os.utime(sys.argv[1],(t,t))' "$1" "$2"; }
sh -c 'sleep 0 & echo $! > "$1"; exec sleep 300' sh "$T/zombie" >/dev/null 2>&1 & zpar=$!
for i in $(seq 1 50); do [ -s "$T/zombie" ] && [ "$(/bin/ps -o stat= -p "$(cat "$T/zombie")" 2>/dev/null | cut -c1)" = Z ] && break; sleep 0.1; done
zp=$(cat "$T/zombie")
mkdir -p "$SG_STATE/locks/gate"; echo "$zp" > "$SG_STATE/locks/gate/pid"; agefile "$SG_STATE/locks/gate/pid" 120
out=$(SERIAL_TRY=1 bash "$T/s/gate.sh" sh -c 'echo ran' 2>"$T/zg.err"); rc=$?
[ "$rc" = 0 ] && [ "$out" = ran ] && ok "a gate lock held by a dead zombie is reaped and the try gate runs" || ko "the try gate deferred on a zombie: rc=$rc out=$out err=$(cat "$T/zg.err")"
[ "$(/usr/bin/grep -c "^serial: reaping stale lock gate: holder pid $zp .* age [0-9]*s\$" "$T/zg.err")" = 1 ] && ok "the reap names the gate lock, the dead pid and the age on one line" || ko "reap line: $(cat "$T/zg.err")"
/usr/bin/grep -q "skipped, not queued" "$T/zg.err" && ko "a dead holder was reported as skipped" || ok "a dead holder is not reported as held"
kill -9 "$zpar" 2>/dev/null; wait "$zpar" 2>/dev/null
sleep 60 >/dev/null 2>&1 & lv=$!
mkdir -p "$SG_STATE/locks/gate"; echo "$lv" > "$SG_STATE/locks/gate/pid"
out=$(SERIAL_TRY=1 bash "$T/s/gate.sh" true 2>"$T/lg.err"); rc=$?
[ "$rc" = 73 ] && [ "$(cat "$SG_STATE/locks/gate/pid")" = "$lv" ] && ok "a gate lock held by a live process is left alone and the try gate skips it" || ko "live gate lock: rc=$rc pid=$(cat "$SG_STATE/locks/gate/pid" 2>/dev/null)"
/usr/bin/grep -q "^serial: lock gate held by pid $lv: skipped, not queued (SERIAL_TRY)" "$T/lg.err" && ok "the skip names the live holder in its own words" || ko "skip line: $(cat "$T/lg.err")"
/usr/bin/grep -q "reaping" "$T/lg.err" && ko "a reap line for a live holder" || ok "a live holder writes no reap line"
kill "$lv" 2>/dev/null; rm -rf "$SG_STATE/locks/gate"

# --- the slots ---------------------------------------------------------------------------------------------------------------
sleep 120 >/dev/null 2>&1 & live=$!; trap 'kill $live 2>/dev/null' EXIT   # a holder that lives, for the leases this suite writes
rm -rf "$T/caps"; mkdir -p "$T/caps"
: > "$T/log"; bash "$T/s/gate.sh" sh -c "sleep 3" & g1=$!; sleep 0.5
bash "$T/s/gate.sh" sh -c "echo second >> $T/log" 2>"$T/err2" & g2=$!; sleep 1.5
[ "$(occ gates)" = 2 ] && ok "a gate queued behind a live holder takes no slot while it waits" || ko "a waiting gate holds slots: $(occ gates) held, want the holder's 2"
/usr/bin/grep -q '^serial: waiting for lock gate held by pid' "$T/err2" && ok "the queued gate says it waits for the lock" || ko "wait line: $(cat "$T/err2")"
wait $g1 $g2; [ "$(cat "$T/log")" = second ] && ok "the gate that waited for the lock ran after it, on its own slots" || ko "second gate: '$(cat "$T/log")'"
[ "$(occ gates)" = 0 ] && ok "a finished gate gives its slots back" || ko "slots left behind: $(occ gates)"
bash "$T/s/gate.sh" sh -c "bash $T/s/gate.sh sh -c 'echo \$SQUAD_GATE_WORKERS > $T/inner; sleep 1.5' & sleep 0.8; PYTHONPATH=$SG_SQUAD python3 -m squad.caps > $T/nested.json; wait"
[ "$(cat "$T/inner" 2>/dev/null)" = 2 ] && python3 -c 'import json,sys; sys.exit(json.load(open(sys.argv[1]))["occupancy"]["gates"] != 2)' "$T/nested.json" && ok "a gate inside a gate holder takes no more slots, and keeps the holder's count" || ko "nested gate: inner '$(cat "$T/inner" 2>&1)', $(cat "$T/nested.json" 2>&1)"
out=$(SG_GATE_KIND=full bash "$T/s/gate.sh" sh -c "echo \$SQUAD_GATE_WORKERS" 2>&1)
case "$out" in 2) ok "a full (promotion) gate takes its slots through gate.sh";; *) ko "full gate: '$out'";; esac
/usr/bin/grep -q 'gate.sh' "$d/promote-pass.sh" && /usr/bin/grep -q 'gate.sh' "$d/promote-stg.sh" && ok "the promotion pass and promote-stg.sh run under gate.sh" || ko "a promotion path skips gate.sh"
mkdir -p "$T/proj/docs/squad"; printf 'caps:\n  gates: 2\n' > "$T/proj/docs/squad/defaults.yaml"
out=$(cd "$T/proj" && SG_GATE_WORKERS=6 bash "$T/s/gate.sh" sh -c "echo \$SQUAD_GATE_WORKERS" 2>&1)
case "$out" in *"2") ok "a project that lowers caps.gates: the gate takes min(workers, ceiling) and never waits";; *) ko "min(workers, ceiling): '$out'";; esac
# the pytest of the battery runs with the count the gate took
. "$d/base-battery.sh"; b=$(SQUAD_GATE_WORKERS=2 SG_GATE_WORKERS=6 sg_base_battery py origin/dev)
case "$b" in *'-n "${SQUAD_GATE_WORKERS:-$workers}"'*"sg_battery_run py origin/dev 6 yes"*) ok "the battery's pytest reads the count the gate took, 6 when none";; *) ko "battery: '$b'";; esac
# (#734) the slots that are free, not six or nothing: 8 of 12 held, a gate that wants 6 takes the 4 free and runs with 4
capsctl take gates other --count 8 --pid "$live" >/dev/null
out=$(SG_GATE_WORKERS=6 bash "$T/s/gate.sh" sh -c 'echo $SQUAD_GATE_WORKERS' 2>&1)
[ "$out" = 4 ] && ok "a gate with 4 free slots takes 4 and runs its command with 4 workers" || ko "short take: '$out', want 4"
[ "$(occ gates)" = 8 ] && ok "a short gate gives its slots back" || ko "short gate left slots: $(occ gates) held, want 8"
capsctl release gates other
# below two free: 11 of 12 held, a gate that wants 6 holds nothing and is skipped at once in try mode
capsctl take gates other --count 11 --pid "$live" >/dev/null
t0=$(date +%s); SERIAL_TRY=1 SG_GATE_WORKERS=6 bash "$T/s/gate.sh" true 2>"$T/err3"; rc=$?
[ $rc = 73 ] && [ $(( $(date +%s) - t0 )) -lt 5 ] && ok "try mode skips at once when fewer than two slots are free (exit 73)" || ko "try mode: rc $rc"
[ "$(occ gates)" = 11 ] && ok "a gate that cannot have two of its slots holds none of them" || ko "partial hold: $(occ gates) held, want 11"
[ -d "$SG_STATE/locks/gate" ] && ko "a try gate that cannot take its slots left the lock held" || ok "a try gate that cannot take its slots gives the lock back at once"
/usr/bin/grep -c 'caps.gates' "$T/err3" | /usr/bin/grep -q '^1$' && ok "the skip says so on one line naming the ceiling" || ko "skip line: $(cat "$T/err3")"
# A gate that is not in try mode answers the same way: it never waits for a slot while it holds the lock, so no other gate
# queues behind a budget it cannot use. The call deadline and the wait bound never decide a slot take that cannot happen.
t0=$(date +%s); SERIAL_WAIT_SECONDS=1 SG_CALL_DEADLINE_SECONDS=1 SG_GATE_WORKERS=6 bash "$T/s/gate.sh" true 2>"$T/err4"; rc=$?
[ $rc = 73 ] && [ $(( $(date +%s) - t0 )) -lt 5 ] && ok "a gate that cannot take its slots answers at once, never waiting under the lock (exit 73)" || ko "full budget: rc $rc"
[ -d "$SG_STATE/locks/gate" ] && ko "a gate that could not take its slots left the lock held" || ok "a gate that could not take its slots gives the lock back at once"
[ "$(occ gates)" = 11 ] && ok "the full budget is unchanged by the gate that could not take its slots" || ko "the backed-off gate changed the budget: $(occ gates) held, want 11"
capsctl release gates other
capsctl take gates ghost --count 12 --pid 99999999 >/dev/null
out=$(SG_GATE_WORKERS=6 bash "$T/s/gate.sh" sh -c 'echo $SQUAD_GATE_WORKERS' 2>&1)
[ "$out" = 6 ] && ok "the slots of a dead holder are reclaimed" || ko "a dead holder's slots blocked the gate: '$out'"

# (#1166) A process that dies holding the lock and its slots loses both to the next caller: the lock is reaped by serial.sh,
# the leases are counted dead by the next count, and the next gate neither waits for the lock nor is short of slots.
kill_tree() {  # <pid>: the leaves first, so nothing is left to keep a lease or the lock alive (ps -P is not on every ps)
  local p=$1 c; for c in $(/bin/ps -ax -o pid=,ppid= | awk -v p="$p" '$2 == p {print $1}'); do kill_tree "$c"; done; kill -KILL "$p" 2>/dev/null
}
rm -rf "$T/caps"; : > "$T/log"
bash "$T/s/gate.sh" sh -c "sleep 60" 2>/dev/null & gk=$!
for i in $(seq 1 100); do [ -d "$SG_STATE/locks/gate" ] && [ "$(occ gates)" = 2 ] && break; sleep 0.05; done
helds=$(occ gates)
kill_tree "$gk"
for i in $(seq 1 100); do /bin/ps -p "$gk" >/dev/null 2>&1 || break; sleep 0.05; done
[ "$(occ gates)" = 0 ] && ok "a killed holder's slots are free again" || ko "slots after the kill: $(occ gates) held, want 0"
t0=$(date +%s); out=$(SERIAL_WAIT_SECONDS=5 SG_GATE_WORKERS=6 bash "$T/s/gate.sh" sh -c 'echo $SQUAD_GATE_WORKERS' 2>"$T/err6"); rc=$?
[ "$helds" = 2 ] && [ $rc = 0 ] && [ "$out" = 6 ] && [ $(( $(date +%s) - t0 )) -lt 5 ] && ok "a killed holder's lock is taken by the next gate, with no wait" || ko "after a kill: held $helds, rc $rc, out '$out', $(cat "$T/err6")"
[ "$(occ gates)" = 0 ] && ok "the next gate gives back its slots" || ko "slots left behind: $(occ gates)"
rm -rf "$T"; exit $fail
