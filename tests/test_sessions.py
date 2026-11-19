"""The Terminals list: the sessions module is the only reader of the session registry, the list route serves it, and the
last-message preview goes through the protected transcript reader and the shared redaction step (#833).

Every test runs in a temporary HOME with a fake registry and fake sockets: no real session and no network.
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

# A listed session is a live process the registry checks through /bin/ps, so every case here needs the process table.
pytestmark = pytest.mark.process_bound

TOKEN = "sk-ant-api03-SEEDEDTOKEN0123456789abcdef"
TAILNET = "hub.tail1234.ts.net"
ANA = "ana@example.com"
EVE = "eve@example.com"


def load_sessions():
    # sessions imports its siblings, so the file runs alone too (#1269)
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("sessions_under_test", SCRIPTS / "sessions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def start_of(pid):
    out = " ".join(subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True).stdout.split())
    try:
        return int(time.mktime(time.strptime(out, "%a %b %d %H:%M:%S %Y")))
    except ValueError:
        return 0


@pytest.fixture
def home(monkeypatch):
    root = pathlib.Path(os.path.realpath(tempfile.mkdtemp(prefix="gs833", dir="/tmp")))
    h = SimpleNamespace(path=root / "home", servers=[])
    h.sessions = h.path / ".claude" / "sessions"
    h.sockets = h.sessions / "sockets"
    h.projects = h.path / ".claude" / "projects"
    h.sockets.mkdir(parents=True)
    h.projects.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h.path))
    yield h
    for s in h.servers:
        s.close()
    shutil.rmtree(root, ignore_errors=True)


def bind_socket(home, sid, folder=None):
    path = (folder or home.sockets) / f"{sid}.sock"
    path.parent.mkdir(parents=True, exist_ok=True)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(path))
    s.listen(1)
    home.servers.append(s)
    return path


def add_session(home, pid, sid, cwd, name="build", status="busy", **over):
    sock = bind_socket(home, sid)
    body = {"version": "2.1.312", "pid": pid, "startedAt": start_of(pid) * 1000 + 296, "sessionId": sid, "cwd": str(cwd),
            "name": name, "status": status, "messagingSocketPath": str(sock), "kind": "interactive", "peerProtocol": 1}
    body.update(over)
    (home.sessions / f"{pid}.json").write_text(json.dumps(body))
    (home.sessions / f"{sid}.token").write_text(TOKEN + "\n")
    folder = home.projects / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    folder.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "user", "message": {"role": "user", "content": "hello"}},
             {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": f"the key is {TOKEN}"}]}}]
    (folder / f"{sid}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return body


def dead_pid():
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


def test_lists_name_status_and_last_message(home, tmp_path):
    cwd = tmp_path / "app"
    add_session(home, os.getpid(), "s1", cwd, name="build", status="waiting")
    out = load_sessions().list_sessions("p1", profiles_of=lambda c: ["p1"])
    assert out["waiting"] == 1
    row = out["groups"][0]["sessions"][0]
    assert (row["name"], row["status"], row["waiting"]) == ("build", "waiting", True)
    assert row["last"] == "the key is [redacted]"


def test_unknown_shape_fails_closed_and_is_named_once(home, tmp_path, capfd):
    cwd = tmp_path / "app"
    add_session(home, os.getpid(), "s1", cwd)
    (home.sessions / f"{os.getppid()}.json").write_text(json.dumps({"version": 2, "pid": os.getppid()}))
    mod = load_sessions()
    assert mod.list_sessions("p1", profiles_of=lambda c: ["p1"])["groups"][0]["sessions"][0]["sid"] == "s1"
    mod.list_sessions("p1", profiles_of=lambda c: ["p1"])
    err = capfd.readouterr().err
    assert err.count(f"{os.getppid()}.json") == 1


@pytest.mark.parametrize("breaks", [
    "socket_is_plain_file", "socket_path_is_link", "socket_outside_folder", "record_is_link",
    "process_dead", "start_time_differs", "other_user", "none",
])
def test_record_listed_only_when_every_check_holds(home, tmp_path, breaks):
    cwd = tmp_path / "app"
    pid = dead_pid() if breaks == "process_dead" else os.getpid()
    body = add_session(home, pid, "s1", cwd)
    rec = home.sessions / f"{pid}.json"
    if breaks == "socket_is_plain_file":
        sock = pathlib.Path(body["messagingSocketPath"]); sock.unlink(); sock.write_text("not a socket")
    elif breaks == "socket_path_is_link":
        link = home.sockets / "s1-link.sock"; link.symlink_to(body["messagingSocketPath"])
        rec.write_text(json.dumps({**body, "messagingSocketPath": str(link)}))
    elif breaks == "socket_outside_folder":
        outside = bind_socket(home, "s1", folder=home.path / "elsewhere")
        rec.write_text(json.dumps({**body, "messagingSocketPath": str(outside)}))
    elif breaks == "record_is_link":
        real = home.path / "real.json"; real.write_text(rec.read_text()); rec.unlink(); rec.symlink_to(real)
    elif breaks == "start_time_differs":
        rec.write_text(json.dumps({**body, "startedAt": body["startedAt"] + 100_000}))
    mod = load_sessions()
    if breaks == "other_user":
        mod.UID = os.getuid() + 1
    out = mod.list_sessions("p1", profiles_of=lambda c: ["p1"])
    listed = [s for g in out["groups"] for s in g["sessions"]]
    assert len(listed) == (1 if breaks == "none" else 0)


def test_session_matching_two_profiles_is_listed_in_neither(home, tmp_path):
    add_session(home, os.getpid(), "s1", tmp_path / "app")
    mod = load_sessions()
    assert mod.list_sessions("p1", profiles_of=lambda c: ["p1", "p2"])["groups"] == []
    assert mod.list_sessions("p2", profiles_of=lambda c: ["p1", "p2"])["groups"] == []


def test_ended_session_leaves_the_list_at_the_next_read(home, tmp_path):
    add_session(home, os.getpid(), "s1", tmp_path / "app")
    mod = load_sessions()
    assert len(mod.list_sessions("p1", profiles_of=lambda c: ["p1"])["groups"]) == 1
    (home.sessions / f"{os.getpid()}.json").unlink()
    assert mod.list_sessions("p1", profiles_of=lambda c: ["p1"])["groups"] == []


def test_membership_goes_through_the_address_seam(home, tmp_path, monkeypatch):
    profiles = tmp_path / "profiles"; profiles.mkdir()
    for slug in ("p1", "p2"):
        (profiles / f"{slug}.sh").write_text(': "${SG_REPO:=owner/app}"\n: "${SG_CHECKOUT:=$HOME/src/app}"\n')
    monkeypatch.setenv("SG_PROFILES_DIR", str(profiles))
    repo = tmp_path / "checkout"; repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:owner/app.git"], check=True)
    other = tmp_path / "other"; other.mkdir()
    subprocess.run(["git", "-C", str(other), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(other), "remote", "add", "origin", "git@github.com:someone/else.git"], check=True)
    mod = load_sessions()
    assert sorted(mod.profiles_of(str(repo))) == ["p1", "p2"]
    assert mod.profiles_of(str(other)) == []


def test_transcript_behind_a_symbolic_link_is_refused(home, tmp_path):
    cwd = tmp_path / "app"
    add_session(home, os.getpid(), "s1", cwd)
    folder = home.projects / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    moved = home.path / "moved"; folder.rename(moved)
    folder.symlink_to(moved, target_is_directory=True)
    mod = load_sessions()
    assert mod.read_last_message(str(cwd), "s1", secrets=[TOKEN]) is None


def test_transcript_file_that_is_a_link_is_refused(home, tmp_path):
    cwd = tmp_path / "app"
    add_session(home, os.getpid(), "s1", cwd)
    folder = home.projects / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    real = home.path / "real.jsonl"; (folder / "s1.jsonl").rename(real)
    (folder / "s1.jsonl").symlink_to(real)
    mod = load_sessions()
    assert mod.read_last_message(str(cwd), "s1", secrets=[TOKEN]) is None


@pytest.fixture
def serve(monkeypatch, home, tmp_path):
    servers = []

    def start(allow=(ANA,)):
        checkout = tmp_path / "checkout"
        (checkout / "docs" / "squad").mkdir(parents=True)
        (checkout / "docs" / "squad" / "defaults.yaml").write_text(
            f"tailnet:\n  host: {TAILNET}\n  allow: {json.dumps(list(allow))}\n")
        for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_PROFILE", "p1"),
                     ("SG_CHECKOUT", str(checkout)), ("SG_HUB_DIR", str(tmp_path / "hub"))):
            monkeypatch.setenv(k, v)
        for k in ("SG_STATE", "SG_STATE_BASE", "SG_SHARED"):
            monkeypatch.delenv(k, raising=False)
        spec = importlib.util.spec_from_file_location("hub_sessions", HUB_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.run_list = lambda: []
        mod.loop_caps = lambda: None
        mod.profiles_of_cwd = lambda cwd: ["p1"] if cwd.startswith(str(home.path / "p1")) else ["p2"] if cwd.startswith(str(home.path / "p2")) else []
        srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return SimpleNamespace(port=srv.server_port, mod=mod)

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def get(port, host, login=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host}
    if login:
        headers["Tailscale-User-Login"] = login
    conn.request("GET", "/api/sessions", headers=headers)
    r = conn.getresponse()
    body = r.read().decode()
    conn.close()
    return r.status, body


def test_list_route_needs_an_allowed_host_and_login(home, serve, tmp_path, monkeypatch):
    add_session(home, os.getpid(), "s1", home.path / "p1" / "app")
    srv = serve()
    status, body = get(srv.port, f"127.0.0.1:{srv.port}")
    assert status == 200 and json.loads(body)["groups"][0]["sessions"][0]["sid"] == "s1"
    assert get(srv.port, TAILNET)[0] == 403
    assert get(srv.port, TAILNET, login=EVE)[0] == 403
    status, body = get(srv.port, TAILNET, login=ANA)
    assert status == 200 and json.loads(body)["groups"][0]["sessions"][0]["sid"] == "s1"


def test_seeded_token_is_in_no_route_answer_and_no_log(home, serve, capfd):
    add_session(home, os.getpid(), "s1", home.path / "p1" / "app")
    add_session(home, os.getppid(), "s2", home.path / "p1" / "other", name="second")
    srv = serve()
    status, body = get(srv.port, f"127.0.0.1:{srv.port}")
    assert status == 200 and "s1" in body
    assert TOKEN not in body
    captured = capfd.readouterr()
    assert TOKEN not in captured.out + captured.err


def test_other_profile_session_is_not_readable_through_the_list_route(home, serve):
    add_session(home, os.getppid(), "s2", home.path / "p2" / "app", name="other-profile-name")
    srv = serve()
    status, body = get(srv.port, f"127.0.0.1:{srv.port}")
    assert status == 200 and json.loads(body)["groups"] == []
    assert "other-profile-name" not in body and "s2" not in body


def user(uuid, text):
    return {"type": "user", "uuid": uuid, "message": {"role": "user", "content": text}}


def answer(uuid, text):
    return {"type": "assistant", "uuid": uuid, "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def write_transcript(home, cwd, sid, entries):
    folder = home.projects / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sid}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))


def fetch(port, path, host, login=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host}
    if login:
        headers["Tailscale-User-Login"] = login
    conn.request("GET", path, headers=headers)
    r = conn.getresponse()
    body = r.read().decode()
    head = {k.lower(): v for k, v in r.getheaders()}
    conn.close()
    return r.status, body, head


def test_a_conversation_page_is_the_last_200_turns(home):
    mod = load_sessions()
    cwd = home.path / "p1" / "app"
    write_transcript(home, cwd, "s1", [user(f"u{i}", f"turn {i}") for i in range(250)])
    page = mod.read_conversation(str(cwd), "s1")
    assert len(page["turns"]) == 200
    assert page["turns"][0]["text"] == "turn 50" and page["turns"][-1]["text"] == "turn 249"
    assert page["next"] is not None


def test_a_turn_carries_the_clock_its_bubble_shows(home):
    """Owner, 2026-11-01: the bubble says when it was said, as WhatsApp does. The clock is the transcript's own timestamp, and a
    line that names none reads empty rather than invented."""
    mod = load_sessions()
    cwd = home.path / "p1" / "app"
    told = {"type": "user", "uuid": "u1", "timestamp": "2026-11-01T21:25:00.000Z",
            "message": {"role": "user", "content": "hi"}}
    said = {"type": "assistant", "uuid": "a1", "timestamp": "2026-11-01T21:26:03.000Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}}
    write_transcript(home, cwd, "s1", [told, said, user("u2", "no clock")])
    page = mod.read_conversation(str(cwd), "s1")
    assert [(t["id"], t["at"]) for t in page["turns"]] == [
        ("u1", "2026-11-01T21:25:00.000Z"), ("a1", "2026-11-01T21:26:03.000Z"), ("u2", "")]


def test_an_answer_carries_the_model_that_wrote_it(home):
    """Owner, 2026-11-02: the owner's bubble says "you", so the answer's says which model spoke. The name is the one the
    harness records on the turn, never inferred, and a turn that names none carries an empty name."""
    mod = load_sessions()
    cwd = home.path / "p1" / "app"
    named = {"type": "assistant", "uuid": "a1", "timestamp": "2026-11-01T21:26:03.000Z",
             "message": {"role": "assistant", "model": "claude-opus-5-5", "content": [{"type": "text", "text": "hello"}]}}
    write_transcript(home, cwd, "s1", [user("u1", "hi"), named, answer("a2", "unnamed")])
    page = mod.read_conversation(str(cwd), "s1")
    assert [(t["id"], t["m"]) for t in page["turns"]] == [("u1", ""), ("a1", "claude-opus-5-5"), ("a2", "")]


def test_a_conversation_page_grows_its_window_until_it_holds_the_conversation(home):
    """A turn is not a byte count: the window doubles until the page holds the recent text turns (#1073)."""
    mod = load_sessions()
    cwd = home.path / "p1" / "app"
    write_transcript(home, cwd, "s1", [user(f"u{i}", "x" * 40000) for i in range(10)])
    page = mod.read_conversation(str(cwd), "s1")
    assert [t["id"] for t in page["turns"]] == [f"u{i}" for i in range(10)]
    assert page["next"] is None


def test_huge_tool_lines_never_fill_the_page_instead_of_the_conversation(home):
    """The live hub answered a chat with three `tool: Bash` lines and nothing else: the tail window fell inside one line
    of a transcript whose tool results run to megabytes. The page carries the turns the owner reads (#1073)."""
    mod = load_sessions()
    cwd = home.path / "p1" / "app"
    entries = []
    for i in range(5):
        entries.append(user(f"u{i}", f"turn {i}"))
        entries.append({"type": "user", "uuid": f"r{i}", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "x" * 300000}]}})
        entries.append({"type": "assistant", "uuid": f"a{i}", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}})
    write_transcript(home, cwd, "s1", entries)
    page = mod.read_conversation(str(cwd), "s1")
    assert [t["text"] for t in page["turns"] if t["role"] == "owner"] == [f"turn {i}" for i in range(5)]


