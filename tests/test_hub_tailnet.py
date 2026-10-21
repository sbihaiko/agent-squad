"""The hub applies the tailnet rule through the access seam (squad/tailnet_access.py): loopback as today, a tailnet GET and
write only for a listed login, a tailnet write only with the tailnet Origin, and the signed-in login in the page header.

The allow list comes from the `tailnet` block of the hub's project file (squad/project_settings.py).
"""
import http.client
import importlib.util
import json
import pathlib
import re
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
TAILNET = "hub.tail1234.ts.net"
ANA = "ana@example.com"
BO = "bo@example.com"
EVE = "eve@example.com"
WRITE_ROUTES = ["/api/models", "/api/plans", "/api/promote"]
HEADER = re.compile(r"<div id=who>(.*?)</div>")


@pytest.fixture
def serve(monkeypatch, tmp_path):
    servers = []

    def start(allow=(ANA, BO)):
        checkout = tmp_path / "checkout"
        (checkout / "docs" / "squad").mkdir(parents=True)
        (checkout / "docs" / "squad" / "defaults.yaml").write_text(
            f"tailnet:\n  host: {TAILNET}\n  allow: {json.dumps(list(allow))}\n")
        for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                     ("SG_CHECKOUT", str(checkout)), ("SG_HUB_DIR", str(tmp_path / "hub"))):
            monkeypatch.setenv(k, v)
        for k in ("SG_STATE", "SG_STATE_BASE", "SG_SHARED", "SG_PROFILE"):
            monkeypatch.delenv(k, raising=False)
        spec = importlib.util.spec_from_file_location("hub_tailnet", HUB_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        calls = {"/api/models": [], "/api/plans": [], "/api/promote": []}
        mod.run_list = lambda: []
        mod.loop_caps = lambda: None
        mod.models_get = lambda: {}
        mod.models_set = lambda body: (calls["/api/models"].append(body), {"ok": True})[1]
        mod.plans_set = lambda body: (calls["/api/plans"].append(body), {"ok": True})[1]
        mod.promote = lambda issue: (calls["/api/promote"].append(issue), {"ok": True})[1]
        srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return SimpleNamespace(port=srv.server_port, calls=calls, mod=mod)

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def request(port, method, path, *, host, login=None, origin=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host}
    if login is not None:
        headers["Tailscale-User-Login"] = login
    if origin is not None:
        headers["Origin"] = origin
    if body is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(body)
    c.request(method, path, body=body, headers=headers)
    r = c.getresponse()
    text = r.read().decode()
    c.close()
    return r.status, text


def loopback(port):
    return f"127.0.0.1:{port}"


def tailnet_origin():
    return f"https://{TAILNET}"


def header_login(page):
    return HEADER.search(page).group(1)


def test_loopback_get_is_answered_as_today(serve):
    s = serve()
    status, text = request(s.port, "GET", "/api/version", host=loopback(s.port))
    assert status == 200 and "v" in json.loads(text)


def test_loopback_page_shows_no_login(serve):
    s = serve()
    status, page = request(s.port, "GET", "/", host=loopback(s.port), login=ANA)
    assert status == 200 and header_login(page) == ""


def test_get_with_a_foreign_host_is_refused_with_its_reason(serve):
    s = serve()
    assert request(s.port, "GET", "/api/version", host=f"evil.example:{s.port}") == (403, "forbidden host")


@pytest.mark.parametrize("login, allow, reason", [
    (None, (ANA,), "no tailnet identity"),
    (EVE, (ANA, BO), "login not allowed"),
    (ANA, (), "login not allowed"),
])
def test_tailnet_get_without_an_allowed_login_is_refused_naming_no_list(serve, login, allow, reason):
    s = serve(allow=allow)
    status, text = request(s.port, "GET", "/api/runs", host=TAILNET, login=login)
    assert (status, text) == (403, reason)
    assert ANA not in text and BO not in text


def test_tailnet_get_with_an_allowed_login_is_answered(serve):
    s = serve()
    status, text = request(s.port, "GET", "/api/runs", host=TAILNET, login=ANA)
    assert status == 200 and json.loads(text) == []


@pytest.mark.parametrize("route, body", [("/api/models", {}), ("/api/plans", {}), ("/api/promote", {"issue": 5})])
def test_tailnet_write_with_an_allowed_login_and_its_origin_passes(serve, route, body):
    s = serve()
    status, _ = request(s.port, "POST", route, host=TAILNET, login=ANA, origin=tailnet_origin(), body=body)
    assert status == 200
    assert len(s.calls[route]) == 1


@pytest.mark.parametrize("route", WRITE_ROUTES)
@pytest.mark.parametrize("origin", [None, "https://evil.example", f"http://{TAILNET}", f"{tailnet_origin()}:8443"])
def test_tailnet_write_with_a_wrong_origin_is_refused_and_writes_nothing(serve, route, origin):
    s = serve()
    status, text = request(s.port, "POST", route, host=TAILNET, login=ANA, origin=origin, body={"issue": 5})
    assert (status, text) == (403, "origin not allowed")
    assert s.calls[route] == []


@pytest.mark.parametrize("route", WRITE_ROUTES)
def test_tailnet_write_by_an_unknown_login_is_refused_and_writes_nothing(serve, route):
    s = serve()
    status, text = request(s.port, "POST", route, host=TAILNET, login=EVE, origin=tailnet_origin(), body={"issue": 5})
    assert (status, text) == (403, "login not allowed")
    assert s.calls[route] == []


def test_a_tailnet_request_is_refused_when_the_seam_cannot_load(serve, tmp_path, monkeypatch):
    s = serve()
    monkeypatch.setattr(s.mod, "tailnet_roots", lambda: [str(tmp_path)])
    assert request(s.port, "GET", "/api/runs", host=TAILNET, login=ANA) == (403, "no tailnet access seam")
    assert request(s.port, "GET", "/api/version", host=loopback(s.port))[0] == 200


def test_loopback_write_with_a_foreign_origin_keeps_today_s_refusal(serve):
    s = serve()
    status, text = request(s.port, "POST", "/api/models", host=loopback(s.port), origin="https://evil.example", body={})
    assert (status, json.loads(text)) == (400, {"err": "forbidden"})
    assert s.calls["/api/models"] == []


def test_tailnet_page_shows_the_signed_in_login(serve):
    s = serve()
    status, page = request(s.port, "GET", "/", host=TAILNET, login=ANA)
    assert status == 200 and header_login(page) == f"signed in as {ANA}"
