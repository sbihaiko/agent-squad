#!/bin/bash
# The promotion push names its own git credential helper (#2126). The owner's ~/.gitconfig routes the remote to a helper whose
# token the loop's process cannot reach, so promote-main.sh must not inherit that route. Offline: a loopback server serves the
# bare remote over the dumb protocol, so reads succeed, and answers 401 to the receive-pack advertisement, so the push must ask
# a helper; the owner's config holds a helper that cannot answer; the push names its own helper.
# usage: bash test-promote-main-credential.sh
for v in $(compgen -e | /usr/bin/grep '^SG_'); do unset "$v"; done   # hermetic: the live loop exports its own SG_* (#1410)
d=$(cd "$(dirname "$0")" && pwd); T=$(mktemp -d); fail=0; srv=
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
trap '[ -n "$srv" ] && kill "$srv" 2>/dev/null; rm -rf "$T"' EXIT
mkdir -p "$T/home" "$T/bin"
G="git -c user.email=a@b -c user.name=t"
$G init -q --bare "$T/remote.git"; $G init -q "$T/w"
$G -C "$T/w" commit -q --allow-empty -m base; $G -C "$T/w" push -q "$T/remote.git" HEAD:refs/heads/main
$G -C "$T/w" commit -q --allow-empty -m work; $G -C "$T/w" push -q "$T/remote.git" HEAD:refs/heads/stg
git -C "$T/remote.git" update-server-info
cat > "$T/deny.py" <<'PY'
import http.server, socketserver, sys
class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=sys.argv[1], **k)
    def deny(self):
        self.send_response(401); self.send_header("WWW-Authenticate", 'Basic realm="x"')
        self.send_header("Content-Length", "0"); self.end_headers()
    def do_GET(self):
        if "git-receive-pack" in self.path: return self.deny()
        return super().do_GET()
    def do_POST(self): return self.deny()
    def log_message(self, *a): pass
with socketserver.TCPServer(("127.0.0.1", 0), H) as s:
    print(s.server_address[1], flush=True); s.serve_forever()
PY
mkfifo "$T/port"; python3 "$T/deny.py" "$T" > "$T/port" & srv=$!
read -r -t 20 port < "$T/port"; rm -f "$T/port"
printf '#!/bin/bash\necho "owner $1" >> "%s/owner.log"; exit 1\n' "$T" > "$T/bin/owner-helper"
printf '#!/bin/bash\necho "own $1" >> "%s/own.log"; [ "$1" = get ] && printf "username=x\\npassword=y\\n"\n' "$T" > "$T/bin/own-helper"
chmod +x "$T/bin/owner-helper" "$T/bin/own-helper"
# The owner's global config: the remote's helper is reset, then named as the owner's helper, as the owner's ~/.gitconfig does.
printf '[credential "http://127.0.0.1:%s"]\n\thelper =\n\thelper = !%s\n' "$port" "$T/bin/owner-helper" > "$T/home/.gitconfig"
# run <VAR=value>...: the push under the loop's minimal environment, with its own environment only.
run() { env -i HOME="$T/home" PATH=/usr/bin:/bin:/opt/homebrew/bin SG_REPO=x/emu \
  SG_PROMOTE_REMOTE="http://127.0.0.1:$port/remote.git" "$@" bash "$d/promote-main.sh" > "$T/out" 2>&1; }
run SG_PROMOTE_CREDENTIAL_HELPER="!$T/bin/own-helper"; rc=$?

[ $rc = 3 ] && ok "the push exits 3 when the credential is unreachable" || ko "exit $rc, out '$(cat "$T/out")'"
[ ! -s "$T/owner.log" ] && ok "the push never calls the helper of the owner's config" || ko "the owner's helper was called: '$(cat "$T/owner.log")'"
/usr/bin/grep -q "own get" "$T/own.log" 2>/dev/null && ok "the push asks its own helper for the credential" || ko "its own helper was not asked: '$(cat "$T/own.log" 2>/dev/null)'"
/usr/bin/grep -q -F "$T/bin/owner-helper" "$T/out" && ko "the push names the owner's helper: '$(cat "$T/out")'" || ok "the push names no helper of the owner's config"
/usr/bin/grep -q -F "$T/bin/own-helper" "$T/out" && ok "the push names its own helper when it cannot reach the credential" || ko "the push names no helper: '$(cat "$T/out")'"

[ $fail = 0 ] && echo "PASS test-promote-main-credential" || { echo "FAIL test-promote-main-credential"; exit 1; }
