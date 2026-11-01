"""The phone's message to a live session (#834): the hub writes the session wire protocol itself. A fake inbox on a Unix socket
in this process stands for the session; its peer pid is this process, so the recorded pid is os.getpid(). Every test runs in a
temporary HOME: no real session, no network.
"""
import importlib.util
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
sys.path.insert(0, str(SCRIPTS))

KEY = "cd" * 16
TAILNET = "hub.tail1234.ts.net"
ANA = "ana@example.com"
EVE = "eve@example.com"
CID = "3f1c9e2a-7b44-4d8e-9a1f-0c2b6d5e8f10"


def load_send():
    spec = importlib.util.spec_from_file_location("session_send_under_test", SCRIPTS / "session_send.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def start_of(pid):
    out = " ".join(subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True).stdout.split())
    try:
        return int(time.mktime(time.strptime(out, "%a %b %d %H:%M:%S %Y")))
    except ValueError:
        return 0


class Inbox:
    """A fake session inbox: reads one connection to its end and keeps the frames. `hang` reads nothing and never closes."""

    def __init__(self, path, hang=False):
        self.frames, self.conns, self.lock, self.hang = [], 0, threading.Lock(), hang
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(str(path))
        self.srv.listen(8)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            if self.hang:
                continue
            data = b""
            conn.settimeout(10)
            try:
                while chunk := conn.recv(65536):
                    data += chunk
            except OSError:
                pass
            with self.lock:
                self.conns += 1
                self.frames += [json.loads(line) for line in data.splitlines() if line.strip()]
            conn.close()

    def close(self):
        self.srv.close()


@pytest.fixture
def home(monkeypatch):
    root = pathlib.Path(os.path.realpath(tempfile.mkdtemp(prefix="gs834", dir="/tmp")))
    h = SimpleNamespace(path=root / "home", inboxes=[])
    h.sessions = h.path / ".claude" / "sessions"
    h.sockets = h.sessions / "sockets"
    h.projects = h.path / ".claude" / "projects"
    h.sockets.mkdir(parents=True)
    h.projects.mkdir(parents=True)
    h.state = root / "state"
    monkeypatch.setenv("HOME", str(h.path))
    yield h
    for box in h.inboxes:
        box.close()
    shutil.rmtree(root, ignore_errors=True)


def add_session(home, sid, cwd, pid=None, hang=False, name="build", status="busy"):
    pid = pid or os.getpid()
    sock = home.sockets / f"{sid}.sock"
    home.inboxes.append(Inbox(sock, hang=hang))
    body = {"version": "2.1.312", "pid": pid, "startedAt": start_of(pid) * 1000 + 296, "sessionId": sid, "cwd": str(cwd),
            "name": name, "status": status, "messagingSocketPath": str(sock), "kind": "interactive", "peerProtocol": 1}
    (home.sessions / f"{pid}.json").write_text(json.dumps(body))
    key = home.sessions / f"{pid}.{__import__('hashlib').sha256(os.path.abspath(sock).encode()).hexdigest()}.key"
    key.write_text(json.dumps({"peerToken": KEY, "procStart": "x", "pidDomain": "x"}))
    key.chmod(0o600)
    folder = home.projects / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sid}.jsonl").write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n")
    return folder / f"{sid}.jsonl"


def inbox_of(home):
    return home.inboxes[-1]


def profile_p1(cwd):
    return ["p1"]


def test_one_message_on_the_wire_follows_the_protocol_fields(home, tmp_path):
    send = load_send()
    add_session(home, "s1", tmp_path / "app")
    out = send.send("p1", "s1", "hello there", CID, profiles_of=profile_p1, root=home.state)
    box = inbox_of(home)
    assert out["status"] == "sent"
    assert box.frames[0] == {"type": "auth", "token": KEY}
    msg = box.frames[1]
    assert set(msg) == {"type", "message", "from_name", "msg_id", "priority"}
    assert msg["type"] == "user" and msg["message"] == {"role": "user", "content": "hello there"}
    assert msg["from_name"] == "squad-hub" and msg["msg_id"] == CID and msg["priority"] == "next"


def test_two_concurrent_sends_with_one_id_send_once(home, tmp_path):
    send = load_send()
    add_session(home, "s1", tmp_path / "app")
    results = []
    gate = threading.Barrier(2)

    def go():
        gate.wait()
        results.append(send.send("p1", "s1", "once", CID, profiles_of=profile_p1, root=home.state))

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert inbox_of(home).conns == 1
    assert len(inbox_of(home).frames) == 2