def test_older_pages_come_on_request_without_a_gap_or_a_repeat(home):
    mod = load_sessions()
    cwd = home.path / "p1" / "app"
    write_transcript(home, cwd, "s1", [user(f"u{i}", f"turn {i}") for i in range(450)])
    first = mod.read_conversation(str(cwd), "s1")
    second = mod.read_conversation(str(cwd), "s1", before=first["next"])
    third = mod.read_conversation(str(cwd), "s1", before=second["next"])
    assert [t["id"] for p in (third, second, first) for t in p["turns"]] == [f"u{i}" for i in range(450)]
    assert third["next"] is None


def test_turns_carry_the_message_id_and_role_and_a_tool_call_is_no_turn(home):
    mod = load_sessions()
    cwd = home.path / "p1" / "app"
    write_transcript(home, cwd, "s1", [
        user("u1", "list the files"),
        {"type": "assistant", "uuid": "a1", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Running it."}, {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "user", "uuid": "r1", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "a b"}]}},
    ])
    page = mod.read_conversation(str(cwd), "s1")
    assert [(t["id"], t["role"], t["text"]) for t in page["turns"]] == [
        ("u1", "owner", "list the files"), ("a1", "answer", "Running it.")]


def test_two_text_parts_of_one_message_are_two_turns_with_distinct_keys(home):
    mod = load_sessions()
    cwd = home.path / "p1" / "app"
    write_transcript(home, cwd, "s1", [
        {"type": "assistant", "uuid": "a1", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "first"}, {"type": "text", "text": "second"}]}},
    ])
    page = mod.read_conversation(str(cwd), "s1")
    assert [(t["id"], t["text"]) for t in page["turns"]] == [("a1", "first"), ("a1", "second")]
    assert len({t["k"] for t in page["turns"]}) == 2


