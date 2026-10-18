#!/bin/bash
# A fast lane whose supervisor died exits, so it never runs beside the lane of the next watchd start; and its exit keeps a pid
# file that names another lane (#527).
# Offline: stub steps beside a copy of fastloop.sh, a 0.2 s beat, and an alarm so a hung loop cannot run on.
# usage: bash test-fastloop-orphan.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
S=$T/s; mkdir -p "$S" "$T/st"; cp "$d/fastloop.sh" "$S/"
printf 'SG_STATE=%s; SG_FAST_INTERVAL=0.2\n' "$T/st" > "$S/env.sh"
for f in single.sh autofrontier.sh autobug.sh; do printf '#!/bin/bash\nexit 0\n' > "$S/$f"; chmod +x "$S/$f"; done
unset "${!SG_@}"
P=$T/st/fastloop.pid
# the supervisor starts the lane and dies at once: the lane is orphaned
lp=$(bash -c 'perl -e "alarm 20; exec @ARGV" bash "$1" >/dev/null 2>&1 & echo $!' _ "$S/fastloop.sh")
for _ in $(seq 200); do [ -s "$P" ] && break; sleep 0.01; done
echo 999999 > "$P"   # the pid of the lane of a later start
for _ in $(seq 300); do kill -0 "$lp" 2>/dev/null || break; sleep 0.01; done
kill -0 "$lp" 2>/dev/null && ko "the orphaned lane still runs" || ok "a lane whose supervisor died exits"
[ "$(cat "$P" 2>/dev/null)" = 999999 ] && ok "its exit keeps the pid file of another lane" || ko "pid file now: $(cat "$P" 2>/dev/null)"
kill "$lp" 2>/dev/null; rm -rf "$T"; exit $fail
