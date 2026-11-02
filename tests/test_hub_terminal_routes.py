"""The hub's terminal routes (ADR-0152 rule 9): a tailnet read, attach, detach or input needs its own grant and the seam's rules,
and a loopback caller needs no grant. The supervisor is a fake here: what the test pins is the hub's call into it.
"""
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
OVERLAY_JS = (SCRIPTS / "hub-overlay.js").read_text()
sys.path.insert(0, str(SCRIPTS))

TAILNET = "hub.tail1234.ts.net"
ANA = "ana@example.com"
CID = "client-0001-abcd"
TAILNET_ORIGIN = f"https://{TAILNET}"


class FakeSupervisor:
    class Refused(Exception):
        pass

    def __init__(self, ensure_error=None):
        self.calls = []
        self.ensures = []
        self.ensure_error = ensure_error

    def ensure(self, state, timeout=10.0):
        self.ensures.append(state)
        if self.ensure_error is not None:
            raise self.ensure_error

    def call(self, state, op, **fields):
        self.calls.append((op, fields))
        return {"ok": True, "data": "", "offset": 0, "gap": False}


def load_hub(monkeypatch, tmp_path, grants=(), allow=(ANA,), ensure_error=None):
    checkout = tmp_path / "checkout"
    (checkout / "docs" / "squad").mkdir(parents=True)
    lines = ["tailnet:", f"  host: {TAILNET}", f"  allow: {json.dumps(list(allow))}"] + [f"  {g}: true" for g in grants]
    (checkout / "docs" / "squad" / "defaults.yaml").write_text("\n".join(lines) + "\n")
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_PROFILE", "p1"),
                 ("SG_CHECKOUT", str(checkout)), ("SG_HUB_DIR", str(tmp_path / "hub")), ("SG_STATE", str(tmp_path / "state"))):
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("hub_terminals_under_test", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.run_list = lambda: []
    mod.loop_caps = lambda: None
    fake = FakeSupervisor(ensure_error=ensure_error)
    mod.terminal_supervisor = lambda: fake
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return SimpleNamespace(port=srv.server_port, fake=fake, server=srv, mod=mod)


@pytest.fixture
def hub(monkeypatch, tmp_path):
    made = []

    def make(**kw):
        h = load_hub(monkeypatch, tmp_path, **kw)
        made.append(h)
        return h

    yield make
    for h in made:
        h.server.shutdown()
        h.server.server_close()


def request(port, method, path, body=None, host=TAILNET, login=ANA, origin=TAILNET_ORIGIN):
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host}
    if origin is not None:
        headers["Origin"] = origin
    if login:
        headers["Tailscale-User-Login"] = login
    if body is not None:
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
    r = conn.getresponse()
    text = r.read().decode()
    conn.close()
    return r.status, text


READ = "/api/terminals/read?sid=s1&client_id=" + CID + "&offset=0"
ATTACH = {"sid": "s1", "client_id": CID}
WRITE = {"sid": "s1", "text": "hello", "client_id": CID}


def test_tailnet_read_without_its_grant_reads_no_stream(hub):
    h = hub()
    status, body = request(h.port, "GET", READ)
    assert status == 403
    assert "tailnet.terminals_read is off" in body
    assert h.fake.calls == []


def test_tailnet_read_with_its_grant_reads_the_stream_as_the_login(hub):
    h = hub(grants=("terminals_read",))
    status, _ = request(h.port, "GET", READ)
    assert status == 200
    assert h.fake.calls == [("read", {"session": "s1", "offset": 0, "login": ANA, "client_id": CID, "transport": "tailnet"})]


def test_tailnet_read_by_an_unlisted_login_with_the_grant_is_refused_and_reads_nothing(hub):
    h = hub(grants=("terminals_read",))
    status, body = request(h.port, "GET", READ, login="eve@example.com")
    assert status == 403
    assert "login not allowed" in body
    assert h.fake.calls == []


def test_tailnet_attach_without_its_grant_writes_nothing(hub):
    h = hub()
    status, _ = request(h.port, "POST", "/api/terminals/attach", ATTACH)
    assert status == 403
    assert h.fake.calls == []


def test_tailnet_attach_and_detach_with_the_grant_reach_the_supervisor(hub):
    h = hub(grants=("terminals_attach",))
    assert request(h.port, "POST", "/api/terminals/attach", ATTACH)[0] == 200
    assert request(h.port, "POST", "/api/terminals/detach", ATTACH)[0] == 200
    assert [op for op, _ in h.fake.calls] == ["attach", "detach"]