def test_the_token_and_credential_shapes_are_in_no_turn_and_no_log(home, capfd):
    mod = load_sessions()
    cwd = home.path / "p1" / "app"
    gh = "ghp_" + "A" * 20
    write_transcript(home, cwd, "s1", [user("u1", f"key {TOKEN}"), answer("a1", f"gh {gh}")])
    page = mod.read_conversation(str(cwd), "s1", secrets=(TOKEN,))
    text = json.dumps(page)
    assert TOKEN not in text and gh not in text and "[redacted]" in text
    captured = capfd.readouterr()
    assert TOKEN not in captured.out + captured.err


def test_a_conversation_behind_a_symbolic_link_or_an_odd_id_is_refused(home, tmp_path):
    mod = load_sessions()
    cwd = home.path / "p1" / "app"
    folder = home.projects / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    folder.mkdir(parents=True)
    real = tmp_path / "real.jsonl"
    real.write_text(json.dumps(user("u1", "hi")) + "\n")
    os.symlink(real, folder / "s1.jsonl")
    assert mod.read_conversation(str(cwd), "s1") is None
    assert mod.read_conversation(str(cwd), "../s1") is None


def test_conversation_route_needs_an_allowed_host_and_login(home, serve):
    cwd = home.path / "p1" / "app"
    add_session(home, os.getpid(), "s1", cwd)
    write_transcript(home, cwd, "s1", [user("u1", "hello there")])
    srv = serve()
    path = "/api/sessions/conversation?sid=s1"
    status, body, _ = fetch(srv.port, path, f"127.0.0.1:{srv.port}")
    assert status == 200 and json.loads(body)["turns"][0]["text"] == "hello there"
    assert fetch(srv.port, path, TAILNET)[0] == 403
    assert fetch(srv.port, path, TAILNET, login=EVE)[0] == 403
    assert fetch(srv.port, path, TAILNET, login=ANA)[0] == 200


