"""The guest poller: a read-only session hook, the checkpoint that code writes, and the own-message and thread stores
(SPEC 2682, #2707, #2708).

The claude.ai Slack connector is called from inside a Claude session, so the model runs the reads. The model never
computes the cursor. A `PreToolUse` hook answers every tool call of the poller session and lets through the connector's
read tools only. A `PostToolUse` hook reads the newest `ts` of each returned page and writes it to the channel's
checkpoint. The checkpoint lives in the guest state folder (#2710), at `gueststate.CHECKPOINT_NAME`, and it is one file
for the machine.

A read moves forward with Slack's `oldest`: the next read of a channel passes the checkpoint, and the code drops every
message at or before it, so a resumed session gets the next page with no gap and no repeat. A post result never moves
the checkpoint, and neither does an own post that a read returns, so a message that arrives between a read and a post is
still delivered by the next read.

The poller never answers itself. The writer records the `ts` of every message and draft it created in the own list
(`OwnTs`), and a message from the owner account that starts with a persona mark is own too, whatever the connector
footer says. A reply in a thread does not appear in the channel read, so the poller rereads every thread where the agent
replied (`RepliedThreads`) until it goes quiet. The polling schedule is not here (#2709).

The policy decision comes from `squad/slackpolicy.py` (#2684), and the caller passes the clock value. This module reads
no clock of its own and calls no network: the connector is a callable that the caller supplies.

Guards: `tests/test_slack_poller.py`.
"""
import json
from datetime import datetime
from pathlib import Path

from . import slackpolicy
from .record import atomic_write

# The connector's own tool name carries its server: `mcp__slack__slack_read_channel`. A read tool's first word is one of
# these, and no word of its name is a write verb, so `slack_get_file_upload_url` never passes.
READ_VERBS = frozenset(("read", "search", "list"))
WRITE_VERBS = frozenset(("send", "post", "write", "upload", "schedule", "react", "complete", "create", "update",
                         "delete", "remove", "set", "add", "invite", "archive", "draft"))
DEFAULT_SERVER = "slack"


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


def read_failed(response):
    """True when a tool result reports a failed read or is not a page: a mapping with `ok` false or with no `messages`
    list, or text the reader cannot parse. `messages_of` reads a failure as an empty page, so a caller that must not lose
    a thread asks this first."""
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            return True
    if isinstance(response, dict):
        return response.get("ok") is False or not isinstance(response.get("messages"), list)
    return not isinstance(response, list)


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


# --- own-message detection (#2708) ----------------------------------------------------------------------------------

def mark(name):
    """The persona mark line of a text sent from the owner's account: `*<name>'s Msg*` (SPEC 2682, story 34)."""
    return f"*{name}'s Msg*"


def persona_marks(committed):
    """The mark line of every persona of the committed file, in the file's order."""
    rows = (committed or {}).get("personas") or []
    return tuple(mark(row.get("name")) for row in rows if isinstance(row, dict) and row.get("name"))


def without_footer(text, footers=()):
    """The text of a message with the connector footer removed. The connector appends one footer line to every text it
    sends from the owner's account, in the language of that account, so the caller names the footer it observes. A
    message with no footer passes unchanged."""
    lines = str(text or "").splitlines()
    wanted = {str(footer).strip().casefold() for footer in footers if str(footer).strip()}
    if not wanted:
        return str(text or "")
    return "\n".join(line for line in lines if line.strip().casefold() not in wanted)


def has_persona_mark(text, marks, footers=()):
    """True when the first non-empty line of the text, with the connector footer removed, is a persona mark line."""
    lines = without_footer(text, footers).splitlines()
    first = next((line.strip() for line in lines if line.strip()), "")
    return any(first == want for want in marks)


def own_message(message, own=(), owner=(), marks=(), footers=()):
    """True when the message is the agent's own. Its `ts` is in the own list, or the text starts with a persona mark and
    it came from the owner account. When the caller names no owner account, a marked message is own whoever wrote it, so
    the poller never treats the agent's mark as a command (story 60)."""
    if str(message.get("ts")) in {str(ts) for ts in own}:
        return True
    if not marks or not has_persona_mark(message.get("text"), marks, footers):
        return False
    user = str(message.get("user") or message.get("bot_id") or "")
    return not owner or user in {str(who) for who in owner}


def _own_ts(own):
    """The own `ts` values of a list or of an `OwnTs` store, as a set of text."""
    values = getattr(own, "values", None)
    if callable(values) and not isinstance(own, (dict, list, tuple, set, frozenset)):
        own = values()
    return {str(ts) for ts in own or ()}


