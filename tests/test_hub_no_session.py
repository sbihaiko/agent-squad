"""The hub serves no session (SPEC 2489, #2491).

One list names every removed method and path pair. Each answers as an unknown route does: a GET serves the page, a POST
answers 400 on loopback. The list runs on the hub and through the mux under a loaded profile prefix, from loopback and
from an admitted tailnet identity. A trap module stands in for the session code: a request that reached it raises.

Fixture profiles over temp checkouts, as test_hub_mux_tailnet.py; no network.
"""
import http.client
import importlib.util
import json
import os
import pathlib
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
TAILNET = "hub.tail1234.ts.net"
ANA = "ana@example.com"
SLUG = "alpha"

GET_PAIRS = ("/api/sessions", "/api/sessions/card?sid=s1&client_id=c1", "/api/sessions/conversation?sid=s1",
             "/api/sessions/clip?sid=s1&k=t1", "/api/sessions/image?sid=s1&p=/tmp/co/images/a.png",
             "/api/sessions/send?sid=s1&client_id=c1", "/api/sessions/project")
POST_PAIRS = ("/api/sessions/send", "/api/sessions/card/answer", "/api/sessions/attach", "/api/sessions/voice",
              "/api/sessions/screenshot")


class Trap:
    """The session code a request must never reach: any attribute access is a failure and is recorded."""
    hits = []

    def __getattr__(self, name):
        Trap.hits.append(name)
        raise AssertionError(f"a request reached session code: {name}")


def _trap():
    Trap.hits[:] = []
    for name in ("sessions", "session_send", "session_card"):
        sys.modules[name] = Trap()
    return Trap.hits


def _untrap(names):
    for name in names:
        sys.modules.pop(name, None)