def test_conversation_of_an_ended_or_other_profile_session_is_not_found(home, serve):
    add_session(home, os.getpid(), "s1", home.path / "p1" / "app")
    add_session(home, dead_pid(), "s2", home.path / "p1" / "gone")
    add_session(home, os.getppid(), "s3", home.path / "p2" / "app")
    srv = serve()
    assert fetch(srv.port, "/api/sessions/conversation?sid=s1", f"127.0.0.1:{srv.port}")[0] == 200
    assert fetch(srv.port, "/api/sessions/conversation?sid=s2", f"127.0.0.1:{srv.port}")[0] == 404
    assert fetch(srv.port, "/api/sessions/conversation?sid=s3", f"127.0.0.1:{srv.port}")[0] == 404


def test_seeded_token_is_in_no_conversation_answer_and_no_log(home, serve, capfd):
    cwd = home.path / "p1" / "app"
    add_session(home, os.getpid(), "s1", cwd)
    plain = "plainmsgtoken0042"
    (home.sessions / "s1.token").write_text(plain + "\n")
    write_transcript(home, cwd, "s1", [user("m-1", f"key is {plain}"), answer("a-1", f"echo {plain}")])
    srv = serve()
    status, body, _ = fetch(srv.port, "/api/sessions/conversation?sid=s1", f"127.0.0.1:{srv.port}")
    assert status == 200 and plain not in body
    assert [t["text"] for t in json.loads(body)["turns"]] == ["key is [redacted]", "echo [redacted]"]
    captured = capfd.readouterr()
    assert plain not in captured.out + captured.err


