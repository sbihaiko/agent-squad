"""The read-only loopback server of `squad dashboard`: what a client sees, never how the server is built."""
import http.client
import json
import os
import threading
import time
from pathlib import Path

import pytest

from squad import dashboard_server as ds

RECORD = json.dumps({"event": "run_start", "run_id": "run-x", "pos": 1, "ts": 1.0}) + "\n"
GRAPH = json.dumps({"name": "g", "nodes": []})


def serve_quietly(srv, n=200):
    """Answer requests until the test closes the socket; a closed socket ends the thread with no noise."""
    for _ in range(n):
        try:
            srv.handle_request()
        except (ValueError, OSError):
            return


@pytest.fixture
def run(tmp_path):
    d = tmp_path / "run-a"
    d.mkdir()
    (d / "record.jsonl").write_text(RECORD)
    (d / "graph.json").write_text(GRAPH)
    (d / "secret.txt").write_text("secret")
    return d


@pytest.fixture
def served(run, tmp_path):
    page = tmp_path / "page.html"
    page.write_text("<html>page</html>")
    srv = ds.RunServer(run, page=page, idle=60)
    t = threading.Thread(target=lambda: serve_quietly(srv), daemon=True)
    t.start()
    yield srv
    srv.server_close()


def get(srv, path: str, method: str = "GET", host: str | None = "default", body: bytes | None = None):
    c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    c.putrequest(method, path, skip_host=True)
    if host is not None:
        c.putheader("Host", f"127.0.0.1:{srv.port}" if host == "default" else host)
    c.putheader("Content-Length", str(len(body or b"")))
    c.endheaders(body)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data, r


def test_it_serves_the_page_the_record_and_the_graph_of_one_run(served):
    assert get(served, "/")[:2] == (200, b"<html>page</html>")
    assert get(served, "/run/record.jsonl")[:2] == (200, RECORD.encode())
    assert get(served, "/run/graph.json")[:2] == (200, GRAPH.encode())
    st, data, _ = get(served, "/run/info.json")
    assert st == 200 and json.loads(data)["name"] == "run-a"


def test_the_info_counts_the_issues_a_chained_run_started_with(tmp_path):       # #157
    assert ds.held_count(tmp_path) == 0
    (tmp_path / "holds.json").write_text(json.dumps({"held": [1, 2, 4]}))
    assert ds.held_count(tmp_path) == 3
    (tmp_path / "holds.json").write_text("not json")
    assert ds.held_count(tmp_path) == 0


def test_the_info_names_the_issue_set_the_run_folder_holds(tmp_path):
    """The record carries no issue-set size, so the page reads it from the run folder: target/issues."""
    d = tmp_path / "run-i"
    (d / "target" / "issues").mkdir(parents=True)
    for n in range(3):
        (d / "target" / "issues" / f"{n + 1:02d}-slug.md").write_text("# issue\n")
    (d / "record.jsonl").write_text(RECORD)
    assert ds.issue_count(d) == 3
    srv = ds.RunServer(d, idle=60)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    try:
        st, data, _ = get(srv, "/run/info.json")
        assert st == 200 and json.loads(data)["issues"] == 3
    finally:
        srv.server_close()


def test_the_issue_count_reads_a_nested_run_folder_or_none(tmp_path):
    w = tmp_path / "wrapped"
    (w / "run" / "target" / "issues").mkdir(parents=True)
    (w / "run" / "target" / "issues" / "01-x.md").write_text("# x\n")
    assert ds.issue_count(w) == 1
    assert ds.issue_count(tmp_path / "empty") == 0


def test_a_wrapped_run_serves_its_nested_record(tmp_path):
    d = tmp_path / "w"
    (d / "run").mkdir(parents=True)
    (d / "run" / "record.jsonl").write_text(RECORD)
    srv = ds.RunServer(d, idle=60)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    try:
        assert get(srv, "/run/record.jsonl")[:2] == (200, RECORD.encode())
    finally:
        srv.server_close()


def test_a_record_that_grows_is_read_fresh_on_every_request(served, run):
    first = get(served, "/run/record.jsonl")[1]
    with (run / "record.jsonl").open("a") as f:
        f.write(json.dumps({"event": "outcome", "pos": 2}) + "\n")
    second = get(served, "/run/record.jsonl")
    assert len(second[1]) > len(first) and second[2].getheader("Cache-Control") == "no-store"


