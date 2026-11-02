"""The hub page's push stream: one versioned stream per page, passed unbuffered by the multiplexer.

Fixture profile over a temp checkout, as test_hub_mux.py; the stream is read over a real socket through the mux.
"""
import importlib
import importlib.util
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"


def _hubstream():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return importlib.import_module("hubstream")


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    t = tmp_path_factory.mktemp("stream")
    prof, base = t / "profiles", t / "state"
    prof.mkdir()
    (t / "co-alpha").mkdir()
    (base / "alpha").mkdir(parents=True)
    (prof / "alpha.sh").write_text(
        f': "${{SG_REPO:=x/alpha}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:=3}}"; : "${{SG_CHECKOUT:={t}/co-alpha}}"\n')
    env = {"SG_PROFILES_DIR": str(prof), "SG_STATE_BASE": str(base)}
    before = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    spec = importlib.util.spec_from_file_location("hubmux_stream_under_test", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    apps = mux.load_apps()
    rows = [{"id": "run-a"}]
    apps["alpha"]["mod"].run_list = lambda: list(rows)
    srv = mux.server(0, apps)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"port": srv.server_address[1], "rows": rows}
    srv.shutdown()
    srv.server_close()
    for k, v in before.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture
def on(hub, monkeypatch):
    monkeypatch.setenv("SQUAD_HUB_STREAM", "1")
    hub["rows"][:] = [{"id": "run-a"}]
    return hub


def _open(port, path, origin=None):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    head = [f"GET {path} HTTP/1.1", f"Host: 127.0.0.1:{port}"]
    if origin is not None:
        head.append(f"Origin: {origin}")
    head += ["", ""]
    s.sendall("\r\n".join(head).encode())
    return s


def _read_until(sock, needle, budget=4.0):
    buf, end = b"", time.monotonic() + budget
    while needle not in buf:
        left = end - time.monotonic()
        if left <= 0:
            break
        sock.settimeout(left)
        try:
            chunk = sock.recv(65536)
        except TimeoutError:
            break
        if not chunk:
            break
        buf += chunk
    return buf


def _version(buf):
    i = buf.index(b"event: runs")
    j = buf.index(b"data: ", i) + len(b"data: ")
    return json.loads(buf[j:buf.index(b"\n", j)])["v"]


def test_stream_answers_and_stays_open(on):
    s = _open(on["port"], "/alpha/api/stream", origin=f"http://127.0.0.1:{on['port']}")
    got = _read_until(s, b"event: runs")
    assert got.startswith(b"HTTP/1.1 200") and b"text/event-stream" in got
    assert b"event: runs" in got
    s.settimeout(1.5)
    with pytest.raises(TimeoutError):
        s.recv(1)   # a closed stream would answer b"" at once; an open one says nothing
    s.close()


def test_multiplexer_passes_the_stream_unbuffered(on):
    s = _open(on["port"], "/alpha/api/stream")
    assert b"event: runs" in _read_until(s, b"event: runs")
    on["rows"].append({"id": "run-b"})
    assert b"event: runs" in _read_until(s, b"event: runs", budget=4.0)   # the change reaches the page now, not at the end
    s.close()


@pytest.mark.parametrize("origin", ["http://evil.example", "null", "https://127.0.0.1:1"])
def test_foreign_origin_gets_no_stream(on, origin):
    req = urllib.request.Request(f"http://127.0.0.1:{on['port']}/alpha/api/stream", headers={"Origin": origin})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 403
    assert b"text/event-stream" not in (e.value.headers.get("Content-Type") or "").encode()


@pytest.mark.heavy
def test_reconnect_repeating_a_held_version_sees_no_duplicate(on):
    s = _open(on["port"], "/alpha/api/stream")
    first = _version(_read_until(s, b"event: runs"))
    s.close()
    s = _open(on["port"], f"/alpha/api/stream?held=runs:{first}")
    assert b"event: runs" not in _read_until(s, b"event: runs", budget=2.5)
    s.close()
    on["rows"].append({"id": "run-b"})
    s = _open(on["port"], f"/alpha/api/stream?held=runs:{first}")
    second = _version(_read_until(s, b"event: runs", budget=4.0))
    s.close()
    assert second > first


def test_stream_off_serves_the_page_as_today(hub, monkeypatch):
    monkeypatch.delenv("SQUAD_HUB_STREAM", raising=False)
    port = hub["port"]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/alpha/api/stream", timeout=5) as r:
        streamed = (r.headers.get("Content-Type"), r.read())
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/alpha/", timeout=5) as r:
        page = (r.headers.get("Content-Type"), r.read())
    assert streamed == page
    assert b"SQUAD_HUB_STREAM" not in page[1]


def test_stream_on_names_the_stream_in_the_page(on):
    with urllib.request.urlopen(f"http://127.0.0.1:{on['port']}/alpha/", timeout=5) as r:
        assert b'window.SQUAD_HUB_STREAM="/alpha/api/stream"' in r.read()


@pytest.mark.heavy
def test_quiet_stream_writes_a_heartbeat(on, monkeypatch):
    monkeypatch.setattr(_hubstream(), "HEARTBEAT_SECONDS", 0.3)
    s = _open(on["port"], "/alpha/api/stream")
    assert b": ping" in _read_until(s, b": ping", budget=4.0)
    s.close()


def test_versions_rise_only_when_content_changes():
    hubstream = _hubstream()
    state = {"rows": [1]}
    v = hubstream.Versions({"runs": lambda: state["rows"]})
    v.refresh()
    one = v.newer({})["runs"]
    v.refresh()
    assert v.newer({"runs": one}) == {}
    state["rows"].append(2)
    v.refresh()
    two = v.newer({"runs": one})["runs"]
    assert two > one
    restarted = hubstream.Versions({"runs": lambda: state["rows"]})
    restarted.refresh()
    assert restarted.newer({})["runs"] > two
    assert hubstream.held_from("held=runs:7,board:x") == {"runs": 7}