def test_the_hub_mux_leaves_turn_text_and_message_ids_unchanged(home, serve):
    spec = importlib.util.spec_from_file_location("hubmux_sessions", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    cwd = home.path / "p1" / "app"
    add_session(home, os.getpid(), "s1", cwd)
    text = 'open "/api/runs" then "/dash/run-1/" and /api/x'
    write_transcript(home, cwd, "s1", [user("m-7", text)])
    srv = serve()
    httpd = mux.server(0, {"p1": {"mod": srv.mod}})
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        status, body, _ = fetch(port, "/p1/api/sessions/conversation?sid=s1", f"127.0.0.1:{port}")
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert status == 200 and json.loads(body)["turns"] == [{"id": "m-7", "k": "0:0", "role": "owner", "text": text, "at": "", "m": ""}]


def test_a_turn_with_markup_is_returned_as_its_plain_text(home, serve):
    cwd = home.path / "p1" / "app"
    add_session(home, os.getpid(), "s1", cwd)
    markup = '<script>alert(1)</script><img src="https://x.example/a.png" onerror="x()"> <b>hi</b>'
    write_transcript(home, cwd, "s1", [answer("a1", markup)])
    srv = serve()
    status, body, head = fetch(srv.port, "/api/sessions/conversation?sid=s1", f"127.0.0.1:{srv.port}")
    assert status == 200 and head["content-type"].startswith("application/json")
    assert json.loads(body)["turns"] == [{"id": "a1", "k": "0:0", "role": "answer", "text": markup, "at": "", "m": ""}]


def test_the_mobile_page_sends_a_csp_that_loads_nothing_from_outside_the_hub(home, serve):
    srv = serve()
    status, _, head = fetch(srv.port, "/mobile/", f"127.0.0.1:{srv.port}")
    csp = head.get("content-security-policy", "")
    assert status == 200 and "default-src 'self'" in csp and "img-src 'self'" in csp
    assert "http:" not in csp and "https:" not in csp and "*" not in csp


def test_a_record_written_seconds_after_the_process_started_is_listed(home, tmp_path):
    body = add_session(home, os.getpid(), "s1", tmp_path / "app")
    (home.sessions / f"{os.getpid()}.json").write_text(json.dumps({**body, "startedAt": body["startedAt"] + 2000}))
    out = load_sessions().list_sessions("p1", profiles_of=lambda c: ["p1"])
    assert [s["sid"] for g in out["groups"] for s in g["sessions"]] == ["s1"]


def test_without_the_variable_the_seam_still_reads_the_global_profiles(home, tmp_path, monkeypatch):
    """The hub starts without SG_PROFILES_DIR: the seam answers the skill's own profiles directory, never an empty list,
    so a live session in the checkout is found instead of the overlay reporting that none runs (#1034 follow-up)."""
    monkeypatch.delenv("SG_PROFILES_DIR", raising=False)
    profiles = tmp_path / ".claude" / "skills" / "squad-goal" / "scripts" / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "p1.sh").write_text(': "${SG_REPO:=owner/app}"\n: "${SG_CHECKOUT:=$HOME/src/app}"\n')
    mod = load_sessions()
    monkeypatch.setattr(mod.pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    assert mod.profiles_dir() == str(profiles)
    repo = tmp_path / "checkout"; repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:owner/app.git"], check=True)
    assert mod.profiles_of(str(repo)) == ["p1"], "the seam answered nothing and the session would be hidden"


def test_a_hook_injected_message_reads_system_and_never_as_the_owner(home):
    """`isMeta` marks what the harness wrote: a hook's feedback stands in the transcript as a user turn, and the panel must
    not put those words in the owner's mouth (owner, 2026-11-01)."""
    mod = load_sessions()
    cwd = home.path / "p1" / "app"
    entries = [user("u1", "real words"),
               {"type": "user", "uuid": "m1", "isMeta": True, "message": {"role": "user", "content": [
                   {"type": "text", "text": "Stop hook feedback: the goal is not met"}]}}]
    folder = mod.claude_home() / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))   # the module's own home, as it reads it
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "s1.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))
    page = mod.read_conversation(str(cwd), "s1")
    assert [(t["id"], t["role"]) for t in page["turns"]] == [("u1", "owner"), ("m1", "system")]