@pytest.mark.parametrize("path", ["/run/secret.txt", "/secret.txt", "/run/../secret.txt", "/../secret.txt", "/%2e%2e/secret.txt", "/run/%2e%2e/secret.txt",
                                  "/run/record.jsonl/../../etc/passwd", "//etc/passwd", "/run/", "/run", "/etc/passwd"])
def test_a_path_off_the_allowlist_or_a_traversal_is_not_found(served, path):
    assert get(served, path)[0] == 404


def test_a_symlink_that_leaves_the_run_folder_is_not_served(tmp_path, run):
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (run / "record.jsonl").unlink()
    (run / "graph.json").unlink()
    os.symlink(outside, run / "record.jsonl")
    os.symlink(outside, run / "graph.json")
    srv = ds.RunServer(run, idle=60)
    threading.Thread(target=lambda: serve_quietly(srv, 4), daemon=True).start()
    try:
        assert get(srv, "/run/record.jsonl")[0] == 404
        assert get(srv, "/run/graph.json")[0] == 404
    finally:
        srv.server_close()


def test_a_symlink_that_stays_inside_the_run_folder_is_served(run):
    (run / "real.jsonl").write_text(RECORD)
    (run / "record.jsonl").unlink()
    os.symlink(run / "real.jsonl", run / "record.jsonl")
    srv = ds.RunServer(run, idle=60)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    try:
        assert get(srv, "/run/record.jsonl")[:2] == (200, RECORD.encode())
    finally:
        srv.server_close()


@pytest.mark.parametrize("host", ["evil.example", "evil.example:1", "127.0.0.1", "127.0.0.1:1", "localhost", "0.0.0.0:80", ""])
def test_a_wrong_host_header_is_refused_so_dns_rebinding_reads_nothing(served, host):
    assert get(served, "/run/record.jsonl", host=host)[0] == 403


def test_a_request_with_no_host_header_is_refused(served):
    assert get(served, "/run/record.jsonl", host=None)[0] in (400, 403)


def test_localhost_with_the_right_port_is_allowed(served):
    assert get(served, "/run/record.jsonl", host=f"localhost:{served.port}")[0] == 200


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
def test_only_get_is_answered_and_nothing_is_written(served, run, method):
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    st, data, _ = get(served, "/run/record.jsonl", method=method, body=b"x" if method in ("POST", "PUT", "PATCH") else None)
    assert st == 405 and data in (b"", b"read only\n")
    assert {p.name: p.read_bytes() for p in run.iterdir()} == before


def test_it_binds_the_loopback_address_only(served):
    assert served.server_address[0] == "127.0.0.1"


