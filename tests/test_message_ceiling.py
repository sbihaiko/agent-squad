"""The transport's JSON ceiling: a session message past the reader's buffer is a fact about the transport, not about the work.

Found on run-20261011-171934-888e92 (the fork-only slice of issue #1809, 2026-10-11): the child's first call died 17.8 s
in with `CLIJSONDecodeError: Failed to decode JSON: JSON message exceeded maximum buffer size of 1048576 bytes`, and the
run record carried a bare `adapter_error` with `spend: null, turns: null, model: null` — no ceiling, no size, and nothing
an operator could read as "a retry is reasonable". The same slice, re-requested unchanged, succeeded.

The stand-in below mirrors the reader that raises it, `claude_agent_sdk/_internal/transport/subprocess_cli.py`
(`_DEFAULT_MAX_BUFFER_SIZE = 1024 * 1024` and the `guard()` inside `_read_messages_impl`, which raises
`CLIJSONDecodeError(<ceiling sentence>, ValueError(f"Buffer size {length} exceeds limit {ceiling}"))`). Read in the
squad venv on 2026-10-11; the SDK is never imported by the tests (it is absent from the test environment).
"""
import json
from dataclasses import dataclass, field

import pytest

from squad import record
from squad.adapter import AdapterError
from squad.engine import Engine
from squad.graph import from_doc
from squad.sdk_adapter import SdkAdapter
from tests.flows import agent, doc

pytestmark = pytest.mark.anyio

CEILING = 1024 * 1024      # the reader's own default, `_DEFAULT_MAX_BUFFER_SIZE`


class CLIJSONDecodeError(Exception):
    """The SDK's own error class: `__init__(self, line, original_error)` keeps both arguments (squad/sdk_adapter.py reads them)."""

    def __init__(self, line, original_error):
        self.line, self.original_error = line, original_error
        super().__init__(f"Failed to decode JSON: {line[:100]}...")


def oversized_message(size=CEILING):
    """One assistant frame longer than the reader's ceiling, as the CLI writes it: one NDJSON line."""
    return json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "x" * size}]}})


def transport_ceiling(line, ceiling):
    """The error the reader's `guard()` raises for that line."""
    return CLIJSONDecodeError(f"JSON message exceeded maximum buffer size of {ceiling} bytes",
                              ValueError(f"Buffer size {len(line)} exceeds limit {ceiling}"))


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict = field(default_factory=dict)


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str = ""
    is_error: bool = False


@dataclass
class AssistantMessage:
    content: list = field(default_factory=list)
    model: str = "claude-sonnet-5-5"
    usage: dict | None = None
    error: str | None = None


@dataclass
class UserMessage:
    content: list = field(default_factory=list)


@dataclass
class ResultMessage:
    subtype: str = "success"
    result: str = ""
    total_cost_usd: float = 0.1
    is_error: bool = False
    num_turns: int = 1
    session_id: str = "run-s1"
    usage: dict | None = None


@dataclass
class Options:
    def __init__(self, **kw):
        self.kw = kw


def request(**over):
    from squad.adapter import CallRequest
    base = dict(node="actor", entry=1, item=None, prompt="do it", model="claude-sonnet-5-5", effort="medium",
                tools=["Read", "Edit"], max_stalls=12, max_spend=2.0, workspace="/tmp/ws", deadline=30.0, session_id="run-s1")
    base.update(over)
    return CallRequest(**base)


def failing_query(exc):
    async def query(*, prompt, options):
        yield AssistantMessage()
        raise exc
    return query


def client(exc):
    return SdkAdapter(query=failing_query(exc), options_cls=Options)


def outs(path):
    return [e for e in record.read(path / "record.jsonl") if e["event"] == "outcome"]


async def test_a_message_past_the_ceiling_names_the_ceiling_and_the_offending_size():
    line = oversized_message()
    with pytest.raises(AdapterError) as info:
        await client(transport_ceiling(line, CEILING)).call(request())
    err = info.value
    assert type(err).__name__ == "MessageOverCeiling"        # a class of its own: the caller can tell it from any other adapter error
    assert (err.ceiling, err.size) == (CEILING, len(line))
    assert str(CEILING) in str(err) and str(len(line)) in str(err)     # the size is a fact of the error, not only the ceiling the SDK prints


