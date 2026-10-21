"""The run dashboard server admits a tailnet request only through the access seam (squad/tailnet_access.py), by Host and identity."""
import http.client
import json
import threading

import pytest

from squad import dashboard_server as ds
from tests.test_dashboard_server import GRAPH, RECORD, serve_quietly

TAILNET = "mac-mini.tail1234.ts.net"
ALLOWED = "ana@example.com"
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
    srv = ds.RunServer(run, page=page, idle=60, tailnet_host=TAILNET, allowed_logins=[ALLOWED])
    threading.Thread(target=lambda: serve_quietly(srv), daemon=True).start()
    yield srv
    srv.server_close()


def send(srv, method, path, host, login=None, origin=None, body=None, ctype="application/json"):
    """(status, body) of one request; a stream answers its status and is closed without its body."""
    c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    c.putrequest(method, path, skip_host=True)
    c.putheader("Host", host if host is not None else f"127.0.0.1:{srv.port}")
    if login is not None:
        c.putheader("Tailscale-User-Login", login)
    if origin is not None:
        c.putheader("Origin", origin)
    if body is not None:
        c.putheader("Content-Type", ctype)
        c.putheader("Content-Length", str(len(body)))
    c.endheaders(body)
    r = c.getresponse()
    if path == "/run/events" and r.status == 200:
        c.close()
        return r.status, b""
    data = r.read()
    c.close()
    return r.status, data


def lines(run):
    p = run / "control.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_a_loopback_request_reads_and_writes_as_before(served, run):
    assert send(served, "GET", "/run/record.jsonl", f"127.0.0.1:{served.port}")[:2] == (200, RECORD.encode())
    assert send(served, "GET", "/", f"localhost:{served.port}")[0] == 200
    st, _ = send(served, "POST", "/run/control", f"127.0.0.1:{served.port}", body=BODY)
    assert st == 200 and [r["action"] for r in lines(run)] == ["pause"]


READS = ["/", "/run/record.jsonl", "/run/events"]


@pytest.mark.parametrize("path", READS)
@pytest.mark.parametrize("login", [None, "eve@example.com", ""])
def test_a_tailnet_read_without_an_allowed_identity_is_refused(served, path, login):
    st, data = send(served, "GET", path, TAILNET, login=login)
    assert st == 403
    assert data.decode().strip() in ("no tailnet identity", "login not allowed")


@pytest.mark.parametrize("path", READS)
def test_a_tailnet_read_with_an_empty_allow_list_is_refused(run, path):
    srv = ds.RunServer(run, idle=60, tailnet_host=TAILNET, allowed_logins=[])
    threading.Thread(target=lambda: serve_quietly(srv, 20), daemon=True).start()
    try:
        assert send(srv, "GET", path, TAILNET, login=ALLOWED)[0] == 403
    finally:
        srv.server_close()


@pytest.mark.parametrize("path", READS)
def test_a_tailnet_read_by_an_allowed_login_is_served(served, path):
    assert send(served, "GET", path, TAILNET, login=ALLOWED)[0] == 200


def test_the_allowed_login_is_matched_without_regard_to_case(served):
    assert send(served, "GET", "/run/record.jsonl", TAILNET, login="Ana@Example.COM")[0] == 200


def test_a_tailnet_write_by_an_allowed_login_with_the_tailnet_origin_appends_one_line(served, run):
    st, data = send(served, "POST", "/run/control", TAILNET, login=ALLOWED, origin=f"https://{TAILNET}", body=BODY)
    assert st == 200 and json.loads(data)["ok"] is True
    (req,) = lines(run)
    assert (req["action"], req["nodes"], req["source"]) == ("pause", ["a"], "dashboard")


@pytest.mark.parametrize("login,origin", [
    (ALLOWED, None),
    (ALLOWED, "https://evil.example"),
    (ALLOWED, f"http://{TAILNET}"),
    (ALLOWED, f"https://{TAILNET}:8443"),
    (None, f"https://{TAILNET}"),
    ("eve@example.com", f"https://{TAILNET}"),
])
def test_a_tailnet_write_that_fails_origin_or_identity_appends_nothing(served, run, login, origin):
    st, _ = send(served, "POST", "/run/control", TAILNET, login=login, origin=origin, body=BODY)
    assert st == 403 and lines(run) == []


def test_a_remote_resume_appends_the_same_line_a_local_one_does(served, run):
    body = json.dumps({"action": "resume", "nodes": ["a"]}).encode()
    send(served, "POST", "/run/control", f"127.0.0.1:{served.port}", body=body)
    send(served, "POST", "/run/control", TAILNET, login=ALLOWED, origin=f"https://{TAILNET}", body=body)
    local, remote = lines(run)
    assert (remote["action"], remote["nodes"], remote["source"]) == (local["action"], local["nodes"], local["source"]) == ("resume", ["a"], "dashboard")


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_an_unknown_host_is_refused_as_forbidden_host_and_no_refusal_names_a_listed_login(served, run, method):
    path = "/run/record.jsonl" if method == "GET" else "/run/control"
    for login in (None, "eve@example.com", ALLOWED):
        st, data = send(served, method, path, "evil.example", login=login, origin=f"https://{TAILNET}", body=None if method == "GET" else BODY)
        assert st == 403 and data == b"forbidden host\n"
        assert ALLOWED.encode() not in data
    assert lines(run) == []


def test_a_plain_install_with_no_tailnet_host_is_loopback_only(run):
    srv = ds.RunServer(run, idle=60)
    threading.Thread(target=lambda: serve_quietly(srv, 20), daemon=True).start()
    try:
        st, data = send(srv, "GET", "/run/record.jsonl", TAILNET, login=ALLOWED)
        assert st == 403 and data == b"forbidden host\n"
        assert send(srv, "GET", "/run/record.jsonl", f"127.0.0.1:{srv.port}")[0] == 200
    finally:
        srv.server_close()


def test_squad_dashboard_reads_the_tailnet_block_of_the_project_file_and_stays_loopback_without_it(tmp_path):
    from squad import cli
    assert cli._tailnet_of(tmp_path) == (None, [])
    (tmp_path / "docs" / "squad").mkdir(parents=True)
    (tmp_path / "docs" / "squad" / "defaults.yaml").write_text(f"tailnet:\n  host: {TAILNET}\n  allow:\n    - {ALLOWED}\n")
    assert cli._tailnet_of(tmp_path) == (TAILNET, [ALLOWED])


def test_the_refusal_reasons_are_the_three_the_seam_names(served):
    assert send(served, "GET", "/", "evil.example")[1] == b"forbidden host\n"
    assert send(served, "GET", "/", TAILNET)[1] == b"no tailnet identity\n"
    assert send(served, "GET", "/", TAILNET, login="eve@example.com")[1] == b"login not allowed\n"
