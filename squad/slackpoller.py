"""The guest poller: a read-only session hook and the checkpoint that code writes (SPEC 2682, #2707).

The claude.ai Slack connector is called from inside a Claude session, so the model runs the reads. The model never
computes the cursor. A `PreToolUse` hook answers every tool call of the poller session and lets through the connector's
read tools only. A `PostToolUse` hook reads the newest `ts` of each returned page and writes it to the channel's
checkpoint. The checkpoint lives in the guest state folder (#2710), and it is one file for the machine.

A read moves forward with Slack's `oldest`: the next read of a channel passes the checkpoint, and the code drops every
message at or before it, so a resumed session gets the next page with no gap and no repeat. A post result never moves
the checkpoint, and neither does an own post that a read returns, so a message that arrives between a read and a post is
still delivered by the next read. The polling schedule is not here (#2709), and neither is the own-post list (#2708).

The policy decision comes from `squad/slackpolicy.py` (#2684), and the caller passes the clock value. This module reads
no clock of its own and calls no network: the connector is a callable that the caller supplies.

Shadow mode (#2711) is the pilot beside the existing cron. A shadow poll reads a page and records the decision it would
take for each message in `guest-shadow.json`, beside the checkpoint in the same private state folder. It hands no
decision to a caller and makes no write call, so nothing leaves the owner's account. The record holds the decision and
never the message text (story 42).

Guards: `tests/test_slack_poller.py`.
"""
import json
from pathlib import Path

from . import slackpolicy
from .record import atomic_write

# The connector's own tool name carries its server: `mcp__slack__slack_read_channel`. A read tool's first word is one of
# these, and no word of its name is a write verb, so `slack_get_file_upload_url` never passes.
READ_VERBS = frozenset(("read", "search", "list"))
WRITE_VERBS = frozenset(("send", "post", "write", "upload", "schedule", "react", "complete", "create", "update",
                         "delete", "remove", "set", "add", "invite", "archive", "draft"))
DEFAULT_SERVER = "slack"
CHECKPOINT_NAME = "guest-checkpoint.json"
SHADOW_NAME = "guest-shadow.json"     # the shadow record, beside the checkpoint in the private state folder


def _tool_word(name, server):
    """The connector's own name of a tool, or None when the tool belongs to another server. `mcp__slack__slack_read_channel`
    and a plain `slack_read_channel` both give `slack_read_channel`."""
    prefix = f"mcp__{server}__"
    if name.startswith(prefix):
        tool = name[len(prefix):]
    elif name.startswith(f"{server}_"):
        tool = name
    else:
        return None
    return tool[len(f"{server}_"):] if tool.startswith(f"{server}_") else tool


def read_denial(name, server=DEFAULT_SERVER):
    """None when the tool is one of the connector's read tools, else the reason the hook denies it."""
    tool = _tool_word(str(name or ""), server)
    if tool is not None:
        words = tool.split("_")
        if words and words[0] in READ_VERBS and not WRITE_VERBS.intersection(words):
            return None
    return f"{name} is not a Slack read tool: a poller session holds read tools only"


def pre_read_tool_use(server=DEFAULT_SERVER):
    """The `PreToolUse` hook: an empty answer for a read tool, and one denial answer for every other call."""
    async def hook(input_data, tool_use_id, context):
        reason = read_denial(input_data.get("tool_name"), server)
        if reason is None:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}
    return hook


def messages_of(response):
    """The messages of one tool result. A result is a mapping with `messages`, a list, or the JSON text of either; a
    result the reader cannot understand holds no message, and never raises."""
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            return []
    if isinstance(response, dict):
        response = response.get("messages")
    if not isinstance(response, list):
        return []
    return [m for m in response if isinstance(m, dict) and m.get("ts") is not None]


def _stamp(ts):
    try:
        return float(ts)
    except (TypeError, ValueError):
        return None


def _newer(ts, oldest):
    """True when `ts` is later than `oldest`. A missing `oldest` accepts every message."""
    if oldest is None:
        return True
    a, b = _stamp(ts), _stamp(oldest)
    return a is not None and b is not None and a > b


def newest_ts(messages, own=()):
    """The newest `ts` a page returned, as the text the connector wrote it, or None. A `ts` of an own post never counts."""
    best, value = None, None
    for message in messages:
        ts = message.get("ts")
        if str(ts) in {str(o) for o in own}:
            continue
        stamp = _stamp(ts)
        if stamp is not None and (value is None or stamp > value):
            best, value = str(ts), stamp
    return best


class Checkpoint:
    """The read cursor of each channel, one JSON file keyed by channel id. A Slack channel id is unique across
    workspaces, and the connector reaches one guest workspace at a time (ADR-0177), so the workspace needs no key. Only
    code writes the file, and every write is atomic (ADR-0030). An unreadable file reads as an empty cursor, so a broken
    file never stops a poll."""

    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            doc = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return doc if isinstance(doc, dict) else {}

    def get(self, channel):
        """The newest `ts` this channel has delivered, or None when it has delivered nothing."""
        doc = self.load()
        return str(doc[str(channel)]) if str(channel) in doc else None

    def advance(self, channel, ts, own=()):
        """Store `ts` as the channel's cursor. Returns False, and writes nothing, when `ts` is missing, is the `ts` of an
        own post, or is not later than the stored value: the cursor never moves to a post and never moves backward."""
        if ts is None or str(ts) in {str(o) for o in own}:
            return False
        doc = self.load()
        if not _newer(ts, doc.get(str(channel))):
            return False
        doc[str(channel)] = str(ts)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(doc, indent=1, sort_keys=True) + "\n")
        return True

    def record_page(self, channel, page, own=()):
        """Advance the cursor to the newest `ts` of one page and return the stored value."""
        self.advance(channel, newest_ts(page, own), own)
        return self.get(channel)


