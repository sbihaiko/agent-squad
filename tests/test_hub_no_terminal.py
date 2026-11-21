"""The hub hosts no terminal and grants no terminal permission (#2492).

Every terminal method and path pair, direct or through the `/api/sessions/terminals/<op>` alias, reaches no terminal code:
an unknown GET serves the page or the sessions answer, an unknown POST answers 400, and no supervisor start leaves a socket
folder behind. The removed modules leave the package, the access seam grants no `terminals_*` permission, the retired
settings keys are ignored, and the mobile setup command names no terminal send setting.
"""
import importlib.util
import json
import pathlib
import sys
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
HUBMUX = SCRIPTS / "hubmux.py"
sys.path.insert(0, str(SCRIPTS))

TAILNET = "hub.tail1234.ts.net"
ANA = "ana@example.com"
CID = "client-0001-abcd"
SID = "s1"
JOB = "p1"

# Every terminal method and path pair, and its alias under the sessions base the overlay names.
REMOVED = [
    ("GET", "/api/terminals/read?sid=s1&client_id=" + CID + "&offset=0", None),
    ("GET", f"/api/sessions/terminals/read?sid={SID}&client_id={CID}&offset=0", None),
    ("POST", "/api/terminals/start", {"job": JOB, "cwd": "/tmp", "client_id": CID}),
    ("POST", "/api/sessions/terminals/start", {"job": JOB, "cwd": "/tmp", "client_id": CID}),
    ("POST", "/api/terminals/attach", {"sid": SID, "client_id": CID}),
    ("POST", "/api/sessions/terminals/attach", {"sid": SID, "client_id": CID}),
    ("POST", "/api/terminals/detach", {"sid": SID, "client_id": CID}),
    ("POST", "/api/sessions/terminals/detach", {"sid": SID, "client_id": CID}),
    ("POST", "/api/terminals/write", {"sid": SID, "text": "hello", "client_id": CID}),
    ("POST", "/api/sessions/terminals/write", {"sid": SID, "text": "hello", "client_id": CID}),
]


def load_hub(monkeypatch, tmp_path):
    checkout = tmp_path / "checkout"
    (checkout / "docs" / "squad").mkdir(parents=True)
    (checkout / "docs" / "squad" / "defaults.yaml").write_text(
        f"tailnet:\n  host: {TAILNET}\n  allow: {json.dumps([ANA])}\n")
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_PROFILE", "p1"),
                 ("SG_CHECKOUT", str(checkout)), ("SG_HUB_DIR", str(tmp_path / "hub")), ("SG_STATE", str(tmp_path / "state"))):
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("hub_no_terminal_under_test", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.run_list = lambda: []
    mod.loop_caps = lambda: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return SimpleNamespace(port=srv.server_port, server=srv, mod=mod, tmp=tmp_path)


@pytest.fixture
def hub(monkeypatch, tmp_path):
    h = load_hub(monkeypatch, tmp_path)
    yield h
    h.server.shutdown()
    h.server.server_close()


def request(port, method, path, body=None, *, tailnet=False):
    """One request from loopback, or from an admitted tailnet identity with the tailnet Origin."""
    own = f"http://127.0.0.1:{port}"
    headers = {"Host": TAILNET if tailnet else f"127.0.0.1:{port}", "Origin": f"https://{TAILNET}" if tailnet else own}
    if tailnet:
        headers["Tailscale-User-Login"] = ANA
    if body is not None:
        headers["Content-Type"] = "application/json"
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
    r = conn.getresponse()
    text = r.read().decode()
    conn.close()
    return r.status, text, r.getheader("Content-Type") or ""


@pytest.mark.parametrize("tailnet", [False, True], ids=["loopback", "tailnet"])
@pytest.mark.parametrize("method,path,body", REMOVED)
def test_a_removed_terminal_route_reaches_no_terminal_code(hub, tailnet, method, path, body):
    """The only answers left are the page (an unknown GET) and 400 (an unknown POST): no terminal read, start or write."""
    status, text, ctype = request(hub.port, method, path, body, tailnet=tailnet)
    if method == "GET":
        assert status == 200, (status, text)   # an unknown GET serves the page, or the sessions payload for its prefix
        if "text/html" not in ctype:
            data = json.loads(text)
            assert "groups" in data and "lines" not in data, data   # the session list, never a terminal read's answer
    else:
        assert status == 400, (status, text)   # an unknown POST answers 400
        assert json.loads(text)["err"] in ("forbidden", "bad request")


@pytest.mark.parametrize("tailnet", [False, True], ids=["loopback", "tailnet"])
def test_a_removed_terminal_route_starts_no_supervisor(hub, tailnet):
    for method, path, body in REMOVED:
        request(hub.port, method, path, body, tailnet=tailnet)
    assert not (hub.tmp / "sup").exists(), "a supervisor start left its socket folder behind"


def test_the_hub_carries_no_terminal_supervisor_start(hub):
    for name in ("start_terminal_supervisor", "terminal_supervisor", "TERMINAL", "hosted_sessions",
                 "terminal_read", "terminal_post", "terminal_start"):
        assert not hasattr(hub.mod, name), name
    src = HUB_PY.read_text()
    for name in ("/api/terminals", "/api/sessions/terminals", "TERMINAL_GRANTS", "TERMINAL_OPS",
                 "start_terminal_supervisor", "terminal_supervisor"):
        assert name not in src, name


def test_the_terminal_modules_leave_the_package():
    """The tree carries neither module. The check reads the tree, not `find_spec`: the interpreter's `squad` package may
    be the editable install of another checkout, and that tree is not this one's to prove."""
    for name in ("supervisor.py", "terminal_audit.py"):
        assert not (ROOT / "squad" / name).exists(), name


STALE = ("squad.supervisor", "squad/supervisor.py", "squad import supervisor", "SQUAD_SUPERVISOR_")


def test_no_executable_code_names_the_removed_supervisor():
    """Executable code that names the removed module or its variables tells a reader the terminal lives. The walk reads
    `squad/` and `contrib/`, so a document may name the retirement without turning this guard red."""
    hits = []
    for base in (ROOT / "squad", ROOT / "contrib"):
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in (".py", ".sh") or "__pycache__" in path.parts:
                continue
            for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if any(token in line for token in STALE):
                    hits.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert hits == [], hits


class _ProfileModule:
    """A profile's hub module that would report its own supervisor start, were one asked for."""

    READ_GATE = None

    def __init__(self, started):
        self.started = started

    def ReadGate(self):
        return SimpleNamespace(touch=lambda: None)

    def start_terminal_supervisor(self):
        self.started.append("start")

    def __getattr__(self, name):   # each background sampler the mux starts
        return lambda *a, **k: None


def test_the_mux_starts_no_supervisor_for_a_first_or_an_added_profile():
    spec = importlib.util.spec_from_file_location("hubmux_no_terminal_under_test", HUBMUX)
    hubmux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hubmux)
    assert not hasattr(hubmux, "supervise")
    src = HUBMUX.read_text()
    assert "supervise(" not in src
    assert "terminal_" not in src
    started = []
    hubmux.start_profile({"repo": "x/alpha", "mod": _ProfileModule(started)})   # the path a profile added later takes
    assert started == []


