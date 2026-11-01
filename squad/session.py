"""The metered call: reserve, deadline, call, settle.

`run_call` runs one adapter call under one reservation and settles it on every exit. A deadline
event is written before the close is awaited. The caller keeps its own events
and outcome fields; it gets back what stopped the call.
"""
import math
import os
import re
from dataclasses import dataclass, field

import anyio

from squad import modelreg
from squad.adapter import AdapterError, MessageOverCeiling
from squad.budget import measured_tokens

# A reason the model writes about itself is a claim, not an engine fact: the contract tells a child to answer `out_of_budget` when it
# gives up, and it did so at $0.36 of a $1.50 cap. The engine's own cap reasons (CAP_REASON) stay apart from it.
# A child writes the reason as a sentence, so the claim is the prefix: `out_of_budget: scope is too large` was read as the
# run's own budget fact and ended a run at $0.59 of $40 (#960). The child's words after the colon are kept.
CHILD_CLAIM = {"out_of_budget": "child_said_out_of_budget"}


def _claim(reason):
    """The claim map read as a prefix: `out_of_budget` and `out_of_budget: <the child's words>` are one claim."""
    if isinstance(reason, str) and reason.split(":", 1)[0].strip() == "out_of_budget":
        return "child_said_out_of_budget" + reason[len("out_of_budget"):]
    return reason
PROVISIONAL = "provisional:"     # the autonomy fragment tells an agent to prefix each decision it made alone
CAP_REASON ={"stall_cap": "node_stall_cap", "spend_cap": "node_spend_cap", "output_cap": "node_output_cap"}

# A provider that refuses to serve is an outage, not a defect of the work: the chain moves on to its next entry. The
# status is an HTTP one (402 exhausted, 429 rate-limited, 5xx down) and the error text has to name it as the API's own
# answer, so a number that happens to sit in a message about something else does not read as a provider failure.
PROVIDER_STATUS = re.compile(r"\b[45]\d\d\b")
PROVIDER_MARKERS = ("API Error", "upstream", '"status"')
# Some providers refuse in a sentence that carries no status at all: the subscription answers `session limit`, a spent
# pool answers `usage limit`, a provider answers `rate limit`. The CLI prints the first one when its login is exhausted,
# and it is the provider refusing to serve like any 429: on 2026-10-13 the router node died on that sentence 116 times
# while the next entry of its chain answered at once. A spent week answers `weekly limit` the same way, and a CLI that
# does not know the id answers `issue with the selected model` (#698): neither is a defect of the work.
PROVIDER_REFUSAL = ("session limit", "weekly limit", "usage limit", "rate limit", "issue with the selected model")
CHAIN_LINE = 200     # characters of one entry's error kept in the single message an exhausted chain reports


def provider_error(err):
    """True when an adapter error names an upstream HTTP status, or a refusal the provider states in words. "socket
    closed" and a malformed result are not: neither is the provider saying anything."""
    text = str(err)
    if any(m in text for m in PROVIDER_REFUSAL):
        return True
    return bool(PROVIDER_STATUS.search(text)) and any(m in text for m in PROVIDER_MARKERS)


def in_window(window, hour):
    """True when local `hour` is inside a "HH-HH" window: start inclusive, end exclusive, wrapping midnight. A window that does
    not parse closes nothing, as the launcher reads it."""
    try:
        a, b = int(window[:2]), int(window[3:5])
    except (TypeError, ValueError):
        return True
    return a <= hour < b if a <= b else (hour >= a or hour < b)


def hours_closed(hours, hour):
    """{model: window} of the models whose `hours` window excludes the local `hour`: the launcher's rule, read again at each call."""
    return {m: w for m, w in (hours or {}).items() if hour is not None and not in_window(w, hour)}


def model_chain(models, first, spent=(), hours=None, hour=None, order=()):
    """The models one entry may answer on, in order: the pick this call starts with leads the envelope's `allow.models`.
    The rest follow `order`, the models.json coding chain the launcher read: the launch picks by that order, so a fallback
    reads the same one (#617). An entry the order does not name keeps its allow-list place behind the named ones and no
    entry is dropped. A `spent` id (the engine option the launcher fills from the shared quota markers) is left out: its
    subscription refuses at once, after a wait. So is a model whose `hours` window excludes the local `hour`. When every
    entry is closed the whole chain stays, so a marker that is wrong never leaves no model."""
    rest = [m for m in (models or []) if m and m != first]
    rank = {m: i for i, m in enumerate(order or [])}
    if rank:
        rest.sort(key=lambda m: rank.get(m, len(rank)))     # stable: an unnamed entry keeps its place, behind the named ones
    chain = [first] + rest
    shut = hours_closed(hours, hour)
    return [m for m in chain if m not in spent and m not in shut] or chain


def hours_skipped(models, first, spent=(), hours=None, hour=None):
    """[(model, reason)] the chain left out for its hours window; empty when `model_chain` kept every entry for want of another."""
    chain = [first] + [m for m in (models or []) if m and m != first]
    shut = hours_closed(hours, hour)
    kept = model_chain(models, first, spent, hours, hour)
    return [(m, f"outside_hours:{shut[m]}") for m in chain if m in shut and m not in kept]


def adapter_reason(err):
    """The reason a call that produced no result ends on. A message past the transport's ceiling is a fact about the
    transport, not about the work, so the reason names the ceiling and the size of the frame that hit it: an operator
    reads what happened without opening the diagnostic, and the next dispatch is a decision, not a guess (issue #1814)."""
    if isinstance(err, MessageOverCeiling):
        return [f"message_over_ceiling:size={err.size if err.size is not None else 'unknown'},ceiling={err.ceiling}"]
    return ["adapter_error"]


