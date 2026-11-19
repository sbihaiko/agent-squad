#!/bin/bash
# Regression test for gate.sh (#392): one command runs a hand gate under the machine-wide `gate` lock, the lock serial.sh owns.
# Two gates started at once run one after the other and never together; the second waits and then runs, and its wait shows on one
# log line; a gate whose holder died takes the lock; the exit code passes through. Offline, a throwaway SG_STATE.
# (#1166) The lock comes first and the slots second: a gate queued behind a holder takes no slot while it waits, a gate that
# cannot take its slots gives the lock back at once (exit 73), every exit frees both, and a killed holder loses both to the
# next gate. A throwaway budget (SQUAD_CAPS_DIR) is the only one these slots are taken from.
# (#1576) A gate whose caller is SIGKILLed is an orphan nobody waits for: it gives its slots back and exits, so the next gate
# takes the budget it held. The cap is one there, because a freed slot other gates can spare proves nothing.
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
# (#1573) Each of the 12 slots is reaped with one line on stderr, so the count is read on stdout alone: the reap lines are
# the required report, never the gate's answer.
out=$(SG_GATE_WORKERS=6 bash "$T/s/gate.sh" sh -c 'echo $SQUAD_GATE_WORKERS' 2>"$T/ghost.err")
[ "$out" = 6 ] && ok "the slots of a dead holder are reclaimed" || ko "a dead holder's slots blocked the gate: '$out' err=$(cat "$T/ghost.err")"
[ "$(/usr/bin/grep -c '^caps: reaping stale slot ghost.slot[0-9]*\.json in gates: holder pid 99999999 is dead$' "$T/ghost.err")" = 12 ] && ok "each slot of a dead holder is reaped with one line naming it and the pid" || ko "reap lines: $(cat "$T/ghost.err")"

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

# (#1576) A gate whose caller dies is an orphan: nothing waits for its answer, so it must give its slots back and exit instead of
# holding them for its whole bound. The suite that started it is SIGKILLed and runs no trap, so the gate itself watches the
# process that asked for it. The cap is one gate slot, lowered by a project file: the slot the orphan frees is then the one the
# next gate needs, and with six slots the next gate would take a free one while the orphan kept its own, so the case would
# prove nothing.
rm -rf "$T/caps"
mkdir -p "$T/one/docs/squad"; printf 'caps:\n  gates: 1\n' > "$T/one/docs/squad/defaults.yaml"
cd "$T/one"
SG_GATE_WORKERS=1 bash -c 'bash "$1" sh -c "sleep 60" & echo $! > "$2"; wait' sh "$T/s/gate.sh" "$T/ogid" 2>"$T/og1.err" & opar=$!
for i in $(seq 1 100); do [ "$(occ gates)" = 1 ] && break; sleep 0.05; done
[ "$(occ gates)" = 1 ] && ok "the orphan case starts with the only gate slot held" || ko "the orphan case held $(occ gates) slots, want 1"
capsctl take gates probe --count 1 --target "$T/one" --pid "$live" --try 2>/dev/null; [ $? = 73 ] && ok "the only gate slot is full while the first gate runs" || ko "a probe took the slot the first gate holds"
capsctl release gates probe >/dev/null 2>&1
SERIAL_TRY=1 SG_GATE_WORKERS=1 bash "$T/s/gate.sh" true >/dev/null 2>&1; [ $? = 73 ] && ok "a second gate is refused while the first holds the only slot" || ko "a second gate was not refused"
kill -9 "$opar" 2>/dev/null; wait "$opar" 2>/dev/null
t0=$(date +%s)
out=$(SERIAL_POLL_SECONDS=0.5 SERIAL_WAIT_SECONDS=15 SG_GATE_WORKERS=1 bash "$T/s/gate.sh" sh -c 'echo took' 2>"$T/og.err"); rc=$?
[ $rc = 0 ] && [ "$out" = took ] && [ $(( $(date +%s) - t0 )) -le 15 ] && ok "an orphan gate gives its slot back: the next gate takes it within 15 s" \
  || ko "the orphan kept its slot: rc=$rc out='$out' in $(( $(date +%s) - t0 ))s: $(tr '\n' ' ' < "$T/og.err")"
[ "$(occ gates)" = 0 ] && ok "the orphan's slot is free after it exits" || ko "slots after the orphan: $(occ gates) held, want 0"
/usr/bin/grep -q '^gate: orphan: ' "$T/og1.err" && ok "the orphan names itself on one line of its log" || ko "orphan line: $(cat "$T/og1.err")"
og=$(cat "$T/ogid" 2>/dev/null); [ -z "$og" ] || ! kill -0 "$og" 2>/dev/null || kill_tree "$og"   # RED leaves the orphan running

# (#2009, ADR-0174) A tip check and a landing gate wait for their slots while they hold the lock: a child gate that holds the
# slots ends inside one gate bound, and no new child gate takes the lock meanwhile. A child gate that names no wait still
# takes one try. The holder here is a lease the case gives back after two seconds.
for c in tip landing child; do
  rm -rf "$T/caps"
  capsctl take gates holder --count 1 --target "$T/one" --pid "$live" >/dev/null 2>&1
  ( sleep 2; capsctl release gates holder >/dev/null 2>&1 ) & rel=$!
  t0=$(date +%s)
  out=$(SG_GATE_CALLER=$c SG_GATE_WORKERS=1 bash "$T/s/gate.sh" sh -c 'echo took' 2>"$T/w.err"); rc=$?
  wait "$rel" 2>/dev/null
  if [ "$c" = child ]; then
    [ $rc = 73 ] && ok "a child gate that names no wait takes one try at a full budget" || ko "child at a full budget: rc=$rc out='$out'"
  else
    [ $rc = 0 ] && [ "$out" = took ] && ok "a $c gate waits for the slot a running gate holds, and then runs" \
      || ko "$c gate at a full budget: rc=$rc out='$out' in $(( $(date +%s) - t0 ))s: $(tr '\n' ' ' < "$T/w.err")"
  fi
