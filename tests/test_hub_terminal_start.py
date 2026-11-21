"""Starting a hosted terminal from the panel (#1216): the hub's start route reaches the supervisor's `start` op. The
supervisor is a fake here: what the test pins is the hub's call into it.
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
HUBMUX = (SCRIPTS / "hubmux.py").read_text()
sys.path.insert(0, str(SCRIPTS))

CID = "client-0001-abcd"
SID = "started-session-1"
JOB = "p1"


class FakeSupervisor:
    class Refused(Exception):
        pass

    def __init__(self, session_rows=(), refused=None):
        self.calls = []
        self.session_rows = list(session_rows)
        self.refused = refused

    def ensure(self, state, timeout=10.0):
        pass

    def call(self, state, op, **fields):
        self.calls.append((op, fields))
        if self.refused is not None:
            raise type(self).Refused(self.refused)
        return {"ok": True, "session": SID, "sessions": list(self.session_rows)}


def load_hub(monkeypatch, tmp_path, session_rows=(), refused=None):
    checkout = tmp_path / "checkout"
    (checkout / "docs" / "squad").mkdir(parents=True)
    (checkout / "docs" / "squad" / "defaults.yaml").write_text(
        "\n".join(["tailnet:", f"  host: {TAILNET}", f"  allow: {json.dumps([ANA])}"]) + "\n")
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_PROFILE", JOB),
                 ("SG_CHECKOUT", str(checkout)), ("SG_HUB_DIR", str(tmp_path / "hub")), ("SG_STATE", str(tmp_path / "state"))):
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("hub_terminal_start_under_test", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.run_list = lambda: []
    mod.loop_caps = lambda: None
    fake = FakeSupervisor(session_rows=session_rows, refused=refused)
    mod.terminal_supervisor = lambda: fake
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return SimpleNamespace(port=srv.server_port, fake=fake, server=srv, mod=mod, checkout=checkout)


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


def request(port, method, path, body=None, host="127.0.0.1", origin=None, login=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host if host != "127.0.0.1" else f"127.0.0.1:{port}"}
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


def loopback_post(port, path, body):
    return request(port, "POST", path, body, origin=f"http://127.0.0.1:{port}")


TAILNET = "hub.tail1234.ts.net"
ANA = "ana@example.com"


def test_a_loopback_start_reaches_the_supervisor_and_answers_its_session(hub):
    h = hub()
    status, body = loopback_post(h.port, "/api/terminals/start", {"job": JOB, "cwd": str(h.checkout), "client_id": CID})
    assert status == 200
    assert json.loads(body)["session"] == SID
    assert ("start", {"job": JOB, "cwd": str(h.checkout), "login": "", "client_id": CID, "transport": "loopback"}) in h.fake.calls


def test_the_start_route_needs_the_send_grant_over_the_tailnet(hub):
    h = hub()
    status, body = request(h.port, "POST", "/api/terminals/start", {"job": JOB, "cwd": str(h.checkout), "client_id": CID},
                           host=TAILNET, origin=f"https://{TAILNET}", login=ANA)
    assert status == 403
    assert "tailnet.terminals_send is off" in body
    assert h.fake.calls == []


def test_the_start_route_surfaces_a_refused_job_from_the_supervisor(hub):
    h = hub(refused="job name 'bad job' is not valid")
    status, body = loopback_post(h.port, "/api/terminals/start", {"job": "bad job", "cwd": str(h.checkout), "client_id": CID})
    assert status == 403
    assert json.loads(body)["err"] == "job name 'bad job' is not valid"
    assert h.fake.calls == [("start", {"job": "bad job", "cwd": str(h.checkout), "login": "", "client_id": CID, "transport": "loopback"})]


@pytest.mark.parametrize("body", [{"job": JOB, "cwd": "/tmp"}, {"job": JOB, "cwd": "/tmp", "client_id": CID, "sid": SID},
                                  {"job": 1, "cwd": "/tmp", "client_id": CID}, {"job": JOB, "cwd": "/tmp", "client_id": "x"}])
def test_the_start_route_reads_exactly_its_three_keys(hub, body):
    h = hub()
    status, _ = loopback_post(h.port, "/api/terminals/start", body)
    assert status == 400
    assert h.fake.calls == []


def test_the_start_route_refuses_a_write_without_the_hubs_own_origin(hub):
    h = hub()
    status, _ = request(h.port, "POST", "/api/terminals/start", {"job": JOB, "cwd": str(h.checkout), "client_id": CID})
    assert status == 403
    assert h.fake.calls == []


def test_the_mux_delegates_the_start_route():
    assert "def terminal_start(self, a): return self.profile.terminal_start(self, a)" in HUBMUX


def test_the_route_the_panel_posts_reaches_the_start(hub):
    """#1307: the panel composes startInfo.api + "/terminals/start", and startInfo.api is the sessions base the overlay names
    (/api/sessions), so the page posts /api/sessions/terminals/start. While only /api/terminals/start was served, that path
    fell through to the unknown-route answer and the button read "Refused: forbidden" without any supervisor call."""
    h = hub()
    status, body = loopback_post(h.port, "/api/sessions/terminals/start",
                                 {"job": JOB, "cwd": str(h.checkout), "client_id": CID})
    assert status == 200, body
    assert json.loads(body)["session"] == SID
    assert any(op == "start" for op, _ in h.fake.calls), h.fake.calls


def test_a_supervisor_answer_the_hub_cannot_read_is_a_503_with_its_reason(hub):
    """#1308: an answer the hub cannot parse raised JSONDecodeError out of the request handler, so the browser got no status.
    The caller reads a 503 that names the failure instead."""
    h = hub()

    def boom(state, op, **fields):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    h.fake.call = boom
    status, body = loopback_post(h.port, "/api/terminals/start", {"job": JOB, "cwd": str(h.checkout), "client_id": CID})
    assert status == 503, (status, body)
    assert "could not read" in body


@pytest.mark.parametrize("op, body", [
    ("attach", {"sid": SID, "client_id": CID}),
    ("detach", {"sid": SID, "client_id": CID}),
    ("write", {"sid": SID, "text": "ls\n", "client_id": CID}),
])
def test_every_terminal_write_the_panel_posts_reaches_its_supervisor_op(hub, op, body):
    """#1311: the panel composes every terminal call from the sessions base (`startInfo.api + "/terminals/<op>"`), and #1307
    served only the start under it. The attach then answered 400 forbidden, so a started session never attached."""
    h = hub()
    status, text = loopback_post(h.port, f"/api/sessions/terminals/{op}", body)
    assert status == 200, (status, text)
    assert any(name == op for name, _ in h.fake.calls), h.fake.calls


def test_the_terminal_read_the_panel_polls_reaches_the_supervisor_not_the_sessions_list(hub):
    """#1311: GET /api/sessions/terminals/read fell into the sessions handler, which answered the sessions list with a 200,
    so the panel read the wrong shape and never painted the terminal."""
    h = hub()
    status, text = request(h.port, "GET", f"/api/sessions/terminals/read?sid={SID}&client_id={CID}&offset=0")
    assert status == 200, (status, text)
    assert any(name == "read" for name, _ in h.fake.calls), h.fake.calls
    assert "groups" not in json.loads(text), text


@pytest.mark.parametrize("method, op", [("GET", "read"), ("POST", "attach"), ("POST", "detach"), ("POST", "write"), ("POST", "start")])
def test_the_sessions_based_terminal_path_asks_the_access_seam_for_the_same_action(hub, method, op):
    """#1311: the access seam (ADR-0152 rule 9) decides on the action, so the path the panel calls must name the action its
    terminal route names, or a tailnet grant would read the panel's call as another action."""
    h = hub()
    assert h.mod.route_action(method, f"/api/sessions/terminals/{op}") == h.mod.route_action(method, f"/api/terminals/{op}")
