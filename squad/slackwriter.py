"""The `guest` draft writer through the `PreToolUse` hook (SPEC 2682, stories 34 to 37; #2712).

The connector is called only from inside a Claude session, so the writer is a short session whose hook applies the
policy decision. It names one write: the tool, the channel and the `thread_ts` of the question, and the exact text with
the persona mark. The hook accepts that write and no other, and it returns `updatedInput` with the decided text, so the
model never copies the text and never redirects the write. A `PostToolUse` hook records the `ts` of each draft in the
own list (`OwnTs`, #2708), so the poller never delivers it back.

Every guest text leaves the owner's account, so every guest text carries the mark that names the AI as the author: the
line `*<persona>'s Msg*`, alone and in bold, with the body in italics (story 34). A write without that shape is denied
(story 35), and so is a draft, so a draft sent unchanged still says an AI wrote it (story 36). A direct send passes the
hook only when the policy decided `send`: `speak: send` with a `send_grant`, and never in a Slack Connect channel or for
a person in `draft_for` (stories 37 and 38). Every write, draft or send, goes through this hook, so the model never
redirects a send to another channel or thread (story 72).

The module reads no clock and calls no network: the caller supplies the decided body, the question's `ts` and, for the
thread store, its clock value.

Guards: `tests/test_slack_writer.py`.
"""
import json
from typing import NamedTuple

from .slackpoller import DEFAULT_SERVER, mark, tool_word

# The connector's write tools, without the server: the draft, and the direct send. A channel's decision picks one.
DRAFT_WORD = "send_message_draft"
SEND_WORD = "send_message"
WRITE_WORDS = frozenset((DRAFT_WORD, SEND_WORD))

_CHANNEL_KEYS = ("channel", "channel_id")


class Draft(NamedTuple):
    """The one write the decision allows: the tool, the channel, the `thread_ts` of the question, the exact text with
    the persona mark, and the persona name the mark names. An empty `thread_ts` is a channel post, never a reply in a
    thread: a release announcement (#2715) names no question to answer."""
    tool: str
    channel: str
    thread_ts: str
    text: str
    name: str


def compose(name, body):
    """The text of one guest reply: the persona mark line alone and in bold, then the body in italics. An empty body is
    the mark line only."""
    text = str(body or "").strip()
    if not text:
        return mark(name)
    return f"{mark(name)}\n_{text}_"


def has_mark(text, name):
    """True when the text is a guest text: the persona mark line first, then an empty or fully italic body."""
    return write_denial(text, (mark(name),)) is None


def _one_italic(body):
    """True when the body is exactly one italic run: a leading and a trailing `_`, a non-empty interior, and no other
    `_` inside. `_a_ and _b_` is two runs with plain text between, so it is not fully italic; nor is `__`, an empty
    run. A body of lines inside the one run stays one run."""
    return len(body) >= 3 and body.startswith("_") and body.endswith("_") and "_" not in body[1:-1]


def write_denial(text, marks):
    """None when the text carries one of the given persona mark lines and an italic body, else the reason a write hook
    denies it. A mark alone, with no body, is a marked text; a body that follows the mark must be one italic run."""
    text = str(text or "").strip()
    for name_mark in marks:
        name = str(name_mark)
        lines = text.splitlines()
        if not lines or lines[0].strip() != name:
            continue
        body = "\n".join(lines[1:]).strip()
        if not body or _one_italic(body):
            return None
    return ("the write carries no persona mark: every guest text starts with the line *<persona>'s Msg*, alone and in "
            "bold, with the body in one italic run")


def plan(decision, channel, thread_ts, body):
    """The exact write a decision allows, or None when the decision names no reply: a `listen` channel, an event that
    stays `data`, or a channel with no persona (no persona means no mark, so no write). A `send` decision needs the
    policy to have granted it; any other reply path writes a draft, so Stage 3 produces drafts only."""
    if decision is None or not getattr(decision, "reply", None):
        return None
    persona = getattr(decision, "persona", None)
    name = getattr(persona, "name", None) if persona is not None else None
    if not name:
        return None
    tool = SEND_WORD if decision.reply == "send" else DRAFT_WORD
    return Draft(tool, str(channel), str(thread_ts), compose(name, body), str(name))


def _deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def _allow(arguments):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                   "updatedInput": arguments}}


def _named(args, channel, thread_ts):
    """The reason the model's arguments differ from the decision, or None. A missing channel or `thread_ts` is not a
    difference: the hook fills both with the decided values, and the tool never chooses them. A different one is."""
    for key in _CHANNEL_KEYS:
        if key in args and str(args[key]) != str(channel):
            return f"the write names channel {args[key]!r}, not the decided channel {channel!r}"
    if "thread_ts" in args and str(args["thread_ts"]) != str(thread_ts):
        return f"the write names thread {args['thread_ts']!r}, not the thread of the question {thread_ts!r}"
    return None


def pre_write_tool_use(draft, server=DEFAULT_SERVER):
    """The `PreToolUse` hook of the writer session. It passes only the decided tool with the decided channel and
    `thread_ts`, and one denial answer for every other call: a different tool, a different channel or thread, or a
    decided text without the persona mark. On a pass it returns `updatedInput` with the decided channel and text, and
    the decided `thread_ts` when the draft names one, so the exact text reaches Slack whatever the model wrote. An
    empty `thread_ts` names a channel post, so the payload omits the key and the comparison refuses any other thread."""
    async def hook(input_data, tool_use_id, context):
        name = input_data.get("tool_name")
        tool = tool_word(str(name or ""), server)
        if tool != draft.tool:
            return _deny(f"{name} is not the decided write tool: the decision names {draft.tool} only")
        args = input_data.get("tool_input") or {}
        reason = _named(args, draft.channel, draft.thread_ts)
        if reason is not None:
            return _deny(reason)
        reason = write_denial(draft.text, (mark(draft.name),))
        if reason is not None:
            return _deny(reason)
        updated = {"channel": draft.channel, "text": draft.text}
        if draft.thread_ts:
            updated["thread_ts"] = draft.thread_ts
        return _allow(updated)
    return hook


def _result_ts(response):
    """The `ts` of one write result, as the connector wrote it, or None. A result is a mapping with `ts`, a mapping with
    a `message` that holds `ts`, or the JSON text of either; anything else yields None and never raises."""
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            return None
    if not isinstance(response, dict):
        return None
    ts = response.get("ts")
    if ts is None and isinstance(response.get("message"), dict):
        ts = response["message"].get("ts")
    return ts


def result_hook(own, threads=None, clock=None):
    """The `PostToolUse` hook of the writer session: it records the `ts` of each draft or send in the own list, so the
    poller never delivers it back. When the caller supplies the reply's thread store and clock, it also records the
    thread as one where the agent replied, so the poller rereads it until it goes quiet."""
    async def hook(input_data, tool_use_id, context):
        if tool_word(str(input_data.get("tool_name") or "")) not in WRITE_WORDS:
            return {}
        ts = _result_ts(input_data.get("tool_response"))
        if ts is None:
            return {}
        own.add(ts)
        args = input_data.get("tool_input") or {}
        channel = next((args[key] for key in _CHANNEL_KEYS if args.get(key)), None)
        thread_ts = args.get("thread_ts")
        if threads is not None and channel and thread_ts:
            at = clock() if callable(clock) else clock
            threads.record(channel, thread_ts, at=at)
        return {}
    return hook
