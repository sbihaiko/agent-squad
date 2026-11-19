#!/bin/bash
# seconds: 0.11
# The fast lane appends one load sample per cycle to $SG_STATE/load.log: the UTC time, then the 1-, 5- and 15-minute averages.
# Offline: stub steps beside a copy of fastloop.sh, one iteration per run (SG_FASTLOOP_ONCE=1).
# usage: bash test-fastloop-load.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
S=$T/s; mkdir -p "$S" "$T/st"; cp "$d/fastloop.sh" "$S/"
printf 'SG_STATE=%s; SG_FAST_INTERVAL=120\n' "$T/st" > "$S/env.sh"
for f in single.sh autofrontier.sh autobug.sh; do printf '#!/bin/bash\nexit 0\n' > "$S/$f"; chmod +x "$S/$f"; done
unset "${!SG_@}"
SG_FASTLOOP_ONCE=1 perl -e 'alarm 20; exec @ARGV' bash "$S/fastloop.sh" >/dev/null 2>&1
SG_FASTLOOP_ONCE=1 perl -e 'alarm 20; exec @ARGV' bash "$S/fastloop.sh" >/dev/null 2>&1
L=$T/st/load.log
sample='^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} LOAD [0-9]+\.[0-9]+ [0-9]+\.[0-9]+ [0-9]+\.[0-9]+$'
[ "$(wc -l < "$L" 2>/dev/null | tr -d ' ')" = 2 ] && ok "one load sample per cycle" || ko "load samples: '$(cat "$L" 2>/dev/null)'"
[ "$(/usr/bin/grep -cE "$sample" "$L" 2>/dev/null)" = 2 ] && ok "each sample is the UTC time and the 1, 5 and 15 minute averages" || ko "bad sample line: '$(cat "$L" 2>/dev/null)'"
rm -rf "$T"; exit $fail