def test_a_repeat_id_sends_nothing_and_reports_the_recorded_status(home, tmp_path):
    send = load_send()
    add_session(home, "s1", tmp_path / "app")
    assert send.send("p1", "s1", "once", CID, profiles_of=profile_p1, root=home.state)["status"] == "sent"
    again = send.send("p1", "s1", "once", CID, profiles_of=profile_p1, root=home.state)
    assert again["status"] == "sent"
    assert inbox_of(home).conns == 1


def test_a_reservation_that_cannot_be_persisted_refuses_the_send(home, tmp_path):
    send = load_send()
    add_session(home, "s1", tmp_path / "app")
    home.state.write_text("a file where the reservation folder belongs")
    out = send.send("p1", "s1", "hello", CID, profiles_of=profile_p1, root=home.state)
    assert out["status"] == "refused"
    assert inbox_of(home).conns == 0


def test_a_crash_after_the_send_and_a_restart_send_nothing_more(home, tmp_path, monkeypatch):
    send = load_send()
    add_session(home, "s1", tmp_path / "app")

    class Crash(BaseException):
        pass

    def die(*a, **k):
        raise Crash()

    monkeypatch.setattr(send, "_finish", die)
    with pytest.raises(Crash):
        send.send("p1", "s1", "hello", CID, profiles_of=profile_p1, root=home.state)
    assert inbox_of(home).conns == 1
    monkeypatch.undo()
    send = load_send()
    out = send.send("p1", "s1", "hello", CID, profiles_of=profile_p1, root=home.state)
    assert out["status"] == "uncertain"
    assert inbox_of(home).conns == 1


def test_text_over_4000_characters_is_refused_before_any_reservation(home, tmp_path):
    send = load_send()
    add_session(home, "s1", tmp_path / "app")
    assert send.send("p1", "s1", "x" * 4001, CID, profiles_of=profile_p1, root=home.state)["status"] == "refused"
    assert not home.state.exists()
    assert inbox_of(home).conns == 0
    assert send.send("p1", "s1", "x" * 4000, CID, profiles_of=profile_p1, root=home.state)["status"] == "sent"


def test_a_socket_peer_that_is_not_the_recorded_pid_is_refused(home, tmp_path, monkeypatch):
    send = load_send()
    add_session(home, "s1", tmp_path / "app")
    monkeypatch.setattr(send, "peer_pid", lambda s: 1)
    assert send.send("p1", "s1", "hello", CID, profiles_of=profile_p1, root=home.state)["status"] == "refused"
    assert inbox_of(home).frames == []


def test_a_session_of_another_profile_or_none_is_refused(home, tmp_path):
    send = load_send()
    add_session(home, "s1", tmp_path / "app")
    assert send.send("p2", "s1", "hello", CID, profiles_of=profile_p1, root=home.state)["status"] == "refused"
    assert send.send("p1", "missing", "hello", CID, profiles_of=profile_p1, root=home.state)["status"] == "refused"
    assert inbox_of(home).conns == 0


def test_a_socket_error_seeded_with_the_token_shows_no_token(home, tmp_path, monkeypatch, capfd):
    send = load_send()
    add_session(home, "s1", tmp_path / "app")

    class Broken(socket.socket):
        def sendall(self, data, *args):
            raise OSError(f"broken pipe carrying {KEY}")

    monkeypatch.setattr(send, "_open", lambda: Broken(socket.AF_UNIX, socket.SOCK_STREAM))
    out = send.send("p1", "s1", "hello", CID, profiles_of=profile_p1, root=home.state)
    captured = capfd.readouterr()
    assert out["status"] == "uncertain"
    assert KEY not in json.dumps(out) and KEY not in captured.out + captured.err


def test_a_silent_inbox_hits_the_five_second_deadline(home, tmp_path, monkeypatch):
    send = load_send()
    assert send.DEADLINE_S == 5.0
    monkeypatch.setattr(send, "DEADLINE_S", 0.3)
    add_session(home, "s1", tmp_path / "app", hang=True)
    started = time.monotonic()
    out = send.send("p1", "s1", "hello", CID, profiles_of=profile_p1, root=home.state)
    assert out["status"] == "uncertain"
    assert time.monotonic() - started < 3


def test_a_reservation_is_kept_for_24_hours_and_then_purged(home, tmp_path):
    send = load_send()
    add_session(home, "s1", tmp_path / "app")
    old = "11111111-2222-4333-8444-555555555555"
    assert send.send("p1", "s1", "a", old, profiles_of=profile_p1, root=home.state)["status"] == "sent"
    for f in home.state.rglob("*.json"):
        os.utime(f, (time.time() - 25 * 3600, time.time() - 25 * 3600))
    assert send.send("p1", "s1", "b", CID, profiles_of=profile_p1, root=home.state)["status"] == "sent"
    assert send.send("p1", "s1", "a", old, profiles_of=profile_p1, root=home.state)["status"] == "sent"
    assert inbox_of(home).conns == 3


