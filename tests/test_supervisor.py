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
from squad.redact import MARK

ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT_KEY = "key sk-ant-api03-abcdefghijklmnopqrstu\n"

# A stand-in for claude_agent_sdk: the adapter builds its real options and hooks, and a prompt "TOOL <name> <json>"
# runs the PreToolUse hooks of those options, so the sandbox wall answers as it does in a real session. No spend.
STUB_SDK = '''
import json
from dataclasses import dataclass, field


@dataclass
class TextBlock:
    text: str


@dataclass
class AssistantMessage:
    content: list
    model: str = "stub"
    error: object = None


class ClaudeAgentOptions:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@dataclass
class HookMatcher:
    matcher: object = None
    hooks: list = field(default_factory=list)
    timeout: object = None


async def query(*, prompt, options):
    if prompt.startswith("TOOL "):
        name, _, raw = prompt[5:].partition(" ")
        out = {}
        for m in options.hooks["PreToolUse"]:
            if m.matcher is None or name in m.matcher.split("|"):
                for h in m.hooks:
                    out = await h({"hook_event_name": "PreToolUse", "tool_name": name, "tool_input": json.loads(raw)}, "tu-1", {"signal": None}) or out
        reason = (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason")
        texts = [reason or "allowed"]
    else:
        texts = ["echo:" + prompt]
    yield AssistantMessage(content=[TextBlock(text=t) for t in texts])
'''

