"""A chain entry that answered nothing hands the step to the next one (ADR-0160, #1236).

The chain moved on when the provider refused to serve. It did not move on when the call stopped at its own stall cap:
the model kept retrying one step without changing the failure, so the node produced no artifact, and the run ended on
the first entry while a second one stood untried. A run that failed that way on the router cost about nine minutes.
"""
from types import SimpleNamespace

import pytest

from squad import session

pytestmark = pytest.mark.anyio


def result(stop_reason=None, output=None):
    """A CallResult stand-in: the chain reads the stop reason and whether an artifact came back."""
    return SimpleNamespace(stop_reason=stop_reason, output=output, spend=0.1, usage=None)


def outcome(kind, *, stop_reason=None, error=None, output=None):
    return session.CallOutcome(kind, result=result(stop_reason, output) if kind == "result" else None, error=error)


async def test_a_stall_cap_hands_the_step_to_the_next_entry():
    seen = []

    async def call(model, first):
        seen.append(model)
        return (outcome("result", stop_reason="stall_cap") if model == "a" else outcome("result")), {"model": model}

    out, tried, who = await session.run_chain(["a", "b"], call)
    assert seen == ["a", "b"], "the second entry answers after the first stalls"
    assert out.kind == "result" and out.result.stop_reason is None
    assert [m for m, _ in tried] == ["a"], "the stalled entry is on the record"
    assert "stall cap" in tried[0][1]
    assert who == {"model": "b"}


async def test_a_stall_cap_that_still_wrote_an_artifact_is_an_answer():
    """A cap ends the node through its artifact (ADR-0105): a capped call that produced one is never retried on another entry."""
    seen = []

    async def call(model, first):
        seen.append(model)
        return outcome("result", stop_reason="stall_cap", output={"diff": {"files": []}}), {"model": model}

    out, tried, _ = await session.run_chain(["a", "b"], call)
    assert seen == ["a"], "the artifact is the answer, so the chain stops there"
    assert tried == [] and out.result.output == {"diff": {"files": []}}


async def test_a_clean_result_ends_the_walk():
    seen = []

    async def call(model, first):
        seen.append(model)
        return outcome("result"), {"model": model}

    await session.run_chain(["a", "b"], call)
    assert seen == ["a"], "a call that answered is never retried on the next entry"


async def test_a_chain_whose_every_entry_stalls_ends_on_the_last_one():
    async def call(model, first):
        return outcome("result", stop_reason="stall_cap"), {"model": model}

    out, tried, _ = await session.run_chain(["a", "b"], call)
    assert [m for m, _ in tried] == ["a"], "the entries passed over are on the record"
    assert not session.exhausted(["a", "b"], tried), "a stall is not a refusal: the last entry's own reason stands"
    assert out.result.stop_reason == "stall_cap", "the last entry's own outcome still reaches the node"


async def test_a_one_entry_chain_reads_exactly_as_it_did():
    async def call(model, first):
        return outcome("result", stop_reason="stall_cap"), {"model": model}

    out, tried, _ = await session.run_chain(["a"], call)
    assert out.result.stop_reason == "stall_cap"
    assert tried == [], "a one-entry chain never reports a refusal it did not make"
    assert not session.exhausted(["a"], tried), "the node keeps its own cap reason"


async def test_a_deadline_still_ends_the_node():
    """A deadline is the node's own bound, not a refusal: the chain never spends it a second time."""
    seen = []

    async def call(model, first):
        seen.append(model)
        return outcome("deadline"), {"model": model}

    await session.run_chain(["a", "b"], call)
    assert seen == ["a"]


# --- a transport failure gets one retry on the same entry (#1293) ---------------------------------------------------

TRANSPORT = "ResultError: Claude Code returned an error result: API Error: Connection refused (ECONNREFUSED) (exit code: 1)"


def transport_out():
    """A call that failed because the transport did not deliver: the model never refused to serve."""
    return session.CallOutcome("adapter_error", error=RuntimeError(TRANSPORT))


async def test_a_transport_failure_gets_one_retry_on_the_same_entry():
    seen = []

    async def call(model, first):
        seen.append((model, first))
        return (transport_out() if len(seen) == 1 else outcome("result")), {"model": model}

    out, tried, who = await session.run_chain(["a", "b"], call, backoff=0)
    assert seen == [("a", True), ("a", False)], "the failed entry is called again, not the next one"
    assert out.kind == "result" and tried == []
    assert who == {"model": "a"}


async def test_a_chain_whose_entries_all_hit_the_transport_ends_adapter_error_after_the_backoff():
    seen, slept = [], []

    async def call(model, first):
        seen.append(model)
        return transport_out(), {"model": model}

    async def sleep(seconds):
        slept.append(seconds)

    out, tried, _ = await session.run_chain(["a", "b"], call, backoff=1.5, sleep=sleep)
    assert seen == ["a", "a", "b", "b"], "one retry per entry, and every entry is tried"
    assert [m for m, _ in tried] == ["a", "b"]
    assert session.exhausted(["a", "b"], tried)
    assert out.kind == "adapter_error"
    assert slept == [1.5, 1.5]


async def test_the_retry_is_reported_for_the_record():
    retried = []

    async def call(model, first):
        return (transport_out() if not retried else outcome("result")), {"model": model}

    def on_retry(model, out):
        retried.append(model)

    out, tried, _ = await session.run_chain(["a"], call, backoff=0, on_retry=on_retry)
    assert retried == ["a"], "the retry is on the record once"
    assert out.kind == "result" and tried == []


async def test_a_transport_failure_never_retries_a_message_over_the_ceiling():
    """A ceiling is a fact about the transport's frame, not a blip: the engine never makes a fresh call silently."""
    from squad.adapter import MessageOverCeiling

    seen = []

    async def call(model, first):
        seen.append(model)
        err = MessageOverCeiling("the transport's reader stopped at its 1000-byte JSON ceiling", 1000, 2000)
        return session.CallOutcome("adapter_error", error=err), {"model": model}

    out, tried, _ = await session.run_chain(["a", "b"], call, backoff=0)
    assert seen == ["a"], "a ceiling ends the walk exactly as it did"
    assert out.kind == "adapter_error" and tried == []