def test_tailnet_input_without_its_grant_types_nothing(hub):
    h = hub(grants=("terminals_read", "terminals_attach"))
    status, body = request(h.port, "POST", "/api/terminals/write", WRITE)
    assert status == 403
    assert "tailnet.terminals_send is off" in body
    assert h.fake.calls == []


def test_tailnet_input_with_its_grant_and_the_tailnet_origin_is_written(hub):
    h = hub(grants=("terminals_send",))
    assert request(h.port, "POST", "/api/terminals/write", WRITE)[0] == 200
    assert h.fake.calls == [("write", {"session": "s1", "text": "hello", "login": ANA, "client_id": CID, "transport": "tailnet"})]


def test_tailnet_input_with_a_foreign_origin_is_refused_even_with_the_grant(hub):
    h = hub(grants=("terminals_send",))
    status, _ = request(h.port, "POST", "/api/terminals/write", WRITE, origin="https://evil.example")
    assert status == 403
    assert h.fake.calls == []


def test_loopback_input_needs_no_grant_and_names_no_login(hub):
    h = hub()
    status, _ = request(h.port, "POST", "/api/terminals/write", WRITE, host=f"127.0.0.1:{h.port}", login=None,
                        origin=f"http://127.0.0.1:{h.port}")
    assert status == 200
    assert h.fake.calls == [("write", {"session": "s1", "text": "hello", "login": "", "client_id": CID, "transport": "loopback"})]


def test_loopback_request_carrying_a_login_never_writes(hub):
    h = hub(grants=("terminals_send",))
    status, _ = request(h.port, "POST", "/api/terminals/write", WRITE, host=f"127.0.0.1:{h.port}", login=ANA,
                        origin=f"http://127.0.0.1:{h.port}")
    assert status == 403
    assert h.fake.calls == []


@pytest.mark.parametrize("path, body", [("/api/terminals/attach", ATTACH), ("/api/terminals/write", WRITE)])
def test_loopback_terminal_post_without_an_origin_is_refused(hub, path, body):
    h = hub()
    status, _ = request(h.port, "POST", path, body, host=f"127.0.0.1:{h.port}", login=None, origin=None)
    assert status == 403
    assert h.fake.calls == []


def test_hub_start_brings_the_terminal_supervisor_up_and_records_it(hub):
    h = hub()
    h.mod.start_terminal_supervisor()
    assert h.fake.ensures == [h.mod.STATE]
    assert h.mod.TERMINAL == {"state": "up", "why": ""}


@pytest.mark.parametrize("error", [RuntimeError("supervisor did not answer in /tmp/state"), OSError("spawn refused")])
def test_refused_supervisor_start_keeps_its_reason_and_the_hub_keeps_serving(hub, capsys, error):
    h = hub(ensure_error=error)
    h.mod.start_terminal_supervisor()
    assert h.mod.TERMINAL == {"state": "refused", "why": str(error)}
    assert str(error) in capsys.readouterr().err


def test_refused_supervisor_start_reaches_the_panel_on_the_project_route_and_the_hub_serves(hub, monkeypatch):
    h = hub(ensure_error=RuntimeError("supervisor did not answer in /tmp/state"))
    monkeypatch.setattr(h.mod.sessions, "project_sessions", lambda profile, checkout, profiles_of=None: [])
    h.mod.start_terminal_supervisor()
    status, body = request(h.port, "GET", "/api/sessions/project", host=f"127.0.0.1:{h.port}", login=None, origin=None)
    assert status == 200
    assert json.loads(body)["supervisor"] == {"state": "refused", "why": "supervisor did not answer in /tmp/state"}


NODE = shutil.which("node")


def supervisor_line(info):
    script = f"{OVERLAY_JS}\nconst SQ=module.exports;\nconsole.log(JSON.stringify(SQ.supervisorLine({json.dumps(info)})));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.mark.node_bound
def test_panel_names_a_refused_supervisor_in_one_sentence():
    line = supervisor_line({"state": "refused", "why": "supervisor did not answer in /tmp/state"})
    assert line == "The terminal supervisor is not running: supervisor did not answer in /tmp/state."


@pytest.mark.node_bound
@pytest.mark.parametrize("info", [{"state": "up", "why": ""}, {"state": "starting", "why": ""}, None])
def test_panel_says_nothing_about_a_supervisor_that_is_up_or_starting(info):
    assert supervisor_line(info) == ""