async def test_the_outcome_names_the_ceiling_and_the_size_and_says_what_the_transport_allowed(tmp_path):
    """The run cannot continue in place — the reader raises out of its own line framer and the stream dies with it — and the record says so."""
    line = oversized_message()
    d = doc([agent("a", output_key="x", next={})], ["a"])
    eng = Engine(from_doc(d), client(transport_ceiling(line, CEILING)), tmp_path)
    v = await eng.run()
    e = outs(tmp_path)[0]
    assert e["outcome"] == "fail" and e["spend"] is None
    assert e["reasons"] == [f"message_over_ceiling:size={len(line)},ceiling={CEILING}"]
    t = e["diagnostic"]["transport"]
    assert (t["ceiling"], t["size"]) == (CEILING, len(line))
    assert (t["dropped"], t["continued"]) == (False, False) and t["recovery"] == "fresh_call"
    assert v.verdict == "failed"


async def test_an_adapter_failure_that_is_not_the_ceiling_keeps_the_bare_reason(tmp_path):
    d = doc([agent("a", output_key="x", next={})], ["a"])
    eng = Engine(from_doc(d), client(RuntimeError("socket closed")), tmp_path)
    await eng.run()
    e = outs(tmp_path)[0]
    assert e["reasons"] == ["adapter_error"] and "transport" not in e["diagnostic"]


# --- #2267: one oversize message must not end the run -------------------------------------------
#
# The reader carries a frame up to its buffer, so the session survives a message the adapter calls oversize: the
# adapter records one event and hands the session a short error for the tool call the frame carries.

OVER = 2 * CEILING      # a frame well past the ceiling the adapter declares


def ceiling_events(path):
    return [e for e in record.read(path / "record.jsonl") if e["event"] == "message_over_ceiling"]


async def pre_tool_use(options, tool, tool_id, tool_input):
    """The hook answer the CLI would get for that call: the first PreToolUse hook that refuses it."""
    for matcher in options.kw["hooks"]["PreToolUse"]:
        answer = await matcher.hooks[0]({"tool_name": tool, "tool_input": tool_input}, tool_id, None)
        if answer:
            return answer
    return {}


def oversize_tool_query(answer_box):
    """One call whose session writes a message past the ceiling: a Write tool call, then the tool result the CLI got
    for it, then a clean result. The CLI reads the hook answer before it returns the result, as it does live."""
    async def query(*, prompt, options):
        tool_input = {"file_path": "big.txt", "content": "x" * OVER}
        yield AssistantMessage(content=[ToolUseBlock(id="t1", name="Write", input=tool_input)])
        answer = await pre_tool_use(options, "Write", "t1", tool_input)
        answer_box.append(answer)
        reason = answer.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        yield UserMessage(content=[ToolResultBlock(tool_use_id="t1", content=reason, is_error=True)])
        yield ResultMessage(result=json.dumps({"status": "pass", "x": "done"}))
    return query


async def test_an_oversize_tool_call_message_continues_the_run_with_one_recorded_event(tmp_path):
    box = []
    adapter = SdkAdapter(query=oversize_tool_query(box), options_cls=Options)
    d = doc([agent("a", output_key="x", next={})], ["a"])
    v = await Engine(from_doc(d), adapter, tmp_path).run()
    assert v.verdict == "completed"                            # (1) one message over the ceiling does not end the run
    assert outs(tmp_path)[0]["outcome"] == "pass"
    events = ceiling_events(tmp_path)
    assert len(events) == 1                                     # (3) one event, naming the tool call and the size
    e = events[0]
    assert e["tool"] == "t1" and e["name"] == "Write"
    assert OVER <= e["size"] < OVER + 4096 and e["ceiling"] == CEILING
    answer = box[0]["hookSpecificOutput"]                       # (2) the session gets a short error for that call
    assert answer["permissionDecision"] == "deny"
    assert str(e["size"]) in answer["permissionDecisionReason"]
    assert str(CEILING) in answer["permissionDecisionReason"]


def oversize_result_query():
    """One call whose session reads a tool result past the ceiling, then ends clean: the frame has no tool call to refuse."""
    async def query(*, prompt, options):
        yield UserMessage(content=[ToolResultBlock(tool_use_id="t7", content="y" * OVER)])
        yield ResultMessage(result=json.dumps({"status": "pass", "x": "done"}))
    return query


async def test_an_oversize_result_message_continues_the_run_and_still_names_its_tool_call(tmp_path):
    adapter = SdkAdapter(query=oversize_result_query(), options_cls=Options)
    d = doc([agent("a", output_key="x", next={})], ["a"])
    v = await Engine(from_doc(d), adapter, tmp_path).run()
    assert v.verdict == "completed"
    assert outs(tmp_path)[0]["outcome"] == "pass"
    events = ceiling_events(tmp_path)
    assert len(events) == 1 and events[0]["tool"] == "t7"
    assert OVER <= events[0]["size"] < OVER + 4096
