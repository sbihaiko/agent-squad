#!/bin/bash
# Regression test for gate.sh (#392): one command runs a hand gate under the machine-wide `gate` lock, the lock serial.sh owns.
# Two gates started at once run one after the other and never together; the second waits and then runs, and its wait shows on one
# log line; a gate whose holder died takes the lock; the exit code passes through. Offline, a throwaway SG_STATE.
# usage: bash test-gate-lock.sh [scripts dir]
unset SG_SERIAL_HELD   # a gate inside a gate hands the lock name down; this suite takes its own gate lock, so the order is proved
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s"; cp "$d/gate.sh" "$d/serial.sh" "$d/caps.sh" "$T/s/" 2>/dev/null
export SG_STATE=$T/st; mkdir -p "$SG_STATE"
# (#567) A gate takes SG_GATE_WORKERS gate slots of the machine-wide budget (caps.gates) before the lock: a throwaway budget here.
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

# --- the slots ---------------------------------------------------------------------------------------------------------------
sleep 120 >/dev/null 2>&1 & live=$!; trap 'kill $live 2>/dev/null' EXIT   # a holder that lives, for the leases this suite writes
rm -rf "$T/caps"; mkdir -p "$T/caps"
: > "$T/log"; bash "$T/s/gate.sh" sh -c "sleep 3" & g1=$!; sleep 0.5
bash "$T/s/gate.sh" sh -c "echo second >> $T/log" 2>"$T/err2" & g2=$!; sleep 1.5
[ "$(occ gates)" = 4 ] && ok "a second gate takes its slots while it waits for the lock: slots come before the lock" || ko "slots before the lock: $(occ gates) held, want 4"
wait $g1 $g2; [ "$(cat "$T/log")" = second ] && ok "the gate that waited for the lock ran after its slots" || ko "second gate: '$(cat "$T/log")'"
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
. "$d/base-battery.sh"; b=$(SQUAD_GATE_WORKERS=2 SG_GATE_WORKERS=6 sg_base_battery py)
case "$b" in *'-n ${SQUAD_GATE_WORKERS:-6}'*) ok "the battery's pytest reads the count the gate took, 6 when none";; *) ko "battery: '$b'";; esac
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
/usr/bin/grep -c 'caps.gates' "$T/err3" | /usr/bin/grep -q '^1$' && ok "the skip says so on one line naming the ceiling" || ko "skip line: $(cat "$T/err3")"
SERIAL_WAIT_SECONDS=1 SG_GATE_WORKERS=6 bash "$T/s/gate.sh" true 2>"$T/err4"; rc=$?
[ $rc = 75 ] && ok "a wait for slots ends at its bound (exit 75)" || ko "slot wait: rc $rc"
[ "$(/usr/bin/grep -c 'waiting' "$T/err4")" = 1 ] && /usr/bin/grep 'waiting' "$T/err4" | /usr/bin/grep -q 'caps.gates.*other' && ok "a step that waits logs one line naming the full ceiling and its holder" || ko "wait line: $(cat "$T/err4")"
SG_CALL_DEADLINE_SECONDS=1 SG_GATE_WORKERS=6 bash "$T/s/gate.sh" true 2>/dev/null; [ $? = 75 ] && ok "inside a run the wait stays inside the call deadline" || ko "the wait outlived SG_CALL_DEADLINE_SECONDS"
capsctl release gates other
capsctl take gates ghost --count 12 --pid 99999999 >/dev/null
out=$(SG_GATE_WORKERS=6 bash "$T/s/gate.sh" sh -c 'echo $SQUAD_GATE_WORKERS' 2>&1)
[ "$out" = 6 ] && ok "the slots of a dead holder are reclaimed" || ko "a dead holder's slots blocked the gate: '$out'"
rm -rf "$T"; exit $fail