class FakeClock:
    """The idle clock of a server under test: time moves only when the test says so."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_it_exits_when_no_request_came_for_the_idle_time(run):
    clock = FakeClock()
    srv = ds.RunServer(run, idle=0.4, clock=clock)
    t = threading.Thread(target=srv.serve_until_idle, daemon=True)
    t.start()
    t.join(timeout=0.3)
    assert t.is_alive()         # the idle time has not passed on the fake clock, whatever the wall clock says
    clock.t += 0.5
    t.join(timeout=5)
    assert not t.is_alive()


def test_a_request_keeps_the_server_alive_past_the_first_idle_window(run):
    clock = FakeClock()
    srv = ds.RunServer(run, idle=0.4, clock=clock)
    t = threading.Thread(target=srv.serve_until_idle, daemon=True)
    t.start()
    for _ in range(3):
        clock.t += 0.3
        assert get(srv, "/run/info.json")[0] == 200
        t.join(timeout=0.25)    # more than one wake-up of the server
        assert t.is_alive()     # 0.9 passed in all, but never 0.4 since a request
    clock.t += 0.5
    t.join(timeout=5)
    assert not t.is_alive()


@pytest.mark.heavy
def test_a_detached_server_is_reused_for_the_same_run_and_exits_when_idle(run, tmp_path, monkeypatch):
    monkeypatch.setenv("SQUAD_DASHBOARD_STATE_DIR", str(tmp_path / "state"))
    port = ds.start(run, idle=1.5)
    try:
        assert ds.start(run, idle=1.5) == port
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/run/record.jsonl")
        assert c.getresponse().read() == RECORD.encode()
        c.close()
        deadline = time.monotonic() + 20
        while ds.running(run) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ds.running(run) is None
        assert not list((tmp_path / "state").glob("*.json"))
    finally:
        f = ds._state_file(run)
        if f.exists():
            pid = json.loads(f.read_text()).get("pid")
            f.unlink()
            if pid:
                try:
                    os.kill(pid, 15)
                except OSError:
                    pass


def test_a_stale_state_file_is_dropped_and_a_new_server_starts(run, tmp_path, monkeypatch):
    monkeypatch.setenv("SQUAD_DASHBOARD_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    ds._state_file(run).write_text(json.dumps({"port": 9, "pid": 1, "folder": str(run)}))
    assert ds.running(run) is None
    assert not ds._state_file(run).exists()


def test_the_server_module_has_no_route_that_writes_or_starts_a_run():
    src = Path(ds.__file__).read_text()
    for banned in ("engine", "Engine", "write_text(self", "open(\"w", "adapter"):
        assert banned not in src.replace("tmp.write_text", "")


# --- the stream route: a client sees the events the record gains ---

def append(run, *events):
    """Append events to the record the way the run does: a whole-file rewrite by atomic replace."""
    p = run / "record.jsonl"
    text = (p.read_text() if p.exists() else "") + "".join(json.dumps(e) + "\n" for e in events)
    tmp = p.parent / (p.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(p)


def stream(srv, path="/run/events", last=None, host="default", timeout=10):
    """Open a stream and return (connection, response). The caller reads the body and closes."""
    c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=timeout)
    c.putrequest("GET", path, skip_host=True)
    c.putheader("Host", f"127.0.0.1:{srv.port}" if host == "default" else host)
    if last is not None:
        c.putheader("Last-Event-ID", str(last))
    c.endheaders()
    return c, c.getresponse()


def blocks(r, n, limit=8000):
    """Read the body until n server-sent event blocks arrived, or the stream ended."""
    buf = b""
    while buf.count(b"\n\n") < n and len(buf) < limit:
        b = r.read(1)
        if not b:
            break
        buf += b
    return buf.decode()


def parse(text):
    """[(event, id, data)] for every complete block in `text`."""
    out = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        ev, eid, data = "message", None, []
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("id: "):
                eid = int(line[4:])
            elif line.startswith("data: "):
                data.append(line[6:])
        out.append((ev, eid, "\n".join(data)))
    return out


def test_the_stream_answers_the_event_stream_content_type_and_live_events(served, run):
    c, r = stream(served)
    try:
        assert r.status == 200 and r.getheader("Content-Type").startswith("text/event-stream")
        assert r.getheader("Cache-Control") == "no-store"
        append(run, {"event": "outcome", "node": "a", "pos": 2})
        got = parse(blocks(r, 2))                   # the record's own first event, then the one that just landed
        assert [g[1] for g in got] == [1, 2] and json.loads(got[1][2])["node"] == "a"
    finally:
        c.close()


def test_the_stream_sends_the_record_lines_unchanged_with_the_position_as_the_id(served, run):
    c, r = stream(served)
    try:
        append(run, {"event": "outcome", "node": "a", "pos": 2}, {"event": "outcome", "node": "b", "pos": 3})
        got = parse(blocks(r, 3))
        assert [g[1] for g in got] == [1, 2, 3]
        assert [json.loads(g[2]) for g in got[1:]] == [{"event": "outcome", "node": "a", "pos": 2}, {"event": "outcome", "node": "b", "pos": 3}]
    finally:
        c.close()


def test_a_client_that_connects_late_is_given_the_events_after_the_id_it_sends(served, run):
    append(run, {"event": "outcome", "node": "a", "pos": 2}, {"event": "outcome", "node": "b", "pos": 3})
    c, r = stream(served, last=2)
    try:
        got = parse(blocks(r, 1))
        assert [g[1] for g in got] == [3]
    finally:
        c.close()


def test_a_client_with_no_id_is_given_every_event_from_the_first(served, run):
    append(run, {"event": "outcome", "node": "a", "pos": 2})
    c, r = stream(served)
    try:
        got = parse(blocks(r, 2))
        assert [g[1] for g in got] == [1, 2]
    finally:
        c.close()


def test_the_stream_ends_when_the_verdict_is_on_the_wire(served, run):
    c, r = stream(served)
    try:
        append(run, {"event": "verdict", "verdict": "passed", "pos": 2})
        got = parse(blocks(r, 3))
        assert got[-1][0] == "end" and json.loads(got[-1][2])["reason"] == "verdict"
        assert r.read() == b""                      # the connection ends: the page has the whole record
    finally:
        c.close()


def test_a_position_gap_ends_the_stream_with_the_reason(served, run):
    c, r = stream(served)
    try:
        append(run, {"event": "outcome", "node": "a", "pos": 7})
        got = parse(blocks(r, 2))
        assert [g[1] for g in got if g[0] != "end"] == [1] and json.loads(got[-1][2])["reason"] == "gap"   # the skipped line never reaches the page
    finally:
        c.close()


def test_lines_before_a_gap_are_delivered_even_when_one_read_holds_both(run):
    """A feed that first reads the file after the gap line was appended (a slow watcher) still hands over the lines before the gap."""
    (run / "record.jsonl").write_text(RECORD + json.dumps({"event": "outcome", "node": "a", "pos": 7}) + "\n")
    feed = ds.RecordFeed(run)
    feed._read(run / "record.jsonl")
    lines, end = feed.since(0)
    assert [p for p, _ in lines] == [1] and end == "gap"


def test_the_stream_refuses_a_foreign_host_and_every_method_but_get(served):
    c, r = stream(served, host="evil.example")
    try:
        assert r.status == 403
    finally:
        c.close()
    for method in ("POST", "PUT", "DELETE", "HEAD"):
        st, _, _ = get(served, "/run/events", method=method)
        assert st == 405


def test_one_watcher_reads_the_record_for_every_open_stream(served, run):
    feed = served.feed()
    c1, r1 = stream(served)
    c2, r2 = stream(served)
    try:
        blocks(r1, 0), blocks(r2, 0)
        before = feed.reads
        append(run, {"event": "outcome", "node": "a", "pos": 2})
        assert [g[1] for g in parse(blocks(r1, 2))] == [1, 2] and [g[1] for g in parse(blocks(r2, 2))] == [1, 2]
        assert feed.reads - before <= 2               # one watcher, not one per client
    finally:
        c1.close(), c2.close()


def test_a_stream_waits_for_a_record_that_is_not_written_yet(tmp_path):
    d = tmp_path / "empty-run"
    d.mkdir()
    srv = ds.RunServer(d, idle=60)
    threading.Thread(target=lambda: serve_quietly(srv, 200), daemon=True).start()
    try:
        c, r = stream(srv)
        try:
            assert r.status == 200
            assert parse(blocks(r, 0)) == []        # no record yet: the stream waits, and says nothing
            append(d, {"event": "run_start", "run_id": "r", "pos": 1})
            assert parse(blocks(r, 1))[0][1] == 1
        finally:
            c.close()
    finally:
        srv.server_close()


def test_a_client_that_went_away_leaves_no_thread_behind(served, run):
    before = set(threading.enumerate())   # a set of threads, not a count: other tests in this process start and end threads of their own

    def left():
        return [t for t in threading.enumerate() if t not in before and "process_request_thread" in t.name]

    c, r = stream(served)
    blocks(r, 0)
    r.close()   # the response holds its own file on the socket: close both, or the socket stays open
    c.close()
    deadline, pos = time.monotonic() + 10, 2
    while left() and time.monotonic() < deadline:
        append(run, {"event": "outcome", "node": "a", "pos": pos})   # the first write to a closed socket can succeed; a later one fails and ends the stream
        pos += 1
        time.sleep(0.1)
    assert left() == []


def _watchers(before):
    """The record-feed threads this test started and that still run."""
    return [t for t in threading.enumerate() if t not in before and t.name == "record-feed" and t.is_alive()]


def test_a_server_with_no_stream_open_runs_no_watcher_timer(served, run, monkeypatch):
    monkeypatch.setattr(ds, "HEARTBEAT_SECONDS", 0.2)
    before = set(threading.enumerate())
    served.feed()
    assert _watchers(before) == []                    # no page asked for a stream: nothing stats the record
    c, r = stream(served)
    try:
        blocks(r, 1)
        assert len(_watchers(before)) == 1            # one open stream, one watcher
    finally:
        r.close()
        c.close()
    deadline = time.monotonic() + 10
    while _watchers(before) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _watchers(before) == []                    # the last stream left: the watcher stops with it


def test_a_stream_opened_after_the_watcher_stopped_still_gets_the_events_written_meanwhile(served, run, monkeypatch):
    monkeypatch.setattr(ds, "HEARTBEAT_SECONDS", 0.2)
    before = set(threading.enumerate())
    c, r = stream(served)
    blocks(r, 1)
    r.close()
    c.close()
    deadline = time.monotonic() + 10
    while _watchers(before) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _watchers(before) == []
    append(run, {"event": "outcome", "node": "a", "pos": 2})   # written while no page was watching
    c2, r2 = stream(served, last=1)
    try:
        assert [g[1] for g in parse(blocks(r2, 1))] == [2]
    finally:
        r2.close()
        c2.close()
