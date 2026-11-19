#!/bin/bash
# seconds: 0.66
# Guard for #1552: a hub that loaded no repository profile refuses a tailnet request by naming that cause, never the
# tailnet host rule, which did not refuse it. A hub with a profile still refuses an unknown Host with the host rule,
# so the fix never blinds the rule itself. The mux serves on an ephemeral loopback port inside this process: no hub is
# started on the live port and no file is written. usage: bash test-hubctl.sh
here=$(cd "$(dirname "$0")" && pwd); export HUBMUX_SRC=$here/hubmux.py; T=$(mktemp -d); fail=0
mkdir -p "$T/empty" "$T/profiles" "$T/co/docs/squad" "$T/state"
printf 'tailnet:\n  host: hub.tail1234.ts.net\n  allow: ["ana@example.com"]\n' > "$T/co/docs/squad/defaults.yaml"
echo ': "${SG_REPO:=x/alpha}"; : "${SG_OWNER:=x}"; : "${SG_PROJECT:=3}"; : "${SG_CHECKOUT:='"$T/co"'}"' > "$T/profiles/alpha.sh"
export EMPTY_PROFILES="$T/empty" PROFILES="$T/profiles" SG_STATE_BASE="$T/state"
out=$(python3 - <<'PY' 2>&1
import http.client, importlib.util, json, os, threading

def load(profiles):
    os.environ["SG_PROFILES_DIR"] = profiles
    spec = importlib.util.spec_from_file_location("hubmux_" + os.path.basename(profiles), os.environ["HUBMUX_SRC"])
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m

def get(port, path, host, login=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": host}
    if login: h["Tailscale-User-Login"] = login
    c.request("GET", path, headers=h); r = c.getresponse(); b = r.read().decode(); c.close()
    return r.status, b

def serve(m, apps):
    srv = m.server(0, apps)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return srv

def report(name, code, body, want_code, want_body):
    ok = code == want_code and body == want_body
    print(("ok   " if ok else "FAIL ") + f"{name}: {code} {body!r}")
    return ok

# A hub that loaded no repository profile: the tailnet refusal names that cause, never the host rule.
m = load(os.environ["EMPTY_PROFILES"]); apps = m.load_apps()
srv = serve(m, apps); port = srv.server_address[1]
code, body = get(port, "/api/hub?identity=1", "hub.tail1234.ts.net", "ana@example.com")
report("no profile, tailnet", code, body, 403, "no profile loaded on this hub")
code, body = get(port, "/api/hub?identity=1", f"127.0.0.1:{port}")
ok = code == 200 and json.loads(body).get("repos") == []
print(("ok   " if ok else "FAIL ") + f"no profile, loopback: {code} {body!r}")
srv.shutdown(); srv.server_close()

# A hub with one profile: a Host that is neither loopback nor the tailnet host still answers the host rule.
m = load(os.environ["PROFILES"]); apps = m.load_apps()
srv = serve(m, apps); port = srv.server_address[1]
code, body = get(port, "/api/hub?identity=1", "evil.example.com", "ana@example.com")
report("one profile, unknown host", code, body, 403, "forbidden host")
srv.shutdown(); srv.server_close()
PY
)
echo "$out"; case "$out" in *FAIL*|*Error*|*Traceback*) fail=1;; esac
rm -rf "$T"; exit $fail
