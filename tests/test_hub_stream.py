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
    home = t / "home"
    home.mkdir()   # the session subject reads HOME: a profile that read the owner's 3000 folders spent 12 s on it, and the suite paid it on every test
    env = {"SG_PROFILES_DIR": str(prof), "SG_STATE_BASE": str(base), "HOME": str(home)}
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
    yield {"port": srv.server_address[1], "rows": rows, "mod": apps["alpha"]["mod"]}
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
    s.settimeout(0.3)   # the wait is how long the test listens for silence, not what it expects: a closed stream answers b"" at once
    while True:   # the rest of the first tick, then quiet: a closed stream would answer b"" at once
        try:
            assert s.recv(65536) != b""
        except TimeoutError:
            break
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
    time.sleep(_hubstream().TICK_SECONDS + 0.1)   # a subject is read once a tick: let the reset land before the first version is held
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


@pytest.mark.heavy
def test_each_run_list_change_reaches_an_open_stream_at_the_tick(on, monkeypatch):
    """#1891: the stg tip rested the run list 5 s (#1879), so a change reached an open page only after the page's own
    wait was over. A subject answers at the tick. The second change comes right after the first landed, so its wait is
    the tick itself and never a rest: the wait is measured in ticks, as the promise is."""
    mod = on["mod"]
    tick = 0.5
    monkeypatch.setattr(mod.hubstream, "TICK_SECONDS", tick)
    monkeypatch.setattr(mod.hubstream, "FIRST_EVENT_SECONDS", 0.0)   # the startup window is over: only the tick counts
    end = time.monotonic() + 10 * tick   # a read already in flight at the former tick lands before this window ends
    while mod.VERSIONS.fresh({}).get("runs", (None, None))[1] != on["rows"] and time.monotonic() < end:
        time.sleep(tick / 10)   # the fixture's reset is held only once the subject has published it
    s = _open(on["port"], "/alpha/api/stream")
    first = _version(_read_until(s, b"event: runs"))
    s.close()
    s = _open(on["port"], f"/alpha/api/stream?held=runs:{first}")
    on["rows"].append({"id": "run-b"})
    second = _version(_read_until(s, b"event: runs", budget=6 * tick))
    on["rows"].append({"id": "run-c"})
    third = _version(_read_until(s, b"event: runs", budget=6 * tick))
    s.close()
    assert second > first and third > second, "a changed run list waited past the tick before it reached the page"


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


def test_stream_on_names_the_stream_in_the_phone_page(on):
    with urllib.request.urlopen(f"http://127.0.0.1:{on['port']}/alpha/mobile", timeout=5) as r:
        assert b'window.SQUAD_HUB_STREAM="/alpha/api/stream"' in r.read()


def test_a_slow_subject_read_never_holds_the_heartbeat(on, monkeypatch):
    """The owner's page froze: one subject read took 12 s on a machine holding 3000 folders.

    The stream's own thread did that read, so neither an event nor a heartbeat reached the page for 12 s. The subjects
    are read on their own thread, and the stream only sends what changed.
    """
    hubstream = _hubstream()
    monkeypatch.setattr(hubstream, "HEARTBEAT_SECONDS", 0.3)
    monkeypatch.setattr(hubstream, "TICK_SECONDS", 0.2)   # the heartbeat is checked once a tick: a fast tick keeps the test fast
    monkeypatch.setitem(on["mod"].VERSIONS._subjects, "slow", lambda: time.sleep(5) or [])
    s = _open(on["port"], "/alpha/api/stream")
    started = time.monotonic()
    assert b"event: ping" in _read_until(s, b"event: ping", budget=2.5)
    assert time.monotonic() - started < 2.5
    s.close()


@pytest.mark.heavy
def test_quiet_stream_writes_a_heartbeat(on, monkeypatch):
    monkeypatch.setattr(_hubstream(), "HEARTBEAT_SECONDS", 0.3)
    s = _open(on["port"], "/alpha/api/stream")
    assert b"event: ping" in _read_until(s, b"event: ping", budget=4.0)
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