def test_a_command_record_and_a_task_notice_are_no_turn_of_the_talk(home):
    """`/compact` and a background task write their own user turns with no `isMeta`: the owner read a command's markup and a
    task's notification as words they typed (owner, 2026-11-01)."""
    mod = load_sessions()
    cwd = home.path / "p1" / "app"
    entries = [user("u1", "real words"),
               user("c1", "<command-name>/compact</command-name>\n            <command-message>compact</command-message>"),
               user("c2", "<local-command-stdout>[2mCompacted (ctrl+o to see full summary)[22m</local-command-stdout>"),
               user("t1", "<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n</task-notification>"),
               user("u2", "a question after the compact")]
    folder = mod.claude_home() / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "s2.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))
    page = mod.read_conversation(str(cwd), "s2")
    assert [t["role"] for t in page["turns"]] == ["owner", "owner"]   # the harness's own records are no turns


def test_the_summary_a_compact_hands_the_next_context_is_no_turn(home):
    """`/compact` writes its summary back as a user turn with no `isMeta`: the feed read the whole earlier conversation as if
    the owner had typed it again (owner, 2026-11-01)."""
    mod = load_sessions()
    cwd = home.path / "p1" / "app"
    entries = [user("u1", "real words"),
               user("k1", "This session is being continued from a previous conversation that ran out of context.\n\n"
                           "<system-reminder>\n<tools-used-before-summary>Bash</tools-used-before-summary>\n</system-reminder>"),
               user("u2", "and then I asked this")]
    folder = mod.claude_home() / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "s3.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))
    page = mod.read_conversation(str(cwd), "s3")
    assert [(t["id"], t["role"]) for t in page["turns"]] == [("u1", "owner"), ("u2", "owner")]


