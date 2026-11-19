#!/bin/bash
# seconds: 0.73
# A fast lane whose supervisor died exits, so it never runs beside the lane of the next watchd start; and its exit keeps a pid
# file that names another lane (#527).
# Offline: stub steps beside a copy of fastloop.sh, a 0.6 s beat, and an alarm so a hung loop cannot run on.
# usage: bash test-fastloop-orphan.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
S=$T/s; mkdir -p "$S" "$T/st"; cp "$d/fastloop.sh" "$S/"
printf 'SG_STATE=%s; SG_FAST_INTERVAL=0.6\n' "$T/st" > "$S/env.sh"
for f in single.sh autofrontier.sh autobug.sh; do printf '#!/bin/bash\nexit 0\n' > "$S/$f"; chmod +x "$S/$f"; done
unset "${!SG_@}"
P=$T/st/fastloop.pid
# the supervisor starts the lane, lives past its first beat, hands the pid file to the lane of a later start and dies: the
# lane is orphaned in its sleep, and its next beat finds it so
lp=$(bash -c 'perl -e "alarm 20; exec @ARGV" bash "$1" >"$3" 2>&1 & echo $!; sleep 0.3; echo 999999 > "$2"' _ "$S/fastloop.sh" "$P" "$T/out1")
for _ in $(seq 300); do kill -0 "$lp" 2>/dev/null || break; sleep 0.01; done
kill -0 "$lp" 2>/dev/null && ko "the orphaned lane still runs" || ok "a lane whose supervisor died exits"
[ "$(cat "$P" 2>/dev/null)" = 999999 ] && ok "its exit keeps the pid file of another lane" || ko "pid file now: $(cat "$P" 2>/dev/null)"
# #1781: the lane says why it exits, in one line, so a dead lane is never a silent one.
[ "$(/usr/bin/grep -c "FASTLOOP $lp exits: its supervisor is gone" "$T/out1")" = 1 ] && ok "the lane writes one line that says why it exits" || ko "exit line: $(cat "$T/out1")"
# #1781: an empty answer of the process reader is no proof that the supervisor died: the lane runs on. Before, the empty
# answer read as a changed parent, and the lane exited in silence.
S2=$T/s2; mkdir -p "$S2" "$T/st2"; cp "$d/fastloop.sh" "$S2/"; printf '#!/bin/bash\nexit 0\n' > "$T/nops"; chmod +x "$T/nops"
printf 'SG_STATE=%s; SG_FAST_INTERVAL=0.3; SG_PS=%s\n' "$T/st2" "$T/nops" > "$S2/env.sh"
for f in single.sh autofrontier.sh autobug.sh; do cp "$S/$f" "$S2/$f"; done
bash -c 'perl -e "alarm 20; exec @ARGV" bash "$1" >/dev/null 2>&1 & echo $! > "$2"; sleep 4' _ "$S2/fastloop.sh" "$T/lp2" & sup=$!
sleep 2.5; lp2=$(cat "$T/lp2" 2>/dev/null)
[ -n "$lp2" ] && kill -0 "$lp2" 2>/dev/null && ok "an empty process reader answer does not end the lane" || ko "the lane ended on an empty process reader answer"
kill "$lp2" 2>/dev/null; { kill "$sup"; wait "$sup"; } 2>/dev/null
kill "$lp" 2>/dev/null; rm -rf "$T"; exit $fail
