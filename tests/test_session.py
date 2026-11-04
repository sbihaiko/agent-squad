"""One test per exit of the metered call module. A fake adapter drives it; each exit settles the reservation once."""
import hashlib

import anyio
import pytest

from squad import session
from squad.adapter import AdapterError, CallRequest, CallResult
from squad.budget import Budget


class Rec:
    def __init__(self):
        self.events = []

    def append(self, kind, **f):
        self.events.append((kind, f))


class Fake:
    def __init__(self, result=None, error=None, hang=False):
        self.result, self.error, self.hang = result, error, hang

    async def call(self, req):
        if self.hang:
            await anyio.sleep(60)
        if self.error:
            raise self.error
        return self.result


def req(res):
    return CallRequest("n", 1, None, "p", "m", None, [], 1, res.amount, None, 1.0, "s1")


async def go(adapter, deadline=5.0, budget=None, chain=("sonnet",), **kw):
    budget = budget or Budget(10.0)
    settled = []
    real = budget.settle
    budget.settle = lambda r, m, t=None: (settled.append(m), real(r, m, t))[1]
    rec = Rec()
    out = await session.run_call(lambda: budget.reserve(2.0, 0.1), deadline, req, chain=list(chain), adapter=adapter, record=rec,
                                 budget=budget, clock=lambda: 0.0, wall=lambda: 0.0, **kw)
    return out, budget, settled, rec


@pytest.mark.anyio
async def test_a_result_settles_with_the_measured_spend():
    out, b, settled, rec = await go(Fake(CallResult({}, 0.5)))
    assert out.kind == "result" and settled == [0.5] and b.measured == 0.5 and b.reserved == 0
    assert rec.events[0][0] == "call_start"


@pytest.mark.anyio
async def test_the_call_start_carries_the_ordered_chain_ids_and_their_hash():
    """SPEC 1035: the chain the call was configured with, in registry ids and one hash of that order. The expected hash is
    computed here from the stated recipe (sha256 over the ids joined by a newline), never by the code under test."""
    _, _, _, rec = await go(Fake(CallResult({}, 0.5)), chain=["opus", "sonnet"])
    kind, f = rec.events[0]
    assert kind == "call_start"
    assert f["chain"] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert f["chain_id"] == hashlib.sha256(b"claude-opus-5-5\nclaude-sonnet-5-5").hexdigest()


@pytest.mark.anyio
async def test_a_different_order_of_the_same_ids_is_a_different_chain():
    _, _, _, first = await go(Fake(CallResult({}, 0.5)), chain=["opus", "sonnet"])
    _, _, _, second = await go(Fake(CallResult({}, 0.5)), chain=["sonnet", "opus"])
    a, b = first.events[0][1], second.events[0][1]
    assert sorted(a["chain"]) == sorted(b["chain"])
    assert a["chain_id"] != b["chain_id"]


@pytest.mark.anyio
async def test_a_deadline_settles_unmeasured_and_writes_its_event_first():
    seen = []
    out, b, settled, _ = await go(Fake(hang=True), deadline=0.05, on_deadline=lambda res, el: seen.append(b_state(res)))
    assert out.kind == "deadline" and settled == [None] and seen == [True] and b.unmeasured == 2.0


def b_state(res):
    return res.settled     # the event hook runs after the settle


@pytest.mark.anyio
async def test_a_cancel_settles_unmeasured_and_reraises():
    b, seen = Budget(10.0), []

    async def run():
        await session.run_call(lambda: b.reserve(2.0, 0.1), 5.0, req, chain=["sonnet"], adapter=Fake(hang=True), record=Rec(), budget=b,
                               clock=lambda: 0.0, wall=lambda: 0.0, on_cancel=lambda res, el: seen.append(res.amount))

    async with anyio.create_task_group() as tg:
        tg.start_soon(run)
        await anyio.sleep(0.05)
        tg.cancel_scope.cancel()
    assert seen == [2.0] and b.unmeasured == 2.0 and b.reserved == 0


@pytest.mark.anyio
async def test_an_adapter_error_settles_unmeasured():
    out, b, settled, _ = await go(Fake(error=AdapterError("boom")))
    assert out.kind == "adapter_error" and str(out.error) == "boom" and settled == [None] and b.unmeasured == 2.0