def shadow_entry(ts, decision):
    """The decision a shadow poll would take for one message, as a JSON-ready row. It records the decision fields only:
    the message text and its author never enter the record (story 42)."""
    persona = decision.persona
    return {
        "ts": str(ts),
        "kind": decision.kind,
        "role": decision.role,
        "actions": list(decision.actions),
        "language": decision.language,
        "persona": persona.id if persona is not None else None,
        "reply": decision.reply,
        "mark": decision.mark,
        "next_poll": decision.next_poll.isoformat() if decision.next_poll is not None else None,
    }


class ShadowLog:
    """The decisions a shadow poll would take, one JSON file keyed by channel id, beside the checkpoint in the private
    state folder. Only code writes it, every write is atomic (ADR-0030), and an unreadable file reads as an empty
    record, so a broken file never stops a poll. The record holds the decision and never the message text (story 42)."""

    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            doc = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return doc if isinstance(doc, dict) else {}

    def entries(self, channel=None):
        """Every recorded decision, by channel then oldest first, or the decisions of one channel."""
        doc = self.load()
        if channel is not None:
            rows = doc.get(str(channel))
            return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        return [row for rows in doc.values() if isinstance(rows, list) for row in rows if isinstance(row, dict)]

    def record(self, channel, ts, decision):
        """Append one decision for one message of a channel, as one atomic write."""
        doc = self.load()
        rows = doc.get(str(channel))
        rows = list(rows) if isinstance(rows, list) else []
        rows.append(shadow_entry(ts, decision))
        doc[str(channel)] = rows
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(doc, indent=1, sort_keys=True) + "\n")


def checkpoint_hook(checkpoint, server=DEFAULT_SERVER, own=()):
    """The `PostToolUse` hook: it writes the checkpoint from the result of a read tool. The model never computes it, and
    the result of any other tool leaves the checkpoint alone. `own` is the list of `ts` of the session's own posts, so a
    read that returns one of them never moves the cursor to it."""
    async def hook(input_data, tool_use_id, context):
        if read_denial(input_data.get("tool_name"), server) is not None:
            return {}
        args = input_data.get("tool_input") or {}
        channel = args.get("channel_id") or args.get("channel")
        if not channel:
            return {}
        checkpoint.record_page(channel, messages_of(input_data.get("tool_response")), own)
        return {}
    return hook


def event_of(message, workspace, channel, own=()):
    """The policy event of one read message. A bot message and an external message are flagged, and so is a message whose
    `ts` is in the own-post list."""
    ts = str(message.get("ts"))
    return {
        "kind": "message",
        "workspace": workspace,
        "channel": channel,
        "user": message.get("user") or message.get("bot_id") or "",
        "bot": bool(message.get("bot_id") or message.get("subtype") == "bot_message"),
        "external": bool(message.get("is_external") or message.get("external")),
        "own": ts in {str(o) for o in own},
    }


class Poller:
    """One read of a due `guest` channel and the policy decision of each new message.

    `connect(channel=..., oldest=..., limit=...)` is the connector's read call. `now()` is the caller's clock value.
    `own` is the list of `ts` of the session's own posts (#2708 supplies it). The caller polls one page at a time: the
    cursor advances after each page, so a later poll of the same channel resumes from the next page.

    `shadow` runs the poll in shadow mode (#2711): `poll` records each decision in `self.shadow` and hands nothing out,
    so the caller has no decision to act on and makes no write call."""

    def __init__(self, connect, checkpoint, committed, local, now, own=(), limit=100, shadow=False):
        self.connect, self.checkpoint = connect, checkpoint
        self.committed, self.local, self.now = committed, local, now
        self.own, self.limit = tuple(own), limit
        # The record lives in the folder the caller chose for the checkpoint, so the private state folder keeps the
        # cursor and the record together and no second path is invented.
        self.shadow = ShadowLog(self.checkpoint.path.with_name(SHADOW_NAME)) if shadow else None

    def read_request(self, workspace, channel):
        """The arguments of the next read of a channel: the channel, the stored cursor as `oldest`, and the page limit.
        A channel with no cursor carries no `oldest`, so its first read takes the channel from the start."""
        request = {"channel": str(channel), "limit": self.limit}
        oldest = self.checkpoint.get(channel)
        if oldest is not None:
            request["oldest"] = oldest
        return request

    def poll(self, workspace, channel):
        """Read one page, store the cursor at its newest `ts` that is not an own post, and return `(message, decision)`
        for each new message. A message at or before the cursor, and an own post, never returns. A page with no new
        message ends the walk of the channel for this tick.

        In shadow mode each decision is recorded in the private state folder, and the poller returns no pair, so no
        caller can act on a decision and no write call is made (#2711)."""
        pairs = self._deliveries(workspace, channel)
        if self.shadow is None:
            return pairs
        for message, decision in pairs:
            self.shadow.record(channel, message.get("ts"), decision)
        return []

    def _deliveries(self, workspace, channel):
        """One page read, the cursor stored, and the `(message, decision)` of each new message of the channel."""
        oldest = self.checkpoint.get(channel)
        page = messages_of(self.connect(**self.read_request(workspace, channel)))
        self.checkpoint.record_page(channel, page, self.own)
        now = self.now()
        own = {str(o) for o in self.own}
        return [(m, slackpolicy.decide(self.committed, self.local, event_of(m, workspace, channel, self.own), now))
                for m in page if _newer(m.get("ts"), oldest) and str(m.get("ts")) not in own]
