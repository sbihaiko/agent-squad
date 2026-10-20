"""The squad models panel posts back what `GET /api/models` handed it, read-only fields included (READONLY).

The registry labels and boxes (#677) grew that body past 19 KB, while the POST handler read 8192 bytes at most: the
JSON arrived cut, so every Save said "save failed" and nothing was written. The handler reads the whole body the
panel sends, inside a bound that only a body no panel builds reaches.
"""
import http.client
import importlib.util
import json
import pathlib
import threading
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture
def hub(monkeypatch, tmp_path):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE", "SG_STATE_BASE", "SG_SHARED", "SG_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("hub_models_save", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    got = []
    monkeypatch.setattr(mod, "models_set", lambda body: got.append(body) or {"saved": True})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_port, got
    srv.shutdown()
    srv.server_close()


def test_a_save_body_over_8_kib_reaches_models_set_whole(hub):
    port, got = hub
    body = {"coding_models": ["gpt-6-luna"], "labels": {f"model-{i}": "a registry label " * 4 for i in range(400)}}
    raw = json.dumps(body)
    assert len(raw) > 19000
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", "/api/models", body=raw, headers={"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}",
                                                         "Content-Type": "application/json"})
    r = c.getresponse()
    data = json.loads(r.read() or b"{}")
    c.close()
    assert (r.status, data) == (200, {"saved": True})
    assert got == [body]
