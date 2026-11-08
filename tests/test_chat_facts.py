"""The chat's server facts (#1601): the transcript's mtime is the session's real last activity, and a session whose
checkout sits in a run's folder names that run. The phone's chat shows the time per project and links the run that acted
on the answer; a session outside a run folder shows no link.

The listing itself is proved beside the live registry in `tests/test_sessions.py`, a process-bound file this runner
deselects (ADR-0139). These helpers carry no process table, so the rules run everywhere.
"""
import importlib.util
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"


def load_sessions():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("sessions_chat_facts", SCRIPTS / "sessions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def transcript(home, cwd, sid, text="the last word"):
    folder = home / ".claude" / "projects" / "".join(c if c.isalnum() else "-" for c in str(cwd))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    path.write_text("\n".join(json.dumps(t) for t in (
        {"type": "user", "message": {"role": "user", "content": text}},
    )) + "\n")
    os.utime(path, (1_700_000_000, 1_700_000_000))
    return path


def test_the_last_turn_carries_the_transcript_time(tmp_path, monkeypatch):
    """#1601, owner: the transcript's mtime is the time the chat row shows, never the session's start."""
    monkeypatch.setenv("HOME", str(tmp_path))
    mod = load_sessions()
    cwd = tmp_path / "app"
    transcript(tmp_path, cwd, "s1")
    text, at = mod.read_last_turn(str(cwd), "s1")
    assert text == "the last word"
    assert at == 1_700_000_000


def test_a_session_under_a_run_folder_names_its_run():
    """`list_sessions` gains the run id from the session's checkout, so the phone links the run only when it has one."""
    mod = load_sessions()
    assert mod.run_id_of("/x/checkout/runs/run-20261015-191451-9f6b30/ws/node1") == "run-20261015-191451-9f6b30"


def test_a_session_outside_a_run_folder_names_no_run():
    mod = load_sessions()
    assert mod.run_id_of("/x/checkout") == ""
    assert mod.run_id_of("/x/checkout/runs/not-a-run/ws/node1") == ""
