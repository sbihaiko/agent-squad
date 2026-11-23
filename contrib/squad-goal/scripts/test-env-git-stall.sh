#!/bin/bash
# seconds: 3
# (#2798, ADR-0051) A loop script's git network call has a deadline: env.sh bounds a stalled transfer with git's own
# low-speed bound (GIT_HTTP_LOW_SPEED_LIMIT bytes/s over GIT_HTTP_LOW_SPEED_TIME seconds, SG_GIT_STALL_SECONDS), so a
# fetch from a GitHub that stops answering fails and its pass releases its lock, instead of waiting for hours.
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/profiles" "$T/plain"
printf ': "${SG_REPO:=acme/emu}"; : "${SG_OWNER:=acme}"; : "${SG_PROJECT:=1}"\n' > "$T/profiles/emu.sh"
# A server that accepts the connection, reads the request and never answers: the stall the loop met.
python3 - "$T/port" <<'PY' &
import socket, sys, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", 0)); s.listen(8)
open(sys.argv[1], "w").write(str(s.getsockname()[1]))
held = []
while True:
    c, _ = s.accept(); c.recv(65536); held.append(c)
PY
srv=$!
for _ in $(seq 50); do [ -s "$T/port" ] && break; sleep 0.1; done
port=$(cat "$T/port")
s0=$(date +%s)
(cd "$T/plain" && env -u GIT_HTTP_LOW_SPEED_LIMIT -u GIT_HTTP_LOW_SPEED_TIME SG_PROFILE=emu SG_PROFILES_DIR="$T/profiles" \
  SG_STATE="$T/state" SG_SHARED="$T/shared" SG_BOARD_CACHE="$T/cache/b.json" SG_SQUAD="$T" SG_GIT_STALL_SECONDS=2 \
  bash -c '. "$1/env.sh" >/dev/null 2>&1; GIT_TERMINAL_PROMPT=0 /usr/bin/git ls-remote "http://127.0.0.1:$2/r.git" >/dev/null 2>&1' _ "$d" "$port") &
g=$!
( sleep 20; kill -9 "$g" 2>/dev/null ) & wd=$!
wait "$g" 2>/dev/null; rc=$?; el=$(( $(date +%s) - s0 ))
kill "$wd" "$srv" 2>/dev/null; wait 2>/dev/null
[ "$rc" != 0 ] && [ "$rc" != 137 ] && [ "$el" -lt 15 ] && ok "2798 a stalled git transfer fails at the env.sh bound (${el}s)" \
  || ko "2798 a stalled git transfer ran ${el}s (rc $rc); env.sh sets no low-speed bound"
rm -rf "$T"; exit $fail