done

# (#2049, ADR-0174) A waiting tip check goes first in the queue: its waiting marker names it as the next holder, and a child
# gate that waits for one slot takes the freed slot only after the tip check gives it back.
rm -rf "$T/caps"; : > "$T/order"
capsctl take gates holder --count 1 --target "$T/one" --pid "$live" >/dev/null 2>&1
SG_GATE_CALLER=tip SG_GATE_WORKERS=1 bash "$T/s/gate.sh" sh -c "echo tip >> $T/order" 2>"$T/p.err" & tipj=$!
for i in $(seq 1 100); do capsctl | /usr/bin/grep -q '"next": "tip"' && break; sleep 0.05; done
capsctl | /usr/bin/grep -q '"holder": "gate-[0-9]*", "next": "tip"' && ok "a waiting tip check's marker names it as the next holder" \
  || ko "no tip marker: $(capsctl) $(tr '\n' ' ' < "$T/p.err")"
( capsctl take gates child-gate-4 --count 1 --wait 20 --target "$T/one" --pid "$live" >/dev/null 2>&1 && echo child >> "$T/order"
  capsctl release gates child-gate-4 >/dev/null 2>&1 ) & kidj=$!
sleep 0.5; capsctl release gates holder >/dev/null 2>&1
wait "$tipj"; wait "$kidj"
[ "$(tr '\n' ' ' < "$T/order")" = "tip child " ] && ok "the tip check takes the freed slot before a waiting child gate" \
  || ko "order: '$(tr '\n' ' ' < "$T/order")': $(tr '\n' ' ' < "$T/p.err")"
capsctl | /usr/bin/grep -q '"next"' && ko "the tip marker outlived the take: $(capsctl)" || ok "the tip marker goes with the take"

# (#2330) A tip check needs its whole count, and a child gate holds one of three slots. When the slot wait runs out, the gate
# answers the waited-out code (75) with the budget's own line, never the try-mode skip (73). A landing reads a skip as a check
# that measured nothing and names no wait, so it lands each run alone.
rm -rf "$T/caps"; mkdir -p "$T/three/docs/squad"; printf 'caps:\n  gates: 3\n' > "$T/three/docs/squad/defaults.yaml"
capsctl take gates "child-gate-$live" --count 1 --target "$T/three" --pid "$live" >/dev/null
t0=$(date +%s)
out=$( (cd "$T/three" && SG_GATE_WORKERS=3 SG_GATE_CALLER=tip SG_GATE_SLOT_WAIT_SECONDS=2 bash "$T/s/gate.sh" sh -c 'echo took') 2>"$T/err5"); rc=$?
el=$(( $(date +%s) - t0 ))
[ $rc = 75 ] && ok "2330 a tip check whose slot wait runs out answers 75" || ko "2330 short take wait: rc $rc out='$out' err=$(tr '\n' ' ' < "$T/err5")"
[ $el -le 10 ] && ok "2330 the tip check's slot wait ends at its own bound" || ko "2330 the short take waited ${el}s, want at most 10"
/usr/bin/grep -qE '^caps: gate-[0-9]+ waited [0-9.]+s for caps\.gates' "$T/err5" && ok "2330 the gate writes the waited-out line the landing reads" \
  || ko "2330 waited-out line: $(tr '\n' ' ' < "$T/err5")"
/usr/bin/grep -q 'skipped, not queued' "$T/err5" && ko "2330 a tip check that waited reports a try-mode skip" || ok "2330 a tip check that waited reports no skip"
[ "$(occ gates)" = 1 ] && ok "2330 the tip check that gave up holds no slot" || ko "2330 slots after the short take: $(occ gates) held, want the one child slot"
capsctl | /usr/bin/grep -q '"next"' && ko "2330 the tip marker outlived the expired wait: $(capsctl)" || ok "2330 the expired wait leaves no tip marker"
capsctl release gates "child-gate-$live" >/dev/null 2>&1
# (#2330) The same bounded take ends in a take when the child's slot frees inside the bound: the tip check gets its whole count,
# runs its command, exits 0, and leaves no slot and no marker behind.
capsctl take gates "child-gate-$live" --count 1 --target "$T/three" --pid "$live" >/dev/null
( sleep 1.5; capsctl release gates "child-gate-$live" >/dev/null 2>&1 ) & rel=$!
out=$( (cd "$T/three" && SG_GATE_WORKERS=3 SG_GATE_CALLER=tip SG_GATE_SLOT_WAIT_SECONDS=20 bash "$T/s/gate.sh" sh -c 'echo "took $SQUAD_GATE_WORKERS"') 2>"$T/err6"); rc=$?
wait "$rel" 2>/dev/null
[ $rc = 0 ] && [ "$out" = "took 3" ] && ok "2330 a tip check whose slot frees inside the bound takes its whole count and runs" \
  || ko "2330 wait then take: rc $rc out='$out' err=$(tr '\n' ' ' < "$T/err6")"
[ "$(occ gates)" = 0 ] && ok "2330 the tip check that took its slots gives them back" || ko "2330 slots after the take: $(occ gates)"
capsctl | /usr/bin/grep -q '"next"' && ko "2330 the tip marker outlived the take: $(capsctl)" || ok "2330 the take leaves no tip marker"
cd "$T"; rm -rf "$T"; exit $fail
