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

A `guest` channel with `voice_reply: true` also gets the answer as a clip with the persona voice (story 70). The
deterministic code synthesizes the clip with the one TTS path the caller supplies, and the writer session uploads it
through the connector's two upload tools. The file comment carries the persona mark, and the hook allows those two calls
only with the decided channel, thread and comment, so the model never redirects the clip or its mark.

The module reads no clock and calls no network: the caller supplies the decided body, the question's `ts` and, for the
thread store, its clock value.

Guards: `tests/test_slack_writer.py`, `tests/test_slack_voice_reply.py`.
"""
import json
import os
import re
from typing import NamedTuple

from .slackpoller import DEFAULT_SERVER, mark, tool_word

# The connector's write tools, without the server: the draft, and the direct send. A channel's decision picks one.
DRAFT_WORD = "send_message_draft"
SEND_WORD = "send_message"
WRITE_WORDS = frozenset((DRAFT_WORD, SEND_WORD))
# The connector's two upload tools, in the order a voice reply calls them: the URL and the bytes, then the completion.
UPLOAD_URL_WORD = "get_file_upload_url"
COMPLETE_UPLOAD_WORD = "complete_file_upload"
UPLOAD_WORDS = frozenset((UPLOAD_URL_WORD, COMPLETE_UPLOAD_WORD))

_CHANNEL_KEYS = ("channel", "channel_id")
_COMMENT_KEYS = ("initial_comment", "comment")   # the file comment of an upload: the decided mark rides here
SPOKEN_MAX = 420                                  # characters, as the vendored reply path caps a spoken script


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


class Upload(NamedTuple):
    """The one clip a voice reply uploads: the local path, the decided channel and `thread_ts`, the file comment with
    the persona mark, and the persona name the comment names."""
    path: str
    channel: str
    thread_ts: str
    comment: str
    name: str


def spoken_script(text):
    """The clip's script: the reply body with Markdown removed, because the voice reads it aloud. Past `SPOKEN_MAX`
    characters the script stops on the last whole sentence that fits, so a clip never cuts a word."""
    t = re.sub(r"[*_`#>|]+", "", str(text or ""))
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= SPOKEN_MAX:
        return t
    cut = t[:SPOKEN_MAX]
    for end in (". ", "! ", "? ", "; "):
        i = cut.rfind(end)
        if i > SPOKEN_MAX * 0.5:
            return cut[:i + 1].strip()
    i = cut.rfind(" ")
    return (cut[:i] if i > 0 else cut).strip() + "."


def voice_answer(decision, channel, thread_ts, body, synth):
    """The voice reply of one guest answer, or None when the channel does not ask for one.

    The clip is synthesized with the persona voice and tone, through the one TTS path the caller supplies as
    `synth(script, voice, tone) -> path`. A decision with no reply, no persona (so no mark) or `voice_reply` false carries
    no clip, and neither does a synthesis that returns no path: a missing voice never fails the text answer."""
    if decision is None or not getattr(decision, "reply", None) or not getattr(decision, "voice_reply", False):
        return None
    persona = getattr(decision, "persona", None)
    name = getattr(persona, "name", None) if persona is not None else None
    if not name:
        return None
    script = spoken_script(body)
    if not script:
        return None
    try:
        path = synth(script, str(getattr(persona, "voice", "") or ""), str(getattr(persona, "tone", "") or ""))
    except Exception:  # noqa: BLE001 - a missing voice never fails the reply
        return None
    if not path:
        return None
    return Upload(str(path), str(channel), str(thread_ts), mark(str(name)), str(name))


def _clip_size(upload):
    """The byte size of the decided clip, or None when the file is not there. The `get_file_upload_url` call declares
    this size, so a model never uploads a different file."""
    try:
        return os.path.getsize(upload.path)
    except OSError:
        return None


def upload_denial(args, upload):
    """The reason an upload call differs from the decided clip, or None. A missing channel, `thread_ts` or comment is not
    a difference: the hook fills all three. A different one is, so the model never redirects the clip or its mark."""
    for key in _CHANNEL_KEYS:
        if key in args and str(args[key]) != upload.channel:
            return f"the upload names channel {args[key]!r}, not the decided channel {upload.channel!r}"
    if "thread_ts" in args and str(args["thread_ts"]) != upload.thread_ts:
        return f"the upload names thread {args['thread_ts']!r}, not the thread of the question {upload.thread_ts!r}"
    for key in _COMMENT_KEYS:
        if key in args and str(args[key]) != upload.comment:
            return f"the upload names comment {args[key]!r}, not the decided comment {upload.comment!r}"
    return None


def url_denial(args, upload):
    """The reason the `get_file_upload_url` call names another file, or None. A missing name or length is filled with the
    decided clip's own."""
    name = os.path.basename(upload.path)
    if "filename" in args and str(args["filename"]) != name:
        return f"the upload names file {args['filename']!r}, not the decided clip {name!r}"
    size = _clip_size(upload)
    if size is None:
        return f"the decided clip {upload.path!r} is not on disk, so no upload starts"
    if "length" in args and str(args["length"]) != str(size):
        return f"the upload names length {args['length']!r}, not the size of the decided clip {size}"
    return None


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


