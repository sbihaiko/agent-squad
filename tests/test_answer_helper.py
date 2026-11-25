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


def queued(tmp_path, channels=(CHANNEL, OTHER), dm=None):
    """A state folder with one inbox and one outbox per channel, and the binding file of one session. A DM session's
    binding also names the one DM channel its answers may post to."""
    state = tmp_path / "state"
    (state / "inboxes").mkdir(parents=True)
    (state / "outboxes").mkdir()
    binding = tmp_path / "binding.json"
    data = {"key": "acme-widget", "state": str(state), "channels": list(channels)}
    if dm is not None:
        data["dm"] = dm
    binding.write_text(json.dumps(data))
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
    """Criterion (4): the rule that binds an answer to an inbox line is the policy module's, never a second copy. The
    helper writes the line the policy function returns, so a copy that reimplements the lookup is caught."""
    from squad import slackpolicy
    mod = helper()
    binding, state = queued(tmp_path)
    asked(state)
    assert mod.slackpolicy is slackpolicy, "the helper imports a second Slack policy module"
    sentinel = {"ts": "1770000000.000001", "channel": CHANNEL, "text": "the policy's own answer"}

    monkeypatch.setattr(mod.slackpolicy, "pending_line", lambda inbox, thread: sentinel)
    mod.answer(mod.read_binding(bound(binding)), CHANNEL, QUESTION, ANSWER)

    assert outbox(state) == [{"thread_ts": "1770000000.000001", "text": ANSWER}], \
        "the helper did not bind its answer through the policy module's own rule"


def test_the_dm_answer_carries_the_inbox_lines_channel(tmp_path):
    """The DM outbox line names the channel, because the daemon reads it from the line alone."""
    mod = helper()
    binding, state = queued(tmp_path, channels=("dm",), dm="D0AAAAAA1")
    asked(state, channel="dm", text="hi")
    line = {"id": "dm:1", "ts": QUESTION, "user": "UPROBE0001", "channel": "D0AAAAAA1", "text": "hi"}
    with open(state / "inboxes" / "dm.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")

    mod.answer(mod.read_binding(bound(binding)), "dm", QUESTION, ANSWER)

    assert outbox(state, "dm") == [{"thread_ts": QUESTION, "text": ANSWER, "channel": "D0AAAAAA1"}]


def test_the_helper_refuses_a_dm_line_that_names_a_project_channel(tmp_path):
    """Criterion (2), the DM: the outbox line carries the inbox line's channel, and the daemon posts there, so a dm
    line that names a project channel would post outside the session's project. The helper refuses it."""
    mod = helper()
    binding, state = queued(tmp_path, channels=("dm",), dm="D0AAAAAA1")
    line = {"id": f"dm:{QUESTION}", "ts": QUESTION, "user": "UPROBE0001", "channel": CHANNEL, "text": "hi"}
    with open(state / "inboxes" / "dm.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")

    with pytest.raises(mod.Refused):
        mod.answer(mod.read_binding(bound(binding)), "dm", QUESTION, ANSWER)

    assert outbox(state, "dm") == []


def test_the_dm_answer_reads_the_one_channel_when_the_binding_names_none(tmp_path):
    """A DM session started before the first DM arrived has no DM channel in its binding, so the helper reads the one
    channel of the inbox, which only the daemon writes."""
    mod = helper()
    binding, state = queued(tmp_path, channels=("dm",))
    for ts in (QUESTION, "1762500000.000200"):
        with open(state / "inboxes" / "dm.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": f"dm:{ts}", "ts": ts, "user": "UPROBE0001", "channel": "D0AAAAAA1",
                                "text": "hi"}) + "\n")

    mod.answer(mod.read_binding(bound(binding)), "dm", "1762500000.000200", ANSWER)

    assert outbox(state, "dm") == [{"thread_ts": "1762500000.000200", "text": ANSWER, "channel": "D0AAAAAA1"}]


def test_the_helper_refuses_a_project_line_that_names_another_channel(tmp_path):
    """Criterion (2), a project: a line in one channel's inbox that names a second channel answers nothing."""
    mod = helper()
    binding, state = queued(tmp_path)
    line = {"id": f"{CHANNEL}:{QUESTION}", "ts": QUESTION, "user": "UPROBE0001", "channel": OTHER, "text": "x"}
    with open(state / "inboxes" / f"{CHANNEL}.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")

    with pytest.raises(mod.Refused):
        mod.answer(mod.read_binding(bound(binding)), CHANNEL, QUESTION, ANSWER)

    assert outbox(state) == []


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


