import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from squad import supervisor as sv

ROOT = Path(__file__).resolve().parents[1]
ECHO_SOURCE = "import sys\nfor line in sys.stdin:\n    print('echo:' + line.rstrip('\\n'), flush=True)\n"

HUB_START = """
import json, sys, time
from squad import supervisor as sv
sid = sv.call(sys.argv[1], "start", job="echo-hub", argv=json.loads(sys.argv[2]))["session"]
print(sid, flush=True)
time.sleep(600)
"""

HUB_WRITE_READ = """
import sys, time
from squad import supervisor as sv
sv.call(sys.argv[1], "write", session=sys.argv[2], text="again\\n")
deadline = time.time() + 10
while time.time() < deadline:
    data = sv.call(sys.argv[1], "read", session=sys.argv[2], offset=0)["data"]
    if "echo:again" in data:
        print(data, flush=True)
        break
    time.sleep(0.05)
"""


@pytest.fixture
def state(tmp_path):
    folder = tmp_path / "state"
    folder.mkdir()
    yield folder
    pid_file = folder / "supervisor.pid"
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text()), signal.SIGKILL)
        except (ProcessLookupError, ValueError):
            pass
    for line in (folder / "sessions.jsonl").read_text().splitlines() if (folder / "sessions.jsonl").exists() else []:
        event = json.loads(line)
        if event.get("event") == "start":
            try:
                os.killpg(event["pid"], signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


@pytest.fixture
def echo(tmp_path):
    program = tmp_path / "echo.py"
    program.write_text(ECHO_SOURCE)
    return [sys.executable, "-u", str(program)]


def pythonpath_env():
    return {**os.environ, "PYTHONPATH": str(ROOT)}


def wait_for_text(state, session, needle, timeout=10.0):
    deadline = time.time() + timeout
    data = ""
    while time.time() < deadline:
        data = sv.call(state, "read", session=session, offset=0)["data"]
        if needle in data:
            return data
        time.sleep(0.05)
    raise AssertionError(f"{needle!r} not seen; output was {data!r}")


def test_echo_program_answers_a_line_through_attach(state, echo):
    sv.ensure(state)
    session = sv.call(state, "start", job="echo-1", argv=echo)["session"]
    sv.call(state, "write", session=session, text="hello\n")
    wait_for_text(state, session, "echo:hello")


def test_daemon_outlives_the_hub_and_the_same_program_answers(state, echo):
    sv.ensure(state)
    hub = subprocess.Popen(
        [sys.executable, "-c", HUB_START, str(state), json.dumps(echo)],
        stdout=subprocess.PIPE, text=True, env=pythonpath_env(),
    )
    session = hub.stdout.readline().strip()
    assert session
    hub.kill()
    hub.wait()

    daemon_pid = int((state / "supervisor.pid").read_text())
    os.kill(daemon_pid, 0)

    again = subprocess.run(
        [sys.executable, "-c", HUB_WRITE_READ, str(state), session],
        capture_output=True, text=True, timeout=30, env=pythonpath_env(),
    )
    assert again.returncode == 0, again.stderr
    assert "echo:again" in again.stdout


def test_socket_and_secret_are_mode_0600(state):
    sv.ensure(state)
    assert (state / "supervisor.sock").stat().st_mode & 0o777 == 0o600
    assert (state / "supervisor.secret").stat().st_mode & 0o777 == 0o600


def test_client_without_the_secret_is_refused(state):
    sv.ensure(state)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(state / "supervisor.sock"))
        client.sendall(json.dumps({"secret": "00" * 32, "op": "ping"}).encode() + b"\n")
        reply = json.loads(client.makefile("rb").readline())
    assert reply["ok"] is False
    assert "secret" in reply["reason"]


def test_client_of_another_user_is_refused(state):
    gate = sv.Gate(state / "clients.json", secret="s3cret", uid=os.geteuid())
    reason = gate.admit(presented="s3cret", peer_pid=os.getpid(), peer_uid=os.geteuid() + 1, peer_lstart="x")
    assert reason is not None
    assert "user" in reason


def test_process_whose_start_time_does_not_match_the_record_is_refused(state):
    gate = sv.Gate(state / "clients.json", secret="s3cret", uid=os.geteuid())
    assert gate.admit(presented="s3cret", peer_pid=os.getpid(), peer_uid=os.geteuid(), peer_lstart="Mon Nov  2 00:07:02 2026") is None
    reason = gate.admit(presented="s3cret", peer_pid=os.getpid(), peer_uid=os.geteuid(), peer_lstart="Tue Nov  3 09:00:00 2026")
    assert reason is not None
    assert "start time" in reason


def test_daemon_answers_no_input_for_a_session_it_did_not_start(state, echo):
    sv.ensure(state)
    with pytest.raises(sv.Refused):
        sv.call(state, "write", session="no-such-session", text="x\n")

    session = sv.call(state, "start", job="echo-4", argv=echo)["session"]
    pid = int((state / "supervisor.pid").read_text())
    os.kill(pid, signal.SIGKILL)
    (state / "supervisor.pid").unlink()
    (state / "supervisor.sock").unlink(missing_ok=True)

    sv.ensure(state)
    with pytest.raises(sv.Refused, match="not started by this daemon"):
        sv.call(state, "write", session=session, text="x\n")


def test_hub_module_holds_no_pty_or_fork_on_the_job_path():
    source = (ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py").read_text()
    for token in ("openpty", "forkpty", "os.fork(", "start_new_session", "pty.spawn"):
        assert token not in source, token
    popen_lines = [line.strip() for line in source.splitlines() if "Popen(" in line]
    assert popen_lines == ['pr = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)']
