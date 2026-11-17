#!/bin/bash
# The promotion push reads a credential this process can reach (#2126). Offline: a loopback server serves the bare remote over
# the dumb protocol, so reads succeed, and answers 401 to the receive-pack advertisement, so the push must ask a helper.
# Every helper here answers nothing, so the push exits 3 and names the helpers it asked. The owner's ~/.gitconfig holds a
# helper the push must never call.
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
# Each helper logs its call and answers nothing: a credential this process cannot reach.
for h in owner-helper quiet-helper token-cmd; do
  printf '#!/bin/bash\necho "%s $*" >> "%s/%s.log"\n' "$h" "$T" "$h" > "$T/bin/$h"; chmod +x "$T/bin/$h"
done
# The owner's global config: the remote's helper is reset, then named as the owner's helper, as the owner's ~/.gitconfig does.
printf '[credential "http://127.0.0.1:%s"]\n\thelper =\n\thelper = !%s\n' "$port" "$T/bin/owner-helper" > "$T/home/.gitconfig"
# run <VAR=value>...: the push under the loop's minimal environment, with its own environment only.
run() { env -i HOME="$T/home" PATH=/usr/bin:/bin:/opt/homebrew/bin SG_REPO=x/emu \
  SG_PROMOTE_REMOTE="http://127.0.0.1:$port/remote.git" SG_PROMOTE_TOKEN_COMMAND="$T/bin/token-cmd" "$@" bash "$d/promote-main.sh" > "$T/out" 2>&1; }
run SG_PROMOTE_CREDENTIAL_HELPER="!$T/bin/quiet-helper"; rc=$?

[ $rc = 3 ] && ok "the push exits 3 when no helper answers" || ko "exit $rc, out '$(cat "$T/out")'"
[ ! -s "$T/owner-helper.log" ] && ok "the push never calls the helper of the owner's config" || ko "the owner's helper was called: '$(cat "$T/owner-helper.log")'"
[ -s "$T/token-cmd.log" ] && ok "the push reads the gh token through its own helper" || ko "the gh token was not read: '$(cat "$T/token-cmd.log" 2>/dev/null)'"
/usr/bin/grep -q "quiet-helper get" "$T/quiet-helper.log" 2>/dev/null && ok "the push asks its fallback helper" || ko "the fallback helper was not asked"
/usr/bin/grep -q -F "$d/promote-credential.sh" "$T/out" && ok "the push names its token helper on exit 3" || ko "the token helper is not named: '$(cat "$T/out")'"
/usr/bin/grep -q -F "$T/bin/quiet-helper" "$T/out" && ok "the push names its fallback helper on exit 3" || ko "the fallback helper is not named: '$(cat "$T/out")'"
/usr/bin/grep -q -F "$T/bin/owner-helper" "$T/out" && ko "the push names the owner's helper: '$(cat "$T/out")'" || ok "the push names no helper of the owner's config"

run; rc=$?
[ $rc = 3 ] && ok "the default fallback helper also exits 3 when it answers nothing" || ko "default helper: exit $rc, out '$(cat "$T/out")'"
/usr/bin/grep -q -F "osxkeychain" "$T/out" && ok "the default fallback helper is osxkeychain" || ko "the default helper is not named: '$(cat "$T/out")'"

[ $fail = 0 ] && echo "PASS test-promote-main-credential" || { echo "FAIL test-promote-main-credential"; exit 1; }
