"""Guard: issue #2940. `contrib/voice/latency_probe.py` measures an inbox append's way to a live session.

The seams under test are the probe's own brief, the queues it measures, the session id it reads back, and the refusal
that keeps the real CLI under the kernel bound. The live CLI, the sandbox and the measured seconds are proven by the
probe run itself and by `contrib/voice/test-latency-probe.sh`, never here: this file proves the probe's edges, and a
measurement that names the wrong command or measures the wrong file would fail before any model is spent.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "contrib" / "voice" / "latency_probe.py"


def probe():
    """Load the probe fresh under its own module name, so one test's imports never answer another's."""
    spec = importlib.util.spec_from_file_location("latency_probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_brief_arms_the_monitor_the_live_session_arms(tmp_path):
    """The Monitor command is exactly `tail -n 0 -F <inbox>`, so the probe measures the arm a live session builds."""
    mod = probe()
    inbox, outbox = mod.queues(tmp_path / "state")

    text = mod.brief(inbox, outbox)

    assert f"tail -n 0 -F {inbox}\n" in text, "the brief arms another command than the live session's Monitor"
    assert str(outbox) in text and "armed" in text
    assert '"thread_ts"' in text, "the brief never asks for the answer the probe measures"


def test_the_queues_are_the_one_inbox_and_outbox_of_its_scope(tmp_path):
    """One channel key means one inbox and one outbox, so the probe never measures a neighbour's queue."""
    mod = probe()

    inbox, outbox = mod.queues(tmp_path)

    assert inbox == tmp_path / "inboxes" / "latency.jsonl"
    assert outbox == tmp_path / "outboxes" / "latency.jsonl"


def test_the_session_id_is_read_only_from_a_word_shaped_like_one():
    """`claude --bg` prints other words too, so the id read back is a session id or nothing."""
    mod = probe()

    assert mod.session_id("Started session abc12345 for you") == "abc12345"
    assert mod.session_id("no id here") is None
    assert mod.session_id("") is None


def test_wait_for_returns_the_matching_record_and_gives_up_past_its_time(tmp_path):
    """A queue read answers with the record the probe measures, and an absent one ends the wait, never a hang."""
    mod = probe()
    path = tmp_path / "queue.jsonl"
    path.write_text(json.dumps({"text": "armed", "pid": 7}) + "\n", encoding="utf-8")

    assert mod.wait_for(path, lambda r: r.get("text") == "armed", 1.0) == {"text": "armed", "pid": 7}
    assert mod.wait_for(path, lambda r: r.get("text") == "pong", 0.3) is None


def test_the_real_cli_never_runs_unwrapped():
    """The bound is the measurement's premise: the probe refuses `--no-sandbox` with the real `claude`."""
    mod = probe()

    with pytest.raises(SystemExit) as refused:
        mod.main(["--no-sandbox", "--cli", "claude"])

    assert refused.value.code == 2, "the probe accepted the real CLI with no kernel bound"
