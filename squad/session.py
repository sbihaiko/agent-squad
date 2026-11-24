"""The metered call: reserve, deadline, call, settle.

`run_call` runs one adapter call under one reservation and settles it on every exit. A deadline
event is written before the close is awaited. The caller keeps its own events
and outcome fields; it gets back what stopped the call.
"""
import math
import os
import re
from dataclasses import dataclass, field, replace

import anyio

from squad import modelreg
from squad.adapter import AdapterError, MessageOverCeiling, Pulse
from squad.adapter_classes import is_transport
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


in_window = modelreg.in_window    # one grammar for a chain entry's hours window, owned by the resolver (ADR-0147)


def hours_closed(hours, at):
    """{model: window} of the models whose `hours` window excludes the moment `at` (a timestamp or a struct_time): the
    launcher's rule, read again at each call. No moment closes nothing."""
    return {m: w for m, w in (hours or {}).items() if at is not None and not in_window(w, at)}


def model_chain(models, first, spent=(), hours=None, at=None, order=()):
    """The models one entry may answer on, in order: the pick this call starts with leads the envelope's `allow.models`.
    The rest follow `order`, the models.json coding chain the launcher read: the launch picks by that order, so a fallback
    reads the same one (#617). An entry the order does not name keeps its allow-list place behind the named ones and no
    entry is dropped. A `spent` id (the engine option the launcher fills from the shared quota markers) is left out: its
    subscription refuses at once, after a wait. So is a model whose `hours` window excludes the moment `at`. When every
    entry is closed the whole chain stays, so a marker that is wrong never leaves no model."""
    rest = [m for m in (models or []) if m and m != first]
    rank = {m: i for i, m in enumerate(order or [])}
    if rank:
        rest.sort(key=lambda m: rank.get(m, len(rank)))     # stable: an unnamed entry keeps its place, behind the named ones
    chain = [first] + rest
    shut = hours_closed(hours, at)
    return [m for m in chain if m not in spent and m not in shut] or chain


def hours_skipped(models, first, spent=(), hours=None, at=None):
    """[(model, reason)] the chain left out for its hours window; empty when `model_chain` kept every entry for want of another."""
    chain = [first] + [m for m in (models or []) if m and m != first]
    shut = hours_closed(hours, at)
    kept = model_chain(models, first, spent, hours, at)
    return [(m, f"outside_hours:{shut[m]}") for m in chain if m in shut and m not in kept]


CHARS_PER_TOKEN = 4     # the prompt-size estimate: one token per four characters, rounded up. The code holds no tokenizer.


