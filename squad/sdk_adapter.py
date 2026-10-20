"""The real adapter: one call is one Claude Agent SDK `query()`. It fills the seam in squad.adapter.

Spend is the `total_cost_usd` of the session's own `ResultMessage` (origin empty). A call with no
`ResultMessage` raises AdapterError, so the engine keeps the node cap reserved and never counts zero.
A gateway model with an entry in the price table (squad/prices.py) spends its tokens at the table rates instead,
and the SDK gets a budget scaled so its borrowed Claude price never stops the call before the true cap.
A deadline cancels the task; the stream is closed in `finally`, which stops the CLI child (docs/research/sdk-live-smoke.md).

A call in flight writes a `progress` event through the sink, at most once per `progress_seconds`. The
record then says where the call has got to — its assistant messages so far, the context the newest message read, and
the tool it last used — so a reader can tell a live call from an abandoned one. Every other message
is read and dropped.
"""
import json
import os
import re
import time

import anyio

from squad import modelreg, prices, sandbox, workcmd
from squad.adapter import AdapterError, CallResult, MessageOverCeiling
from squad.stall import StallWatch

STOP_REASONS = {"error_max_budget_usd": "spend_cap"}
OUTPUT_CAP_TEXT = "output token maximum"
FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
CEILING_TEXT = re.compile(r"exceeded maximum buffer size of (\d+) bytes")
FRAME_TEXT = re.compile(r"Buffer size (\d+) exceeds limit (\d+)")
PROGRESS_SECONDS = 10.0     # the default interval between two progress events of one call; a setting in agent-defaults.yaml
INPUT_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
CACHE_KEYS = (("cache_read", "cache_read_input_tokens"), ("cache_write", "cache_creation_input_tokens"))


def ended_of(result, subtype):
    """How a session ended, for an outcome with no artifact: the result subtype, the error flag and the head of its last text."""
    last = getattr(result, "result", None)
    return {"subtype": subtype, "is_error": bool(getattr(result, "is_error", False)), "last": last[:500] if isinstance(last, str) else None}


def usage_of(result):
    """tokens_in counts every prompt token (plain, cache write, cache read); tokens_out counts output; cache_read and cache_write are the cache share of tokens_in. None when unreported."""
    u = getattr(result, "usage", None)
    if not isinstance(u, dict) or not isinstance(u.get("output_tokens"), int):
        return None
    tin = sum(v for k in INPUT_KEYS if isinstance(v := u.get(k), int))
    out = {"tokens_in": tin, "tokens_out": u["output_tokens"]}
    for field, key in CACHE_KEYS:       # the split of tokens_in; a key the provider omits stays absent, a reported 0 stays 0
        if isinstance(v := u.get(key), int):
            out[field] = v
    return out


def failed_spend(table, model, result):
    """What a call spent before the provider ended it: the result's measured cost, or its tokens at the model's price.

    The CLI reports a terminal error as a `result` message and then exits non-zero, so a call that did work before it was
    refused still holds the spend and the tokens of that work. The figure follows the same rule as a normal result: a
    priced model's tokens at the table rates, else the SDK's `total_cost_usd`. A result with neither is estimated from
    its tokens at the client rates the SDK borrows. None when there is no result: its spend is unknown, never zero, and
    the grant stays reserved.
    """
    if result is None:
        return None, None, None
    usage = usage_of(result)
    rates = prices.rates(table, model)
    if rates is not None and usage is not None:
        return prices.spend(rates, usage), usage, prices.TABLE_SOURCE
    spend = getattr(result, "total_cost_usd", None)
    if spend is not None:
        return spend, usage, prices.SDK_SOURCE
    if usage is not None and (table or {}).get("client"):
        return prices.spend(table["client"], usage), usage, prices.TABLE_SOURCE
    return None, usage, None


def extract_json(text):
    """The node artifact: the last fenced JSON object in the text, else the last top-level object. None when absent."""
    if not text:
        return None
    for body in reversed(FENCE.findall(text)):
        obj = _loads(body)
        if obj is not None:
            return obj
    dec, best, i = json.JSONDecoder(), None, text.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i = text.find("{", i + 1)
            continue
        best = obj if isinstance(obj, dict) else best
        i = text.find("{", end)
    return best


