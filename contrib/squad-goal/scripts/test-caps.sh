#!/bin/bash
# seconds: 1.16
# Regression test for caps.sh and squad/caps.py (#1573): the machine-wide cap counts only live holders.
# A gate slot whose holder pid is dead, or is a zombie, is stale: it holds no slot and is reaped with exactly one line naming
# the slot and its holder. A slot whose holder lives still holds the cap and writes no line. serial.sh already applies this
# rule to its lock (#1057); the cap reads a lease the same way.
# usage: bash test-caps.sh [scripts dir]
# mirrors: caps.sh
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s"; cp "$d/caps.sh" "$T/s/" 2>/dev/null
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps   # the module of this repository, and a throwaway budget
py=$(bash -c ". '$T/s/caps.sh'; sg_gate_python")
mkdir -p "$SQUAD_CAPS_DIR/gates" "$T/proj/docs/squad"; printf 'caps:\n  gates: 1\n' > "$T/proj/docs/squad/defaults.yaml"   # one gate slot: one stale lease would fill it
capsctl() { PYTHONPATH="$SG_SQUAD" SQUAD_CAPS_DIR="$SQUAD_CAPS_DIR" "$py" -m squad.caps "$@"; }
occ() { capsctl --target "$T/proj" 2>>"$T/occ.err" | "$py" -c 'import json,sys; print(json.load(sys.stdin)["occupancy"]["gates"])'; }
lease() { printf '{"holder": "gate-%s", "pid": %s, "since": %s}\n' "$1" "$1" "${2:-1}" > "$SQUAD_CAPS_DIR/gates/gate-$1.slot0.json"; }
rm -f "$T/occ.err"

# a dead holder: the slot holds nothing, one line names it, and the next take gets the slot
sh -c 'exit 0' & dp=$!; wait "$dp"
lease "$dp"
out=$(capsctl take gates gate-new --count 1 --try --pid $$ --target "$T/proj" 2>"$T/dead.err"); rc=$?
[ "$rc" = 0 ] && [ "$out" = 1 ] && ok "a dead holder's slot is no slot: the next take gets the cap at once" || ko "dead slot held the cap: rc=$rc out='$out' err=$(cat "$T/dead.err")"
[ -e "$SQUAD_CAPS_DIR/gates/gate-$dp.slot0.json" ] && ko "the dead holder's slot survived the count" || ok "the dead holder's slot is reaped"
[ "$(/usr/bin/grep -c "^caps: reaping stale slot gate-$dp.slot0.json in gates: holder pid $dp is dead\$" "$T/dead.err")" = 1 ] && ok "the reap names the slot, its kind and the dead pid on one line" || ko "reap line: $(cat "$T/dead.err")"
[ "$(/usr/bin/grep -c '^caps:' "$T/dead.err")" = 1 ] && ok "the reap is exactly one line" || ko "lines: $(cat "$T/dead.err")"
capsctl release gates gate-new >/dev/null

# a live holder: the slot holds the cap, and no line is written for it
sleep 60 & lv=$!
lease "$lv" "$(date +%s)"   # the lease is written now: the live pid is the one this process started as, never a reused one
out=$(capsctl take gates gate-new --count 1 --try --pid $$ --target "$T/proj" 2>"$T/live.err"); rc=$?
[ "$rc" = 73 ] && [ "$(occ)" = 1 ] && ok "a live holder's slot still holds the cap" || ko "live slot lost: rc=$rc occ=$(occ) err=$(cat "$T/live.err")"
[ -e "$SQUAD_CAPS_DIR/gates/gate-$lv.slot0.json" ] && ok "a live holder's slot is left alone" || ko "a live holder's slot was reaped"
/usr/bin/grep -q 'reaping' "$T/live.err" && ko "a reap line for a live holder: $(cat "$T/live.err")" || ok "a live holder writes no reap line"
kill "$lv" 2>/dev/null; wait "$lv" 2>/dev/null; rm -rf "$SQUAD_CAPS_DIR/gates"; mkdir -p "$SQUAD_CAPS_DIR/gates"

# a zombie holder: it answers a signal and works nothing, so its slot is stale and the line says why
"$py" -c 'import subprocess,time,sys
p = subprocess.Popen(["true"])
print(p.pid, flush=True)
time.sleep(20)' > "$T/zpid" & zpar=$!
for _ in $(seq 1 100); do [ -s "$T/zpid" ] && break; sleep 0.1; done
zp=$(cat "$T/zpid" 2>/dev/null)
for _ in $(seq 1 100); do [ "$(PYTHONPATH="$SG_SQUAD" "$py" -c 'import sys; from squad import procs; print(procs.snapshot([int(sys.argv[1])])[int(sys.argv[1])].stat)' "$zp" 2>/dev/null)" = Z ] && break; sleep 0.1; done
lease "$zp"
[ "$(occ)" = 0 ] && ok "a zombie holder's slot is no slot" || ko "a zombie slot held the cap: occ=$(occ)"
[ "$(/usr/bin/grep -c "^caps: reaping stale slot gate-$zp.slot0.json in gates: holder pid $zp is a zombie\$" "$T/occ.err")" = 1 ] && ok "the reap names the zombie and its pid on one line" || ko "zombie reap line: $(cat "$T/occ.err")"
kill -9 "$zpar" 2>/dev/null; wait "$zpar" 2>/dev/null

exit $fail
