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
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MARKER = "MARKER-7f3c9a"
FIRST_SESSION = "sess-first-0001"


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
