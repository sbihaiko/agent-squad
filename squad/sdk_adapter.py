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
import dataclasses
import json
import os
import re
import subprocess
import sys
import time
import uuid
from typing import Any

import anyio

from squad import bash_time, modelreg, prices, sandbox, workcmd
from squad.adapter import AdapterError, CallRequest, CallResult, MessageOverCeiling
from squad.stall import StallWatch

STOP_REASONS = {"error_max_budget_usd": "spend_cap"}
OUTPUT_CAP_TEXT = "output token maximum"
FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
ESCAPE = re.compile(r"\\(.)", re.S)     # one backslash pair; JSON allows only the escapes in JSON_ESCAPES
JSON_ESCAPES = '"\\/bfnrtu'
CEILING_TEXT = re.compile(r"exceeded maximum buffer size of (\d+) bytes")
FRAME_TEXT = re.compile(r"Buffer size (\d+) exceeds limit (\d+)")
# #2267: the reader's own 1 MiB limit killed a run: one 1114112-byte message ended run-20261118-091256-7b8177 after 75 s
# with nothing delivered. The adapter now carries a frame up to READER_BUFFER, so the reader never dies on a message the
# CLI wrote, and it keeps its own ceiling below that: a frame past TRANSPORT_CEILING is a fact of the record, and the
# session goes on. A frame past READER_BUFFER still ends the call, because the reader cannot frame it at all.
TRANSPORT_CEILING = 1024 * 1024     # bytes of one message; the SDK reader's own default, kept as the ceiling the adapter declares
READER_BUFFER = 16 * TRANSPORT_CEILING    # the SDK reader's buffer: the hard bound above which no message is framed


def frame_size(message):
    """Bytes of one message as the transport frames it: the JSON text of the message, read as UTF-8.
    The reader's limit counts the bytes of that one line, so the adapter measures the same text it was framed from."""
    try:
        text = json.dumps(dataclasses.asdict(message), separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):     # a message the SDK builds outside a dataclass: its text is the fact there is
        text = str(message)
    return len(text.encode())


def over_ceiling_line(size, ceiling):
    """The one line the session reads for a tool call whose message was past the ceiling."""
    return (f"transport: the message for this tool call was {size} bytes, over the {ceiling}-byte JSON ceiling of the "
            f"session's reader. It was not delivered. Make the same call with a smaller request.")