def error_fields(err):
    """What a refused call spent, for its outcome: the measured cost, or the estimate from its tokens, and the source.
    A call with no data carries `spend: None`, so its grant stays unmeasured and replay keeps it reserved."""
    spend = getattr(err, "spend", None)
    source = getattr(err, "spend_source", None) if spend is not None else None
    return {"spend": spend, **(getattr(err, "usage", None) or {}), **({"spend_source": source} if source else {})}


def adapter_diagnostic(err, message=None):
    """What the record says about a call that produced no result, and — for the transport ceiling — what the transport
    allowed. The SDK's reader raises out of its own line framer, so the frame cannot be dropped and the session cannot
    go on: neither recovery is possible in place, and the record says so instead of leaving the run's death unexplained.
    The only recovery is a fresh call, which the engine never makes silently (ADR-0102 rule 1: one entry is one fresh
    session), so the record asks for one.
    """
    d = {"error": message or str(err)}
    if isinstance(err, MessageOverCeiling):
        d["transport"] = {"ceiling": err.ceiling, "size": err.size, "dropped": False, "continued": False, "recovery": "fresh_call"}
    return d


async def run_chain(chain, call):
    """Call one entry of a model chain at a time while the provider refuses to serve. `call(model, first)` runs one attempt
    and returns `(outcome, who)`. Return the last outcome, every entry that refused with its error, and the last `who`."""
    tried, out, who = [], None, None
    for i, model in enumerate(chain):
        out, who = await call(model, i == 0)
        if out.kind != "adapter_error" or not provider_error(out.error):
            break
        tried.append((model, str(out.error)))
    return out, tried, who


def exhausted(chain, tried):
    """True when every entry of the chain refused to serve: the walk never found one that answered."""
    return bool(tried) and len(tried) == len(chain)


def chain_message(tried):
    """One message naming every entry of a chain that failed and its error. A chain of one keeps its own error, so a node
    that had no alternative reads exactly as it did before the chain moved."""
    if len(tried) < 2:
        return tried[0][1] if tried else ""
    return "every model in the chain failed: " + "; ".join(f"{m} ({e[:CHAIN_LINE]})" for m, e in tried)


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


async def run_call(source, deadline, make_request, *, chain, adapter, record, budget, clock, wall,
                   registry=None, prepare=None, on_deadline=None, on_cancel=None):
    """Reserve from `source()`, run the call, settle once. `make_request(res)` builds the CallRequest.

    `chain` is the ordered models this call may answer on, already led by its pick and with the spent and hours-closed
    entries left out: the record states it beside the call, as registry ids (`modelreg.chain_ids`) and one hash of that
    order (`modelreg.chain_sha256`), so a reader never rebuilds it from a later configuration (SPEC 1035). `registry` is
    the registry those ids resolve through; None is the committed one.
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
    ids = modelreg.chain_ids(chain, registry)
    record.append("call_start", node=req.node, entry=req.entry, item=req.item, session=req.session_id,
                  pid=os.getpid(), limit=None if deadline == math.inf else deadline, started_at=wall(),
                  chain=ids, chain_id=modelreg.chain_sha256(ids))

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
        if getattr(box["err"], "served", True):     # the work it did before the refusal is spent; no data leaves the grant whole
            budget.settle(res, getattr(box["err"], "spend", None), measured_tokens(getattr(box["err"], "usage", None)))
        else:               # refused before one message: nothing was spent, so the grant goes back (#494)
            budget.release(res)
        return CallOutcome("adapter_error", res, error=box["err"], elapsed=elapsed)
    budget.settle(res, box["r"].spend, measured_tokens(box["r"].usage))
    return CallOutcome("result", res, result=box["r"], elapsed=elapsed)


def ac_commands(out):
    """The held-out commands an artifact lists, and whether the list is well formed. A command is a non-empty string. One level of
    nesting (`[["cmd"]]`, a router's "one-item list" copied whole) is flattened. Any other entry (a mapping such as `{"cp": "..."}`) is
    malformed: it would reach the shell as text and fail the final gate for a reason that is not the code."""
    raw = out.get("ac_commands", []) if isinstance(out, dict) else []
    if not isinstance(raw, list):
        return [], False
    cmds, ok = [], True
    for c in raw:
        for x in (c if isinstance(c, list) else [c]):
            if isinstance(x, str) and x.strip():
                cmds.append(x)
            else:
                ok = False
    return cmds, ok


def agent_outcome(node, res, cap_reason):
    """Derive the outcome from the declared artifact. An agent node and a dispatch child share it."""
    out = res.output
    if not isinstance(out, dict):
        return "fail", [cap_reason or "artifact_missing"], None
    key = node.get("output_key")
    if key and key not in out and out.get("status") not in ("needs-human", "fail"):    # an explicit stop answer needs no `output_key`
        return "fail", [cap_reason or "artifact_missing"], None
    if "ac_commands" in out and not ac_commands(out)[1]:
        return "fail", ["ac_commands_invalid"], None      # the child's contract is malformed: it fails, so the router repairs it
    status = out.get("status", "pass")
    if status not in ("pass", "fail", "needs-human"):
        return "fail", ["artifact_invalid"], None
    if status == "pass":      # a pass keeps only its provisional decisions; every other reason on a pass is dropped as before
        return status, [r for r in out.get("reasons", []) if isinstance(r, str) and r.startswith(PROVISIONAL)], cap_reason
    reasons = [_claim(r) for r in out.get("reasons", [status])]
    return status, reasons, cap_reason
