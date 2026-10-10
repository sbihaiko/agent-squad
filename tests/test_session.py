"""One test per exit of the metered call module. A fake adapter drives it; each exit settles the reservation once."""
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


async def go(adapter, deadline=5.0, budget=None, **kw):
    budget = budget or Budget(10.0)
    settled = []
    real = budget.settle
    budget.settle = lambda r, m: (settled.append(m), real(r, m))[1]
    rec = Rec()
    out = await session.run_call(lambda: budget.reserve(2.0, 0.1), deadline, req, adapter=adapter, record=rec,
                                 budget=budget, clock=lambda: 0.0, wall=lambda: 0.0, **kw)
    return out, budget, settled, rec


@pytest.mark.anyio
async def test_a_result_settles_with_the_measured_spend():
    out, b, settled, rec = await go(Fake(CallResult({}, 0.5)))
    assert out.kind == "result" and settled == [0.5] and b.measured == 0.5 and b.reserved == 0
    assert rec.events[0][0] == "call_start"


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
        await session.run_call(lambda: b.reserve(2.0, 0.1), 5.0, req, adapter=Fake(hang=True), record=Rec(), budget=b,
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
