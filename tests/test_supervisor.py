import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from squad import supervisor as sv
from squad.redact import MARK

ROOT = Path(__file__).resolve().parents[1]

# A hosted session is a live process the supervisor starts, reads and reaps through the process table.
pytestmark = pytest.mark.process_bound

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


# A stand-in SDK that answers each prompt with the loop it ran on and the version-probe switch it read, so a test reads a
# hosted session's loop and its environment from the session itself. No spend.
HOST_PROBE_SDK = '''
import asyncio
import os
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
    skip = os.environ.get("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "unset")
    yield AssistantMessage(content=[TextBlock(text=f"{prompt} loop={id(asyncio.get_running_loop())} skip={skip}")])
'''


def host_probe(state, tmp_path, monkeypatch, sdk_text, job):
    """A hosted session on the real adapter, whose SDK is `sdk_text`: the loop and the environment come from the session."""
    folder = tmp_path / "probe-sdk"
    folder.mkdir()
    (folder / "claude_agent_sdk.py").write_text(sdk_text)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(folder), str(ROOT)]))
    supervisor = sv.Supervisor(state)
    return supervisor, supervisor.start(job, str(tmp_path))


def read_until(supervisor, sid, needle, timeout=10.0):
    deadline = time.time() + timeout
    data = ""
    while time.time() < deadline:
        data = supervisor.read(sid, 0)["data"]
        if needle in data:
            return data
        time.sleep(0.05)
    raise AssertionError(f"{needle!r} not seen; output was {data!r}")


def test_a_hosted_session_serves_every_prompt_on_one_event_loop(state, tmp_path, monkeypatch):
    """#1320: the hosted process made a new event loop per prompt, so an SDK child of one prompt outlived the loop that
    watched it, and a later prompt's child read as reaped behind the SDK (`Unknown child process pid N, will report
    returncode 255`). One loop serves every prompt of the session."""
    supervisor, sid = host_probe(state, tmp_path, monkeypatch, HOST_PROBE_SDK, "p#50")
    loops = []
    for prompt in ("first", "second"):
        supervisor.write(sid, prompt + "\n", sv.Caller())
        found = re.search(rf"{prompt} loop=(\d+)", read_until(supervisor, sid, f"{prompt} loop="))
        loops.append(found.group(1))
    assert loops[0] == loops[1], f"each prompt ran on its own loop: {loops}"


def test_the_sdk_version_probe_is_switched_off_in_a_hosted_session(state, tmp_path, monkeypatch):
    """#1320: the SDK spawns and reaps a `claude -v` child before every prompt, a second child the hosted session never
    asked for and one more `waitpid` on the CLI family. The hosted environment switches the probe off; the session owns
    one child, its CLI."""
    supervisor, sid = host_probe(state, tmp_path, monkeypatch, HOST_PROBE_SDK, "p#51")
    supervisor.write(sid, "first\n", sv.Caller())
    assert "skip=1" in read_until(supervisor, sid, "first loop=")


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


def test_socket_folder_is_read_from_the_environment(tmp_path, monkeypatch, state):
    folder = tmp_path / "supervisor"
    monkeypatch.setenv("SQUAD_SUPERVISOR_DIR", str(folder))
    assert sv.socket_path(state).parent == folder


def test_start_command_is_read_from_the_environment_and_names_the_state_folder(tmp_path, monkeypatch, state):
    monkeypatch.setenv("SQUAD_SUPERVISOR_CMD", "my-supervisor serve {state}")
    assert sv.start_argv(state) == ["my-supervisor", "serve", str(state)]


def test_default_start_command_runs_the_module(state):
    assert sv.start_argv(state)[:4] == [sys.executable, "-m", "squad.supervisor", "serve"]
    assert sv.start_argv(state)[4] == str(state)