@pytest.mark.anyio
async def test_a_run_that_cannot_fund_the_call_never_calls():
    out, b, settled, rec = await go(Fake(CallResult({}, 0.5)), budget=Budget(0.0))
    assert out.kind == "no_funds" and settled == [] and rec.events == []


@pytest.mark.anyio
async def test_an_expired_deadline_releases_the_grant():
    out, b, settled, rec = await go(Fake(), deadline=0)
    assert out.kind == "expired" and b.reserved == 0 and b.unmeasured == 0 and rec.events == []


def test_the_cap_reason_map():
    assert session.cap_reason("stall_cap", 1.0) == "node_stall_cap"
    assert session.cap_reason("output_cap", 1.0) == "node_output_cap"
    assert session.cap_reason("spend_cap", 1.0, 2.0) == "run_spend_cap"
    assert session.cap_reason("spend_cap", 2.0, 2.0) == "node_spend_cap"
    assert session.cap_reason(None, 1.0) is None


# --- the no-progress window of one entry (#1289): the upstream sent nothing, so the entry ends and the chain moves on ---
class Beating:
    """An adapter that keeps its call alive: it beats the pulse `beats` times, `gap` seconds apart, then answers."""

    def __init__(self, beats, gap, result):
        self.beats, self.gap, self.result = beats, gap, result

    async def call(self, req):
        for _ in range(self.beats):
            await anyio.sleep(self.gap)
            req.pulse.beat()
        return self.result


class OpenTool:
    """An adapter whose tool runs longer than the window: the tool's own time is not the upstream's silence."""

    async def call(self, req):
        req.pulse.tool_started("t1")
        await anyio.sleep(0.2)
        req.pulse.tool_done("t1")
        return CallResult({}, 0.5)


@pytest.mark.anyio
async def test_a_silent_entry_ends_as_no_progress_at_its_window_and_settles_unmeasured():
    out, b, settled, _ = await go(Fake(hang=True), deadline=5.0, no_progress=0.05)
    assert out.kind == "no_progress" and out.window == 0.05 and out.error is None
    assert settled == [None] and b.unmeasured == 2.0 and b.reserved == 0


@pytest.mark.anyio
async def test_a_call_that_keeps_beating_runs_past_its_window_and_completes():
    out, b, settled, _ = await go(Beating(beats=8, gap=0.02, result=CallResult({}, 0.5)), deadline=5.0, no_progress=0.1)
    assert out.kind == "result" and settled == [0.5]


@pytest.mark.anyio
async def test_the_node_deadline_wins_when_it_passes_before_the_window():
    out, b, settled, _ = await go(Fake(hang=True), deadline=0.05, no_progress=5.0)
    assert out.kind == "deadline" and settled == [None]


@pytest.mark.anyio
async def test_a_tool_that_is_running_is_not_silence():
    out, _, settled, _ = await go(OpenTool(), deadline=5.0, no_progress=0.05)
    assert out.kind == "result" and settled == [0.5]


@pytest.mark.anyio
async def test_the_window_is_read_from_the_last_beat_not_the_call_start():
    """Beats every 0.03 s against a 0.1 s window: the call is never silent for the whole window, so it runs to its answer."""
    out, _, _, _ = await go(Beating(beats=10, gap=0.03, result=CallResult({}, 0.1)), deadline=5.0, no_progress=0.1)
    assert out.kind == "result"


def test_an_ac_commands_entry_written_as_command_and_expected_is_the_command():    # #1346: a child wrote {command, expected} objects and the run failed
    assert session.ac_commands({"ac_commands": [{"command": "true", "expected": "x"}]}) == (["true"], True)
    assert session.ac_commands({"ac_commands": [[{"command": "true", "expected": "x"}]]}) == (["true"], True)


def test_an_ac_commands_mapping_without_a_command_string_stays_malformed():    # #1346: only a non-empty `command` string counts
    for bad in ({"cp": "x"}, {"command": ""}, {"command": "  "}, {"command": ["true"]}, {"expected": "x"}):
        assert session.ac_commands({"ac_commands": [bad]}) == ([], False), bad


def test_an_artifact_with_the_command_and_expected_form_does_not_fail_as_ac_commands_invalid():    # #1346
    out = {"status": "pass", "ac_commands": [{"command": "true", "expected": "x"}]}
    assert session.agent_outcome({}, type("R", (), {"output": out})(), None)[1] != ["ac_commands_invalid"]
    assert session.agent_outcome({}, type("R", (), {"output": out})(), None)[0] == "pass"