def estimate_tokens(text):
    """The prompt's size in tokens, one per four characters rounded up. An estimate, never a measure."""
    return (len(text or "") + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def ctx_skipped(chain, prompt_tokens, windows):
    """([models kept], [(model, reason)]) for a chain entry whose declared context window is under the prompt (#1470).

    `windows` maps a model spelling to its `ctx` declaration as models.json writes it (`"128k"`). An entry with no
    readable window has no ceiling and stays. A chain whose every entry is skipped stays whole, so a wrong declaration
    never leaves no model, as the spent and hours rules do.
    """
    closed = [(m, w) for m in chain if (w := modelreg.ctx_tokens((windows or {}).get(m))) is not None and w < prompt_tokens]
    kept = [m for m in chain if m not in {m for m, _ in closed}]
    if not kept:
        return list(chain), []
    return kept, [(m, f"context_window:window={w},prompt={prompt_tokens}") for m, w in closed]


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
    allowed. The adapter carries a message up to its reader buffer and records a message over its own ceiling while the
    call goes on (#2267); this diagnostic is the frame past the buffer, which the reader cannot frame at all, so the
    session cannot go on and the record says so instead of leaving the run's death unexplained. The only recovery is a
    fresh call, which the engine never makes silently (ADR-0102 rule 1: one entry is one fresh session), so the record
    asks for one.
    """
    d = {"error": message or str(err)}
    if isinstance(err, MessageOverCeiling):
        d["transport"] = {"ceiling": err.ceiling, "size": err.size, "dropped": False, "continued": False, "recovery": "fresh_call"}
    return d


STALL_LINE = "the call stopped at its stall cap"
# The silence one entry may keep before its call ends (#1289): an upstream that sends nothing for this long is hung, and the
# chain moves on to its next entry. A call with a tool open is not silent (the pulse). A project may set limits.no_progress_seconds.
NO_PROGRESS_SECONDS = 300.0


def no_progress_line(window):
    """The one line a silent entry carries on the record: the window it kept silent for."""
    return f"no_progress: no message for {window:g} s"


def answered_nothing(out):
    """True when an entry answered nothing and another entry of the chain may serve the same step.

    Two ways to answer nothing, and the chain moves on from both. The provider refused to serve (an outage, not a defect
    of the work). Or the call stopped at its own stall cap with no artifact: the model kept retrying one failing step
    without changing the failure, so the node would end on the first entry while a second stood untried (#1236).
    Or the upstream sent nothing for the no-progress window (#1289): a hung entry ends, and the next one answers.
    A cap that still produced an artifact is an answer, not this: the cap ends the node through its artifact (ADR-0105),
    so the chain never retries it on another entry. A clean result and a deadline are not this either: a clean result is
    an answer, and a deadline is the node's own bound, which the chain never spends twice."""
    if out.kind == "no_progress":
        return True
    if out.kind == "adapter_error":
        return provider_error(out.error)
    if out.kind != "result" or getattr(out.result, "stop_reason", None) != "stall_cap":
        return False
    return not isinstance(getattr(out.result, "output", None), dict)


TRANSPORT_BACKOFF = 2.0      # seconds one entry waits before its one retry after a transport failure (#1293)


def transport_error(out):
    """True when the call failed because the transport did not deliver, not because the model refused to serve. A
    connection refused or reset clears in seconds, so the entry is worth one fresh call; a message past the reader's
    ceiling is not this, and neither is a provider's refusal."""
    return out.kind == "adapter_error" and is_transport(str(out.error))


def entry_reason(out):
    """The line an entry that answered nothing carries on the record: its silence, its stall cap, or its own error."""
    if out.kind == "no_progress":
        return no_progress_line(out.window)
    return STALL_LINE if out.kind == "result" else str(out.error)


class ChainLog:
    """The chain's events on the record, one writer for the agent node and the dispatch child: a transport retry as it
    happens (#1293), a silent entry as it ends (#1289), and every other refusal when the walk ends (#494). `refuse(model, error)`
    sees each refusal the walk left, so a refusal that names its reset closes that account (#572)."""

    def __init__(self, record, node, entry, refuse=None):
        self.record, self.node, self.entry, self.silent, self.refuse = record, node, entry, set(), refuse

    def retried(self, model, out):
        self.record.append("model_retried", node=self.node, entry=self.entry, model=model, error=str(out.error)[:CHAIN_LINE])

    def silent_entry(self, model, out):
        self.silent.add(model)
        self.record.append("model_refused", node=self.node, entry=self.entry, model=model, error=entry_reason(out)[:CHAIN_LINE])

    def close(self, tried):
        """Every refusal the walk left is on the record, exhausted chain or not; a silent entry already is."""
        for m, err in tried:
            if m not in self.silent:
                self.record.append("model_refused", node=self.node, entry=self.entry, model=m, error=err[:CHAIN_LINE])
            if self.refuse is not None:
                self.refuse(m, err)


async def run_chain(chain, call, *, on_retry=None, on_silent=None, backoff=None, sleep=None):
    """Call one entry of a model chain at a time while the entry answers nothing. `call(model, first)` runs one attempt and
    returns `(outcome, who)`. Return the last outcome, every entry that answered nothing with its reason, and the last
    `who`.

    One entry that fails on the transport gets one second call after `backoff`, and `on_retry(model, out)` puts it on the
    record. Each call is its own metered call, so the retry counts the node's budget and the run's spend and never starts
    a new run. A retry that fails again is an entry that could not serve, so the chain goes on to its next entry. A silent
    entry (`no_progress`) is not retried: its window already waited, so the chain moves on at once, and `on_silent(model, out)`
    puts that end on the record as it happens, before the next entry starts (#1289)."""
    tried, out, who = [], None, None
    wait = anyio.sleep if sleep is None else sleep
    pause = TRANSPORT_BACKOFF if backoff is None else backoff    # read per call, so a test may shorten the module's value
    for i, model in enumerate(chain):
        out, who = await call(model, i == 0)
        if transport_error(out):
            if on_retry is not None:
                on_retry(model, out)
            await wait(pause)
            out, who = await call(model, False)
            if transport_error(out):    # the retry hit the transport too: this entry never served
                tried.append((model, str(out.error)))
                continue
        if not answered_nothing(out):
            break
        if out.kind == "no_progress" and on_silent is not None:
            on_silent(model, out)
        stalled = out.kind == "result"
        if stalled and i == len(chain) - 1:
            break       # a stall on the last entry keeps its own cap reason, so a one-entry chain reads as it always did
        tried.append((model, entry_reason(out)))
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
    """kind: no_funds | expired | prepare_failed | result | deadline | no_progress | adapter_error. A cancel raises, after the settle.
    `window` is the no-progress window a `no_progress` call kept silent for. `limited_by` names the bound that ended the
    call below its own deadline (`wave_ceiling`, #2840)."""
    kind: str
    res: object = None
    result: object = None
    error: Exception | None = None
    elapsed: float = 0.0
    reasons: list = field(default_factory=list)
    window: float | None = None
    limited_by: str | None = None


CEILING_POLL_SECONDS = 0.05   # how often a running call reads the wave ceiling again: a ceiling rewritten under a live wave bounds it (§#2840)


async def _ends_before_silence(done, pulse, window, ceiling=None, wall=None):
    """Why the wait ended: `ended` when the call answered, `silent` when the upstream kept no beat for `window` seconds
    with no tool open, or `ceiling` when the wave's age cap passed while the call ran (#2840). The pulse is read every
    tenth of the window, so silence is seen within that step; a call with a ceiling reads it again each poll, so a bound
    the loop rewrote under a live wave ends the call it already runs."""
    windowed = window is not None and not math.isinf(window)
    if not windowed and ceiling is None:
        await done.wait()
        return "ended"
    step = max(min(1.0, window / 10), 0.001) if windowed else 1.0
    if ceiling is not None:
        step = min(step, CEILING_POLL_SECONDS)
    while not done.is_set():
        if ceiling is not None:
            epoch = ceiling()
            if epoch is not None and wall() >= epoch:
                return "ceiling"
        with anyio.move_on_after(step):
            await done.wait()
        quiet = pulse.silent_for() if windowed else None
        if not done.is_set() and quiet is not None and quiet >= window:
            return "silent"
    return "ended"


async def run_call(source, deadline, make_request, *, chain, adapter, record, budget, clock, wall,
                   registry=None, prepare=None, on_deadline=None, on_cancel=None, no_progress=NO_PROGRESS_SECONDS, limit_by=None,
                   ceiling=None, on_ceiling=None):
    """Reserve from `source()`, run the call, settle once. `make_request(res)` builds the CallRequest.

    `chain` is the ordered models this call may answer on, already led by its pick and with the spent and hours-closed
    entries left out: the record states it beside the call, as registry ids (`modelreg.chain_ids`) and one hash of that
    order (`modelreg.chain_sha256`), so a reader never rebuilds it from a later configuration (SPEC 1035). `registry` is
    the registry those ids resolve through; None is the committed one.
    `prepare(res)` may refuse with a list of reasons before the call starts; the grant is released.
    `on_deadline(res, elapsed)` and `on_cancel(res, elapsed)` write the caller's event after the settle. `on_ceiling`
    writes it instead when the wave ceiling ended the call (#2840); it falls back to `on_deadline`.
    `no_progress` is the silence, in seconds, one call may keep: the adapter beats the pulse on each message it reads, and a
    call with no beat for that long ends as `no_progress` (#1289). The node deadline is checked first and still wins.
    `limit_by` names the bound that set `deadline` when it is not the call's own deadline (`wave_ceiling`, #2840); the
    `call_start` event records it beside the limit.
    `ceiling()` reads the absolute epoch the call must end by, or None; the call reads it at its start and again while it
    runs, so a ceiling the loop writes or rewrites under a live wave ends the call it already runs (#2840).
    """
    res = source()
    if res is None:
        return CallOutcome("no_funds")
    by = limit_by
    if ceiling is not None:
        epoch = ceiling()
        if epoch is not None and epoch - wall() < deadline:
            deadline, by = max(0.0, epoch - wall()), "wave_ceiling"
    if deadline <= 0:
        budget.release(res)
        return CallOutcome("expired", res, limited_by=by)
    if prepare is not None:
        why = await prepare(res)
        if why:
            budget.release(res)
            return CallOutcome("prepare_failed", res, reasons=list(why))
    pulse = Pulse()
    req = replace(make_request(res), pulse=pulse)
    t0, box, hit, silent = clock(), {}, [], []
    done = anyio.Event()
    ids = modelreg.chain_ids(chain, registry)
    record.append("call_start", node=req.node, entry=req.entry, item=req.item, session=req.session_id,
                  pid=os.getpid(), limit=None if deadline == math.inf else deadline, started_at=wall(),
                  chain=ids, chain_id=modelreg.chain_sha256(ids), **({"limit_by": by} if by else {}))

    async def runner():
        try:
            box["r"] = await adapter.call(req)
        except AdapterError as e:
            box["err"] = e
        finally:
            done.set()

    def fire(hook, el):
        fn = hook or on_deadline
        if fn:
            fn(res, el)

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(runner)
            ended = "ended"
            with anyio.move_on_after(deadline) as scope:
                ended = await _ends_before_silence(done, pulse, no_progress, ceiling, wall)
            if scope.cancelled_caught and not done.is_set():
                budget.settle(res, None)
                hit.append(clock() - t0)
                fire(on_ceiling if by == "wave_ceiling" else on_deadline, hit[0])
                tg.cancel_scope.cancel()
            elif ended == "ceiling":
                budget.settle(res, None)
                hit.append(clock() - t0)
                by = "wave_ceiling"
                fire(on_ceiling, hit[0])
                tg.cancel_scope.cancel()
            elif ended == "silent":
                budget.settle(res, None)
                silent.append(clock() - t0)
                tg.cancel_scope.cancel()
    except anyio.get_cancelled_exc_class():
        with anyio.CancelScope(shield=True):
            budget.settle(res, None)
            if on_cancel:
                on_cancel(res, clock() - t0)
        raise
    if hit:
        return CallOutcome("deadline", res, elapsed=hit[0], limited_by=by)
    if silent:
        return CallOutcome("no_progress", res, elapsed=silent[0], window=no_progress)
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
    """The held-out commands an artifact lists, and whether the list is well formed. A command is a non-empty string, or a mapping
    whose `command` value is one (`{"command": "...", "expected": "..."}`: `expected` is prose, not a command, and is ignored). One
    level of nesting (`[["cmd"]]`, a router's "one-item list" copied whole) is flattened. Any other entry (a mapping such as
    `{"cp": "..."}`) is malformed: it would reach the shell as text and fail the final gate for a reason that is not the code."""
    raw = out.get("ac_commands", []) if isinstance(out, dict) else []
    if not isinstance(raw, list):
        return [], False
    cmds, ok = [], True
    for c in raw:
        for x in (c if isinstance(c, list) else [c]):
            if isinstance(x, dict):
                x = x.get("command")
            if isinstance(x, str) and x.strip():
                cmds.append(x)
            else:
                ok = False
    return cmds, ok


def missing_artifact_reason(res, cap_reason, granted=None):
    """Why a session that wrote no artifact stopped: the cap, its own error, a clean end that spent its whole grant
    (late), or a clean end with budget left (early)."""
    if cap_reason:
        return cap_reason
    if not res.ended:
        return "session_end_unknown"
    if res.ended.get("is_error") or res.ended.get("subtype") != "success":
        return f"session_error:{res.ended.get('subtype') or 'unknown'}"
    if granted is not None and res.spend is not None and res.spend >= granted:
        return "artifact_missing"
    return "child_ended_early"


# The words a reason uses to name the check that could not run: a gate line, a check of the run, or a held-out command.
CHECK_NAMED = re.compile(r"\b(unproven|gate|check|ac-verify|ac_failed|fast-gate)\b", re.IGNORECASE)


def _unproven_evidence(out):
    """True when an `unproven` artifact names work that exists or the check that could not run it.

    `unproven` says the work is done and the proof could not finish (ADR-0164), so a bare status proves nothing: the
    artifact carries a diff with a file, a commit, or a reason naming the check that could not run (#1825).
    """
    diff = out.get("diff")
    files = diff.get("files") if isinstance(diff, dict) else None
    if isinstance(files, list) and files:
        return True
    if out.get("commit") or out.get("head"):
        return True
    return any(isinstance(r, str) and CHECK_NAMED.search(r) for r in out.get("reasons", []))


END_CLASSES = ("owner-only", "does-not-reproduce")


def end_class(out):
    """The class a failed artifact names for its end reason. The artifact's own field decides it, never the reason text (#2217)."""
    if not isinstance(out, dict) or out.get("status") != "fail":
        return None
    return out.get("end_class") if out.get("end_class") in END_CLASSES else None


def spawns_shape(out):
    """The shape a dispatch entry's answer gave for its `spawns` list: `none` (no artifact), `missing` (no key), `list`
    (the expected shape), or the JSON type of the value it read. A dispatch entry's contract is that list, so any other
    shape is outside it (#2336)."""
    if not isinstance(out, dict):
        return "none"
    if "spawns" not in out:
        return "missing"
    v = out["spawns"]
    if isinstance(v, list):
        return "list"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, str):
        return "string"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def agent_outcome(node, res, cap_reason, granted=None):
    """Derive the outcome from the declared artifact. An agent node and a dispatch child share it.

    The artifact carries the three outcomes of ADR-0164: `pass`, `fail`, and `unproven` for work no check could measure
    (#1825). An `unproven` artifact is no failing child: it names the check that could not run, and the run carries that
    obligation instead of holding the work for a person. It carries evidence of the work or of that check, though: a bare
    `unproven` status proves nothing, so it fails.
    """
    out = res.output
    if not isinstance(out, dict):
        return "fail", [missing_artifact_reason(res, cap_reason, granted)], None
    key = node.get("output_key")
    if key and key not in out and out.get("status") not in ("needs-human", "fail", "unproven"):    # an explicit stop answer needs no `output_key`
        return "fail", [missing_artifact_reason(res, cap_reason, granted)], None
    if key == "spawns" and out.get("status") not in ("needs-human", "fail", "unproven") and spawns_shape(out) != "list":
        return "fail", [f"spawns_shape:{spawns_shape(out)}"], None      # a dispatch entry's contract is the list; an explicit stop answer needs none (#2336)
    if "ac_commands" in out and not ac_commands(out)[1]:
        return "fail", ["ac_commands_invalid"], None      # the child's contract is malformed: it fails, so the router repairs it
    status = out.get("status", "pass")
    if status not in ("pass", "fail", "needs-human", "unproven"):
        return "fail", ["artifact_invalid"], None
    if status == "pass":      # a pass keeps only its provisional decisions; every other reason on a pass is dropped as before
        return status, [r for r in out.get("reasons", []) if isinstance(r, str) and r.startswith(PROVISIONAL)], cap_reason
    if status == "unproven" and not _unproven_evidence(out):
        return "fail", ["unproven_without_evidence"], None    # nothing done and nothing named: it proved nothing at all (#1825)
    reasons = [_claim(r) for r in out.get("reasons", [status])]
    return status, reasons, cap_reason