class _Walked(dict):
    """A body that counts how often its items are walked: json.dumps(sort_keys=True) sorts them, so a walk here is one
    full serialization of the body."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.walks = 0
    def items(self):
        self.walks += 1
        return super().items()


def test_a_subject_that_names_its_digest_is_never_serialized():
    """A subject may answer (content, digest) instead of content: the stream compares the digest it was handed and never
    serializes the body to compare it. The board is a thousand cards, and dumping it to a hash on every tick was most of
    the hub's CPU (#1790). The body sent to the page is still the content, never the digest."""
    hubstream = _hubstream()
    body, digests = _Walked(rows=[1, 2]), ["a"]
    v = hubstream.Versions({"board": lambda: (body, digests[0])})
    v.refresh()
    one = v.newer({})["board"]
    assert v.fresh({})["board"][1]["rows"] == [1, 2], "the page is sent the content, never the digest"
    for _ in range(5):
        v.refresh()
    assert body.walks == 0, "a subject that names its digest is never walked to compare it"
    assert v.newer({"board": one}) == {}, "an unchanged digest publishes nothing"
    digests[0] = "b"
    v.refresh()
    assert v.newer({"board": one})["board"] > one, "a new digest is a new version"


def test_one_read_per_subject_per_tick_whatever_the_pages():
    hubstream = _hubstream()
    reads = []
    v = hubstream.Versions({"runs": lambda: reads.append(1) or []})
    for _ in range(3):   # three open pages, one tick
        v.refresh(tick=7)
    assert len(reads) == 1
    v.refresh(tick=8)
    assert len(reads) == 2


def test_a_subject_with_no_answer_yet_publishes_nothing():
    hubstream = _hubstream()
    v = hubstream.Versions({"quiet": lambda: None})
    v.refresh_one("quiet")
    assert v.newer({}) == {}, "None is no answer yet, never a version the page paints"


def test_the_stream_never_reads_a_subject_in_its_own_thread(on, monkeypatch):
    """A stream sends only what its readers already hold: a subject read inside the stream's thread holds the page's
    first event for the whole read (ADR-0164, #1217)."""
    read = []
    monkeypatch.setattr(_hubstream().Versions, "refresh", lambda *a, **k: read.append(1))
    s = _open(on["port"], "/alpha/api/stream")
    buf = _read_until(s, b"event: ", budget=2.0)
    s.close()
    assert b"event: " in buf and not read, "the stream read the subjects itself instead of its readers"


def test_a_slow_subject_read_never_delays_the_streams_first_event(on, monkeypatch):
    """A profile whose board read takes seconds still paints at once: the stream sends from the versions its readers
    hold, and no request pays a subject's read (ADR-0164, #1217)."""
    mod = on["mod"]

    def slow():
        time.sleep(3.0)
        return {"groups": []}

    monkeypatch.setitem(mod.VERSIONS._subjects, "slow", slow)
    opened = time.monotonic()
    s = _open(on["port"], "/alpha/api/stream")
    buf = _read_until(s, b"event: ", budget=1.0)
    waited = time.monotonic() - opened
    s.close()
    assert b"event: " in buf, f"no event within one second: the stream waited {waited:.2f} s"


def test_the_stream_offers_run_board_and_chart_subjects_and_no_session(on):
    """The live stream keeps its run, board and chart subjects and offers no session subject."""
    assert set(on["mod"].VERSIONS.names()) == {"runs", "board", "cfd", "lead"}


def test_the_stream_module_carries_no_conversation_machinery():
    hubstream = _hubstream()
    for name in ("Transcripts", "Window", "conv_from", "WINDOW_BYTES", "WINDOWS_KEPT"):
        assert not hasattr(hubstream, name), f"hubstream still carries {name}"
