"""Guard: issue #2949 (SPEC 2946). The operator session answers through the one shipped `answer` helper, and through
nothing else.

The helper writes one outbox line for a pending inbox line of the session's own project, and it refuses any other
channel or thread. The seam is the helper's own `answer` function and its `main` entry: one binding file, one inbox
line in, one outbox line out. The binding is the file the supervisor writes at a session start, so a case never reads
the owner's state. Every id is a neutral placeholder, so a case names no real workspace or channel.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "contrib" / "voice"

QUESTION = "1762500000.000100"
ANSWER = "the loop is green"
CHANNEL, OTHER = "C0AAAAAA1", "C0AAAAAA2"


def helper():
    """The shipped helper module, loaded from the tree under test."""
    spec = importlib.util.spec_from_file_location("answer", VOICE / "answer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def queued(tmp_path, channels=(CHANNEL, OTHER)):
    """A state folder with one inbox and one outbox per channel, and the binding file of one session."""
    state = tmp_path / "state"
    (state / "inboxes").mkdir(parents=True)
    (state / "outboxes").mkdir()
    binding = tmp_path / "binding.json"
    binding.write_text(json.dumps({"key": "acme-widget", "state": str(state), "channels": list(channels)}))
    return binding, state


def asked(state, channel=CHANNEL, ts=QUESTION, text="check the loop"):
    """One pending inbox line: the question an answer must thread under."""
    line = {"id": f"{channel}:{ts}", "ts": ts, "user": "UPROBE0001", "channel": channel, "text": text,
            "language": "en-US"}
    with open(state / "inboxes" / f"{channel}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def outbox(state, channel=CHANNEL):
    """Every line of one channel's outbox, in the order the helper wrote them."""
    path = state / "outboxes" / f"{channel}.jsonl"
    if not path.exists():
        return []
    return [json.loads(raw) for raw in path.read_text().splitlines() if raw.strip()]


def bound(binding):
    return {"VOICE_ANSWER_BINDING": str(binding)}


def run_cli(binding, *args):
    """The helper's own entry, as the session's Bash call runs it."""
    env = {**os.environ, **bound(binding)}
    return subprocess.run([sys.executable, str(VOICE / "answer.py"), *args], capture_output=True, text=True,
                          timeout=30, env=env, check=False)


def test_the_helper_writes_one_outbox_line_for_a_pending_inbox_line(tmp_path):
    """Criterion (1): the answer lands in the outbox of the line's channel, threaded under the question's ts."""
    mod = helper()
    binding, state = queued(tmp_path)
    asked(state)

    record = mod.answer(mod.read_binding(bound(binding)), CHANNEL, QUESTION, ANSWER)

    lines = outbox(state)
    assert lines == [{"thread_ts": QUESTION, "text": ANSWER}], lines
    assert record["thread_ts"] == QUESTION


def test_the_helper_writes_the_speech_and_persona_it_is_given(tmp_path):
    """Criterion (1), the optional fields: a voice summary and the channel persona travel in the same one line."""
    mod = helper()
    binding, state = queued(tmp_path)
    asked(state)

    mod.answer(mod.read_binding(bound(binding)), CHANNEL, QUESTION, ANSWER, speech="The loop is green.", persona="hal")

    assert outbox(state) == [{"thread_ts": QUESTION, "text": ANSWER, "speech": "The loop is green.", "persona": "hal"}]


def test_the_helper_refuses_a_channel_outside_the_sessions_project(tmp_path):
    """Criterion (2): a channel the session's project does not own writes nothing."""
    mod = helper()
    binding, state = queued(tmp_path)          # the project owns CHANNEL and OTHER, never a third
    asked(state, channel="C0AAAAAA9")

    with pytest.raises(mod.Refused) as raised:
        mod.answer(mod.read_binding(bound(binding)), "C0AAAAAA9", QUESTION, ANSWER)

    assert "outside this session's project" in str(raised.value)
    assert outbox(state, "C0AAAAAA9") == []