def test_a_refused_start_command_names_its_reason(tmp_path, monkeypatch, state):
    folder = Path(tempfile.mkdtemp(prefix="sq1160-", dir="/tmp"))
    monkeypatch.setenv("SQUAD_SUPERVISOR_DIR", str(folder))
    monkeypatch.setenv("SQUAD_SUPERVISOR_CMD", "/nonexistent/supervisor {state}")
    try:
        with pytest.raises(FileNotFoundError, match="nonexistent"):
            sv.ensure(state)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


@pytest.mark.heavy
@pytest.mark.slow(reason="starts a real supervisor daemon")
def test_ensure_starts_the_daemon_in_the_environment_folder_and_it_answers(tmp_path, monkeypatch, state):
    folder = Path(tempfile.mkdtemp(prefix="sq1160-", dir="/tmp"))
    monkeypatch.setenv("SQUAD_SUPERVISOR_DIR", str(folder))
    try:
        sv.ensure(state)
        assert sv.socket_path(state).parent == folder
        assert sv.call(state, "ping") == {"ok": True}
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        monkeypatch.delenv("SQUAD_SUPERVISOR_DIR")


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
    # The in-memory window is dropped at the end; a read then serves the redacted transcript and names the end (#1318).
    out = sv.call(state, "read", session=session, offset=0)
    assert out["ended"] is True and out["data"] == (state / "transcripts" / f"{session}.txt").read_text()[-4000:]


def test_hub_module_holds_no_pty_or_fork_on_the_job_path():
    source = (ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py").read_text()
    for token in ("openpty", "forkpty", "os.fork(", "pty.spawn"):
        assert token not in source, token
    popen_lines = [line.strip() for line in source.splitlines() if "Popen(" in line]
    assert popen_lines == ['pr = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)']


TYPED = "SECRET-TYPED-TEXT"
AUDIT_KEYS = {"ts", "event", "job", "session", "login", "transport", "client_id", "input_bytes"}


def audit_lines(state):
    path = state / "terminal-audit.log"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def wait_for_audit(state, event, timeout=10.0):
    deadline = time.time() + timeout
    while not any(line["event"] == event for line in audit_lines(state)):
        assert time.time() < deadline, f"no {event} line"
        time.sleep(0.05)


def test_each_of_the_five_events_writes_one_audit_line_and_no_input_text(state):
    sv.ensure(state)
    who = {"login": "ana@example.com", "client_id": "c1", "transport": "tailnet"}
    session = sv.call(state, "start", job="p#30", **who)["session"]
    sv.call(state, "read", session=session, offset=0, **who)
    sv.call(state, "write", session=session, text=TYPED, **who)
    sv.call(state, "detach", session=session, **who)
    sv.call(state, "stop", session=session, **who)
    wait_for_audit(state, "end")
    lines = audit_lines(state)
    assert [line["event"] for line in lines] == ["start", "attach", "detach", "stop", "end"]
    for line in lines:
        assert set(line) == AUDIT_KEYS
        assert line["job"] == "p#30" and line["session"] == session
    typed = len(TYPED.encode())
    assert [line["input_bytes"] for line in lines] == [0, 0, typed, 0, 0]
    for line in lines[:4]:
        assert (line["login"], line["client_id"], line["transport"]) == ("ana@example.com", "c1", "tailnet")
    assert TYPED not in (state / "terminal-audit.log").read_text()


def test_a_refused_request_writes_one_refused_audit_line(state):
    sv.ensure(state)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(sv.socket_path(state)))
        client.sendall(json.dumps({"secret": "00" * 32, "op": "ping"}).encode() + b"\n")
        client.makefile("rb").readline()
    assert [line["event"] for line in audit_lines(state)] == ["refused"]


def test_attach_op_writes_one_attach_line_and_a_second_attach_writes_none(state):
    sv.ensure(state)
    who = {"login": "ana@example.com", "client_id": "c2", "transport": "tailnet"}
    session = sv.call(state, "start", job="p#31", **who)["session"]
    sv.call(state, "attach", session=session, **who)
    sv.call(state, "attach", session=session, **who)
    assert [line["event"] for line in audit_lines(state)] == ["start", "attach"]


