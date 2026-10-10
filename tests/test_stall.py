"""The stall cap: a call stops on repeated failures of one step whose failure does not change, never on its total turns.

Guards: `squad.stall` counts and resets as the rules say; the SDK adapter stops the stream at the cap, passes no turn cap to the
SDK, and reports `stall_cap` with unknown spend; a long call with changing or no failures never stops.
"""
from dataclasses import dataclass, field

import pytest

from squad.adapter import CallRequest
from squad.sdk_adapter import SdkAdapter
from squad.session import cap_reason
from squad.stall import StallWatch, key_of, signature

pytestmark = pytest.mark.anyio


def run(cap, *steps):
    """Feed (id, name, input, is_error, text) steps to a watch. Return the index of the step that tripped it, or None."""
    w = StallWatch(cap)
    for i, (tid, name, tool_input, err, text) in enumerate(steps):
        w.use(tid, name, tool_input)
        if w.result(tid, err, text):
            return i
    return None


def bash(n, cmd, err, text):
    return (f"t{n}", "Bash", {"command": cmd}, err, text)


def test_the_same_failure_of_the_same_step_trips_the_cap_at_the_nth_attempt():
    steps = [bash(i, "pytest -q", True, "1 failed: test_a") for i in range(5)]
    assert run(3, *steps) == 2
    assert run(6, *steps) is None


def test_a_changed_failure_is_evolution_and_restarts_the_count():
    same = [bash(i, "pytest -q", True, "2 failed") for i in range(2)]
    better = [bash(i + 2, "pytest -q", True, "1 failed") for i in range(2)]
    assert run(3, *same, *better) is None          # two, then a different failure, then two: never three alike
    assert run(3, *same, *better, bash(9, "pytest -q", True, "1 failed")) == 4


def test_a_success_clears_the_step():
    steps = [bash(0, "make", True, "boom"), bash(1, "make", True, "boom"), bash(2, "make", False, "ok"), bash(3, "make", True, "boom")]
    assert run(3, *steps) is None


def test_two_steps_are_counted_apart():
    steps = [bash(i, f"cmd{i % 2}", True, "boom") for i in range(6)]
    assert run(3, *steps) == 4        # cmd0 fails at 0, 2, 4


def test_durations_and_temp_paths_are_noise_but_a_count_of_failures_is_not():
    assert signature("3 failed in 0.52s") == signature("3 failed in 1.31s")
    assert signature("3 failed in 0.5s") != signature("2 failed in 0.5s")
    assert signature("see /private/var/folders/ab/cd/x.log") == signature("see /private/var/folders/zz/yy/x.log")


def test_a_key_is_the_tool_and_what_it_acts_on():
    assert key_of("Bash", {"command": "pytest   -q"}) == "Bash:pytest -q"
    assert key_of("Edit", {"file_path": "a.py", "old_string": "x"}) == "Edit:a.py"
    assert key_of("Task", None) == "Task"


def test_a_result_with_no_known_use_is_ignored():
    assert StallWatch(1).result("ghost", True, "boom") is False


# --- the adapter ---
@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass
class AssistantMessage:
    content: list = field(default_factory=list)
    usage: dict | None = None


@dataclass
class UserMessage:
    content: list = field(default_factory=list)


@dataclass
class ResultMessage:
    subtype: str = "success"
    is_error: bool = False
    num_turns: int = 3
    session_id: str = "sdk-1"
    total_cost_usd: float | None = 0.25
    result: str | None = '{"status": "pass"}'
    origin: object = None
    usage: dict | None = None


@dataclass
class Options:
    kw: dict

    def __init__(self, **kw):
        self.kw = kw


def request(**over):
    base = dict(node="actor", entry=1, item=None, prompt="go", model="m", effort=None, tools=["Bash"], max_stalls=3,
                max_spend=2.0, workspace="/tmp/ws", deadline=30.0, session_id="run-s1")
    base.update(over)
    return CallRequest(**base)


def attempt(n, cmd, text, err=True):
    return [AssistantMessage([ToolUseBlock(f"u{n}", "Bash", {"command": cmd})]), UserMessage([ToolResultBlock(f"u{n}", text, err)])]


def stream(messages, consumed):
    async def query(*, prompt, options):
        for m in messages:
            consumed.append(m)
            yield m
    return query


async def test_the_stream_stops_at_the_cap_and_the_call_reports_stall_cap_with_unknown_spend():
    msgs = [m for n in range(6) for m in attempt(n, "pytest -q", "1 failed")] + [ResultMessage()]
    seen = []
    r = await SdkAdapter(query=stream(msgs, seen), options_cls=Options).call(request())
    assert (r.stop_reason, r.spend, r.output, r.turns) == ("stall_cap", None, None, None)
    assert r.ended["last"] == "Bash:pytest -q" and len(seen) == 6          # three attempts of two messages each; nothing after the third
    assert cap_reason(r.stop_reason, 2.0) == "node_stall_cap"


async def test_a_long_call_that_never_repeats_a_failure_is_never_stopped():
    msgs = [m for n in range(200) for m in attempt(n, "pytest -q", f"{n} failed")] + [ResultMessage()]
    r = await SdkAdapter(query=stream(msgs, []), options_cls=Options).call(request())
    assert (r.stop_reason, r.spend, r.output) == (None, 0.25, {"status": "pass"})


async def test_a_call_with_many_successes_is_never_stopped():
    msgs = [m for n in range(200) for m in attempt(n, "ls", "ok", err=False)] + [ResultMessage()]
    r = await SdkAdapter(query=stream(msgs, []), options_cls=Options).call(request(max_stalls=2))
    assert r.stop_reason is None and r.output == {"status": "pass"}