def test_a_poll_that_meets_no_new_byte_reads_no_file_again(home):
    """The chat polls every few seconds and this transcript runs to hundreds of megabytes: an unchanged file answers from
    the page already built (owner, 2026-11-01)."""
    mod = load_sessions()
    cwd = home.path / "p1" / "app"
    write_transcript(home, cwd, "s1", [user(f"u{i}", f"turn {i}") for i in range(30)])
    calls = []
    real = mod._read_under_home
    mod._read_under_home = lambda parts, **kw: (calls.append(parts), real(parts, **kw))[1]
    first = mod.read_conversation(str(cwd), "s1")
    n = len(calls)
    second = mod.read_conversation(str(cwd), "s1")
    assert n > 0 and len(calls) == n and second == first
    with open(home.projects / re.sub(r"[^A-Za-z0-9]", "-", str(cwd)) / "s1.jsonl", "a") as f:
        f.write(json.dumps(user("u99", "a new turn")) + "\n")
    third = mod.read_conversation(str(cwd), "s1")
    assert len(calls) > n and third["turns"][-1]["text"] == "a new turn"


def timed(uuid, at, text):
    return {"type": "user", "uuid": uuid, "timestamp": at, "message": {"role": "user", "content": text}}


def reply(uuid, at, mid, output_tokens, text=None):
    content = [{"type": "text", "text": text}] if text else [{"type": "tool_use", "id": uuid, "name": "Bash", "input": {}}]
    return {"type": "assistant", "uuid": uuid, "timestamp": at,
            "message": {"id": mid, "role": "assistant", "model": "claude-test", "content": content,
                        "usage": {"output_tokens": output_tokens}}}


def test_the_page_carries_the_live_activity_of_a_busy_session(home, tmp_path):
    cwd = tmp_path / "app"
    add_session(home, os.getpid(), "s1", cwd, status="busy")
    write_transcript(home, cwd, "s1", [
        timed("u0", "2026-11-01T10:00:00.000Z", "build it"),
        reply("a0", "2026-11-01T10:00:05.000Z", "m1", 300),
        reply("a1", "2026-11-01T10:00:06.000Z", "m1", 300, text="ok"),
        reply("a2", "2026-11-01T10:00:09.000Z", "m2", 2100, text="more"),
    ])
    activity = load_sessions().conversation("p1", "s1", profiles_of=lambda c: ["p1"])["activity"]
    assert activity["status"] == "busy"
    assert activity["tokens"] == 2400   # m1 is one message on two lines: its 300 output tokens count once
    assert isinstance(activity["elapsed"], int) and activity["elapsed"] >= 0


def test_the_page_reports_an_idle_session_as_idle(home, tmp_path):
    cwd = tmp_path / "app"
    add_session(home, os.getpid(), "s1", cwd, status="idle")
    write_transcript(home, cwd, "s1", [timed("u0", "2026-11-01T10:00:00.000Z", "build it"),
                                       reply("a0", "2026-11-01T10:00:05.000Z", "m1", 40, text="done")])
    assert load_sessions().conversation("p1", "s1", profiles_of=lambda c: ["p1"])["activity"]["status"] == "idle"


