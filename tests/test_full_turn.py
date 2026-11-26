"""One owner message as one user turn of the project's own Claude session (ADR-0180).

The CLI is a stand-in script that records its argv, working folder, stdin and config home, then prints one JSON result.
Guards: the first turn creates the session and names no `--resume`; a later turn resumes it by id; the turn carries no
tool bound, no MCP bound and no operator brief; the message reaches the CLI on stdin only; the owner's environment drops
every operator override; the state folder keeps each project's session id; a failed turn never raises; the audit row
holds no message and no answer.
"""
import importlib.util
import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MARKER = "MARKER-7f3c9a"
FIRST_SESSION = "sess-first-0001"
REPO = "owner/project"


def fullturn():
    spec = importlib.util.spec_from_file_location("fullturn", ROOT / "contrib" / "voice" / "fullturn.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STUB = """#!{python}
import json, os, sys
argv = sys.argv[1:]
stdin = sys.stdin.read()
record = {{"argv": argv, "cwd": os.path.realpath(os.getcwd()), "stdin": stdin,
          "config": os.environ.get("CLAUDE_CONFIG_DIR"), "tmp": os.environ.get("CLAUDE_CODE_TMPDIR")}}
with open(os.environ["STUB_RECORD"], "a", encoding="utf-8") as f:
    f.write(json.dumps(record) + "\\n")
if os.environ.get("STUB_FAIL"):
    sys.stderr.write("stand-in failure\\n")
    sys.exit(int(os.environ["STUB_FAIL"]))
session = argv[argv.index("--resume") + 1] if "--resume" in argv else os.environ.get("STUB_SESSION", "{first}")
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
                  "result": "answer to: " + stdin.strip(), "session_id": session}}))
