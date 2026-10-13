#!/bin/bash
# Regression test for #81: the full gate is ONE machine-wide run. Two shell-suites.sh at once never overlap -- the second prints
# one wait line naming the holder and runs when the first ends -- a suite run inside a `gate` holder runs at once (the land pass
# runs a suite that runs the gate), and a lock whose holder pid is gone is taken over. usage: bash test-shell-suites-gate.sh [path/to/shell-suites.sh]
src=${1:-$(dirname "$0")/shell-suites.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
S=$T/s; mkdir -p "$S"; cp "$src" "$(dirname "$src")/serial.sh" "$S/"   # the gate lock serial.sh owns, beside the gate as in the checkout
export SG_STATE=$T/st; mkdir -p "$SG_STATE"
printf '#!/bin/bash\necho start >> %s/log; sleep 2; echo end >> %s/log\n' "$T" "$T" > "$S/test-slow.sh"
: > "$T/log"
bash "$S/shell-suites.sh" > "$T/1.out" 2>&1 & p1=$!
bash "$S/shell-suites.sh" > "$T/2.out" 2>&1 & p2=$!
two=0; while kill -0 $p1 2>/dev/null && kill -0 $p2 2>/dev/null; do   # sampled while both gates run: the lock never carries two pids
  [ "$(ls "$SG_STATE/locks/gate" 2>/dev/null | wc -l | tr -d ' ')" -le 1 ] || two=1
  [ "$([ -f "$SG_STATE/locks/gate/pid" ] && wc -l < "$SG_STATE/locks/gate/pid" | tr -d ' ' || echo 0)" -le 1 ] || two=1
  sleep 0.2; done
wait $p1; r1=$?; wait $p2; r2=$?
[ "$r1$r2" = "00" ] && ok "both full gates are green" || ko "gate exit codes: $r1 $r2"
[ "$(tr '\n' ' ' < "$T/log")" = "start end start end " ] && ok "two full gates run one after the other, never at once" || ko "the gates overlapped: $(tr '\n' ' ' < "$T/log")"
[ "$two" = 0 ] && ok "the lock directory never holds two pids" || ko "the lock directory held two pids at once"
w=$(cat "$T/1.out" "$T/2.out" | /usr/bin/grep -c '^serial: waiting for lock gate held by pid ')
[ "$w" = 1 ] && ok "the second gate prints one wait line" || ko "wait lines: $w"
[ -e "$SG_STATE/locks/gate" ] && ko "the gate lock outlived its holder" || ok "the lock is released when the gate ends"
# Re-entrant: a suite run started inside the holder (the land pass runs the gate from a suite) waits for nobody, and does not
# take the holder's lock away with it.
mkdir -p "$SG_STATE/locks/gate"; echo $$ > "$SG_STATE/locks/gate/pid"
: > "$T/log"
SG_SERIAL_HELD=gate SERIAL_WAIT_SECONDS=3 bash "$S/shell-suites.sh" > "$T/3.out" 2>&1; r3=$?
[ "$r3" = 0 ] && ok "a gate inside a gate holder runs without waiting" || ko "re-entrant gate exit $r3: $(cat "$T/3.out")"
[ "$(tr '\n' ' ' < "$T/log")" = "start end " ] && ok "the re-entrant gate ran its suites" || ko "re-entrant gate log: $(tr '\n' ' ' < "$T/log")"
[ -f "$SG_STATE/locks/gate/pid" ] && ok "the holder's lock survives the gate inside it" || ko "the re-entrant gate released the holder's lock"
rm -rf "$SG_STATE/locks/gate"
mkdir -p "$SG_STATE/locks/gate"; echo 99999999 > "$SG_STATE/locks/gate/pid"   # a holder that died before the suite started
: > "$T/log"
SERIAL_WAIT_SECONDS=3 bash "$S/shell-suites.sh" > "$T/4.out" 2>&1; r4=$?
[ "$r4" = 0 ] && ok "a lock whose holder pid is gone is taken over" || ko "stale gate lock blocked the run (exit $r4): $(cat "$T/4.out")"
rm -rf "$T"; exit $fail