def test_status_stays_sent_when_the_transcript_names_the_message(home, tmp_path):
    send = load_send()
    transcript = add_session(home, "s1", tmp_path / "app")
    send.send("p1", "s1", "hello", CID, profiles_of=profile_p1, root=home.state)
    assert send.status("p1", "s1", CID, profiles_of=profile_p1, root=home.state) == {"status": "sent"}
    line = {"type": "user", "isMeta": True, "origin": {"kind": "peer", "msg_id": CID}}
    transcript.write_text(transcript.read_text() + json.dumps(line) + "\n")
    assert send.status("p1", "s1", CID, profiles_of=profile_p1, root=home.state) == {"status": "sent"}


def test_the_sender_answers_only_sent_uncertain_or_refused():
    assert load_send().STATES == ("sent", "uncertain", "refused")


def load_hub(monkeypatch, home, tmp_path, terminals_send=None, allow=(ANA,)):
    checkout = tmp_path / "checkout"
    (checkout / "docs" / "squad").mkdir(parents=True)
    lines = ["tailnet:", f"  host: {TAILNET}", f"  allow: {json.dumps(list(allow))}"]
    if terminals_send is not None:
        lines.append(f"  terminals_send: {str(terminals_send).lower()}")
    (checkout / "docs" / "squad" / "defaults.yaml").write_text("\n".join(lines) + "\n")
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_PROFILE", "p1"),
                 ("SG_CHECKOUT", str(checkout)), ("SG_HUB_DIR", str(tmp_path / "hub")), ("SG_STATE", str(home.state))):
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("hub_send", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.run_list = lambda: []
    mod.loop_caps = lambda: None
    mod.profiles_of_cwd = lambda cwd: ["p1"] if cwd.startswith(str(home.path / "p1")) else ["p2"]
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return SimpleNamespace(port=srv.server_port, mod=mod, server=srv)


@pytest.fixture
def hub(monkeypatch, home, tmp_path):
    made = []

    def make(**kw):
        h = load_hub(monkeypatch, home, tmp_path, **kw)
        made.append(h)
        return h

    yield make
    for h in made:
        h.server.shutdown()
        h.server.server_close()


def post(port, body, host=TAILNET, login=ANA, origin=f"https://{TAILNET}", ctype="application/json"):
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host, "Content-Type": ctype, "Origin": origin}
    if login:
        headers["Tailscale-User-Login"] = login
    conn.request("POST", "/api/sessions/send", body=json.dumps(body), headers=headers)
    r = conn.getresponse()
    text = r.read().decode()
    conn.close()
    return r.status, text


def msg(**over):
    return {"sid": "s1", "text": "hello", "client_id": CID, **over}


def test_the_send_route_is_off_until_the_profile_sets_terminals_send(home, tmp_path, hub):
    add_session(home, "s1", home.path / "p1" / "app")
    h = hub()
    status, _ = post(h.port, msg())
    assert status == 403
    assert inbox_of(home).conns == 0


def test_the_send_route_is_off_when_the_profile_sets_it_false(home, tmp_path, hub):
    add_session(home, "s1", home.path / "p1" / "app")
    assert post(hub(terminals_send=False).port, msg())[0] == 403
    assert inbox_of(home).conns == 0


def test_an_allowed_login_sends_through_the_hub(home, tmp_path, hub):
    add_session(home, "s1", home.path / "p1" / "app")
    status, body = post(hub(terminals_send=True).port, msg())
    assert status == 200 and json.loads(body)["status"] == "sent"
    assert inbox_of(home).conns == 1
    assert inbox_of(home).frames[1]["from_name"] == "squad-hub"


