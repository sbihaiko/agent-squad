"""The hub forwards each run dashboard under its own origin, at /dash/<run id>/ (ticket #721).

A listed run id reaches its dashboard on loopback; an unlisted id or any crafted port, host or escape in the path answers 404
and opens no connection. A tailnet request passes the access seam before any forward. The hub page names no 127.0.0.1 frame.
The dashboards are real run servers (squad/dashboard_server.py) in this process; the upstream stub only counts connections.
"""
import http.client
import importlib.util
import json
import pathlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from squad import dashboard_server as ds
from tests.test_dashboard_server import GRAPH, RECORD, serve_quietly

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
RUN = "run-20261021-032628-72d18f"
UNLISTED = "run-20261021-000000-000000"
TAILNET = "hub.tail1234.ts.net"
ANA = "ana@example.com"
EVE = "eve@example.com"
PAGE = b"<html>run dashboard page</html>"
NEW_LINE = json.dumps({"event": "step", "pos": 2, "ts": 2.0}) + "\n"
CONTROL = json.dumps({"action": "pause", "nodes": ["a"]})
CRAFTED = [
    "/dash/" + UNLISTED + "/",
    "/dash/127.0.0.1:{stub}/",
    "/dash/127.0.0.1%3A{stub}/",
    "/dash/" + RUN + "@evil.example/",
    "/dash/%72un-20261021-032628-72d18f/",
    "/dash/" + RUN + "/%2e%2e/",
    "/dash/" + RUN + "/run/../../x",
    "//127.0.0.1:{stub}/",
]


class Upstream(ThreadingHTTPServer):
    """A loopback stub that counts the connections it accepts."""

    def __init__(self):
        super().__init__(("127.0.0.1", 0), BaseHTTPRequestHandler)
        self.accepted = 0

    def get_request(self):
        self.accepted += 1
        return super().get_request()


@pytest.fixture
def world(monkeypatch, tmp_path):
    run = tmp_path / RUN
    run.mkdir()
    (run / "record.jsonl").write_text(RECORD)
    (run / "graph.json").write_text(GRAPH)
    page = tmp_path / "page.html"
    page.write_bytes(PAGE)
    dash = ds.RunServer(run, page=page, idle=60)
    threading.Thread(target=lambda: serve_quietly(dash, n=500), daemon=True).start()
    stub = Upstream()
    threading.Thread(target=stub.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    checkout = tmp_path / "checkout"
    (checkout / "docs" / "squad").mkdir(parents=True)
    (checkout / "docs" / "squad" / "defaults.yaml").write_text(
        f"tailnet:\n  host: {TAILNET}\n  allow: {json.dumps([ANA])}\n")
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(checkout)), ("SG_HUB_DIR", str(tmp_path / "hub"))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE", "SG_STATE_BASE", "SG_SHARED", "SG_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("hub_dash_forward", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rows = [{"id": RUN, "port": str(dash.port), "state": "running", "held": False}]
    mod.run_list = lambda: rows
    mod.loop_caps = lambda: None
    mod.models_get = lambda: {}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    yield SimpleNamespace(hub=srv.server_port, dash=dash, run=run, stub=stub, rows=rows)

    srv.shutdown()
    srv.server_close()
    stub.shutdown()
    stub.server_close()
    dash.server_close()


def send(port, method, path, *, host=None, login=None, origin=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if login is not None:
        headers["Tailscale-User-Login"] = login
    if origin is not None:
        headers["Origin"] = origin
    if body is not None:
        headers["Content-Type"] = "application/json"
    c.request(method, path, body=body, headers=headers)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


def test_a_listed_run_serves_its_page_its_record_and_its_info_under_the_prefix(world):
    base = f"/dash/{RUN}/"
    status, page = send(world.hub, "GET", base)
    assert status == 200 and page.startswith(PAGE)
    assert send(world.hub, "GET", base + "run/record.jsonl") == (200, RECORD.encode())
    status, info = send(world.hub, "GET", base + "run/info.json")
    assert status == 200 and json.loads(info)["name"] == RUN


@pytest.mark.parametrize("template", CRAFTED)
def test_an_unlisted_run_or_a_crafted_port_or_host_is_404_and_opens_no_connection(world, template):
    path = template.format(stub=world.stub.server_address[1])
    status, _ = send(world.hub, "GET", path)
    assert status == 404
    assert world.stub.accepted == 0


def test_run_events_reach_the_client_before_the_stream_ends(world):
    c = http.client.HTTPConnection("127.0.0.1", world.hub, timeout=5)
    c.request("GET", f"/dash/{RUN}/run/events", headers={"Host": f"127.0.0.1:{world.hub}"})
    r = c.getresponse()
    assert r.status == 200
    first = r.read1(65536)
    assert b"data: " in first and b"event: end" not in first
    with (world.run / "record.jsonl").open("a") as f:
        f.write(NEW_LINE)
    second = r.read1(65536)
    assert b"data: " in second and b"event: end" not in second
    c.close()


@pytest.mark.parametrize("login, reason", [(None, b"no tailnet identity"), (EVE, b"login not allowed")])
def test_a_tailnet_request_without_an_allowed_login_is_refused_before_any_forward(world, login, reason):
    world.rows[0]["port"] = str(world.stub.server_address[1])
    status, text = send(world.hub, "GET", f"/dash/{RUN}/", host=TAILNET, login=login)
    assert (status, text) == (403, reason)
    assert world.stub.accepted == 0


def test_a_forwarded_control_from_an_allowed_tailnet_login_appends_one_line(world):
    status, _ = send(world.hub, "POST", f"/dash/{RUN}/run/control", host=TAILNET, login=ANA,
                     origin=f"https://{TAILNET}", body=CONTROL)
    assert status == 200
    (line,) = (world.run / "control.jsonl").read_text().splitlines()
    req = json.loads(line)
    assert (req["action"], req["nodes"], req["source"]) == ("pause", ["a"], "dashboard")


def test_a_forwarded_loopback_control_needs_the_hubs_own_origin_on_its_own_port(world):
    """#824: the forwarded control is a loopback write too; another local port's Origin is refused before any forward."""
    path = f"/dash/{RUN}/run/control"
    assert send(world.hub, "POST", path, origin=f"http://127.0.0.1:{world.hub + 1}", body=CONTROL) == (403, b"forbidden")
    assert not (world.run / "control.jsonl").exists()
    status, _ = send(world.hub, "POST", path, origin=f"http://127.0.0.1:{world.hub}", body=CONTROL)
    assert status == 200


def test_the_hub_page_names_no_loopback_frame_or_pip_url(world):
    status, page = send(world.hub, "GET", "/")
    text = page.decode()
    assert status == 200
    assert "http://127.0.0.1:" not in text
    assert "/dash/" in text
