"""The real SDK adapter against a stub of `query()`. No live call, no spend.

Guards: one call is one session, a deadline stops the child,
a killed call has unknown spend, never zero.
"""
import json
from dataclasses import dataclass, field

import anyio
import pytest

from squad import sdk_adapter
from squad.adapter import AdapterError, CallRequest
from squad.sdk_adapter import SdkAdapter, extract_json

pytestmark = pytest.mark.anyio


@dataclass
class ResultMessage:
    subtype: str = "success"
    is_error: bool = False
    num_turns: int = 3
    session_id: str = "sdk-1"
    total_cost_usd: float | None = 0.25
    result: str | None = None
    origin: object = None
    usage: dict | None = None


@dataclass
class ToolUseBlock:
    name: str


@dataclass
class AssistantMessage:
    content: list = field(default_factory=list)
    usage: dict | None = None


@dataclass
class Options:
    kw: dict

    def __init__(self, **kw):
        self.kw = kw


def request(**over):
    base = dict(node="actor", entry=1, item=None, prompt="do it", model="claude-sonnet-5-5", effort="medium",
                tools=["Read", "Edit"], max_stalls=12, max_spend=2.0, workspace="/tmp/ws", deadline=30.0, session_id="run-s1")
    base.update(over)
    return CallRequest(**base)


def stub(messages, seen=None):
    async def query(*, prompt, options):
        if seen is not None:
            seen.append((prompt, options))
        for m in messages:
            yield m
    return query


def adapter(messages, seen=None):
    return SdkAdapter(query=stub(messages, seen), options_cls=Options)


async def test_a_result_yields_output_spend_turns_and_session():
    out = json.dumps({"status": "pass", "diff": {"files": []}})
    r = await adapter([ResultMessage(result=f"done\n```json\n{out}\n```")]).call(request())
    assert r.output == {"status": "pass", "diff": {"files": []}}
    assert (r.spend, r.turns, r.stop_reason, r.session_id) == (0.25, 3, None, "sdk-1")


async def test_node_settings_map_onto_sdk_options():
    seen = []
    await adapter([ResultMessage(result="{}")], seen).call(request())
    prompt, opt = seen[0]
    kw = opt.kw
    assert prompt == "do it"
    assert (kw["model"], kw["effort"], kw["tools"], kw["max_budget_usd"], kw["cwd"]) == \
        ("claude-sonnet-5-5", "medium", ["Read", "Edit"], 2.0, "/tmp/ws")
    assert "max_turns" not in kw and "max_stalls" not in kw   # total turns are never capped; the stall watch lives in the adapter
    assert kw["permission_mode"] == "bypassPermissions"
    assert kw["setting_sources"] == ["project"]   # the project's own CLAUDE.md applies, as in any repo


async def test_a_spend_cap_is_reported_with_measured_spend():
    r = await adapter([ResultMessage(subtype="error_max_budget_usd", is_error=True, total_cost_usd=2.01)]).call(request())
    assert (r.stop_reason, r.spend) == ("spend_cap", 2.01)


async def test_a_subagent_result_does_not_count_as_the_session_result():
    sub = ResultMessage(total_cost_usd=9.0, origin="subagent")
    r = await adapter([sub, ResultMessage(total_cost_usd=0.3, result='{"a": 1}')]).call(request())
    assert (r.spend, r.output, r.subagents) == (0.3, {"a": 1}, 1)


async def test_a_stream_with_no_result_is_an_adapter_error_not_zero_spend():
    with pytest.raises(AdapterError):
        await adapter([AssistantMessage()]).call(request())


async def test_a_raising_query_is_an_adapter_error():
    async def query(*, prompt, options):
        raise RuntimeError("boom")
        yield
    with pytest.raises(AdapterError, match="boom"):
        await SdkAdapter(query=query, options_cls=Options).call(request())


async def test_cancellation_closes_the_stream():
    closed = []

    async def query(*, prompt, options):
        try:
            yield AssistantMessage()
            await anyio.sleep(30)
        finally:
            closed.append(True)

    with anyio.move_on_after(0.2):
        await SdkAdapter(query=query, options_cls=Options).call(request())
    assert closed == [True]


