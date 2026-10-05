"""The metered call: reserve, deadline, call, settle (ADR-0102, ADR-0108).

`run_call` runs one adapter call under one reservation and settles it on every exit. A deadline
event is written before the close is awaited (ADR-0102 rule 4). The caller keeps its own events
and outcome fields; it gets back what stopped the call.
"""
import math
import os
from dataclasses import dataclass, field

import anyio

from squad.adapter import AdapterError

CAP_REASON = {"turn_cap": "node_turn_cap", "spend_cap": "node_spend_cap", "output_cap": "node_output_cap"}


def cap_reason(stop_reason, granted, spend_max=None):
    """The reason a cap stopped the call. A spend cap on a grant cut below the node's own max is the run's cap."""
    if stop_reason == "spend_cap" and spend_max is not None and granted < spend_max:
        return "run_spend_cap"
    return CAP_REASON.get(stop_reason)


@dataclass
class CallOutcome:
    """kind: no_funds | expired | prepare_failed | result | deadline | adapter_error. A cancel raises, after the settle."""
    kind: str
    res: object = None
    result: object = None
    error: Exception | None = None
    elapsed: float = 0.0
    reasons: list = field(default_factory=list)


async def run_call(source, deadline, make_request, *, adapter, record, budget, clock, wall,
                   prepare=None, on_deadline=None, on_cancel=None):
    """Reserve from `source()`, run the call, settle once. `make_request(res)` builds the CallRequest.

    `prepare(res)` may refuse with a list of reasons before the call starts; the grant is released.
    `on_deadline(res, elapsed)` and `on_cancel(res, elapsed)` write the caller's event after the settle.
    """
    res = source()
    if res is None:
        return CallOutcome("no_funds")
    if deadline <= 0:
        budget.release(res)
        return CallOutcome("expired", res)
    if prepare is not None:
        why = await prepare(res)
        if why:
            budget.release(res)
            return CallOutcome("prepare_failed", res, reasons=list(why))
    req = make_request(res)
    t0, box, hit = clock(), {}, []
    done = anyio.Event()
    record.append("call_start", node=req.node, entry=req.entry, item=req.item, session=req.session_id,
                  pid=os.getpid(), limit=None if deadline == math.inf else deadline, started_at=wall())

    async def runner():
        try:
            box["r"] = await adapter.call(req)
        except AdapterError as e:
            box["err"] = e
        finally:
            done.set()

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(runner)
            with anyio.move_on_after(deadline) as scope:
                await done.wait()
            if scope.cancelled_caught and not done.is_set():
                budget.settle(res, None)
                hit.append(clock() - t0)
                if on_deadline:
                    on_deadline(res, hit[0])
                tg.cancel_scope.cancel()
    except anyio.get_cancelled_exc_class():
        with anyio.CancelScope(shield=True):
            budget.settle(res, None)
            if on_cancel:
                on_cancel(res, clock() - t0)
        raise
    if hit:
        return CallOutcome("deadline", res, elapsed=hit[0])
    elapsed = clock() - t0
    if "err" in box:
        budget.settle(res, None)
        return CallOutcome("adapter_error", res, error=box["err"], elapsed=elapsed)
    budget.settle(res, box["r"].spend)
    return CallOutcome("result", res, result=box["r"], elapsed=elapsed)


def agent_outcome(node, res, cap_reason):
    """Derive the outcome from the declared artifact (ADR-0102 rule 8, ADR-0108 rule 4). An agent node and a dispatch child share it."""
    out = res.output
    if not isinstance(out, dict):
        return "fail", [cap_reason or "artifact_missing"], None
    key = node.get("output_key")
    if key and key not in out:
        return "fail", [cap_reason or "artifact_missing"], None
    status = out.get("status", "pass")
    if status not in ("pass", "fail", "needs-human"):
        return "fail", ["artifact_invalid"], None
    return status, ([] if status == "pass" else list(out.get("reasons", [status]))), cap_reason
