"""Ticket #107: the run server's one write route asks a launched run to pause or resume."""
import http.client
import json
import threading

import pytest

from squad import dashboard_server as ds
from squad.control import Control
from tests.test_dashboard_server import GRAPH, RECORD, get, serve_quietly

BODY = json.dumps({"action": "pause", "nodes": ["a"]}).encode()


@pytest.fixture
def run(tmp_path):
    d = tmp_path / "run-a"
    d.mkdir()
    (d / "record.jsonl").write_text(RECORD)
    (d / "graph.json").write_text(GRAPH)
    return d


@pytest.fixture
def served(run, tmp_path):
    page = tmp_path / "page.html"
    page.write_text("<html>page</html>")
    srv = ds.RunServer(run, page=page, idle=60)
    threading.Thread(target=lambda: serve_quietly(srv), daemon=True).start()
    yield srv
    srv.server_close()


def post(srv, path="/run/control", body=BODY, ctype="application/json", origin=None, host="default"):
    c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    c.putrequest("POST", path, skip_host=True)
    c.putheader("Host", f"127.0.0.1:{srv.port}" if host == "default" else host)
    if ctype:
        c.putheader("Content-Type", ctype)
    if origin:
        c.putheader("Origin", origin)
    c.putheader("Content-Length", str(len(body)))
    c.endheaders(body)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


def lines(run):
    p = run / "control.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_a_pause_request_appends_one_line_to_the_control_file_and_nothing_else(served, run):
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    st, data = post(served)
    assert st == 200 and json.loads(data)["ok"] is True
    (req,) = lines(run)
    assert (req["action"], req["nodes"], req["source"]) == ("pause", ["a"], "dashboard")
    after = {p.name: p.read_bytes() for p in run.iterdir() if p.name != "control.jsonl"}
    assert after == before                                  # the record and the graph are untouched


def test_the_control_state_is_readable_and_follows_the_requests(served, run):
    assert json.loads(get(served, "/run/control.json")[1]) == {"whole": False, "nodes": [], "released": []}
    post(served)
    post(served, body=json.dumps({"action": "pause"}).encode())
    post(served, body=json.dumps({"action": "resume", "nodes": ["a"]}).encode())
    assert json.loads(get(served, "/run/control.json")[1]) == {"whole": True, "nodes": [], "released": ["a"]}
    assert Control(run).held("b") and not Control(run).held("a")


@pytest.mark.parametrize("body", [b"", b"not json", b"[1]", b'{"action":"stop"}', b'{"action":"pause","nodes":"a"}', b'{"action":"pause","nodes":[1]}',
                                  b'{"action":"pause","nodes":["../x"]}', b'{"action":"pause","nodes":["a#2"]}', b'{"action":"pause","nodes":[]}', json.dumps({"action": "pause", "nodes": ["a"] * 101}).encode()])
def test_a_body_that_is_not_a_valid_request_is_refused_and_writes_nothing(served, run, body):
    st, _ = post(served, body=body)
    assert st == 400 and lines(run) == []


def test_a_body_over_the_size_cap_is_refused(served, run):
    assert post(served, body=b" " * 5000)[0] == 413 and lines(run) == []


def test_a_request_without_a_json_content_type_is_refused_so_a_cross_site_form_cannot_pause_a_run(served, run):
    assert post(served, ctype="text/plain")[0] == 415
    assert post(served, ctype="application/x-www-form-urlencoded")[0] == 415
    assert post(served, ctype=None)[0] == 415 and lines(run) == []


@pytest.mark.parametrize("origin", ["http://evil.example", "https://127.0.0.1:1", "null", "http://127.0.0.1:1"])
def test_a_request_from_another_origin_is_refused(served, run, origin):
    assert post(served, origin=origin)[0] == 403 and lines(run) == []


def test_the_page_origin_is_allowed(served, run):
    assert post(served, origin=f"http://127.0.0.1:{served.port}")[0] == 200


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1", "0.0.0.0:80", ""])
def test_a_wrong_host_header_is_refused_so_dns_rebinding_pauses_nothing(served, run, host):
    assert post(served, host=host)[0] == 403 and lines(run) == []


@pytest.mark.parametrize("path", ["/", "/run/record.jsonl", "/run/graph.json", "/run/info.json", "/run/control.json", "/run/control/x", "/run/../control", "/run"])
def test_no_other_path_takes_a_post(served, run, path):
    assert post(served, path=path)[0] in (404, 405) and lines(run) == []


def _verdict(run, verdict, reasons=()):
    ev = {"event": "verdict", "verdict": verdict, "exit_code": 0, "reasons": list(reasons), "remains": [], "folded": [], "pos": 2, "ts": 2.0}
    (run / "record.jsonl").write_text(RECORD + json.dumps(ev) + "\n")


@pytest.mark.parametrize("verdict", ["completed", "failed", "conflict", "needs-human", "no-repro"])
def test_a_run_that_has_ended_takes_no_request(served, run, verdict):
    _verdict(run, verdict)
    st, data = post(served)
    assert st == 409 and json.loads(data)["error"] == "run_ended" and lines(run) == []


def test_a_run_that_ended_on_a_pause_takes_a_resume_request(served, run):
    _verdict(run, "stopped", ["paused:b"])
    st, _ = post(served, body=json.dumps({"action": "resume", "nodes": ["b"]}).encode())
    assert st == 200 and lines(run)[0]["action"] == "resume"


def test_a_run_that_ended_on_stop_after_takes_no_request(served, run):
    _verdict(run, "stopped", ["stopped_after:a"])
    assert post(served)[0] == 409 and lines(run) == []


def test_the_one_write_route_only_appends_to_the_control_file_and_starts_no_run():
    from pathlib import Path

    from squad import dashboard_server as ds
    src = Path(ds.__file__).read_text()
    assert src.count("def do_POST") == 1 and src.count("Control(self._run.folder).append(") == 1
    for banned in ("engine", "Engine", "adapter", "write_bytes", "subprocess.run", "os.kill"):
        assert banned not in src