class OwnTs:
    """The `ts` of every message and every draft the writer created. One JSON file holds the list, and the poller reads
    it so that it never delivers an own post again. Only code writes the file, and every write is atomic (ADR-0030). An
    unreadable file reads as an empty list, so a broken file never stops a poll."""

    def __init__(self, path):
        self.path = Path(path)

    def values(self):
        try:
            doc = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return ()
        return tuple(str(ts) for ts in doc) if isinstance(doc, list) else ()

    def has(self, ts):
        return str(ts) in set(self.values())

    def add(self, ts):
        """Record one `ts`. Returns False, and writes nothing, when the `ts` is missing or already recorded."""
        if ts is None or self.has(ts):
            return False
        doc = sorted(set(self.values()) | {str(ts)})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(doc, indent=1) + "\n")
        return True


# --- the replied-thread store (#2708) -------------------------------------------------------------------------------

class RepliedThreads:
    """The threads where the agent replied, keyed by channel and `thread_ts`. Each entry holds the read cursor (the
    newest `ts` the thread read delivered) and the time of the last activity. The poller rereads every open thread and
    closes it after it goes quiet. The writer records a reply, so a thread opens the moment the agent answers in it. Only
    code writes the file, every write is atomic (ADR-0030), and an unreadable file reads as empty."""

    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            doc = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return doc if isinstance(doc, dict) else {}

    def _save(self, doc):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(doc, indent=1, sort_keys=True) + "\n")

    def record(self, channel, thread_ts, at=None, ts=None):
        """Record a reply in a thread, or move its activity and cursor forward. `at` is the caller's clock value of the
        last activity, and `ts` the newest `ts` the thread read delivered. A new thread needs an `at`, because the anchor
        of its quiet time cannot be guessed. Returns False, and writes nothing, when no thread is recorded."""
        doc = self.load()
        key = str(channel)
        entry = (doc.get(key) or {}).get(str(thread_ts))
        if entry is None and at is None:
            return False
        entry = dict(entry or {})
        if at is not None:
            entry["last_at"] = at.isoformat()
        if ts is not None:
            new, old = _stamp(ts), _stamp(entry.get("cursor"))
            if new is not None and (old is None or new > old):
                entry["cursor"] = str(ts)
        doc.setdefault(key, {})[str(thread_ts)] = entry
        self._save(doc)
        return True

    def close(self, channel, thread_ts):
        """Forget a thread, so the poller never rereads it again."""
        doc = self.load()
        threads = doc.get(str(channel)) or {}
        if str(thread_ts) not in threads:
            return False
        del threads[str(thread_ts)]
        if threads:
            doc[str(channel)] = threads
        else:
            doc.pop(str(channel), None)
        self._save(doc)
        return True

    def open_threads(self, channel):
        """The open threads of a channel as `{thread_ts: {"cursor": text or None, "last_at": datetime or None}}`. An
        entry without a readable `last_at` takes its cursor time, and None when the cursor is unreadable too, so a thread
        a writer stored with a cursor only is still reread."""
        out = {}
        for thread_ts, entry in (self.load().get(str(channel)) or {}).items():
            entry = entry if isinstance(entry, dict) else {}
            cursor = entry.get("cursor")
            try:
                last_at = datetime.fromisoformat(entry["last_at"])
            except (KeyError, TypeError, ValueError):
                stamp = _stamp(cursor)
                last_at = datetime.fromtimestamp(stamp) if stamp is not None else None
            out[str(thread_ts)] = {"cursor": cursor, "last_at": last_at}
        return out

    def last_at(self, channel):
        """The last activity of the channel's open threads, as the caller's clock value, or None."""
        stamps = [entry["last_at"] for entry in self.open_threads(channel).values()
                  if entry["last_at"] is not None]
        return max(stamps) if stamps else None


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
    `ts` is in the own-post list. A thread reply carries its `thread_ts`."""
    ts = str(message.get("ts"))
    return {
        "kind": "message",
        "workspace": workspace,
        "channel": channel,
        "user": message.get("user") or message.get("bot_id") or "",
        "bot": bool(message.get("bot_id") or message.get("subtype") == "bot_message"),
        "external": bool(message.get("is_external") or message.get("external")),
        "own": ts in {str(o) for o in own},
        "thread_ts": message.get("thread_ts"),
    }


_THREAD_OPEN_DEFAULT = "30m"
_MS = {"s": 1, "m": 60, "h": 3600}


def _duration(value, default):
    """A duration of the channel's `poll` block as seconds: `1m`, `30m`, `1h`, or a number of seconds. A missing or
    unreadable value answers the default."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    try:
        return float(text[:-1]) * _MS[text[-1]] if text and text[-1] in _MS else float(text)
    except ValueError:
        return default