def _loads(s):
    try:
        obj = json.loads(s)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def ceiling_hit(err):
    """`(ceiling, size)` when this error is the SDK reader's JSON ceiling, else None.

    The reader (`claude_agent_sdk/_internal/transport/subprocess_cli.py`, the `guard()` inside `_read_messages_impl`)
    raises `CLIJSONDecodeError("JSON message exceeded maximum buffer size of <ceiling> bytes", ValueError("Buffer size
    <size> exceeds limit <ceiling>"))` — the ceiling in the sentence it keeps as `line`, the size of the frame that hit
    it in the original error. Both are read off the error rather than assumed, and `size` stays None when a version
    reports the ceiling without the frame behind it. The SDK is never imported for this: the text is the fact.
    """
    texts = [str(err), str(getattr(err, "line", "") or "")]
    for e in (getattr(err, "original_error", None), err.__cause__, err.__context__):
        if e is not None:
            texts.append(str(e))
    ceiling = size = None
    for t in texts:
        if m := FRAME_TEXT.search(t):      # the reader's own complaint names the frame that hit the limit
            ceiling, size = int(m.group(2)), int(m.group(1))
        elif ceiling is None and (m := CEILING_TEXT.search(t)):
            ceiling = int(m.group(1))
    return (ceiling, size) if ceiling is not None else None


def _stalled(watch, message):
    """Feed the tool results of a user message to the stall watch. True when one of them reaches the cap."""
    content = getattr(message, "content", None)
    hit = False
    for b in content if isinstance(content, list) else []:
        if type(b).__name__ == "ToolResultBlock":
            hit = watch.result(getattr(b, "tool_use_id", None), bool(getattr(b, "is_error", False)), getattr(b, "content", None)) or hit
    return hit


class _Progress:
    """Where one call in flight has got to, written to the run record now and then.

    `messages` counts the assistant messages the call has produced. It is not the result's `turns`: probed on
    2026-10-02, one turn reaches the stream as two assistant messages, so the two figures differ and neither stands for
    the other. `context_tokens` is the prompt the newest message read: the CLI reports `input_tokens` plus the cache
    buckets of that response, so the last message gives the size of the context now. It stays None until a message
    reports usage, so an unreported figure reads unknown, never zero. The CLI reports no output tokens
    before the result, so none is claimed. The tool is the last one the call used. The first write waits out the
    interval, so a call shorter than the interval writes none, and an interval of None writes none at all.
    """

    def __init__(self, req, sink, seconds):
        self.req, self.sink, self.seconds = req, sink, seconds
        self.messages, self.context_tokens, self.tool = 0, None, None
        self.last = time.monotonic()

    def take(self, message):
        self.messages += 1
        u = getattr(message, "usage", None)
        counts = [v for k in INPUT_KEYS if isinstance(v := (u.get(k) if isinstance(u, dict) else None), int)]
        if counts:      # a message that reports no whole number leaves the last reading alone: an unreported figure is unknown, never zero
            self.context_tokens = sum(counts)
        for block in getattr(message, "content", None) or []:
            if type(block).__name__ == "ToolUseBlock":
                self.tool = getattr(block, "name", None)
        self.write()

    def write(self):
        if not self.sink or self.seconds is None:
            return
        now = time.monotonic()
        if now - self.last < self.seconds:
            return
        self.last = now
        self.sink("progress", node=self.req.node, entry=self.req.entry, item=self.req.item, session=self.req.session_id,
                  messages=self.messages, context_tokens=self.context_tokens, tool=self.tool)