def test_the_access_seam_grants_no_terminal_permission():
    from squad import tailnet_access as ta
    assert not hasattr(ta, "GRANT_OF")
    assert not hasattr(ta, "READ") and not hasattr(ta, "ATTACH")
    allowed = ta.decide(host=TAILNET, origin=f"https://{TAILNET}", action=ta.SEND, login=ANA, port=7700,
                        tailnet_host=TAILNET, allowed_logins=[ANA])
    assert allowed.allowed, allowed.reason   # the seam holds no terminal grant; the route keeps its own rule
    assert ta.decide(host=TAILNET, origin="https://evil.example", action=ta.CONTROL, login=ANA, port=7700,
                     tailnet_host=TAILNET, allowed_logins=[ANA]).allowed is False   # a kept write keeps its origin rule


def test_validation_ignores_the_three_retired_terminal_keys():
    from squad import project_settings as P
    P.validate({"tailnet": {"terminals_read": True, "terminals_attach": False, "terminals_send": "left as it was"}})


@pytest.mark.parametrize("data,key", [
    ({"tailnet": {"terminals_other": True}}, "tailnet.terminals_other"),
    ({"tailnet": {"terminal_read": True}}, "tailnet.terminal_read"),
    ({"limits": {"terminal_send": True}}, "limits.terminal_send"),
])
def test_validation_still_refuses_another_unknown_key(data, key):
    from squad import project_settings as P
    from squad.options import OptionError
    with pytest.raises(OptionError, match=key):
        P.validate(data)


def test_the_env_load_ignores_the_three_retired_terminal_keys(tmp_path):
    from squad import project_settings as ps
    (tmp_path / ".env").write_text(
        "SQUAD_TAILNET_HOST=box.tail1.ts.net\n"
        "SQUAD_TAILNET_ALLOW=me@example.com\n"
        "SQUAD_TAILNET_TERMINALS_READ=true\n"
        "SQUAD_TAILNET_TERMINALS_ATTACH=true\n"
        "SQUAD_TAILNET_TERMINALS_SEND=true\n")
    assert ps.env_tailnet(tmp_path) == {"host": "box.tail1.ts.net", "allow": ["me@example.com"]}
    data, _ = ps.load(tmp_path)
    assert data["tailnet"] == {"host": "box.tail1.ts.net", "allow": ["me@example.com"]}


def test_the_env_load_ignores_the_retired_supervisor_keys(tmp_path):
    """A `.env` line for the removed supervisor changes no tailnet key, so no setting reaches a start."""
    from squad import project_settings as ps
    (tmp_path / ".env").write_text(
        "SQUAD_TAILNET_HOST=box.tail1.ts.net\n"
        "SQUAD_SUPERVISOR_DIR=/tmp/sup\n"
        "SQUAD_SUPERVISOR_CMD=python3 -m squad.supervisor\n")
    assert ps.env_tailnet(tmp_path) == {"host": "box.tail1.ts.net"}


def test_the_shipped_defaults_name_no_terminal_grant():
    text = (ROOT / "config" / "agent-defaults.yaml").read_text()
    for name in ("terminals_read", "terminals_attach", "terminals_send"):
        assert name not in text, name


def test_the_mobile_setup_command_names_no_terminal_send_setting():
    for path in (ROOT / "commands" / "start-mobile.md", ROOT / "scripts" / "start-mobile.sh"):
        text = path.read_text()
        assert "TERMINALS_" not in text and "terminals_" not in text, path
