#!/bin/bash
# seconds: 0.70
# A holder whose lock another process took over leaves that lock alone at its exit (#531): else a third pass starts beside
# the second, as two childcc passes did. usage: bash test-single-owner.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
export SG_STATE=$T/st; mkdir -p "$SG_STATE"; L=$SG_STATE/locks/single-job
bash "$d/single.sh" job sleep 0.6 & a=$!
for _ in $(seq 100); do [ -s "$L/pid" ] && break; sleep 0.01; done
sleep 5 & b=$!; rm -rf "$L"; mkdir "$L"; echo "$b" > "$L/pid"   # the lock passes to b while the first holder still runs
wait "$a"
[ "$(cat "$L/pid" 2>/dev/null)" = "$b" ] && ok "the old holder's exit keeps the new holder's lock" || ko "the lock of pid $b was removed by the old holder"
bash "$d/single.sh" job sh -c "echo ran > $T/ran"
[ -e "$T/ran" ] && ko "a pass started beside the live holder" || ok "no pass starts beside the live holder"
bash "$d/single.sh" mine true; [ -d "$SG_STATE/locks/single-mine" ] && ko "a holder left its own lock" || ok "a holder still removes its own lock"
kill "$b" 2>/dev/null; wait "$b" 2>/dev/null; rm -rf "$T"; exit $fail