class SdkAdapter:
    """`query` and `options_cls` are injectable so a test runs with no SDK and no spend."""

    def __init__(self, query=None, options_cls=None, setting_sources=("project",), extra_options=None, hook_cls=None, log_compaction=False,
                 hook_timeout=60, progress_seconds=PROGRESS_SECONDS, prices=None, registry=None, resolve=False):
        self.prices = prices      # the `prices` block of agent-defaults.yaml: {client, models}; None prices no model
        self.registry, self.resolve = registry, resolve   # the run_start `models` setting: map a spelling only when it is true
        self._reg = None          # the loaded registry, read once
        self._query, self._options_cls, self._hook_cls = query, options_cls, hook_cls
        self.setting_sources = list(setting_sources)
        self.extra_options = dict(extra_options or {})
        self.log_compaction, self.hook_timeout, self.sink = log_compaction, hook_timeout, None
        self.progress_seconds = progress_seconds

    @classmethod
    def from_defaults(cls, cfg, root=None, **kw):
        """Build the adapter from the engine-level keys of config/agent-defaults.yaml."""
        from squad import defaults
        opts = defaults.sdk_kwargs(cfg, root or defaults.ROOT)
        progress = (cfg.get("progress") or {}).get("seconds", PROGRESS_SECONDS)
        return cls(setting_sources=opts.pop("setting_sources"), extra_options=opts, log_compaction=cfg["hooks"]["log_compaction"],
                   hook_timeout=cfg["hooks"]["timeout_seconds"], progress_seconds=progress, prices=cfg.get("prices"), **kw)

    def bind(self, sink):
        """The engine hands over a function that appends an event to the run record."""
        self.sink = sink

    def _matcher_cls(self):
        if self._hook_cls is None:
            try:
                import claude_agent_sdk as sdk
                self._hook_cls = sdk.HookMatcher
            except ImportError:     # a dev install has no SDK, and no session runs there
                self._hook_cls = sandbox.Matcher
        return self._hook_cls

    def _hooks(self, req):
        """The sandbox wall on every session (PreToolUse, squad/sandbox.py), and a PreCompact hook that logs each
        compaction to the run record when the setting asks for it. The timeout is set on the matcher (G12)."""
        cls = self._matcher_cls()

        def on_deny(tool, reason):
            if self.sink:
                self.sink("sandbox_deny", node=req.node, entry=req.entry, item=req.item, session=req.session_id, tool=tool, reason=reason)
        hooks = {"PreToolUse": [cls(matcher=sandbox.MATCHER, hooks=[sandbox.pre_tool_use(req.workspace or os.getcwd(), on_deny)],
                                    timeout=self.hook_timeout)]}
        if not self.log_compaction:
            return hooks

        async def on_compact(input_data, tool_use_id, context):
            if self.sink:
                self.sink("compaction", node=req.node, entry=req.entry, item=req.item, session=req.session_id, trigger=input_data.get("trigger"))
            return {}
        hooks["PreCompact"] = [cls(hooks=[on_compact], timeout=self.hook_timeout)]
        return hooks

    def _sdk(self):
        if self._query is None or self._options_cls is None:
            import claude_agent_sdk as sdk  # imported late: the engine and its tests never need it
            self._query = self._query or sdk.query
            self._options_cls = self._options_cls or sdk.ClaudeAgentOptions
        return self._query, self._options_cls

    def _model(self, req):
        """The model string the SDK sends: the resolver's wire string when run_start asked for resolution, else raw."""
        if not self.resolve:
            return req.model
        if self._reg is None:
            self._reg = modelreg.load(self.registry) if self.registry else modelreg.default_registry()
        return modelreg.invocation(req.model, "sdk", reg=self._reg).wire

    def options(self, req):
        _, cls = self._sdk()
        kw: dict[str, object] = dict(cwd=req.workspace, model=self._model(req), tools=list(req.tools),
                                     max_budget_usd=prices.sdk_budget(self.prices, req.model, req.max_spend), permission_mode="bypassPermissions",
                                     setting_sources=self.setting_sources)
        kw.update(self.extra_options)   # the engine's defaults land first: the node's own pick has the last word
        if req.effort:
            kw["effort"] = req.effort
        for name, var in (("max_output_tokens", "CLAUDE_CODE_MAX_OUTPUT_TOKENS"), ("context_tokens", "CLAUDE_CODE_AUTO_COMPACT_WINDOW")):
            if getattr(req, name, None):    # the project settings file reaches the call
                kw["env"] = {**kw.get("env", {}), var: str(getattr(req, name))}
        kw["env"] = {**kw.get("env", {}), **workcmd.bounded_env(workcmd.child_workers())}   # the actor's pytest runs a bounded worker count (#608)
        if "VIRTUAL_ENV" in os.environ:     # the actor's Bash reaches the project's python3, as a gate command does
            clean = workcmd.clean_env(os.environ)
            kw["env"] = {**kw.get("env", {}), "VIRTUAL_ENV": "", "PATH": clean["PATH"]}
        prof = {var: os.environ[var] for var in ("SG_PROFILE", "SG_PROFILES_DIR") if os.environ.get(var)}
        if prof:     # #664: the worktree's own prepush.sh resolves the same profile the loop uses
            kw["env"] = {**kw.get("env", {}), **prof}
        if req.instructions:     # the role text joins the system prompt after the shared contract; the user message holds only the JSON
            sp = kw.get("system_prompt")
            sp = dict(sp) if isinstance(sp, dict) else {"type": "preset", "preset": "claude_code"}
            sp["append"] = "\n\n".join(x for x in (sp.get("append"), req.instructions) if x)
            kw["system_prompt"] = sp
        kw["hooks"] = self._hooks(req)
        return cls(**kw)

    async def call(self, req):
        query, _ = self._sdk()
        result, subagents, output_cap, served = None, 0, False, False
        prog = _Progress(req, self.sink, self.progress_seconds)
        watch = StallWatch(req.max_stalls)
        stream = query(prompt=req.prompt, options=self.options(req))
        try:
            async for m in stream:
                kind = type(m).__name__
                if kind == "AssistantMessage":
                    # A spent subscription answers with a synthetic message that carries an error (#507): the model served
                    # nothing, so it does not make the call served, and the grant goes back.
                    served = served or not (getattr(m, "error", None) or getattr(m, "model", None) == "<synthetic>")
                    prog.take(m)
                    for b in getattr(m, "content", None) or []:
                        if type(b).__name__ == "ToolUseBlock":
                            watch.use(getattr(b, "id", None), getattr(b, "name", None), getattr(b, "input", None))
                    continue
                if kind == "UserMessage":
                    if _stalled(watch, m):
                        break
                    continue
                if kind != "ResultMessage":
                    continue
                if getattr(m, "origin", None):
                    subagents += 1
                else:
                    result = m
        except Exception as e:   # broad on purpose: the SDK raises several types; each becomes AdapterError with its type kept; only a capped result is kept
            # A refusal after the work carries what the work spent: the result is in hand before the CLI exits non-zero.
            spend, usage, source = failed_spend(self.prices, req.model, result)
            # Probed 2026-10-11 (run-20261011-171934-888e92, issue #1814): one message past the reader's JSON ceiling
            # ends the read stream inside the transport — `guard()` raises out of its own frame loop — so the frame
            # cannot be dropped and the session cannot go on. The ceiling and the size of that frame are the only facts
            # there are, and they are what this error carries.
            if over := ceiling_hit(e):
                ceiling, size = over
                raise MessageOverCeiling(
                    f"the transport's reader stopped at its {ceiling}-byte JSON ceiling: the session's message was "
                    f"{size if size is not None else 'an unmeasured number of'} bytes", ceiling, size,
                    spend=spend, usage=usage, spend_source=source) from e
            # Probed 2026-09-30: a response past CLAUDE_CODE_MAX_OUTPUT_TOKENS ends the call with an error that follows a measured ResultMessage.
            output_cap = result is not None and OUTPUT_CAP_TEXT in str(e)
            # Probed 2026-10-01: a call stopped at its spend cap raises in the same way (subtype
            # error_max_budget_usd). Keep its ResultMessage too: the spend rules end a capped
            # node through its artifact, and the result carries the reason, the spend, and the turns.
            capped = result is not None and getattr(result, "subtype", None) in STOP_REASONS
            if not output_cap and not capped:   # an unmapped error: its result's spend if it produced one, else unknown, unless nothing was served (#494)
                raise AdapterError(f"{type(e).__name__}: {e}", served=served, spend=spend, usage=usage, spend_source=source) from e
        finally:
            close = getattr(stream, "aclose", None)
            if close:
                with anyio.CancelScope(shield=True):
                    await close()
        if watch.tripped:     # the call stopped itself: no ResultMessage exists, so its spend is unknown and no artifact is read
            return CallResult(None, None, None, "stall_cap", subagents, req.session_id, ended={"subtype": "stall_cap", "is_error": True, "last": watch.tripped})
        if result is None:
            raise AdapterError("the stream ended with no ResultMessage")
        subtype = getattr(result, "subtype", None)
        reason = "output_cap" if output_cap else (STOP_REASONS.get(subtype) if isinstance(subtype, str) else None)
        usage, spend, source = usage_of(result), getattr(result, "total_cost_usd", None), prices.SDK_SOURCE
        rates = prices.rates(self.prices, req.model)
        if rates is not None and usage is not None:     # a priced gateway model: its own price, never the borrowed one
            spend, source = prices.spend(rates, usage), prices.TABLE_SOURCE
        return CallResult(extract_json(getattr(result, "result", None)), spend,
                          getattr(result, "num_turns", None), reason,
                          subagents, getattr(result, "session_id", None) or req.session_id,
                          ended=ended_of(result, subtype), usage=usage, spend_source=source if spend is not None else None)
