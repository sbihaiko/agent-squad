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
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from http.client import HTTPConnection
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
TOKEN = "sk-ant-api03-SEEDEDTOKEN0123456789abcdef"
TAILNET = "hub.tail1234.ts.net"
ANA = "ana@example.com"
EVE = "eve@example.com"


def load_sessions():
    spec = importlib.util.spec_from_file_location("sessions_under_test", SCRIPTS / "sessions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def start_of(pid):
    out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return int(time.mktime(time.strptime(out, "%a %b %d %H:%M:%S %Y")))


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
    body = {"version": 1, "pid": pid, "startedAt": start_of(pid), "sessionId": sid, "cwd": str(cwd),
            "name": name, "status": status, "socket": str(sock)}
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
        sock = pathlib.Path(body["socket"]); sock.unlink(); sock.write_text("not a socket")
    elif breaks == "socket_path_is_link":
        link = home.sockets / "s1-link.sock"; link.symlink_to(body["socket"])
        rec.write_text(json.dumps({**body, "socket": str(link)}))
    elif breaks == "socket_outside_folder":
        outside = bind_socket(home, "s1", folder=home.path / "elsewhere")
        rec.write_text(json.dumps({**body, "socket": str(outside)}))
    elif breaks == "record_is_link":
        real = home.path / "real.json"; real.write_text(rec.read_text()); rec.unlink(); rec.symlink_to(real)
    elif breaks == "start_time_differs":
        rec.write_text(json.dumps({**body, "startedAt": body["startedAt"] + 100}))
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
        return SimpleNamespace(port=srv.server_port)

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def get(port, host, login=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host}
    if login: headers["Tailscale-User-Login"] = login
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
