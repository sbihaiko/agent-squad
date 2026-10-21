"""The many-repository hub applies the tailnet rule per profile (#722).

Loopback sees every profile. A tailnet request is checked against each profile's own tailnet block through the access seam
(squad/tailnet_access.py, hub.py access()): the root and /api/hub list only the profiles that admit the login, a refusal is a
403 with its reason, and a run's dashboard streams through the forward without the mux's buffer. Fixture profiles over temp
checkouts and stub upstreams on 127.0.0.1; no network.
"""
import http.client
import importlib.util
import json
import os
import pathlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
TAILNET = "hub.tail1234.ts.net"
ANA = "ana@example.com"
BO = "bo@example.com"
EVE = "eve@example.com"
RUN = "run-20261021-032628-72d18f"
EVENT = b'data: {"event": "step", "pos": 1}\n\n'


def _profile(d: pathlib.Path, slug: str, repo: str, checkout: pathlib.Path, allow: list[str]) -> None:
    (checkout / "docs" / "squad").mkdir(parents=True)
    (checkout / "docs" / "squad" / "defaults.yaml").write_text(f"tailnet:\n  host: {TAILNET}\n  allow: {json.dumps(allow)}\n")
    (d / f"{slug}.sh").write_text(
        f': "${{SG_REPO:={repo}}}"; : "${{SG_OWNER:={repo.split("/")[0]}}}"; : "${{SG_PROJECT:=3}}"; : "${{SG_CHECKOUT:={checkout}}}"\n')


class Stream(BaseHTTPRequestHandler):
    """A run dashboard stub: one event now, the rest only after the test has read that event."""

    release = threading.Event()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(EVENT)
        self.wfile.flush()
        self.release.wait(2)

    def log_message(self, *a): pass


@pytest.fixture(scope="module")
def fleet(tmp_path_factory):
    t =tmp_path_factory.mktemp("hubmux_tailnet")
    prof, base = t / "profiles", t / "state"
    prof.mkdir()
    _profile(prof, "alpha", "x/alpha", t / "co-alpha", [ANA])
    _profile(prof, "beta", "y/beta", t / "co-beta", [BO])
    env = {"SG_PROFILES_DIR": str(prof), "SG_STATE_BASE": str(base)}
    before = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    spec = importlib.util.spec_from_file_location("hubmux_tailnet_under_test", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    apps = mux.load_apps()
    stub = ThreadingHTTPServer(("127.0.0.1", 0), Stream)
    threading.Thread(target=stub.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    for slug in apps:
        apps[slug]["mod"].runs = lambda: []
    apps["alpha"]["mod"].run_list = lambda: [{"id": RUN, "port": stub.server_address[1]}]
    srv = mux.server(0, apps)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield {"port": srv.server_address[1], "stream": Stream}
    srv.shutdown()
    stub.shutdown()
    for k, v in before.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _get(port, path, *, host=TAILNET, login=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Host": host}
    if login is not None:
        headers["Tailscale-User-Login"] = login
    c.request("GET", path, headers=headers)
    r = c.getresponse()
    body = r.read().decode()
    c.close()
    return r.status, body


def test_loopback_root_lists_every_profile(fleet):
    code, body = _get(fleet["port"], "/", host=f"127.0.0.1:{fleet['port']}")
    assert code == 200
    assert 'href="/alpha"' in body and 'href="/beta"' in body


def test_tailnet_root_lists_only_the_profiles_that_allow_the_login(fleet):
    code, body = _get(fleet["port"], "/", login=ANA)
    assert code == 200
    assert 'href="/alpha"' in body
    assert 'href="/beta"' not in body


def test_tailnet_login_no_profile_allows_gets_403_with_reason_and_no_list(fleet):
    for path in ("/", "/api/hub"):
        code, body = _get(fleet["port"], path, login=EVE)
        assert code == 403, path
        assert body == "login not allowed", path
        assert "alpha" not in body and "beta" not in body, path


def test_tailnet_unknown_path_shows_only_admitted_profiles(fleet):
    code, body = _get(fleet["port"], "/nowhere", login=EVE)
    assert code == 403 and "alpha" not in body and "beta" not in body
    code, body = _get(fleet["port"], "/nowhere", login=ANA)
    assert code == 404
    assert 'href="/alpha"' in body and 'href="/beta"' not in body


def test_tailnet_profile_route_is_checked_against_that_profiles_list_only(fleet):
    assert _get(fleet["port"], "/alpha/", login=ANA)[0] == 200
    code, body = _get(fleet["port"], "/alpha/", login=BO)
    assert code == 403 and body == "login not allowed"
    assert _get(fleet["port"], "/beta/", login=BO)[0] == 200
    assert _get(fleet["port"], "/beta/", login=ANA)[0] == 403


def test_run_dashboard_events_arrive_before_the_stream_ends(fleet):
    c = http.client.HTTPConnection("127.0.0.1", fleet["port"], timeout=0.8)
    c.request("GET", f"/alpha/dash/{RUN}/run/events", headers={"Host": TAILNET, "Tailscale-User-Login": ANA})
    try:
        r = c.getresponse()
        assert r.status == 200
        first = r.readline()
        assert first == EVENT.split(b"\n")[0] + b"\n"
        assert not fleet["stream"].release.is_set()
    finally:
        fleet["stream"].release.set()
        c.close()