def arguments_differ(args, channel, thread_ts, text=None):
    """The reason the model's arguments differ from the decision, or None. A missing channel or `thread_ts` is not a
    difference: the hook fills both with the decided values, and the tool never chooses them. A different one is. When
    the caller names the decided `text`, a different text is a difference too, so the model never rewrites the write."""
    for key in _CHANNEL_KEYS:
        if key in args and str(args[key]) != str(channel):
            return f"the write names channel {args[key]!r}, not the decided channel {channel!r}"
    if "thread_ts" in args and str(args["thread_ts"]) != str(thread_ts):
        return f"the write names thread {args['thread_ts']!r}, not the thread of the question {thread_ts!r}"
    if text and "text" in args and str(args["text"]) != str(text):
        return "the write names a text different from the decided text"
    return None


def _named(args, channel, thread_ts):
    """The reason the model's channel or thread differs from the decision (see `arguments_differ`)."""
    return arguments_differ(args, channel, thread_ts)


def _upload_answer(tool, args, upload):
    """The one answer to an upload call: a pass with the decided values filled, or one denial that names the difference.
    No decided upload means no upload call at all, so a text-only decision never sends a clip."""
    if upload is None:
        return _deny(f"{tool} is an upload call, but the decision names no voice reply: this write sends no clip")
    reason = upload_denial(args, upload)
    if reason is not None:
        return _deny(reason)
    if tool == UPLOAD_URL_WORD:
        reason = url_denial(args, upload)
        if reason is not None:
            return _deny(reason)
        return _allow({"filename": os.path.basename(upload.path), "length": _clip_size(upload)})
    file_id = args.get("file_id")
    if not file_id:
        return _deny("the upload names no file_id: the hook never invents the id of the clip")
    return _allow({"file_id": file_id, "channel": upload.channel, "thread_ts": upload.thread_ts,
                   "initial_comment": upload.comment})


def pre_write_tool_use(draft, upload=None, server=DEFAULT_SERVER):
    """The `PreToolUse` hook of the writer session. It passes only the decided tool with the decided channel and
    `thread_ts`, and one denial answer for every other call: a different tool, a different channel or thread, or a
    decided text without the persona mark. On a pass it returns `updatedInput` with the decided channel, `thread_ts` and
    text, so the exact text reaches Slack whatever the model wrote. An empty `thread_ts` names a channel post, and the
    comparison refuses any other thread there.

    `upload` is the decided `Upload` of a voice reply. When it is there, the hook also passes the connector's two upload
    calls with the decided clip, channel, `thread_ts` and comment, and denies every other upload argument; when it is
    None, both upload calls are denied."""
    async def hook(input_data, tool_use_id, context):
        name = input_data.get("tool_name")
        tool = tool_word(str(name or ""), server)
        if tool in UPLOAD_WORDS:
            return _upload_answer(tool, input_data.get("tool_input") or {}, upload)
        if tool != draft.tool:
            return _deny(f"{name} is not the decided write tool: the decision names {draft.tool} only")
        args = input_data.get("tool_input") or {}
        reason = _named(args, draft.channel, draft.thread_ts)
        if reason is not None:
            return _deny(reason)
        reason = write_denial(draft.text, (mark(draft.name),))
        if reason is not None:
            return _deny(reason)
        updated = {"channel": draft.channel, "thread_ts": draft.thread_ts, "text": draft.text}
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