def ceiling_tools(message):
    """`[(tool_call_id, tool_name)]` of the calls one message carries; a tool result names the id it answers and no
    name. Empty when the message names no call."""
    out = []
    for block in getattr(message, "content", None) or []:
        kind = type(block).__name__
        if kind == "ToolUseBlock":
            out.append((getattr(block, "id", None), getattr(block, "name", None)))
        elif kind == "ToolResultBlock":
            out.append((getattr(block, "tool_use_id", None), None))
    return out


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
    if "cache_read" not in out:
        alt = _alt_cache_read(u)        # a gateway's own name for the cache share of input_tokens (#783)
        if alt is not None:
            out["cache_read"] = min(alt, tin)
        elif isinstance(turns := getattr(result, "num_turns", None), int) and turns > 1:
            # No cache key at all: a multi-turn session re-sends its growing context each turn, so the fresh input is
            # about the last context, 2 * total / turns. An estimate, marked so, never a measure.
            if (est := tin - min(tin, 2 * tin // turns)) > 0:   # an estimate of 0 says nothing, so none is written
                out["cache_read"], out["cache_estimated"] = est, True
    return out


def _alt_cache_read(u):
    """The cache-read count under a non-Anthropic name, or None. Each is a share of input_tokens, never an addition."""
    for path in (("prompt_tokens_details", "cached_tokens"), ("input_tokens_details", "cached_tokens"),
                 ("cache_read_tokens",), ("cached_tokens",)):
        v = u
        for k in path:
            v = v.get(k) if isinstance(v, dict) else None
        if isinstance(v, int):
            return v
    return None


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
    text = ESCAPE.sub(_escape, text)    # a copied shell command (`[ \\! -d x ]`) holds an escape JSON refuses; keep it literal (#766)
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


def _escape(m):
    return m.group(0) if m.group(1) in JSON_ESCAPES else "\\\\" + m.group(1)


def _loads(s):
    try:
        obj = json.loads(s)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


STDERR_TAIL = 2048    # characters of the CLI's stderr an unmapped error keeps (#698)


class _Tail:
    """The SDK's stderr callback: it keeps the last STDERR_TAIL characters the CLI wrote. The SDK raises an opaque
    ProcessError ("Check stderr output for details") and drops the text, so the record would not say why the CLI died.
    It holds only what the CLI printed; the session environment is never written to it."""

    def __init__(self, chained=None):
        self.buf, self.chained = "", chained

    def __call__(self, line):
        self.buf = (self.buf + str(line))[-STDERR_TAIL:]
        if self.chained:
            self.chained(line)

    def text(self):
        return self.buf.strip()


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
        self.command_class = None      # the class of the last Bash call; None for any other tool
        self.pending = {}              # Bash calls in flight: tool_use_id -> (class, monotonic start)
        self.last = time.monotonic()

    def take(self, message):
        self.messages += 1
        u = getattr(message, "usage", None)
        counts = [v for k in INPUT_KEYS if isinstance(v := (u.get(k) if isinstance(u, dict) else None), int)]
        if counts:      # a message that reports no whole number leaves the last reading alone: an unreported figure is unknown, never zero
            self.context_tokens = sum(counts)
        started = False
        for block in getattr(message, "content", None) or []:
            if type(block).__name__ == "ToolUseBlock":
                self.tool, self.command_class = getattr(block, "name", None), None
                if self.tool == "Bash":
                    command = (getattr(block, "input", None) or {}).get("command")
                    self.command_class = bash_time.classify(command)
                    self.pending[getattr(block, "id", None)] = (self.command_class, time.monotonic())
                    self._emit("start")
                    started = True
        if not started:
            self.write()

    def tool_done(self, tool_use_id):
        """The end of a Bash call: its class and elapsed seconds. A call the adapter did not start is ignored."""
        if (begun := self.pending.pop(tool_use_id, None)) is None:
            return
        self.tool, self.command_class = "Bash", begun[0]
        self._emit("end", round(time.monotonic() - begun[1], 3))

    def write(self):
        if not self.sink or self.seconds is None:
            return
        if time.monotonic() - self.last < self.seconds:
            return
        self._emit(None)

    def _emit(self, phase, elapsed=None):
        if not self.sink or self.seconds is None:
            return
        self.last = time.monotonic()
        self.sink("progress", node=self.req.node, entry=self.req.entry, item=self.req.item, session=self.req.session_id,
                  messages=self.messages, context_tokens=self.context_tokens, tool=self.tool,
                  command_class=self.command_class, phase=phase, elapsed=elapsed)


class SdkAdapter:
    """`query` and `options_cls` are injectable so a test runs with no SDK and no spend."""

    def __init__(self, query=None, options_cls=None, setting_sources=("project",), extra_options=None, hook_cls=None, log_compaction=False,
                 hook_timeout=60, progress_seconds=PROGRESS_SECONDS, prices=None, registry=None, resolve=False,
                 ceiling=TRANSPORT_CEILING, buffer_size=READER_BUFFER):
        self.ceiling, self.buffer_size = ceiling, buffer_size   # #2267: the declared ceiling, and the reader's buffer above it
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

    def _hooks(self, req, denies=None):
        """The sandbox wall on every session (PreToolUse, squad/sandbox.py), and a PreCompact hook that logs each
        compaction to the run record when the setting asks for it. The timeout is set on the matcher (G12).

        `denies` maps a tool call id to the reason a message past the ceiling left for it (#2267): the CLI asks this
        hook for that call, and the answer is the short error the session reads for it."""
        cls = self._matcher_cls()

        def on_deny(tool, reason):
            if self.sink:
                self.sink("sandbox_deny", node=req.node, entry=req.entry, item=req.item, session=req.session_id, tool=tool, reason=reason)

        async def over_ceiling(input_data, tool_use_id, context):
            reason = (denies or {}).get(tool_use_id)
            if reason is None:
                return {}
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}

        hooks = {"PreToolUse": [cls(matcher=sandbox.MATCHER, hooks=[over_ceiling, sandbox.pre_tool_use(req.workspace or os.getcwd(), on_deny)],
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

    def options(self, req: CallRequest, tail: _Tail | None = None, denies: dict | None = None) -> Any:
        _, cls = self._sdk()
        kw: dict[str, Any] = dict(cwd=req.workspace, model=self._model(req), tools=list(req.tools),
                                     max_budget_usd=prices.sdk_budget(self.prices, req.model, req.max_spend), permission_mode="bypassPermissions",
                                     setting_sources=self.setting_sources, max_buffer_size=self.buffer_size)   # #2267: the reader's buffer above the ceiling
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
        kw["hooks"] = self._hooks(req, denies)
        kw["cli_path"] = sandbox.WRAPPER     # #1469: every child's CLI, and so every process it starts, runs under the signal profile
        kw["env"] = {**kw.get("env", {}), **sandbox.sdk_cli_env()}
        if tail is not None:    # the CLI's stderr is piped only when a callback asks for it
            tail.chained = kw.get("stderr")
            kw["stderr"] = tail
        return cls(**kw)

    def _over_ceiling(self, req, message, size, denies):
        """One message past the adapter's ceiling: one record event naming the tool call and the size, and a short error
        the session reads for that call. The call goes on; the stream is never dropped for a frame the reader carried.

        A tool call in the frame is refused by the `over_ceiling` hook, so the model reads the size and the ceiling and
        makes a smaller call. A tool result in the frame answers a call the CLI already ran, so the session holds that
        result: the event names it, and the session needs no error for it.
        """
        tool, name = (ceiling_tools(message) or [(None, None)])[0]
        if self.sink:
            self.sink("message_over_ceiling", node=req.node, entry=req.entry, item=req.item, session=req.session_id,
                      tool=tool, name=name, size=size, ceiling=self.ceiling)
        for tool_id, tool_name in ceiling_tools(message):
            if tool_name is not None and tool_id is not None:     # a tool use: the hook refuses this call
                denies[tool_id] = over_ceiling_line(size, self.ceiling)

    async def call(self, req):
        query, _ = self._sdk()
        result, subagents, output_cap, served = None, 0, False, False
        prog = _Progress(req, self.sink, self.progress_seconds)
        watch = StallWatch(req.max_stalls)
        tail = _Tail()
        denies = {}     # #2267: tool call id -> the short error a message past the ceiling left for it
        stream = query(prompt=req.prompt, options=self.options(req, tail, denies))
        try:
            async for m in stream:
                kind = type(m).__name__
                if req.pulse is not None:      # every message the call reads is a sign of life for the no-progress window (#1289)
                    req.pulse.beat()
                if (size := frame_size(m)) > self.ceiling:     # #2267: one oversize message is a record fact, never the run's end
                    self._over_ceiling(req, m, size, denies)
                if kind == "AssistantMessage":
                    # A spent subscription answers with a synthetic message that carries an error (#507): the model served
                    # nothing, so it does not make the call served, and the grant goes back.
                    served = served or not (getattr(m, "error", None) or getattr(m, "model", None) == "<synthetic>")
                    prog.take(m)
                    for b in getattr(m, "content", None) or []:
                        if type(b).__name__ == "ToolUseBlock":
                            watch.use(getattr(b, "id", None), getattr(b, "name", None), getattr(b, "input", None))
                            if req.pulse is not None:      # a tool that runs is not silence: its own time is the node deadline's
                                req.pulse.tool_started(getattr(b, "id", None))
                    continue
                if kind == "UserMessage":
                    for b in m.content if isinstance(getattr(m, "content", None), list) else []:
                        if type(b).__name__ == "ToolResultBlock":
                            prog.tool_done(getattr(b, "tool_use_id", None))
                            if req.pulse is not None:
                                req.pulse.tool_done(getattr(b, "tool_use_id", None))
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
            # ends the read stream inside the transport — `guard()` raises out of its own frame loop. The adapter's
            # buffer (READER_BUFFER) now carries every message up to that bound, and a message past the adapter's own
            # ceiling is handled in the loop (#2267); only a frame past the buffer reaches here, and the ceiling and
            # the size of that frame are the only facts there are.
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
                said = f"\nstderr tail: {tail.text()}" if tail.text() else ""
                raise AdapterError(f"{type(e).__name__}: {e}{said}", served=served, spend=spend, usage=usage, spend_source=source) from e
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


HOSTED_MODEL = "claude-sonnet-5-5"
HOSTED_TOOLS = ["Read", "Glob", "Grep", "Edit", "MultiEdit", "Write", "Bash"]


def host():
    """A hosted session: each stdin line is a prompt run through this adapter's options, or a `!` shell command run in the
    working directory. The caller writes the lines and reads what this prints.

    One event loop serves every prompt of the process (#1320). A loop per prompt let an SDK child outlive the loop that
    watched it: the child watcher is process-wide, so a child of one prompt was still being waited on when that loop
    closed, and the next prompt's transport could read its own child as reaped behind the SDK (`Unknown child process
    pid N, will report returncode 255`)."""
    adapter = SdkAdapter()
    anyio.run(_serve, adapter)


async def _serve(adapter):
    """The hosted process's one loop: a line of stdin, then a `!` command or a prompt, until stdin closes."""
    while True:
        line = await anyio.to_thread.run_sync(sys.stdin.readline)
        if not line:
            return
        text = line.rstrip("\n")
        if text.startswith("!"):
            done = subprocess.run(text[1:], shell=True, capture_output=True, text=True)
            print(done.stdout + done.stderr, end="", flush=True)
        elif text:
            await _answer(adapter, text)


async def _answer(adapter, prompt):
    query, _ = adapter._sdk()
    req = CallRequest(node="hosted", entry=0, item=None, prompt=prompt, model=HOSTED_MODEL, effort=None,
                      tools=HOSTED_TOOLS, max_stalls=3, max_spend=5.0, workspace=os.getcwd(), deadline=600.0,
                      session_id=uuid.uuid4().hex)
    async for message in query(prompt=prompt, options=adapter.options(req)):
        if type(message).__name__ == "AssistantMessage":
            for block in getattr(message, "content", None) or []:
                if type(block).__name__ == "TextBlock":
                    print(block.text, flush=True)


if __name__ == "__main__" and sys.argv[1:] == ["host"]:
    host()