async def test_a_cancellation_from_the_stream_propagates_and_the_stream_closes():
    # Guard: the broad `except Exception` in SdkAdapter.call must not swallow cancellation (a BaseException).
    import asyncio
    closed = []

    async def query(*, prompt, options):
        try:
            yield AssistantMessage()
            raise asyncio.CancelledError()
        finally:
            closed.append(True)

    with pytest.raises(asyncio.CancelledError) as info:
        await SdkAdapter(query=query, options_cls=Options).call(request())
    assert not isinstance(info.value, AdapterError)
    assert closed == [True]


def test_extract_json_prefers_the_last_fenced_block_then_the_last_object():
    assert extract_json('x ```json\n{"a": 1}\n``` y ```json\n{"b": 2}\n```') == {"b": 2}
    assert extract_json('prose {"a": 1} more {"c": {"d": 3}}') == {"c": {"d": 3}}
    assert extract_json("no json here") is None
    assert extract_json("[1, 2]") is None   # an artifact is an object


async def test_an_output_cap_error_after_a_result_keeps_the_measured_spend_and_reports_output_cap():
    async def query(*, prompt, options):
        yield ResultMessage(total_cost_usd=0.022, result="cut off")
        raise RuntimeError("Claude Code returned an error result: API Error: Claude's response exceeded the 300 output token maximum.")
    r = await SdkAdapter(query=query, options_cls=Options).call(request())
    assert (r.stop_reason, r.spend) == ("output_cap", 0.022)


async def test_a_spend_cap_error_after_a_result_keeps_its_reason_spend_and_turns():
    """Found on run-20261001-234426: the real SDK raises on a capped call the same way it raises on an output cap.

    Three children of that run died `adapter_error` with `spend: null, turns: null`, though each had already
    delivered a ResultMessage naming the cap. the spend rules end a capped node through its artifact, so the
    reason must survive the raise.
    """
    async def budget(*, prompt, options):
        yield ResultMessage(subtype="error_max_budget_usd", is_error=True, num_turns=4, total_cost_usd=2.4)
        raise RuntimeError("Claude Code returned an error result: Reached maximum budget ($2) (exit code: 1)")
    r = await SdkAdapter(query=budget, options_cls=Options).call(request())
    assert (r.stop_reason, r.spend) == ("spend_cap", 2.4)


async def test_any_other_error_after_a_result_is_still_an_adapter_error():
    async def query(*, prompt, options):
        yield ResultMessage(total_cost_usd=0.5)
        raise RuntimeError("socket closed")
    with pytest.raises(AdapterError, match="socket closed"):
        await SdkAdapter(query=query, options_cls=Options).call(request())


async def test_the_result_usage_becomes_tokens_in_and_tokens_out():
    usage = {"input_tokens": 10, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 30, "output_tokens": 7}
    r = await adapter([ResultMessage(result="{}", usage=usage)]).call(request())
    assert r.usage == {"tokens_in": 60, "tokens_out": 7, "cache_read": 30, "cache_write": 20}


async def test_the_cache_split_is_recorded_next_to_the_tokens_and_a_reported_zero_stays_zero():
    usage = {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 30, "output_tokens": 7}
    r = await adapter([ResultMessage(result="{}", usage=usage)]).call(request())
    assert r.usage == {"tokens_in": 40, "tokens_out": 7, "cache_read": 30, "cache_write": 0}


async def test_a_result_with_no_usage_leaves_tokens_unknown_never_zero():
    r = await adapter([ResultMessage(result="{}")]).call(request())
    assert r.usage is None


# --- a call in flight tells the record where it is ---
def bound(messages, seconds, seen=None):
    """An adapter whose progress interval is `seconds` and whose sink collects the events it writes."""
    events = []
    a = SdkAdapter(query=stub(messages, seen), options_cls=Options, progress_seconds=seconds)
    a.bind(lambda kind, **f: events.append({"event": kind, **f}))
    return a, events


def turn(*blocks, usage=None):
    return AssistantMessage(content=list(blocks), usage=usage)


