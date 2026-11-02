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


def test_one_read_per_subject_per_tick_whatever_the_pages():
    hubstream = _hubstream()
    reads = []
    v = hubstream.Versions({"runs": lambda: reads.append(1) or []})
    for _ in range(3):   # three open pages, one tick
        v.refresh(tick=7)
    assert len(reads) == 1
    v.refresh(tick=8)
    assert len(reads) == 2


def _turn(uid, text):
    return {"id": uid, "role": "owner", "text": text, "at": "2026-11-02T00:00:00Z", "m": None}


def _one_page(turns):
    return lambda sid: {"turns": list(turns), "next": None, "activity": {}}


def test_a_window_answers_the_bytes_after_an_offset():
    w = _hubstream().Window()
    w.append(b'{"id":1}\n')
    w.append(b'{"id":2}\n')
    assert w.since(0) == (None, b'{"id":1}\n{"id":2}\n')
    assert w.since(9) == (None, b'{"id":2}\n')
    assert w.since(w.end) == (None, b"")


def test_a_window_names_its_start_when_the_offset_is_no_longer_retained():
    w = _hubstream().Window(keep=20)
    for line in (b"aaaa\n", b"bbbb\n", b"cccc\n", b"dddd\n", b"eeee\n"):
        w.append(line)
    gap, data = w.since(0)
    assert gap == w.start > 0
    assert data == w.since(w.start)[1] and data.endswith(b"\n")
    assert w.since(w.end) == (None, b"")


def test_a_reader_that_stops_and_resumes_loses_no_byte_and_sees_no_duplicate():
    w = _hubstream().Window()
    w.append(b"line 0\n")
    at, got = 0, b""
    gap, data = w.since(at)
    got, at = got + data, w.end
    for n in (1, 2, 3):   # the reader is away: the writer goes on
        w.append(f"line {n}\n".encode())
    gap, data = w.since(at)
    got, at = got + data, w.end
    assert got == b"line 0\nline 1\nline 2\nline 3\n"
    assert w.since(at) == (None, b"")


def test_a_conversation_is_read_once_per_tick_whatever_the_pages():
    hubstream = _hubstream()
    reads = []
    tr = hubstream.Transcripts(lambda sid: reads.append(sid) or _one_page([_turn("t1", "one")])(sid))
    for _ in range(3):   # three open pages, one tick
        tr.refresh("sid-a", tick=7)
    assert reads == ["sid-a"]
    tr.refresh("sid-a", tick=8)
    assert reads == ["sid-a", "sid-a"]


def test_a_conversation_window_holds_each_turn_once_in_order():
    hubstream = _hubstream()
    turns = [_turn("t1", "one")]
    tr = hubstream.Transcripts(_one_page(turns))
    tr.refresh("sid-a", tick=1)
    turns.append(_turn("t2", "two"))
    page = tr.refresh("sid-a", tick=2)
    assert page["turns"] == turns and page["end"] == tr.window("sid-a").end
    lines = b'{"at":"2026-11-02T00:00:00Z","id":"t1","m":null,"role":"owner","text":"one"}\n'
    lines += b'{"at":"2026-11-02T00:00:00Z","id":"t2","m":null,"role":"owner","text":"two"}\n'
    assert tr.window("sid-a").since(0) == (None, lines)


def test_a_reader_from_another_epoch_gets_the_window_start_as_a_gap():
    hubstream = _hubstream()
    tr = hubstream.Transcripts(_one_page([_turn("t1", "one")]), epoch=5)
    tr.refresh("sid-a", tick=1)
    events, epoch, offset = tr.pull("sid-a", 4, 0)
    assert [name for name, _ in events] == ["gap", "conversation"]
    assert events[0][1] == {"sid": "sid-a", "epoch": 5, "start": 0, "subject": "conversation"}
    assert events[1][1]["lines"] == tr.window("sid-a").since(0)[1].decode()
    assert (epoch, offset) == (5, tr.window("sid-a").end)


def test_the_held_conversations_come_from_the_query():
    hubstream = _hubstream()
    assert hubstream.conv_from("held=runs:3&conv=11111111-2222:9:40,bad!:x:1") == {"11111111-2222": (9, 40)}