def test_no_owner_turn_leaves_elapsed_and_tokens_unknown(home, tmp_path):
    cwd = tmp_path / "app"
    add_session(home, os.getpid(), "s1", cwd, status="busy")
    write_transcript(home, cwd, "s1", [reply("a0", "2026-11-01T10:00:05.000Z", "m1", 40, text="hi")])
    activity = load_sessions().conversation("p1", "s1", profiles_of=lambda c: ["p1"])["activity"]
    assert activity == {"status": "busy", "elapsed": None, "tokens": None}


def test_background_session_claimed_late_by_its_spare_process_is_listed(home, tmp_path):
    """A background job runs in a pre-spawned spare process, which starts long before the session claims it (#1269)."""
    body = add_session(home, os.getpid(), "s1", tmp_path / "app", kind="bg")
    (home.sessions / f"{os.getpid()}.json").write_text(json.dumps({**body, "startedAt": body["startedAt"] + 10_000_000}))
    out = load_sessions().list_sessions("p1", profiles_of=lambda c: ["p1"])
    assert [s["sid"] for g in out["groups"] for s in g["sessions"]] == ["s1"]


def test_background_session_older_than_its_process_is_a_reused_pid(home, tmp_path):
    body = add_session(home, os.getpid(), "s1", tmp_path / "app", kind="bg")
    (home.sessions / f"{os.getpid()}.json").write_text(json.dumps({**body, "startedAt": body["startedAt"] - 100_000}))
    assert load_sessions().list_sessions("p1", profiles_of=lambda c: ["p1"])["groups"] == []


def test_parked_front_does_not_hide_the_job_it_parked_into(home, tmp_path):
    """A front that ran /resume parked its work in a background job (#1269): the panel opens the job, not the empty front."""
    cwd = tmp_path / "app"
    add_session(home, os.getpid(), "front1", cwd, name="front", parkedJobId="job1")
    add_session(home, os.getppid(), "job1", cwd, name="job", kind="bg")
    out = load_sessions().list_sessions("p1", profiles_of=lambda c: ["p1"])
    assert [s["sid"] for g in out["groups"] for s in g["sessions"]] == ["job1"]


def test_parked_front_stays_when_its_job_is_gone(home, tmp_path):
    cwd = tmp_path / "app"
    add_session(home, os.getpid(), "front1", cwd, name="front", parkedJobId="job-gone")
    out = load_sessions().list_sessions("p1", profiles_of=lambda c: ["p1"])
    assert [s["sid"] for g in out["groups"] for s in g["sessions"]] == ["front1"]


def test_a_row_carries_the_transcript_time_and_the_run_that_opened_it(home, tmp_path):
    """#1601: the transcript's mtime is the session's real last activity (owner), and a session whose checkout sits in a
    run's folder names that run, so the phone's chat can show the time and link the run that acted on the answer."""
    run = "run-20261015-191451-9f6b30"
    cwd = tmp_path / "checkout" / "runs" / run / "ws" / "node1"
    add_session(home, os.getpid(), "s1", cwd)
    transcript = home.projects / re.sub(r"[^A-Za-z0-9]", "-", str(cwd)) / "s1.jsonl"
    os.utime(transcript, (1_700_000_000, 1_700_000_000))
    row = load_sessions().list_sessions("p1", profiles_of=lambda c: ["p1"])["groups"][0]["sessions"][0]
    assert row["t"] == 1_700_000_000, row
    assert row["run"] == run, row


def test_a_row_outside_a_run_folder_names_no_run(home, tmp_path):
    """A session the owner opened in a plain checkout belongs to no run: the page shows no acted-on link for it."""
    add_session(home, os.getpid(), "s1", tmp_path / "app")
    row = load_sessions().list_sessions("p1", profiles_of=lambda c: ["p1"])["groups"][0]["sessions"][0]
    assert row["run"] == "", row


def test_project_sessions_open_the_job_not_the_parked_front(home, tmp_path):
    cwd = tmp_path / "app"
    add_session(home, os.getpid(), "front1", cwd, name="front", parkedJobId="job1")
    add_session(home, os.getppid(), "job1", cwd, name="job", kind="bg")
    out = load_sessions().project_sessions("p1", cwd, profiles_of=lambda c: ["p1"])
    assert [s["sid"] for s in out] == ["job1"]
