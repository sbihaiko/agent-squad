#!/usr/bin/env bash
# Suite for probe-session-send.py (#834): a fake inbox in a child process stands for the session, under a temporary HOME.
# It checks the frames, the refusals, and that the session key is never printed.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
probe="$here/probe-session-send.py"
py="${SG_GATE_PY:-python3}"
tmp="$(mktemp -d /tmp/probe834.XXXXXX)"
srv=""
fail=0
pass() { echo "ok - $1"; }
bad() { echo "FAIL - $1"; fail=1; }
cleanup() { [ -n "$srv" ] && { kill "$srv" 2>/dev/null; wait "$srv" 2>/dev/null; }; rm -rf "$tmp"; }
trap cleanup EXIT

home="$tmp/home"
sessions="$home/.claude/sessions"
sock="$sessions/sockets/s1.sock"
frames="$tmp/frames"
token="abababababababababababababababab"
mkdir -p "$(dirname "$sock")"

"$py" - "$sock" "$frames" <<'PY' &
import socket, sys
path, out = sys.argv[1], sys.argv[2]
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.bind(path)
s.listen(4)
while True:
    c, _ = s.accept()
    data = b""
    while chunk := c.recv(65536):
        data += chunk
    open(out, "ab").write(data)
    c.close()
PY
srv=$!

for _ in $(seq 1 100); do [ -S "$sock" ] && break; done
digest="$("$py" -c 'import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())' "$sock")"

record() {
  printf '{"pid": %s, "sessionId": "s1", "messagingSocketPath": "%s"}\n' "$srv" "$sock" > "$sessions/$srv.json"
}
key() {
  printf '{"peerToken": "%s", "procStart": "x", "pidDomain": "x"}\n' "$token" > "$sessions/$srv.$digest.key"
  chmod "$1" "$sessions/$srv.$digest.key"
}

record
key 600
out="$(HOME="$home" "$py" "$probe" s1 "PROBE hello" 2>&1)"; rc=$?
[ "$rc" -eq 0 ] && [[ "$out" == "sent msg_id="* ]] && pass "a session with its key receives the message" || bad "send failed rc=$rc out=$out"

if [ -s "$frames" ]; then
  if "$py" - "$frames" "$token" <<'PY'
import json, sys
lines = open(sys.argv[1]).read().splitlines()
auth, msg = json.loads(lines[0]), json.loads(lines[1])
assert auth == {"type": "auth", "token": sys.argv[2]}, auth
assert msg["type"] == "user" and msg["message"] == {"role": "user", "content": "PROBE hello"}, msg
assert msg["from_name"] == "squad-hub" and msg["priority"] == "next" and msg["msg_id"], msg
PY
  then pass "the frames carry the auth line and the message fields"; else bad "the frames do not follow the protocol"; fi
else
  bad "the inbox received no frames"
fi

case "$out" in *"$token"*) bad "the key was printed" ;; *) pass "the key is not printed" ;; esac

: > "$frames"
key 644
out="$(HOME="$home" "$py" "$probe" s1 "PROBE hello" 2>&1)"; rc=$?
[ "$rc" -ne 0 ] && [[ "$out" == *"readable by others"* ]] && [ ! -s "$frames" ] && pass "a key readable by others is refused and nothing is written" || bad "a readable key was accepted rc=$rc out=$out"
case "$out" in *"$token"*) bad "the key was printed on refusal" ;; *) pass "the key is not printed on refusal" ;; esac

key 600
out="$(HOME="$home" "$py" "$probe" missing "PROBE hello" 2>&1)"; rc=$?
[ "$rc" -ne 0 ] && [[ "$out" == *"no session record"* ]] && pass "an unknown session id is refused" || bad "an unknown session id was not refused rc=$rc out=$out"

exit "$fail"