def _event(buf, name):
    i = buf.index(f"event: {name}\n".encode())
    j = buf.index(b"data: ", i) + len(b"data: ")
    return json.loads(buf[j:buf.index(b"\n", j)])


SID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def conversations(on, monkeypatch):
    mod, turns = on["mod"], []
    fake = _one_page(turns)
    monkeypatch.setattr(mod, "TRANSCRIPTS", _hubstream().Transcripts(fake))
    return {"turns": turns, "mod": mod}


def _page_of(port, sid):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/alpha/api/sessions/conversation?sid={sid}", timeout=5) as r:
        return json.loads(r.read())


def test_a_board_and_a_session_list_are_pushed_with_their_payload(on, monkeypatch):
    mod = on["mod"]
    monkeypatch.setattr(mod, "board_served", lambda: (json.dumps({"groups": [{"name": "Backlog", "items": []}]}).encode(), None))
    monkeypatch.setattr(mod, "sessions_payload", lambda: {"groups": [{"project": "p", "sessions": [{"sid": SID}]}]})
    mod.VERSIONS.refresh()   # the stream reads a subject once a tick; the patched readers take this read
    s = _open(on["port"], "/alpha/api/stream")
    buf = _read_until(s, b"event: sessions")
    s.close()
    assert _event(buf, "board")["data"] == {"groups": [{"name": "Backlog", "items": []}]}
    assert _event(buf, "sessions")["data"] == {"groups": [{"project": "p", "sessions": [{"sid": SID}]}]}


@pytest.mark.heavy
def test_a_new_turn_is_pushed_with_its_byte_offset(conversations, on):
    turns = conversations["turns"]
    turns.append(_turn("t1", "one"))
    page = _page_of(on["port"], SID)
    s = _open(on["port"], f"/alpha/api/stream?conv={SID}:{page['epoch']}:{page['end']}")
    turns.append(_turn("t2", "two"))
    buf = _read_until(s, b'two\\"')
    s.close()
    pushed = _event(buf, "conversation")
    assert pushed["at"] == page["end"] and pushed["sid"] == SID
    assert [json.loads(line)["id"] for line in pushed["lines"].splitlines()] == ["t2"]


@pytest.mark.heavy
def test_a_reconnect_inside_the_window_replays_exactly_the_bytes_after_the_held_offset(conversations, on):
    turns = conversations["turns"]
    turns.append(_turn("t1", "one"))
    page = _page_of(on["port"], SID)
    s = _open(on["port"], f"/alpha/api/stream?conv={SID}:{page['epoch']}:{page['end']}")
    turns.append(_turn("t2", "two"))
    first = _event(_read_until(s, b'two\\"'), "conversation")
    s.close()
    turns.append(_turn("t3", "three"))
    s = _open(on["port"], f"/alpha/api/stream?conv={SID}:{page['epoch']}:{first['end']}")
    second = _event(_read_until(s, b'three\\"'), "conversation")
    s.close()
    assert first["lines"] + second["lines"] == (
        '{"at":"2026-11-02T00:00:00Z","id":"t2","m":null,"role":"owner","text":"two"}\n'
        '{"at":"2026-11-02T00:00:00Z","id":"t3","m":null,"role":"owner","text":"three"}\n')


@pytest.mark.heavy
def test_a_reconnect_from_a_dropped_offset_gets_the_window_start_and_a_named_gap(conversations, on, monkeypatch):
    hubstream = _hubstream()
    turns = conversations["turns"]
    monkeypatch.setattr(conversations["mod"], "TRANSCRIPTS", hubstream.Transcripts(_one_page(turns), keep=200))
    for n in range(6):
        turns.append(_turn(f"t{n}", f"turn {n}"))
    s = _open(on["port"], f"/alpha/api/stream?conv={SID}:{conversations['mod'].TRANSCRIPTS.epoch}:0")
    buf = _read_until(s, b'turn 5\\"')
    s.close()
    gap = _event(buf, "gap")
    pushed = _event(buf, "conversation")
    assert gap == {"subject": "conversation", "sid": SID, "epoch": conversations["mod"].TRANSCRIPTS.epoch, "start": gap["start"]}
    assert gap["start"] > 0 and pushed["at"] == gap["start"]
    assert "turn 5" in pushed["lines"] and '"turn 0"' not in pushed["lines"]