"""


def build_stand_in(tmp_path, monkeypatch):
    path = tmp_path / "claude-stand-in"
    path.write_text(STUB.format(python=sys.executable, first=FIRST_SESSION))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    record = tmp_path / "record.jsonl"
    monkeypatch.setenv("STUB_RECORD", str(record))
    monkeypatch.delenv("STUB_FAIL", raising=False)
    monkeypatch.delenv("STUB_SESSION", raising=False)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    return {"claude": str(path), "checkout": str(checkout), "state": str(state), "record": record}


@pytest.fixture
def stand_in(tmp_path, monkeypatch):
    return build_stand_in(tmp_path, monkeypatch)


def calls(stand):
    if not stand["record"].exists():
        return []
    return [json.loads(line) for line in stand["record"].read_text().splitlines() if line.strip()]


def turn(mod, stand, message, repo="owner/project", environ=None):
    return mod.run_turn(stand["claude"], stand["checkout"], stand["state"], repo, message,
                        environ=environ or dict(os.environ))


def test_the_first_turn_asks_for_the_json_output_and_names_no_resume(stand_in):
    mod = fullturn()
    turn(mod, stand_in, "hello")
    argv = calls(stand_in)[0]["argv"]
    assert "-p" in argv, argv
    assert "--output-format" in argv and argv[argv.index("--output-format") + 1] == "json", argv
    assert "--resume" not in argv, argv


def test_a_later_turn_resumes_the_session_by_id(stand_in):
    mod = fullturn()
    turn(mod, stand_in, "first")
    turn(mod, stand_in, "second")
    later = calls(stand_in)[1]["argv"]
    assert later[later.index("--resume") + 1] == FIRST_SESSION, later


def test_the_full_command_carries_no_tool_bound_and_no_operator_brief(stand_in):
    mod = fullturn()
    turn(mod, stand_in, "hello")
    argv = calls(stand_in)[0]["argv"]
    for flag in ("--tools", "--allowedTools", "--strict-mcp-config", "--mcp-config", "--append-system-prompt",
                 "--system-prompt", "--permission-mode", "--settings"):
        assert flag not in argv, (flag, argv)


def test_the_message_reaches_the_cli_on_stdin_and_never_as_an_argument(stand_in):
    mod = fullturn()
    turn(mod, stand_in, f"please check {MARKER}")
    call = calls(stand_in)[0]
    assert MARKER in call["stdin"]
    assert not any(MARKER in arg for arg in call["argv"]), call["argv"]


def test_the_turn_runs_in_the_project_checkout(stand_in):
    mod = fullturn()
    turn(mod, stand_in, "hello")
    assert calls(stand_in)[0]["cwd"] == os.path.realpath(stand_in["checkout"])


def test_the_first_turn_returns_the_session_the_cli_printed_and_the_final_text(stand_in):
    mod = fullturn()
    result = turn(mod, stand_in, "hello")
    assert result.session == FIRST_SESSION
    assert result.text == "answer to: hello"
    assert result.exit == 0 and not result.failed


def test_the_state_folder_keeps_the_session_id_of_each_project(stand_in, monkeypatch):
    mod = fullturn()
    turn(mod, stand_in, "one project", repo="owner/one")
    monkeypatch.setenv("STUB_SESSION", "sess-second-0002")
    turn(mod, stand_in, "other project", repo="owner/two")
    saved = json.loads((Path(stand_in["state"]) / "full-sessions.json").read_text())
    assert saved == {"owner/one": FIRST_SESSION, "owner/two": "sess-second-0002"}


def test_the_owner_environment_drops_every_operator_override(stand_in):
    mod = fullturn()
    environ = {**os.environ, "CLAUDE_CONFIG_DIR": "/operator/root", "CLAUDE_CODE_TMPDIR": "/operator/tmp",
               "CLAUDE_SECURESTORAGE_CONFIG_DIR": ""}
    turn(mod, stand_in, "hello", environ=environ)
    call = calls(stand_in)[0]
    assert call["config"] is None and call["tmp"] is None, call


def test_a_failed_turn_never_raises_and_keeps_its_exit_code(stand_in, monkeypatch):
    mod = fullturn()
    monkeypatch.setenv("STUB_FAIL", "3")
    result = turn(mod, stand_in, "hello")
    assert result.exit == 3
    assert result.failed is True
    assert result.text == ""


def test_the_audit_row_holds_no_message_and_no_answer(stand_in):
    mod = fullturn()
    result = turn(mod, stand_in, f"secret-ish {MARKER}")
    row = json.dumps(result.audit())
    assert MARKER not in row
    assert "answer to" not in row
    assert result.session in row


def script(tmp_path, body):
    path = tmp_path / "claude-script"
    path.write_text(f"#!{sys.executable}\nimport json, os, sys, time\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def test_the_default_deadline_is_thirty_minutes():
    assert fullturn().TURN_SECONDS == 30 * 60


def test_the_default_spend_cap_is_five_dollars_on_every_turn(stand_in):
    mod = fullturn()
    turn(mod, stand_in, "hello")
    argv = calls(stand_in)[0]["argv"]
    assert argv[argv.index("--max-budget-usd") + 1] == "5", argv


def test_a_turn_past_its_deadline_stops_and_names_the_deadline(tmp_path, monkeypatch):
    stand = build_stand_in(tmp_path, monkeypatch)
    claude = script(tmp_path, "time.sleep(30)\n")
    started = time.monotonic()
    result = fullturn().run_turn(claude, stand["checkout"], stand["state"], REPO, "hello", timeout=1)
    assert time.monotonic() - started < 10
    assert result.failed is True and result.reason == "deadline", result


def test_a_turn_past_its_deadline_ends_its_whole_process_tree(tmp_path, monkeypatch):
    stand = build_stand_in(tmp_path, monkeypatch)
    pidfile = tmp_path / "child.pid"
    claude = script(tmp_path, "import subprocess\n"
                    "child = subprocess.Popen(['sleep', '30'], stdin=subprocess.DEVNULL,"
                    " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                    f"open({str(pidfile)!r}, 'w').write(str(child.pid))\n"
                    "time.sleep(30)\n")
    fullturn().run_turn(claude, stand["checkout"], stand["state"], REPO, "hello", timeout=1)
    child = int(pidfile.read_text())
    try:
        for _ in range(40):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        raise AssertionError(f"child {child} still runs after its turn ended")
    finally:
        try:
            os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_end_tree_signals_the_group_and_collects_the_process():
    """`end_tree` is the blocking primitive: it signals the whole group and collects the process, so a caller that
    holds no reader gets the process reaped and holds no zombie behind (#2968 review)."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    try:
        fullturn().end_tree(proc)
        assert proc.returncode is not None, "end_tree returned before the process was collected"
        assert proc.poll() is not None, proc.poll()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_a_stop_ends_only_the_turn_of_the_requester_who_started_it():
    """The registry compares the identity that started the turn, so a full role handed to anyone else ends nothing
    (#2968 review)."""
    mod = fullturn()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    registry = mod.TurnRegistry()
    registry.hold(REPO, proc, "U-the-owner")
    try:
        assert registry.stop(REPO, "U-someone-else") is False
        assert proc.poll() is None, "another identity ended the turn it never started"
        assert registry.stop(REPO, "U-the-owner") is True
        assert proc.wait(timeout=10) < 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_a_turn_that_hits_the_spend_cap_names_the_spend_cap(tmp_path, monkeypatch):
    stand = build_stand_in(tmp_path, monkeypatch)
    claude = script(tmp_path, "print(json.dumps({'type': 'result', 'subtype': 'error_max_budget_usd',"
                    " 'is_error': True, 'session_id': 'sess-cap-0003'}))\n")
    result = fullturn().run_turn(claude, stand["checkout"], stand["state"], REPO, "hello")
    assert result.failed is True and result.reason == "spend_cap", result


def test_turns_of_one_project_run_in_the_order_they_arrived():
    mod = fullturn()
    order = mod.TurnOrder()
    tickets = [order.arrive(REPO) for _ in range(3)]
    ran = []

    def run(ticket):
        order.wait(REPO, ticket)
        ran.append(ticket)
        order.leave(REPO)

    threads = [threading.Thread(target=run, args=(ticket,)) for ticket in reversed(tickets)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert ran == tickets, ran


def test_a_live_claude_process_resuming_the_session_is_its_holder():
    """Criterion (1): the holder is a live `claude` whose arguments resume or name the project's session id."""
    rows = [(10, ["/usr/local/bin/claude", "--resume", "sess-held"]),
            (11, ["/usr/local/bin/node", "/opt/claude-code/cli.js", "--resume", "sess-held"]),
            (12, ["/usr/local/bin/claude", "--resume", "sess-other"]),
            (13, ["/usr/local/bin/claude", "-p"])]
    assert fullturn().holder_of(rows, "sess-held") == 10


def test_a_session_argument_of_another_program_is_no_holder():
    """Only the CLI writes a conversation: another program that carries the same words holds no session."""
    rows = [(20, ["/usr/bin/git", "--resume", "sess-held"]), (21, ["/bin/ps"])]
    assert fullturn().holder_of(rows, "sess-held") is None


def test_the_session_holder_reads_the_process_table_and_no_session_names_no_holder():
    """Criterion (1): `session_holder` finds the holder through the machine's process table; an empty session names
    no holder."""
    mod = fullturn()
    rows = [(os.getpid(), ["/usr/local/bin/claude", "--resume", "sess-x"])]
    assert mod.session_holder("sess-x", lister=lambda: rows) == os.getpid()
    assert mod.session_holder("", lister=lambda: rows) is None


def test_a_holder_whose_session_id_sits_past_the_piped_width_is_still_found(tmp_path, monkeypatch):
    """Criterion (1) on Linux: a piped `ps` cuts each command line at 80 columns unless it is asked for full width,
    so the reading must ask for it or the session id past that column names no holder."""
    line = f"4321 /usr/local/bin/claude --note {'n' * 90} -p --resume sess-long-0001 --verbose"
    ps = tmp_path / "ps"
    ps.write_text(f"""#!{sys.executable}
import sys
line = {line!r}
wide = any("w" in arg for arg in sys.argv[1:] if arg.startswith("-") and not arg.startswith("--"))
print(line if wide else line[:80])
""")
    ps.chmod(ps.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    assert fullturn().session_holder("sess-long-0001") == 4321


def test_a_turn_is_refused_while_a_live_process_holds_the_session(stand_in):
    """Criterion (2): with a live holder, `run_turn` starts no CLI and names the refusal."""
    mod = fullturn()
    turn(mod, stand_in, "first")
    asked = []
    later = mod.run_turn(stand_in["claude"], stand_in["checkout"], stand_in["state"], REPO, "second",
                         holder=lambda session: asked.append(session) or 4321)
    assert asked == [FIRST_SESSION], asked
    assert later.failed is True and later.reason == "session_held", later
    assert len(calls(stand_in)) == 1, calls(stand_in)


def test_a_turn_runs_once_the_holder_is_gone(stand_in):
    """Criterion (3): with no holder, the turn runs and resumes the session."""
    mod = fullturn()
    turn(mod, stand_in, "first")
    later = mod.run_turn(stand_in["claude"], stand_in["checkout"], stand_in["state"], REPO, "second",
                         holder=lambda session: None)
    assert later.failed is False and later.session == FIRST_SESSION, later
    assert len(calls(stand_in)) == 2, calls(stand_in)
