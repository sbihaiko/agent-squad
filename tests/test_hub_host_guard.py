"""The hub answers a request only when its Host names the hub's own loopback name and port, so a page that rebinds its
name to 127.0.0.1 cannot read a GET route. GET and POST share one rule."""
import http.client
import importlib.util
import pathlib
import threading
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture
def hub_port(monkeypatch, tmp_path):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE", "SG_STATE_BASE", "SG_SHARED", "SG_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("hub_host_guard", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_port
    srv.shutdown()
    srv.server_close()


def get_status(port, host):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest("GET", "/api/version", skip_host=True)
    c.putheader("Host", host)
    c.endheaders()
    status = c.getresponse().status
    c.close()
    return status


def test_get_with_a_foreign_host_is_refused(hub_port):
    assert get_status(hub_port, f"evil.example:{hub_port}") == 403
    assert get_status(hub_port, "127.0.0.1:1") == 403


def test_get_with_the_hub_own_host_is_answered(hub_port):
    assert get_status(hub_port, f"127.0.0.1:{hub_port}") == 200
    assert get_status(hub_port, f"localhost:{hub_port}") == 200
