"""The SDK adapter seam: one call is one session. The engine knows only this interface.

A later step swaps the scripted adapter for the real Claude Agent SDK. The real adapter owns its child
process and must stop it when its task is cancelled (see squad.procs).
"""
import itertools
from dataclasses import dataclass


class AdapterError(Exception):
    """The call failed before it produced a result. Its spend is unknown."""


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


@dataclass
class CallResult:
    """The result of one session. `spend` is the measured cost of the result whose origin is empty, or None."""
    output: dict | None
    spend: float | None
    turns: int | None = None
    stop_reason: str | None = None   # "stall_cap" | "spend_cap" | "output_cap" | None
    subagents: int = 0               # diagnostic only: a subagent is never a node
    session_id: str | None = None
    usage: dict | None = None        # {"tokens_in", "tokens_out", optional "cache_read", "cache_write"} when the result reports them; None means unknown, never zero
    ended: dict | None = None        # {"subtype", "is_error", "last"}: how the session ended and its last text, kept on an artifact_missing outcome


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
            raise AdapterError(step["error"])
        if step.get("effect"):
            step["effect"](req)
        return CallResult(step.get("output"), step.get("spend"), step.get("turns"), step.get("stop_reason"),
                          step.get("subagents", 0), req.session_id, step.get("usage"))


_session = itertools.count(1)


def new_session_id(run_id):
    return f"{run_id}-s{next(_session)}"