def _channel_row(committed, local, workspace, channel):
    """The channel's row of the local file, then of the committed file, or an empty row for an unknown channel."""
    for doc in (local, committed):
        for ws in (doc or {}).get("workspaces") or []:
            if str(ws.get("id")) != str(workspace):
                continue
            for row in ws.get("channels") or []:
                if str(row.get("id")) == str(channel):
                    return row
    return {}


class Poller:
    """One read of a due `guest` channel, the reread of its open threads, and the policy decision of each new message.

    `connect(channel=..., oldest=..., limit=...)` is the connector's channel read, and `connect(channel=...,
    thread_ts=..., limit=...)` its thread read. `now()` is the caller's clock value. `own` is the list of `ts` of the
    session's own posts, or the `OwnTs` store the writer fills. `owner` names the owner account, `footers` the connector
    footer its texts carry, and `threads` the `RepliedThreads` store. The caller polls one page at a time: the cursor
    advances after each page, so a later poll of the same channel resumes from the next page."""

    def __init__(self, connect, checkpoint, committed, local, now, own=(), limit=100, owner=(), threads=None,
                 footers=()):
        self.connect, self.checkpoint = connect, checkpoint
        self.committed, self.local, self.now = committed, local, now
        self.own, self.limit = own, limit
        self.owner, self.threads = tuple(owner), threads
        self.footers, self.marks = tuple(footers), persona_marks(committed)

    def read_request(self, workspace, channel):
        """The arguments of the next read of a channel: the channel, the stored cursor as `oldest`, and the page limit.
        A channel with no cursor carries no `oldest`, so its first read takes the channel from the start."""
        request = {"channel": str(channel), "limit": self.limit}
        oldest = self.checkpoint.get(channel)
        if oldest is not None:
            request["oldest"] = oldest
        return request

    def is_own(self, message):
        """True when the message is the agent's own: its `ts` is in the own list, or it carries a persona mark from the
        owner account, whatever the connector footer says."""
        return own_message(message, _own_ts(self.own), self.owner, self.marks, self.footers)

    def _remember_own(self, page):
        """Record the `ts` of each message the poller reads and classifies as own in the `OwnTs` store, when the caller
        supplies one, so a reply the writer never recorded is never delivered back to the agent."""
        add = getattr(self.own, "add", None)
        if not callable(add):
            return
        for message in page:
            if self.is_own(message):
                add(message.get("ts"))

    def poll(self, workspace, channel):
        """Read one page, store the cursor at its newest `ts` that is not an own post, reread each open thread, and
        return `(message, decision)` for each new message. A message at or before the cursor, and an own message, never
        returns. A page with no new message ends the walk of the channel for this tick."""
        oldest = self.checkpoint.get(channel)
        page = messages_of(self.connect(**self.read_request(workspace, channel)))
        self._remember_own(page)
        own = _own_ts(self.own)
        self.checkpoint.record_page(channel, page, own)
        now = self.now()
        pairs = [(m, slackpolicy.decide(self.committed, self.local, event_of(m, workspace, channel, own), now))
                 for m in page if _newer(m.get("ts"), oldest) and not self.is_own(m)]
        return pairs + self._reread_threads(workspace, channel, now)

    def _reread_threads(self, workspace, channel, now):
        """Reread each open thread where the agent replied. A message newer than the thread's cursor reaches the policy;
        a thread with no new message closes once it has been quiet for `thread_open`, after one last read. A failed read
        leaves the thread open with no cursor or activity change, so a transient error never loses a reply."""
        if self.threads is None:
            return []
        row = _channel_row(self.committed, self.local, workspace, channel)
        poll = row.get("poll") if isinstance(row.get("poll"), dict) else {}
        quiet = _duration(poll.get("thread_open"), _duration(_THREAD_OPEN_DEFAULT, 1800.0))
        pairs = []
        for thread_ts, state in sorted(self.threads.open_threads(channel).items()):
            response = self.connect(channel=str(channel), thread_ts=thread_ts, limit=self.limit)
            if read_failed(response):
                continue
            page = messages_of(response)
            self._remember_own(page)
            new = [m for m in page if _newer(m.get("ts"), state["cursor"]) and not self.is_own(m)]
            newest = newest_ts(page)
            last_at = state["last_at"]
            if new or (newest is not None and _newer(newest, state["cursor"])):
                self.threads.record(channel, thread_ts, at=now, ts=newest)
                last_at = now
            if last_at is not None and (now - last_at).total_seconds() > quiet:
                self.threads.close(channel, thread_ts)
            if new:
                pairs += [(m, slackpolicy.decide(self.committed, self.local,
                                                 event_of(m, workspace, channel, _own_ts(self.own)), now))
                          for m in new]
        return pairs