def test_the_dm_answer_answers_the_newest_line_when_the_thread_is_omitted(tmp_path):
    """The documented DM shorthand: with no --thread, the DM answers the newest pending line of its own channel."""
    binding, state = queued(tmp_path, channels=("dm",), dm="D0AAAAAA1")
    older = "1762500000.000100"
    newest = "1762500000.000900"
    with open(state / "inboxes" / "dm.jsonl", "w", encoding="utf-8") as f:
        for ts in (older, newest):
            f.write(json.dumps({"id": f"dm:{ts}", "ts": ts, "user": "UPROBE0001", "channel": "D0AAAAAA1",
                                "text": "hi"}) + "\n")

    done = run_cli(binding, "--scope", "dm", "--text", ANSWER)

    assert done.returncode == 0, done.stderr
    assert outbox(state, "dm") == [{"thread_ts": newest, "text": ANSWER, "channel": "D0AAAAAA1"}]


def test_an_unreadable_binding_is_refused_with_its_path(tmp_path):
    """A binding that exists but does not parse is a read failure, never an absent binding."""
    mod = helper()
    binding = tmp_path / "binding.json"
    binding.write_text('{"key": "acme-widget", "state": ')

    with pytest.raises(mod.Refused) as raised:
        mod.read_binding(bound(binding))

    assert str(binding) in str(raised.value), raised.value


def test_a_corrupt_inbox_line_is_refused_with_its_path(tmp_path):
    """A corrupt line in the middle of an inbox is a broken queue, never 'no pending inbox line'."""
    mod = helper()
    binding, state = queued(tmp_path)
    asked(state)
    with open(state / "inboxes" / f"{CHANNEL}.jsonl", "a", encoding="utf-8") as f:
        f.write("{not json\n")
        f.write(json.dumps({"id": f"{CHANNEL}:2", "ts": "1762500000.000200", "channel": CHANNEL, "text": "x"}) + "\n")

    with pytest.raises(mod.Refused) as raised:
        mod.answer(mod.read_binding(bound(binding)), CHANNEL, QUESTION, ANSWER)

    assert "inbox" in str(raised.value) and f"{CHANNEL}.jsonl" in str(raised.value), raised.value
    assert outbox(state) == []


def test_a_half_line_a_crash_left_at_the_end_is_skipped(tmp_path):
    """The last line may be a half write a crash left behind: the helper reads the whole lines and answers."""
    mod = helper()
    binding, state = queued(tmp_path)
    asked(state)
    with open(state / "inboxes" / f"{CHANNEL}.jsonl", "a", encoding="utf-8") as f:
        f.write('{"id": "torn"')

    mod.answer(mod.read_binding(bound(binding)), CHANNEL, QUESTION, ANSWER)

    assert outbox(state) == [{"thread_ts": QUESTION, "text": ANSWER}]


def test_the_helper_refuses_a_scope_that_escapes_the_state_folder(tmp_path):
    """A tampered binding cannot point the answer at a file outside the session's state folder."""
    mod = helper()
    binding, state = queued(tmp_path, channels=("../../leak",))
    line = {"id": "x:1", "ts": QUESTION, "user": "UPROBE0001", "channel": "../../leak", "text": "x"}
    leak = tmp_path / "leak.jsonl"
    leak.write_text(json.dumps(line) + "\n")          # the same file the helper would read as the inbox

    with pytest.raises(mod.Refused):
        mod.answer(mod.read_binding(bound(binding)), "../../leak", QUESTION, ANSWER)

    assert leak.read_text() == json.dumps(line) + "\n"


def test_the_dm_fallback_reads_the_answered_lines_channel_not_the_oldest(tmp_path):
    """Criterion (2): a DM session whose binding names no channel must never compare the answered line against the
    oldest line's channel. The daemon posts to the channel the answered line itself names, so a second DM channel that
    arrived after the first must not make the answer refuse."""
    mod = helper()
    binding, state = queued(tmp_path, channels=("dm",))
    oldest, answered_ts = "1762500000.000100", "1762500000.000200"
    with open(state / "inboxes" / "dm.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({"id": f"dm:{oldest}", "ts": oldest, "user": "UPROBE0001", "channel": "D0OLD0001",
                            "text": "hi"}) + "\n")
        f.write(json.dumps({"id": f"dm:{answered_ts}", "ts": answered_ts, "user": "UPROBE0001",
                            "channel": "D0NEW0002", "text": "hi"}) + "\n")

    mod.answer(mod.read_binding(bound(binding)), "dm", answered_ts, ANSWER)

    assert outbox(state, "dm") == [{"thread_ts": answered_ts, "text": ANSWER, "channel": "D0NEW0002"}]


def test_a_binding_whose_state_folder_is_missing_writes_nothing_and_names_it(tmp_path):
    """Criterion (4): a state folder that does not exist refuses before the outbox is resolved, so the helper creates
    no folder and the refusal names the missing folder."""
    mod = helper()
    missing = tmp_path / "gone"
    binding = tmp_path / "binding.json"
    binding.write_text(json.dumps({"key": "acme-widget", "state": str(missing), "channels": [CHANNEL]}))

    with pytest.raises(mod.Refused, match="does not exist"):
        mod.answer(mod.read_binding(bound(binding)), CHANNEL, QUESTION, ANSWER)

    assert not missing.exists()
    assert not (missing / "outboxes").exists()