def test_every_allowed_login_of_the_profile_may_send_and_another_login_may_not(home, tmp_path, hub):
    add_session(home, "s1", home.path / "p1" / "app")
    h = hub(terminals_send=True, allow=(ANA, EVE))
    assert post(h.port, msg(), login=EVE)[0] == 200
    assert post(h.port, msg(client_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"), login="mallory@example.com")[0] == 403


@pytest.mark.parametrize("kw", [
    {"host": "127.0.0.1:%d"},
    {"origin": "https://evil.example"},
    {"origin": ""},
    {"ctype": "text/plain"},
    {"login": None},
])
def test_a_send_needs_the_allowed_host_login_origin_and_json_body(home, tmp_path, hub, kw):
    add_session(home, "s1", home.path / "p1" / "app")
    h = hub(terminals_send=True)
    if "host" in kw:
        kw = {"host": kw["host"] % h.port}
    assert post(h.port, msg(), **kw)[0] == 403
    assert inbox_of(home).conns == 0


@pytest.mark.parametrize("extra", [{"from_name": "owner"}, {"priority": "now"}, {"msg_id": "x"}])
def test_a_field_beyond_session_text_and_client_id_is_refused(home, tmp_path, hub, extra):
    add_session(home, "s1", home.path / "p1" / "app")
    status, _ = post(hub(terminals_send=True).port, msg(**extra))
    assert status == 400
    assert inbox_of(home).conns == 0


def test_the_status_route_reads_sent_for_a_sent_message(home, tmp_path, hub):
    transcript = add_session(home, "s1", home.path / "p1" / "app")
    h = hub(terminals_send=True)
    post(h.port, msg())
    transcript.write_text(transcript.read_text() + json.dumps({"origin": {"kind": "peer", "msg_id": CID}}) + "\n")
    conn = HTTPConnection("127.0.0.1", h.port, timeout=10)
    conn.request("GET", f"/api/sessions/send?sid=s1&client_id={CID}", headers={"Host": TAILNET, "Tailscale-User-Login": ANA})
    r = conn.getresponse()
    assert r.status == 200 and json.loads(r.read())["status"] == "sent"
    conn.close()


# ---------------------------------------------------------------- the session overlay (#898): the loopback send rule (ADR-0152 rule 9)

def loopback_post(port, body, origin=None):
    own = f"http://127.0.0.1:{port}"
    return post(port, body, host=f"127.0.0.1:{port}", login=None, origin=own if origin is None else origin)


def get_json(port, path, host=None, login=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if login:
        headers["Tailscale-User-Login"] = login
    conn.request("GET", path, headers=headers)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, json.loads(data) if r.status == 200 else data


def test_a_loopback_send_with_the_hubs_own_origin_reaches_the_session_with_no_grant(home, tmp_path, hub):
    add_session(home, "s1", home.path / "p1" / "app")
    h = hub()
    status, body = loopback_post(h.port, msg())
    assert status == 200 and json.loads(body)["status"] == "sent"
    assert inbox_of(home).conns == 1


@pytest.mark.parametrize("origin", ["https://evil.example", "", f"https://{TAILNET}", "http://127.0.0.1:1"])
def test_a_loopback_send_needs_the_hubs_own_origin(home, tmp_path, hub, origin):
    add_session(home, "s1", home.path / "p1" / "app")
    h = hub()
    assert loopback_post(h.port, msg(), origin=origin)[0] == 403
    assert inbox_of(home).conns == 0


def test_the_project_route_lists_only_the_sessions_in_the_profiles_checkout(home, tmp_path, hub):
    h = hub()
    checkout = pathlib.Path(h.mod.CHECKOUT)
    h.mod.profiles_of_cwd = lambda cwd: ["p1"]
    add_session(home, "s1", checkout, name="main")
    add_session(home, "s2", checkout / "sub", pid=os.getppid(), name="elsewhere")
    status, out = get_json(h.port, "/api/sessions/project")
    assert status == 200
    assert [s["sid"] for s in out["sessions"]] == ["s1"]
    assert out["send"] is True and out["why"] == ""


def test_the_project_route_lists_two_sessions_in_the_checkout_so_the_owner_picks(home, tmp_path, hub):
    h = hub()
    checkout = pathlib.Path(h.mod.CHECKOUT)
    h.mod.profiles_of_cwd = lambda cwd: ["p1"]
    add_session(home, "s1", checkout, name="one")
    add_session(home, "s2", checkout, pid=os.getppid(), name="two")
    _, out = get_json(h.port, "/api/sessions/project")
    assert sorted(s["sid"] for s in out["sessions"]) == ["s1", "s2"]
    assert all(set(s) == {"sid", "name", "status"} for s in out["sessions"])


def test_over_the_tailnet_with_send_off_the_project_route_names_the_setting(home, tmp_path, hub):
    _, out = get_json(hub().port, "/api/sessions/project", host=TAILNET, login=ANA)
    assert out["send"] is False and "SQUAD_TAILNET_TERMINALS_SEND" in out["why"]


def test_over_the_tailnet_with_send_on_the_project_route_lets_the_owner_send(home, tmp_path, hub):
    _, on = get_json(hub(terminals_send=True).port, "/api/sessions/project", host=TAILNET, login=ANA)
    assert on["send"] is True and on["why"] == ""


def test_adr_0152_states_the_loopback_send_rule():
    adr = next((ROOT / "docs" / "adr").glob("0152-*.md")).read_text()
    rule = next(line for line in adr.splitlines() if line.startswith("9. "))
    assert "loopback" in rule and "own Origin" in rule and "always sends" in rule
    assert "SQUAD_TAILNET_TERMINALS_SEND" in rule
    assert "tests/test_session_send.py" in rule