def test_a_start_whose_process_cannot_spawn_answers_a_refusal_not_a_dead_socket(tmp_path, monkeypatch):
    """#1308: an exception `dispatch` does not expect -- the live shape was `Popen('')` raising FileNotFoundError -- escaped
    the handler thread, which closed the socket with no answer. The caller then read an empty line and `call` raised
    JSONDecodeError inside the hub's request handler, so the browser got no status at all. The supervisor answers a refusal
    that names the failure instead."""
    import threading
    state = tmp_path / "state"
    state.mkdir()
    sup = sv.Supervisor(state)
    srv = sv._Server(str(sv.socket_path(state)), sup)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        (state / sv.SECRET).write_text(sup.secret, encoding="utf-8")

        def boom(*a, **k):
            raise FileNotFoundError(2, "No such file or directory", "")

        monkeypatch.setattr(sv.subprocess, "Popen", boom)
        with pytest.raises(sv.Refused) as err:
            sv.call(state, "start", job="e2e", cwd=str(tmp_path), client_id="c1")
        assert "could not do 'start'" in str(err.value), str(err.value)
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_hosted_session_runs_on_the_repository_venv_when_it_has_one(tmp_path):
    """#1317: the supervisor ran under a python with no claude_agent_sdk, and the hosted session died at import, so the web
    terminal never answered. The repository's own venv is the interpreter that carries the SDK."""
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("")
    assert sv.hosted_argv(tmp_path) == [str(venv_python), "-m", "squad.sdk_adapter", "host"]
    assert sv.hosted_argv(tmp_path / "none")[0] == sys.executable


def test_a_start_whose_interpreter_cannot_import_the_sdk_is_refused_naming_both(state, tmp_path, monkeypatch):
    """#1317: a session that cannot import the SDK is refused at start, naming the interpreter and the module, instead of
    a live-looking session that never answers."""
    folder = tmp_path / "nosdk"
    folder.mkdir()
    (folder / "claude_agent_sdk.py").write_text("raise ImportError('no sdk here')\n")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(folder), str(ROOT)]))
    with pytest.raises(sv.Refused) as err:
        sv.Supervisor(state).start("p#31", str(tmp_path))
    assert sv.hosted_argv()[0] in str(err.value) and "claude_agent_sdk" in str(err.value), str(err.value)
    assert not (state / sv.LEDGER).exists() or '"start"' not in (state / sv.LEDGER).read_text()
def test_a_read_of_an_ended_session_names_the_end_its_return_code_and_its_last_output(state, tmp_path, monkeypatch):
    """#1318: a session that exited answered reads as a live, silent one, so the panel showed `Working` for a dead
    process. A read after the end says it ended, with the return code and the transcript tail, and a write refuses."""
    monkeypatch.setattr(sv, "hosted_argv", lambda root=sv.ROOT: [sys.executable, "-c", "print('boom at import'); raise SystemExit(3)"])
    sup = sv.Supervisor(state)
    sid = sup.start("p#41", str(tmp_path))
    deadline = time.time() + 10
    out = sup.read(sid, 0)
    while not out.get("ended") and time.time() < deadline:
        time.sleep(0.05)
        out = sup.read(sid, 0)
    assert out["ended"] is True and out["returncode"] == 3, out
    assert "boom at import" in out["data"], out
    with pytest.raises(sv.Refused, match="session ended"):
        sup.write(sid, "hello\n", sv.Caller())


def test_a_read_of_a_live_session_says_it_has_not_ended(state, tmp_path, monkeypatch):
    monkeypatch.setattr(sv, "hosted_argv", lambda root=sv.ROOT: [sys.executable, "-c", "import time; time.sleep(30)"])
    sup = sv.Supervisor(state)
    sid = sup.start("p#42", str(tmp_path))
    try:
        assert sup.read(sid, 0)["ended"] is False
    finally:
        sup.stop(sid, sv.Caller())