@pytest.fixture(scope="module")
def fleet(tmp_path_factory):
    t = tmp_path_factory.mktemp("hub_no_session")
    prof, base = t / "profiles", t / "state"
    prof.mkdir()
    checkout = t / "co-alpha"
    (checkout / "docs" / "squad").mkdir(parents=True)
    (checkout / "docs" / "squad" / "defaults.yaml").write_text(
        f"tailnet:\n  host: {TAILNET}\n  allow: {json.dumps([ANA])}\n")
    (prof / f"{SLUG}.sh").write_text(
        f': "${{SG_REPO:=x/alpha}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:=3}}"; : "${{SG_CHECKOUT:={checkout}}}"\n')
    home = t / "home"
    home.mkdir()
    env = {"SG_PROFILES_DIR": str(prof), "SG_STATE_BASE": str(base), "HOME": str(home)}
    before = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    spec = importlib.util.spec_from_file_location("hubmux_no_session_under_test", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    apps = mux.load_apps()
    mod = apps[SLUG]["mod"]
    mod.run_list = lambda: []
    mod.runs = lambda: []
    direct = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=direct.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    muxed = mux.server(0, apps)
    threading.Thread(target=muxed.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        yield {"hub": direct.server_address[1], "mux": muxed.server_address[1], "mod": mod}
    finally:
        direct.shutdown(); direct.server_close()
        muxed.shutdown(); muxed.server_close()
        for k, v in before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _request(port, method, path, *, host=None, login=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{port}", "Content-Type": "application/json"}
    if login is not None:
        headers["Tailscale-User-Login"] = login
        headers["Origin"] = f"https://{TAILNET}"
    elif method == "POST":
        headers["Origin"] = f"http://127.0.0.1:{port}"
    c.request(method, path, body=body, headers=headers)
    r = c.getresponse()
    out = r.status, r.getheader("Content-Type") or "", r.read()
    c.close()
    return out


def _prefix(port, path):
    return f"/{SLUG}{path}"


@pytest.mark.parametrize("path", GET_PAIRS)
def test_a_removed_get_serves_the_page_on_loopback(fleet, path):
    trap = _trap()
    try:
        for port in (fleet["hub"], fleet["mux"]):
            status, ct, body = _request(port, "GET", path if port == fleet["hub"] else _prefix(port, path))
            assert status == 200, (port, path)
            assert ct.startswith("text/html"), (port, path, ct)
            assert b"<title>" in body or b"<!doctype html" in body.lower(), (port, path)
    finally:
        _untrap(("sessions", "session_send", "session_card"))
    assert trap == [], f"a removed GET reached session code: {trap}"


@pytest.mark.parametrize("path", GET_PAIRS)
def test_a_removed_get_serves_the_page_from_an_admitted_tailnet_identity(fleet, path):
    trap = _trap()
    try:
        for port in (fleet["hub"], fleet["mux"]):
            status, ct, _ = _request(port, "GET", path if port == fleet["hub"] else _prefix(port, path),
                                     host=TAILNET, login=ANA)
            assert status == 200, (port, path, status)
            assert ct.startswith("text/html"), (port, path, ct)
    finally:
        _untrap(("sessions", "session_send", "session_card"))
    assert trap == [], f"a removed GET reached session code: {trap}"


@pytest.mark.parametrize("path", POST_PAIRS)
def test_a_removed_post_answers_400_on_loopback(fleet, path):
    body = b'{"sid":"s1","text":"hi","client_id":"c1"}'
    trap = _trap()
    try:
        for port in (fleet["hub"], fleet["mux"]):
            status, ct, raw = _request(port, "POST", path if port == fleet["hub"] else _prefix(port, path), body=body)
            assert status == 400, (port, path, status)
            assert json.loads(raw)["err"] == "forbidden", (port, path, raw[:200])
    finally:
        _untrap(("sessions", "session_send", "session_card"))
    assert trap == [], f"a removed POST reached session code: {trap}"


@pytest.mark.parametrize("path", POST_PAIRS)
def test_a_removed_post_answers_no_session_from_an_admitted_tailnet_identity(fleet, path):
    body = b'{"sid":"s1","text":"hi","client_id":"c1"}'
    trap = _trap()
    try:
        for port in (fleet["hub"], fleet["mux"]):
            status, _, raw = _request(port, "POST", path if port == fleet["hub"] else _prefix(port, path),
                                      host=TAILNET, login=ANA, body=body)
            assert status in (400, 403), (port, path, status)
            assert b"status" not in raw or b"msg_id" not in raw, (port, path, raw[:200])
    finally:
        _untrap(("sessions", "session_send", "session_card"))
    assert trap == [], f"a removed POST reached session code: {trap}"


def test_the_hub_imports_no_session_module(tmp_path, monkeypatch):
    """The hub starts with no session module: an import at load reaches the trap and the load fails."""
    for k, v in (("SG_REPO", "x/alpha"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_PROFILE", "alpha"),
                 ("SG_CHECKOUT", str(tmp_path / "co")), ("SG_STATE", str(tmp_path / "state")),
                 ("SG_HUB_DIR", str(tmp_path / "hub")), ("HOME", str(tmp_path / "home"))):
        monkeypatch.setenv(k, v)
    (tmp_path / "home").mkdir(exist_ok=True)
    trap = _trap()
    try:
        spec = importlib.util.spec_from_file_location("hub_no_session_load_under_test", SCRIPTS / "hub.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        _untrap(("sessions", "session_send", "session_card"))
    assert trap == [], f"the hub imported session code at load: {trap}"
    assert not hasattr(mod, "sessions_payload")
    assert not hasattr(mod, "conversation_payload")
    assert not hasattr(mod, "send_to_session")


def test_the_session_modules_and_their_scripts_are_gone():
    for name in ("sessions.py", "session_send.py", "session_card.py", "probe-session-send.py",
                 "hub-overlay.js", "vendor-html2canvas-1.4.1.min.js", "vendor-html2canvas.VENDOR"):
        assert not (SCRIPTS / name).exists(), f"{name} still ships"


def test_the_hub_names_none_of_the_removed_paths():
    src = (SCRIPTS / "hub.py").read_text()
    for path in GET_PAIRS + POST_PAIRS:
        route = path.split("?")[0]
        assert f'"{route}"' not in src, f"hub.py still names {route}"


def test_the_mux_passes_on_no_session_handler():
    src = (SCRIPTS / "hubmux.py").read_text()
    for name in ("send_to_session", "answer_card", "send_attach", "send_voice"):
        assert f".{name}(" not in src, f"hubmux.py still passes on {name}"
    assert "SQUAD_OV_SOURCES" not in src
    assert "hub-overlay.js" not in src
