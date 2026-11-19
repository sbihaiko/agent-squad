#!/bin/bash
# Regression test for #94: shell-suites.sh takes no machine-wide `gate` lock. The lock guarded the full pytest gate only,
# and one holder held across the shell suites blocked every gate on the machine for a quarter hour. The local gate retired
# with the local land path (#115): the machine's full gate is the CI check job, which the landing pass (branchland.sh) reads,
# and tests/test_squad_full_gate_machine_lock.py now guards that no script of the loop takes that lock. So two
# shell-suites.sh at once both run at once and print no wait line, and a run while another gate holds the lock runs at once
# and leaves that lock alone.
# usage: bash test-shell-suites-gate.sh [path/to/shell-suites.sh]
src=${1:-$(dirname "$0")/shell-suites.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
S=$T/s; mkdir -p "$S"; cp "$src" "$(dirname "$src")/serial.sh" "$(dirname "$src")/caps.sh" "$S/"   # serial.sh and caps.sh (the git scrub, #2306) beside the suites, as in the checkout
export SG_STATE=$T/st; mkdir -p "$SG_STATE"
printf '#!/bin/bash\necho start >> %s/log; sleep 2; echo end >> %s/log\n' "$T" "$T" > "$S/test-slow.sh"
: > "$T/log"
bash "$S/shell-suites.sh" > "$T/1.out" 2>&1 & p1=$!
bash "$S/shell-suites.sh" > "$T/2.out" 2>&1 & p2=$!
took=0; while kill -0 $p1 2>/dev/null || kill -0 $p2 2>/dev/null; do [ -e "$SG_STATE/locks/gate" ] && took=1; sleep 0.2; done
wait $p1; r1=$?; wait $p2; r2=$?
[ "$r1$r2" = "00" ] && ok "both shell suite runs are green" || ko "exit codes: $r1 $r2"
[ "$(tr '\n' ' ' < "$T/log")" = "start start end end " ] && ok "two shell suite runs run at once; neither waits for the other" || ko "one shell suite run waited for the other: $(tr '\n' ' ' < "$T/log")"
[ "$took" = 0 ] && ok "no shell suite run takes the gate lock" || ko "a shell suite run took the gate lock"
w=$(cat "$T/1.out" "$T/2.out" | /usr/bin/grep -c '^serial: waiting for lock gate')
[ "$w" = 0 ] && ok "no shell suite run prints a wait line" || ko "wait lines: $w"
# Another gate holds the lock (a live pid): the shell suites run at once and do not take the holder's lock away.
mkdir -p "$SG_STATE/locks/gate"; echo $$ > "$SG_STATE/locks/gate/pid"
: > "$T/log"
SERIAL_WAIT_SECONDS=3 bash "$S/shell-suites.sh" > "$T/3.out" 2>&1; r3=$?
[ "$r3" = 0 ] && ok "a shell suite run beside a gate holder runs without waiting" || ko "shell suites beside a holder exit $r3: $(cat "$T/3.out")"
[ "$(tr '\n' ' ' < "$T/log")" = "start end " ] && ok "the shell suite run beside the holder ran its suites" || ko "log beside the holder: $(tr '\n' ' ' < "$T/log")"
/usr/bin/grep -q '^serial: waiting for lock gate' "$T/3.out" && ko "the run beside the holder waited for it" || ok "the run beside the holder prints no wait line"
[ "$(cat "$SG_STATE/locks/gate/pid" 2>/dev/null)" = "$$" ] && ok "the holder's lock survives the shell suite run" || ko "the shell suite run took the holder's lock away"
rm -rf "$T"; exit $fail
