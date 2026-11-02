"""The many-repository hub serves a profile's terminal routes through the mux (ADR-0152 rule 9).

A tailnet read, attach and input reach the supervisor for the profile the path names, with the grant the profile lists.
The supervisor is a fake; the mux and the hub handler are the real code. No network.
"""
import http.client
import importlib.util
import json
import pathlib
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
TAILNET = "hub.tail1234.ts.net"
ANA = "ana@example.com"
CID = "client-0001-abcd"


class FakeSupervisor:
    class Refused(Exception):
        pass

    def __init__(self):
        self.calls = []

    def call(self, state, op, **fields):
        self.calls.append((op, fields))
        return {"ok": True, "data": "", "offset": 0, "gap": False}


@pytest.fixture
def mux(monkeypatch, tmp_path):
    profiles, checkout = tmp_path / "profiles", tmp_path / "co-alpha"
    (checkout / "docs" / "squad").mkdir(parents=True)
    grants = "".join(f"  {g}: true\n" for g in ("terminals_read", "terminals_attach", "terminals_send"))
    (checkout / "docs" / "squad" / "defaults.yaml").write_text(
        f"tailnet:\n  host: {TAILNET}\n  allow: {json.dumps([ANA])}\n{grants}")
    profiles.mkdir()
    (profiles / "alpha.sh").write_text(
        f': "${{SG_REPO:=x/alpha}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:=3}}"; : "${{SG_CHECKOUT:={checkout}}}"\n')
    monkeypatch.setenv("SG_PROFILES_DIR", str(profiles))
    monkeypatch.setenv("SG_STATE_BASE", str(tmp_path / "state"))
    spec = importlib.util.spec_from_file_location("hubmux_terminals_under_test", SCRIPTS / "hubmux.py")
    hubmux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hubmux)
    apps = hubmux.load_apps()
    fake = FakeSupervisor()
    apps["alpha"]["mod"].terminal_supervisor = lambda: fake
    apps["alpha"]["mod"].run_list = lambda: []
    srv = hubmux.server(0, apps)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield srv.server_address[1], fake
    srv.shutdown()
    srv.server_close()


def tailnet_request(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": TAILNET, "Origin": f"https://{TAILNET}", "Tailscale-User-Login": ANA}
    if body is not None:
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
    r = conn.getresponse()
    text = r.read().decode()
    conn.close()
    return r.status, text


def test_the_mux_passes_a_tailnet_terminal_read_to_the_supervisor(mux):
    port, fake = mux
    status, _ = tailnet_request(port, "GET", f"/alpha/api/terminals/read?sid=s1&client_id={CID}&offset=0")
    assert status == 200
    assert fake.calls == [("read", {"session": "s1", "offset": 0, "login": ANA, "client_id": CID, "transport": "tailnet"})]


def test_the_mux_passes_a_tailnet_input_to_the_supervisor(mux):
    port, fake = mux
    status, _ = tailnet_request(port, "POST", "/alpha/api/terminals/write", {"sid": "s1", "text": "hello", "client_id": CID})
    assert status == 200
    assert fake.calls == [("write", {"session": "s1", "text": "hello", "login": ANA, "client_id": CID, "transport": "tailnet"})]