def test_the_helper_refuses_a_thread_that_matches_no_pending_inbox_line(tmp_path):
    """Criterion (3): a thread the channel's inbox never held answers nothing."""
    mod = helper()
    binding, state = queued(tmp_path)
    asked(state)

    with pytest.raises(mod.Refused) as raised:
        mod.answer(mod.read_binding(bound(binding)), CHANNEL, "1762500000.999999", ANSWER)

    assert "no pending inbox line" in str(raised.value)
    assert outbox(state) == []


def test_the_helper_refuses_a_channel_whose_inbox_holds_no_line(tmp_path):
    """Criterion (3), the empty channel: an own channel with no question has nothing to answer."""
    mod = helper()
    binding, state = queued(tmp_path)

    with pytest.raises(mod.Refused):
        mod.answer(mod.read_binding(bound(binding)), OTHER, QUESTION, ANSWER)

    assert outbox(state, OTHER) == []


def test_the_helper_needs_the_session_binding(tmp_path):
    """No binding, no answer: the helper runs only inside an operator session, so a bare shell cannot post."""
    mod = helper()
    with pytest.raises(mod.Refused):
        mod.read_binding({})


def test_the_helper_reuses_the_policy_modules_binding_rule(tmp_path, monkeypatch):
    """Criterion (4): the rule that binds an answer to an inbox line is the policy module's, never a second copy."""
    mod = helper()
    binding, state = queued(tmp_path)
    asked(state)
    seen = {}
    original = mod.slackpolicy.pending_line

    def spy(inbox, thread):
        seen["called"] = (list(inbox), thread)
        return original(inbox, thread)

    monkeypatch.setattr(mod.slackpolicy, "pending_line", spy)
    mod.answer(mod.read_binding(bound(binding)), CHANNEL, QUESTION, ANSWER)

    assert seen["called"][1] == QUESTION and seen["called"][0][0]["ts"] == QUESTION, seen


def test_the_dm_answer_carries_the_inbox_lines_channel(tmp_path):
    """The DM outbox line names the channel, because the daemon reads it from the line alone."""
    mod = helper()
    binding, state = queued(tmp_path, channels=("dm",))
    asked(state, channel="dm", text="hi")
    line = {"id": "dm:1", "ts": QUESTION, "user": "UPROBE0001", "channel": "D0AAAAAA1", "text": "hi"}
    with open(state / "inboxes" / "dm.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")

    mod.answer(mod.read_binding(bound(binding)), "dm", QUESTION, ANSWER)

    assert outbox(state, "dm") == [{"thread_ts": QUESTION, "text": ANSWER, "channel": "D0AAAAAA1"}]


def test_the_entry_writes_the_line_and_exits_zero(tmp_path):
    """Criterion (1) through the helper's own command line: the line lands and the call exits 0."""
    binding, state = queued(tmp_path)
    asked(state)

    done = run_cli(binding, "--scope", CHANNEL, "--thread", QUESTION, "--text", ANSWER)

    assert done.returncode == 0, done.stderr
    assert outbox(state) == [{"thread_ts": QUESTION, "text": ANSWER}]


def test_the_entry_refuses_a_foreign_channel_and_exits_two(tmp_path):
    """Criterion (2) through the command line: the refusal names the channel and writes nothing."""
    binding, state = queued(tmp_path)
    asked(state, channel="C0AAAAAA9")

    done = run_cli(binding, "--scope", "C0AAAAAA9", "--thread", QUESTION, "--text", ANSWER)

    assert done.returncode == 2, done.stdout + done.stderr
    assert "outside this session's project" in done.stderr
    assert outbox(state, "C0AAAAAA9") == []


def test_the_entry_refuses_a_thread_with_no_pending_line_and_exits_two(tmp_path):
    """Criterion (3) through the command line: a stale thread writes nothing."""
    binding, state = queued(tmp_path)
    asked(state)

    done = run_cli(binding, "--scope", CHANNEL, "--thread", "1762500000.999999", "--text", ANSWER)

    assert done.returncode == 2, done.stdout + done.stderr
    assert "no pending inbox line" in done.stderr
    assert outbox(state) == []