async def test_each_assistant_message_records_the_progress_of_the_call():
    """With an interval of zero, every assistant message is one progress event: messages so far, the context it read, its last tool.

    Probed 2026-10-02 on a real call: one assistant message reports the prompt of that response, so the newest message
    gives the context size now. The events never sum: the second reading replaces the first.
    """
    msgs = [turn(ToolUseBlock("Read"), usage={"input_tokens": 100, "cache_read_input_tokens": 400}),
            turn(ToolUseBlock("Bash"), usage={"input_tokens": 50}),
            ResultMessage(result='{"status": "pass"}', num_turns=2, usage={"input_tokens": 150, "output_tokens": 30})]
    a, events = bound(msgs, 0)
    r = await a.call(request())
    assert [(e["messages"], e["context_tokens"], e["tool"]) for e in events] == [(1, 500, "Read"), (2, 50, "Bash")]
    assert all(e["node"] == "actor" and e["entry"] == 1 and e["session"] == "run-s1" for e in events)
    assert r.output == {"status": "pass"} and r.turns == 2          # the progress events never replace the result
    assert r.usage == {"tokens_in": 150, "tokens_out": 30}          # the outcome keeps the session's own figures


async def test_a_long_interval_holds_the_progress_events_and_the_result_still_lands():
    msgs = [turn(ToolUseBlock("Read")), ResultMessage(result="{}", num_turns=1, total_cost_usd=0.1)]
    a, events = bound(msgs, 3600)
    r = await a.call(request())
    assert events == [] and r.spend == 0.1


async def test_an_interval_of_none_writes_no_progress_at_all():
    msgs = [turn(ToolUseBlock("Read"), usage={"input_tokens": 10}), ResultMessage(result="{}")]
    a, events = bound(msgs, None)
    assert (await a.call(request())).output == {} and events == []


class Clock:
    """A monotonic clock that moves `step` seconds per reading, so an interval can be tested without a sleep."""

    def __init__(self, step):
        self.t, self.step = 1000.0, step

    def monotonic(self):
        now, self.t = self.t, self.t + self.step
        return now


async def test_a_progress_event_waits_out_the_interval_and_the_interval_restarts_at_each_write(monkeypatch):
    """Four seconds a message against a ten-second interval: the third message writes (12 s), the sixth writes again (12 s)."""
    monkeypatch.setattr(sdk_adapter, "time", Clock(4.0))
    msgs = [turn(ToolUseBlock("Read"), usage={"input_tokens": 10 * n}) for n in range(1, 7)] + [ResultMessage(result="{}")]
    a, events = bound(msgs, 10)
    await a.call(request())
    assert [e["messages"] for e in events] == [3, 6] and [e["context_tokens"] for e in events] == [30, 60]


async def test_an_adapter_with_no_sink_records_nothing_and_never_fails():
    a = SdkAdapter(query=stub([turn(ToolUseBlock("Read")), ResultMessage(result="{}")]), options_cls=Options, progress_seconds=0)
    assert (await a.call(request())).output == {}


def test_the_progress_interval_comes_from_the_agent_defaults():
    from squad import defaults
    cfg = defaults.load(defaults.PATH)
    assert SdkAdapter.from_defaults(cfg).progress_seconds == cfg["progress"]["seconds"]


async def test_a_progress_event_with_no_usage_yet_leaves_the_context_unknown_never_zero():
    a, events = bound([turn(ToolUseBlock("Read")), ResultMessage(result="{}")], 0)
    await a.call(request())
    assert events[0]["messages"] == 1 and events[0]["context_tokens"] is None


async def test_a_message_that_reports_no_whole_count_leaves_the_last_context_reading_alone():
    """A usage dict whose input keys hold no number is not a reading of zero: the earlier figure stands."""
    msgs = [turn(ToolUseBlock("Read"), usage={"input_tokens": 4000}),
            turn(ToolUseBlock("Read"), usage={"input_tokens": None, "cache_read_input_tokens": None}),
            ResultMessage(result="{}")]
    a, events = bound(msgs, 0)
    await a.call(request())
    assert [e["context_tokens"] for e in events] == [4000, 4000]


def test_pyproject_declares_the_sdk_extra_at_an_exact_pin():
    """The `sdk` extra exists and carries the exact pin."""
    import re
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    extras = tomllib.loads((root / "pyproject.toml").read_text())["project"]["optional-dependencies"]
    assert re.fullmatch(r"claude-agent-sdk==\d+\.\d+\.\d+", extras["sdk"][0]) and len(extras["sdk"]) == 1
