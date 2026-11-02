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
from pathlib import Path
from squad import supervisor as sv
sid = sv.call(Path(sys.argv[1]), "start", job="echo-hub", argv=json.loads(sys.argv[2]))["session"]
print(sid, flush=True)
time.sleep(600)
"""

HUB_WRITE_READ = """
import sys, time
from pathlib import Path
from squad import supervisor as sv
state = Path(sys.argv[1])
sv.call(state, "write", session=sys.argv[2], text="again\\n")
deadline = time.time() + 10
while time.time() < deadline:
    data = sv.call(state, "read", session=sys.argv[2], offset=0)["data"]
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
    sv.socket_path(folder).unlink(missing_ok=True)
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
    assert sv.socket_path(state).stat().st_mode & 0o777 == 0o600
    assert (state / "supervisor.secret").stat().st_mode & 0o777 == 0o600


def test_client_without_the_secret_is_refused(state):
    sv.ensure(state)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(sv.socket_path(state)))
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
    sv.socket_path(state).unlink(missing_ok=True)

    sv.ensure(state)
    with pytest.raises(sv.Refused, match="not started by this daemon"):
        sv.call(state, "write", session=session, text="x\n")


@pytest.fixture
def sleeper():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    yield child
    child.kill()
    child.wait()


@pytest.fixture
def dead_pid():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def events(state):
    path = state / "sessions.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def write_start(state, **fields):
    with (state / "sessions.jsonl").open("a") as ledger:
        ledger.write(json.dumps({"ts": 0.0, "event": "start", **fields}) + "\n")


def end_reasons(state, session):
    return [e["reason"] for e in events(state) if e["event"] == "end" and e["session"] == session]


def test_start_writes_one_record_with_identity_process_terminal_and_cwd(state, echo, tmp_path):
    sid = sv.Supervisor(state).start("p#7", echo, str(tmp_path))
    [start] = [e for e in events(state) if e["event"] == "start"]
    assert start["session"] == sid
    assert start["job"] == "p#7"
    assert start["lstart"] == sv.lstart(start["pid"])
    assert start["terminal"].startswith("/dev/")
    assert start["cwd"] == str(tmp_path)


def test_attach_transport_is_recorded_once_per_session(state, echo):
    sv.ensure(state)
    session = sv.call(state, "start", job="p#11", argv=echo)["session"]
    sv.call(state, "read", session=session, offset=0)
    sv.call(state, "read", session=session, offset=0)
    attaches = [(e["session"], e["transport"]) for e in events(state) if e["event"] == "attach"]
    assert attaches == [(session, "unix")]


def test_live_record_survives_a_restart_and_keeps_its_job(state, echo, sleeper):
    write_start(state, session="old-1", job="p#9", pid=sleeper.pid, lstart=sv.lstart(sleeper.pid))
    restarted = sv.Supervisor(state)
    assert end_reasons(state, "old-1") == []
    with pytest.raises(sv.Refused, match="old-1"):
        restarted.start("p#9", echo, None)


def test_record_of_a_dead_process_is_ended_lost_and_frees_its_job(state, echo, dead_pid):
    write_start(state, session="old-2", job="p#10", pid=dead_pid, lstart="Mon Jan  1 00:00:00 1990")
    restarted = sv.Supervisor(state)
    assert end_reasons(state, "old-2") == ["lost"]
    assert restarted.start("p#10", echo, None) != "old-2"


def test_record_with_a_reused_pid_is_ended_lost(state, sleeper):
    write_start(state, session="old-3", job="p#12", pid=sleeper.pid, lstart="Mon Jan  1 00:00:00 1990")
    sv.Supervisor(state)
    assert end_reasons(state, "old-3") == ["lost"]


def test_second_start_for_a_live_job_is_refused_naming_the_live_session(state, echo):
    supervisor = sv.Supervisor(state)
    first = supervisor.start("p#8", echo, None)
    with pytest.raises(sv.Refused, match=first):
        supervisor.start("p#8", echo, None)


def test_owner_grant_naming_both_sessions_admits_a_second_start(state, echo):
    supervisor = sv.Supervisor(state)
    first = supervisor.start("p#13", echo, None)
    with pytest.raises(sv.Refused, match=first):
        supervisor.start("p#13", echo, None, session="second-13")
    (state / "grants.json").write_text(json.dumps([{"replaces": first, "with": "second-13"}]))
    assert supervisor.start("p#13", echo, None, session="second-13") == "second-13"


def test_grant_naming_another_session_admits_nothing(state, echo):
    supervisor = sv.Supervisor(state)
    first = supervisor.start("p#14", echo, None)
    (state / "grants.json").write_text(json.dumps([{"replaces": "someone-else", "with": "second-14"}]))
    with pytest.raises(sv.Refused, match=first):
        supervisor.start("p#14", echo, None, session="second-14")


@pytest.mark.parametrize("job", ["", "p#", "p#0", "#7", "p #1", "p/x", "p#1#2", "p\n", "a" * 65])
def test_malformed_job_name_is_refused_before_anything_starts(state, echo, job):
    with pytest.raises(sv.Refused):
        sv.Supervisor(state).start(job, echo, None)
    assert events(state) == []


@pytest.mark.parametrize("job", ["bihaiko#1094", "label-x", "p.q_r", "a" * 64])
def test_well_formed_job_name_is_accepted(job):
    assert sv.valid_job(job)


def test_hub_module_holds_no_pty_or_fork_on_the_job_path():
    source = (ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py").read_text()
    for token in ("openpty", "forkpty", "os.fork(", "pty.spawn"):
        assert token not in source, token
    popen_lines = [line.strip() for line in source.splitlines() if "Popen(" in line]
    assert popen_lines == ['pr = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)']