HUB_START = """
import sys, time
from pathlib import Path
from squad import supervisor as sv
sid = sv.call(Path(sys.argv[1]), "start", job="echo-hub")["session"]
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


@pytest.fixture(autouse=True)
def stub_sdk(tmp_path, monkeypatch):
    folder = tmp_path / "sdk"
    folder.mkdir()
    (folder / "claude_agent_sdk.py").write_text(STUB_SDK)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(folder), str(ROOT)]))


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


def pythonpath_env():
    return dict(os.environ)


def wait_for_text(state, session, needle, timeout=10.0):
    deadline = time.time() + timeout
    data = ""
    while time.time() < deadline:
        data = sv.call(state, "read", session=session, offset=0)["data"]
        if needle in data:
            return data
        time.sleep(0.05)
    raise AssertionError(f"{needle!r} not seen; output was {data!r}")


def wait_for_file(path, needle, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and needle in path.read_text():
            return path.read_text()
        time.sleep(0.05)
    raise AssertionError(f"{needle!r} not written to {path}")


def hosted(state, job, cwd=None):
    return sv.call(state, "start", job=job, cwd=cwd)["session"]


def test_hosted_session_answers_a_line_through_attach(state):
    sv.ensure(state)
    session = hosted(state, "echo-1")
    sv.call(state, "write", session=session, text="hello\n")
    wait_for_text(state, session, "echo:hello")


def test_hook_of_the_adapter_refuses_a_tool_call_in_a_hosted_session(state, tmp_path):
    sv.ensure(state)
    session = hosted(state, "p#20", cwd=str(tmp_path))
    sv.call(state, "write", session=session, text='TOOL Bash {"command": "cat ~/.claude/settings.json"}\n')
    data = wait_for_text(state, session, "sandbox:")
    assert "a command that names ~/.claude is denied" in data


def test_daemon_outlives_the_hub_and_the_same_program_answers(state):
    sv.ensure(state)
    hub = subprocess.Popen(
        [sys.executable, "-c", HUB_START, str(state)],
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


def test_daemon_answers_no_input_for_a_session_it_did_not_start(state):
    sv.ensure(state)
    with pytest.raises(sv.Refused):
        sv.call(state, "write", session="no-such-session", text="x\n")

    session = hosted(state, "echo-4")
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


def test_start_writes_one_record_with_identity_process_terminal_and_cwd(state, tmp_path):
    sid = sv.Supervisor(state).start("p#7", str(tmp_path))
    [start] = [e for e in events(state) if e["event"] == "start"]
    assert start["session"] == sid
    assert start["job"] == "p#7"
    assert start["lstart"] == sv.lstart(start["pid"])
    assert start["terminal"].startswith("/dev/")
    assert start["cwd"] == str(tmp_path)
    assert "squad.sdk_adapter" in start["argv"]


def test_attach_transport_is_recorded_once_per_session(state):
    sv.ensure(state)
    session = hosted(state, "p#11")
    sv.call(state, "read", session=session, offset=0)
    sv.call(state, "read", session=session, offset=0)
    attaches = [(e["session"], e["transport"]) for e in events(state) if e["event"] == "attach"]
    assert attaches == [(session, "unix")]


def test_live_record_survives_a_restart_and_keeps_its_job(state, sleeper):
    write_start(state, session="old-1", job="p#9", pid=sleeper.pid, lstart=sv.lstart(sleeper.pid))
    restarted = sv.Supervisor(state)
    assert end_reasons(state, "old-1") == []
    with pytest.raises(sv.Refused, match="old-1"):
        restarted.start("p#9", None)


def test_record_of_a_dead_process_is_ended_lost_and_frees_its_job(state, dead_pid):
    write_start(state, session="old-2", job="p#10", pid=dead_pid, lstart="Mon Jan  1 00:00:00 1990")
    restarted = sv.Supervisor(state)
    assert end_reasons(state, "old-2") == ["lost"]
    assert restarted.start("p#10", None) != "old-2"


def test_record_with_a_reused_pid_is_ended_lost(state, sleeper):
    write_start(state, session="old-3", job="p#12", pid=sleeper.pid, lstart="Mon Jan  1 00:00:00 1990")
    sv.Supervisor(state)
    assert end_reasons(state, "old-3") == ["lost"]


def test_second_start_for_a_live_job_is_refused_naming_the_live_session(state):
    supervisor = sv.Supervisor(state)
    first = supervisor.start("p#8", None)
    with pytest.raises(sv.Refused, match=first):
        supervisor.start("p#8", None)


def test_owner_grant_naming_both_sessions_admits_a_second_start(state):
    supervisor = sv.Supervisor(state)
    first = supervisor.start("p#13", None)
    with pytest.raises(sv.Refused, match=first):
        supervisor.start("p#13", None, session="second-13")
    (state / "grants.json").write_text(json.dumps([{"replaces": first, "with": "second-13"}]))
    assert supervisor.start("p#13", None, session="second-13") == "second-13"


def test_grant_naming_another_session_admits_nothing(state):
    supervisor = sv.Supervisor(state)
    first = supervisor.start("p#14", None)
    (state / "grants.json").write_text(json.dumps([{"replaces": "someone-else", "with": "second-14"}]))
    with pytest.raises(sv.Refused, match=first):
        supervisor.start("p#14", None, session="second-14")


@pytest.mark.parametrize("job", ["", "p#", "p#0", "#7", "p #1", "p/x", "p#1#2", "p\n", "a" * 65])
def test_malformed_job_name_is_refused_before_anything_starts(state, job):
    with pytest.raises(sv.Refused):
        sv.Supervisor(state).start(job, None)
    assert events(state) == []


@pytest.mark.parametrize("job", ["bihaiko#1094", "label-x", "p.q_r", "a" * 64])
def test_well_formed_job_name_is_accepted(job):
    assert sv.valid_job(job)


@pytest.mark.parametrize("session", ["../escape", "a/b", ".hidden", "x" * 65])
def test_session_name_that_is_not_a_file_name_is_refused(state, session):
    with pytest.raises(sv.Refused, match="session name"):
        sv.Supervisor(state).start("p#18", None, session=session)
    assert events(state) == []


def test_bang_command_outside_the_working_directory_is_refused_in_a_hosted_session(state, tmp_path):
    sv.ensure(state)
    session = hosted(state, "p#15", cwd=str(tmp_path))
    with pytest.raises(sv.Refused, match="outside"):
        sv.call(state, "write", session=session, text="!cat /etc/hosts\n")


def test_bang_command_the_sandbox_denies_is_refused_in_a_hosted_session(state, tmp_path):
    sv.ensure(state)
    session = hosted(state, "p#16", cwd=str(tmp_path))
    with pytest.raises(sv.Refused, match="sandbox"):
        sv.call(state, "write", session=session, text="!cat ~/.claude/settings.json\n")


def test_bang_command_inside_the_working_directory_runs(state, tmp_path):
    sv.ensure(state)
    session = hosted(state, "p#17", cwd=str(tmp_path))
    sv.call(state, "write", session=session, text="!echo done-$((6*7))\n")
    wait_for_text(state, session, "done-42")


def test_transcript_is_written_redacted(state):
    sv.ensure(state)
    session = hosted(state, "p#21")
    sv.call(state, "write", session=session, text=TRANSCRIPT_KEY)
    wait_for_text(state, session, MARK)
    sv.call(state, "stop", session=session)
    text = wait_for_file(state / "transcripts" / f"{session}.txt", MARK)
    assert "sk-ant-api03" not in text


def test_scrollback_read_is_redacted_on_replay(state):
    sv.ensure(state)
    session = hosted(state, "p#22")
    sv.call(state, "write", session=session, text=TRANSCRIPT_KEY)
    live = wait_for_text(state, session, MARK)
    replay = sv.call(state, "read", session=session, offset=0)["data"]
    assert "sk-ant-api03" not in live
    assert "sk-ant-api03" not in replay
    assert MARK in replay


def test_scrollback_is_dropped_when_the_session_ends(state):
    sv.ensure(state)
    session = hosted(state, "p#23")
    sv.call(state, "write", session=session, text="hello\n")
    wait_for_text(state, session, "echo:hello")
    sv.call(state, "stop", session=session)
    deadline = time.time() + 10
    while time.time() < deadline and '"event": "end"' not in (state / "sessions.jsonl").read_text():
        time.sleep(0.05)
    assert sv.call(state, "read", session=session, offset=0)["data"] == ""


def test_hub_module_holds_no_pty_or_fork_on_the_job_path():
    source = (ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py").read_text()
    for token in ("openpty", "forkpty", "os.fork(", "pty.spawn"):
        assert token not in source, token
    popen_lines = [line.strip() for line in source.splitlines() if "Popen(" in line]
    assert popen_lines == ['pr = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)']
