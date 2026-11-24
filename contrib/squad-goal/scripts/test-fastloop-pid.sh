#!/bin/bash
# seconds: 0.21
# The fast lane keeps its pid file: watch.sh reads the file to know the lane is up, and a missing file made the cycle run childcc inline (#513).
# Offline: stub steps beside a copy of fastloop.sh, a 0.2 s beat, and an alarm so a hung loop cannot run on.
# usage: bash test-fastloop-pid.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
S=$T/s; mkdir -p "$S" "$T/st"; cp "$d/fastloop.sh" "$S/"
printf 'SG_STATE=%s; SG_FAST_INTERVAL=0.2\n' "$T/st" > "$S/env.sh"
for f in single.sh autofrontier.sh autobug.sh; do printf '#!/bin/bash\nexit 0\n' > "$S/$f"; chmod +x "$S/$f"; done
unset "${!SG_@}"
perl -e 'alarm 20; exec @ARGV' bash "$S/fastloop.sh" >/dev/null 2>&1 & lp=$!
P=$T/st/fastloop.pid
for _ in $(seq 200); do [ -s "$P" ] && break; sleep 0.01; done
[ -s "$P" ] && ok "the lane writes its pid file" || ko "no pid file"
rm -f "$P"
for _ in $(seq 200); do [ -s "$P" ] && break; sleep 0.01; done
[ -s "$P" ] && kill -0 "$(cat "$P")" 2>/dev/null && ok "a deleted pid file is written again on the next beat" || ko "the pid file stayed gone"
kill "$lp" 2>/dev/null; wait "$lp" 2>/dev/null; rm -rf "$T"; exit $fail
