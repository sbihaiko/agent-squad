"""The SDK adapter seam: one call is one session. The engine knows only this interface.

A later step swaps the scripted adapter for the real Claude Agent SDK. The real adapter owns its child
process and must stop it when its task is cancelled (see squad.procs).
"""
import itertools
from dataclasses import dataclass, field

import anyio


class AdapterError(Exception):
    """The call failed before it produced a result. Its spend is unknown, unless `served` is False: the provider refused
    the request before it produced one message, so the call spent nothing and its grant goes back (#494). A refusal that
    did work carries what that work spent: the result's measured cost, or an estimate from its tokens."""

    def __init__(self, message="", served=True, spend=None, usage=None, spend_source=None):
        super().__init__(message)
        self.served = served
        self.spend = spend
        self.usage = usage
        self.spend_source = spend_source


class MessageOverCeiling(AdapterError):
    """The transport's reader stopped on one message bigger than the buffer it frames.

    The ceiling and the size of the frame that hit it are facts about the transport, not about the work: the model never
    answered badly, its message was simply past what the reader reads. They are kept apart from every other adapter
    failure so an operator does not have to read the diagnostic text to tell the two apart (issue #1814). A message over
    the adapter's own ceiling, but within its reader buffer, is no longer this: the call goes on and the record carries
    one `message_over_ceiling` event (issue #2267).
    """

    def __init__(self, message, ceiling, size, **kw):
        super().__init__(message, **kw)
        self.ceiling, self.size = ceiling, size


class Pulse:
    """What the no-progress watch reads of one call in flight (#1289, squad/session.py). The adapter beats it on every message
    the session streams, and marks a tool open from its use until its result. A tool that is still running is not silence:
    its time is bounded by the node deadline, never by the upstream's no-progress window. `beats` counts the messages read."""

    def __init__(self, now=None):
        self.now = now or anyio.current_time
        self.touched, self.beats, self.open = self.now(), 0, set()

    def beat(self):
        """One message read from the stream."""
        self.beats += 1
        self.touched = self.now()

    def tool_started(self, tool_id):
        self.open.add(tool_id)
        self.touched = self.now()

    def tool_done(self, tool_id):
        self.open.discard(tool_id)
        self.touched = self.now()

    def silent_for(self):
        """Seconds since the last message, or None while a tool the call started has not returned."""
        return None if self.open else self.now() - self.touched


@dataclass
class CallRequest:
    node: str
    entry: int
    item: int | None
    prompt: str
    model: str
    effort: str | None
    tools: list
    max_stalls: int          # failed attempts at one step with no change in the failure (squad.stall); not a count of turns
    max_spend: float
    workspace: str | None
    deadline: float          # seconds the engine allows, after time scaling
    session_id: str          # always new: a repeat never resumes a session
    skill: str | None = None
    instructions: str | None = None   # the node's role text: a system prompt addition, not part of the user message
    max_output_tokens: int | None = None   # set by the project settings file; None leaves the adapter's own value
    context_tokens: int | None = None      # same: the context window before compaction
    pulse: Pulse | None = field(default=None, compare=False)   # set by the session's metered call; the adapter beats it per message (#1289)


@dataclass
class CallResult:
    """The result of one session. `spend` is the measured cost of the result whose origin is empty, or None."""
    output: dict | None
    spend: float | None
    turns: int | None = None
    stop_reason: str | None = None   # "stall_cap" | "spend_cap" | "output_cap" | None
    subagents: int = 0               # diagnostic only: a subagent is never a node
    session_id: str | None = None
    usage: dict | None = None        # {"tokens_in", "tokens_out", optional "cache_read", "cache_write", "cache_estimated"} when the result reports them (cache_estimated: the cache_read is an estimate, #783); None means unknown, never zero
    ended: dict | None = None        # {"subtype", "is_error", "last"}: how the session ended and its last text, kept on an outcome with no artifact
    spend_source: str | None = None  # "price_table" | "total_cost_usd": where `spend` came from (squad/prices.py); None when spend is unknown


class ScriptedAdapter:
    """An offline adapter. `script` maps a node name, or `node@entry`, to a list of steps used in order.

    A step is a dict (output, spend, turns, stop_reason, subagents, usage, delay, error, effect) or a callable
    taking the CallRequest. `effect(req)` may touch files in req.workspace. The last step of a list repeats
    when `repeat_last` is set; otherwise an exhausted script is a test defect and raises.
    """

    def __init__(self, script=None, repeat_last=False, default=None):
        self.script = {k: list(v) if isinstance(v, list) else [v] for k, v in (script or {}).items()}
        self.repeat_last, self.default = repeat_last, default
        self.calls = []
        self.sessions = set()

    def _step(self, req):
        for key in (f"{req.node}@{req.entry}", req.node):
            q = self.script.get(key)
            if q:
                return q.pop(0) if len(q) > 1 or not self.repeat_last else q[0]
        if self.default is not None:
            return self.default
        raise AssertionError(f"scripted adapter has no step for {req.node} entry {req.entry}")

    async def call(self, req):
        import anyio
        self.calls.append(req)
        assert req.session_id not in self.sessions, "a session id was reused"
        self.sessions.add(req.session_id)
        step = self._step(req)
        if callable(step):
            step = step(req)
        if isinstance(step, CallResult):
            return step
        if step.get("delay"):
            await anyio.sleep(step["delay"])
        if step.get("error"):
            raise AdapterError(step["error"], served=step.get("served", True), spend=step.get("spend"), usage=step.get("usage"),
                               spend_source=step.get("spend_source"))
        if step.get("effect"):
            step["effect"](req)
        return CallResult(step.get("output"), step.get("spend"), step.get("turns"), step.get("stop_reason"),
                          step.get("subagents", 0), req.session_id, step.get("usage"), ended=step.get("ended"),
                          spend_source=step.get("spend_source"))


_session = itertools.count(1)


def new_session_id(run_id):
    return f"{run_id}-s{next(_session)}"
