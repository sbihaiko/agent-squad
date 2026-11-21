#!/bin/bash
# seconds: 0.81
# Regression test for #1000: a hub on a port other than the live hub's (7700) is a check hub, and starting it never creates the
# live board cache folder under the machine state base (no GitHub: the first board pass fails and writes nothing). usage: bash test-hubmux-check-board.sh
here=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; pid=""
trap '[ -n "$pid" ] && { kill "$pid"; wait "$pid"; } 2>/dev/null; rm -rf "$T"' EXIT
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/home" "$T/profiles" "$T/co"
echo ': "${SG_REPO:=x/alpha}"; : "${SG_OWNER:=x}"; : "${SG_PROJECT:=3}"; : "${SG_CHECKOUT:='"$T/co"'}"' > "$T/profiles/alpha.sh"
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
# The start path starts each profile's background reads and no daemon (#2492).
env -u SG_STATE -u SG_STATE_BASE -u SG_BOARD_CACHE -u SG_HUB_DIR -u SG_PROFILE -u SG_SHARED -u SG_CHECKOUT \
  HOME="$T/home" SG_PROFILES_DIR="$T/profiles" python3 "$here/hubmux.py" "$port" >"$T/hub.log" 2>&1 &
pid=$!
for _ in $(seq 1 100); do curl -s "http://127.0.0.1:$port/api/hub" | grep -q squad-hub && break; sleep 0.2; done
curl -s "http://127.0.0.1:$port/api/hub" | grep -q squad-hub && ok "the check hub answers on its own port" \
  || ko "the check hub did not answer: $(tail -3 "$T/hub.log" | tr '\n' ' ')"
[ ! -e "$T/home/.cache/squad-goal/_boards" ] && ok "a check hub creates no folder under the live board cache base" \
  || ko "a check hub created $(find "$T/home/.cache/squad-goal/_boards" -maxdepth 2 | tr '\n' ' ')"
exit $fail
