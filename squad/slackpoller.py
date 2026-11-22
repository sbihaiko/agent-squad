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
replied (`RepliedThreads`) until it goes quiet.

The polling schedule of each channel is `PollSchedule` (#2709). A tick fires every minute and does nothing before the
`next_due` of the channel. The interval of a poll comes from `slackpolicy.poll_interval`, over the channel's `poll`
block, so the schedule and the decision read one set of keys.

The policy decision comes from `squad/slackpolicy.py` (#2684), and the caller passes the clock value. This module reads
no clock of its own and calls no network: the connector is a callable that the caller supplies.

Shadow mode (#2711) is the pilot beside the existing cron. A shadow poll reads a page and records the decision it would
take for each message in `guest-shadow.json`, beside the checkpoint in the same private state folder. It hands no
decision to a caller and makes no write call, so nothing leaves the owner's account. The record holds the decision and
never the message text (story 42).

Guards: `tests/test_slack_poller.py`, `tests/test_slack_schedule.py`.
"""
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import guesstranscribe, slackpolicy
from .gueststate import DIR_MODE, FILE_MODE
from .record import atomic_write

# The connector's own tool name carries its server: `mcp__slack__slack_read_channel`. A read tool's first word is one of
# these, and no word of its name is a write verb, so `slack_get_file_upload_url` never passes.
READ_VERBS = frozenset(("read", "search", "list"))
WRITE_VERBS = frozenset(("send", "post", "write", "upload", "schedule", "react", "complete", "create", "update",
                         "delete", "remove", "set", "add", "invite", "archive", "draft"))
DEFAULT_SERVER = "slack"
SHADOW_NAME = "guest-shadow.json"     # the shadow record, beside the checkpoint in the private state folder


def tool_word(name, server=DEFAULT_SERVER):
    """The connector's own name of a tool, without its server: `mcp__slack__slack_read_channel` and a plain
    `slack_read_channel` both give `read_channel`. None when the tool belongs to another server, so a writer or a poller
    never acts on a tool of a different server (the writer uses it too, #2712)."""
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
    tool = tool_word(str(name or ""), server)
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


def _utc(value):
    """The clock value on the UTC clock, so one store never mixes an aware value with a naive one. A naive value is the
    UTC wall clock, which is how a file written before the clock carried an offset still reads. None stays None."""
    if value is None:
        return None
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _time_of(value):
    """The clock value of an ISO text, on the UTC clock, or None when the text is missing or unreadable."""
    try:
        return _utc(datetime.fromisoformat(value))
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

_THREAD_KEY_SEP = "\x1f"


def _thread_key(workspace, channel):
    """The store key of a channel: the workspace and the channel id, so two guest workspaces that expose the same
    channel id never read each other's threads. A caller that names no workspace keeps the bare channel id."""
    return str(channel) if workspace is None else f"{workspace}{_THREAD_KEY_SEP}{channel}"


class RepliedThreads:
    """The threads where the agent replied, keyed by workspace, channel and `thread_ts`. Each entry holds the read cursor (the
    newest `ts` the thread read delivered) and the time of the last activity, on the UTC clock. A file written before the
    workspace entered the key holds the bare channel id, and the store keeps reading that table until its first write; the
    write moves the table to the composite key of the workspace that writes, so no upgrade loses an open thread and no
    other workspace reads it. The poller rereads every open thread and closes it after it goes quiet. The writer records a
    reply, so a thread opens the moment the agent answers in it. Only code writes the file, every write is atomic
    (ADR-0030), and an unreadable file reads as empty."""

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

    @staticmethod
    def _key(doc, workspace, channel):
        """The key that holds the channel's threads. The composite `(workspace, channel)` key wins whenever the file
        holds it. A file written before the workspace entered the key holds the bare channel id, and such a file stays
        readable until the first write moves it: the bare table answers only while the composite key is absent."""
        key, legacy = _thread_key(workspace, channel), str(channel)
        if key in doc:
            return key
        return legacy if legacy in doc else key

    @staticmethod
    def _migrate(doc, workspace, channel):
        """Move a table the file holds under the bare channel id to the composite key of the writing workspace, so the
        threads never reach a reader of another workspace that exposes the same channel id. A composite table already
        present wins the merge, and no later write reuses the bare key."""
        legacy = str(channel)
        if workspace is None or legacy not in doc:
            return doc
        key = _thread_key(workspace, channel)
        merged = dict(doc.pop(legacy) or {})
        merged.update(doc.get(key) or {})
        if merged:
            doc[key] = merged
        else:
            doc.pop(key, None)
        return doc

    def record(self, channel, thread_ts, at=None, ts=None, workspace=None):
        """Record a reply in a thread, or move its activity and cursor forward. `at` is the caller's clock value of the
        last activity, and `ts` the newest `ts` the thread read delivered. A new thread needs an `at`, because the anchor
        of its quiet time cannot be guessed. `workspace` separates two guest workspaces that share a channel id, and the
        first write of a channel moves a legacy bare-key table to that workspace. Returns False, and writes nothing,
        when no thread is recorded."""
        doc = self.load()
        doc = self._migrate(doc, workspace, channel)
        key = self._key(doc, workspace, channel)
        entry = (doc.get(key) or {}).get(str(thread_ts))
        if entry is None and at is None:
            return False
        entry = dict(entry or {})
        if at is not None:
            entry["last_at"] = _utc(at).isoformat()
        if ts is not None:
            new, old = _stamp(ts), _stamp(entry.get("cursor"))
            if new is not None and (old is None or new > old):
                entry["cursor"] = str(ts)
        doc.setdefault(key, {})[str(thread_ts)] = entry
        self._save(doc)
        return True

    def close(self, channel, thread_ts, workspace=None):
        """Forget a thread, so the poller never rereads it again. The write moves a legacy bare-key table to the
        workspace, as `record` does."""
        doc = self.load()
        doc = self._migrate(doc, workspace, channel)
        key = self._key(doc, workspace, channel)
        threads = doc.get(key) or {}
        if str(thread_ts) not in threads:
            return False
        del threads[str(thread_ts)]
        if threads:
            doc[key] = threads
        else:
            doc.pop(key, None)
        self._save(doc)
        return True

    def open_threads(self, channel, workspace=None):
        """The open threads of a channel as `{thread_ts: {"cursor": text or None, "last_at": datetime or None}}`. An
        entry without a readable `last_at` takes its cursor time, and None when the cursor is unreadable too, so a thread
        a writer stored with a cursor only is still reread."""
        out = {}
        doc = self.load()
        key = self._key(doc, workspace, channel)
        for thread_ts, entry in (doc.get(key) or {}).items():
            entry = entry if isinstance(entry, dict) else {}
            cursor = entry.get("cursor")
            last_at = _time_of(entry.get("last_at"))
            if last_at is None:
                stamp = _stamp(cursor)
                last_at = datetime.fromtimestamp(stamp, tz=UTC) if stamp is not None else None
            out[str(thread_ts)] = {"cursor": cursor, "last_at": last_at}
        return out

    def last_at(self, channel, workspace=None):
        """The last activity of the channel's open threads, as the caller's clock value, or None."""
        stamps = [entry["last_at"] for entry in self.open_threads(channel, workspace=workspace).values()
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


class ShadowLogUnreadable(Exception):
    """The state file exists but its content cannot be read, so a record never writes over it and drops the decisions
    it holds. `load` stays the tolerant read path; only `record` raises this."""


class ShadowLog:
    """The decisions a shadow poll would take, one JSON file keyed by channel id, beside the checkpoint in the private
    state folder. Only code writes it, every write is atomic (ADR-0030). An unreadable file reads as an empty record
    through `load`, so a broken file never stops a read of the record; `record` refuses to write over a file it could
    not read. The record holds the decision and never the message text (story 42)."""

    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            doc = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return doc if isinstance(doc, dict) else {}

    def _read(self):
        """The recorded document for a write, or `{}` when the file is absent. A file that exists but cannot be read,
        or whose content is not a JSON object, raises `ShadowLogUnreadable`: a write never erases what it cannot read."""
        try:
            text = self.path.read_text()
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise ShadowLogUnreadable(f"{self.path}: {exc}") from exc
        try:
            doc = json.loads(text)
        except ValueError as exc:
            raise ShadowLogUnreadable(f"{self.path}: not JSON: {exc}") from exc
        if not isinstance(doc, dict):
            raise ShadowLogUnreadable(f"{self.path}: the record is not a JSON object")
        return doc

    def entries(self, channel=None):
        """Every recorded decision, by channel then oldest first, or the decisions of one channel."""
        doc = self.load()
        if channel is not None:
            rows = doc.get(str(channel))
            return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        return [row for rows in doc.values() if isinstance(rows, list) for row in rows if isinstance(row, dict)]

    def record(self, channel, ts, decision):
        """Append one decision for one message of a channel, as one atomic write. A row of the channel already carries
        the same `ts`, so a repeat of one message appends nothing. A file that exists but cannot be read raises
        `ShadowLogUnreadable`, so a corrupt file keeps every decision it holds."""
        doc = self._read()
        rows = doc.get(str(channel))
        rows = list(rows) if isinstance(rows, list) else []
        if any(isinstance(row, dict) and row.get("ts") == str(ts) for row in rows):
            return
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
_POLL_SLOW_DEFAULT = "10m"     # the slow interval of a two_state channel with no message yet
_EXP_FLOOR = 300.0             # the exponential defaults of `slackpolicy.poll_interval`, in seconds
_EXP_CEILING = 3600.0
_EXP_FACTOR = 2.0
_EXP_WINDOW = 900.0
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


def message_time(message, now):
    """The clock value of one message's `ts`, on the UTC clock, or `now` when the `ts` is not a number. A message plans
    the next poll from its own `ts`, never from the clock value of the tick that read it. A caller that keeps a naive
    clock gets a naive value, so the two never meet as an aware value against a naive one."""
    stamp = _stamp(message.get("ts"))
    if stamp is None:
        return now
    if now is not None and now.tzinfo is None:
        return datetime.fromtimestamp(stamp)        # the caller keeps a naive clock: answer on the same one
    return datetime.fromtimestamp(stamp, tz=UTC)


def open_thread_last(threads, committed, local, workspace, channel, now):
    """The newest activity of the channel's open threads when it is inside `thread_open`, or None. A thread that went
    quiet hours ago never keeps the channel fast, and a thread of another guest workspace that shares the channel id
    never counts (the store keys by workspace and channel)."""
    if threads is None:
        return None
    thread = threads.last_at(channel, workspace=workspace)
    if thread is None:
        return None
    poll = _channel_row(committed, local, workspace, channel).get("poll")
    poll = poll if isinstance(poll, dict) else {}
    open_ = _duration(poll.get("thread_open"), _duration(_THREAD_OPEN_DEFAULT, 1800.0))
    return thread if (_utc(now) - thread).total_seconds() <= open_ else None


def guest_channels(committed, local):
    """Every channel of a `guest` Slack workspace, as `(workspace_id, channel_id, row)` in file order. The local file
    comes first, because it declares the `guest` workspaces (ADR-0177), and a channel the two files share reads once."""
    out, seen = [], set()
    for doc in (local, committed):
        for ws in (doc or {}).get("workspaces") or []:
            if str(ws.get("class") or "owned") != "guest":
                continue
            for row in ws.get("channels") or []:
                key = (str(ws.get("id")), str(row.get("id")))
                if key in seen:
                    continue
                seen.add(key)
                out.append((key[0], key[1], row))
    return out


class PollSchedule:
    """The polling schedule of each `guest` channel (#2709): the `next_due` of the channel and the interval that
    follows each poll.

    A tick fires every minute and asks `due(now)`; the answer holds each channel whose `next_due` has passed, so a tick
    does nothing before it. In `two_state` mode the interval is `fast` after a message from another person and `slow`
    after `quiet_after` of silence, and a channel with no message yet is silent. In `exponential` mode it starts at
    `floor`, multiplies by `factor` at each idle window and stops at `ceiling`; the entry stores that idle `step`, so the
    next interval reads the state and never a value recomputed from `last_at`. A new message (`note_message`) and an
    announced event (`note_event`) return it to fast and store the floor as the step, so the growth restarts there. An
    open agent thread newer than `thread_open` keeps it fast. A message older than the stored activity never moves it
    backward, so a page read twice plans the same next poll. Every two_state interval comes from
    `slackpolicy.poll_interval`, so the schedule and the decision read one set of keys.

    The state lives in one JSON file in the guest state folder, keyed by Slack workspace and channel, and every clock
    value in it is UTC with an explicit offset. An unreadable file reads as an empty schedule, so a broken file never
    stops a poll. Every write is atomic (ADR-0030) and holds the file's lock, so a tick and a poll that note one channel
    at the same moment lose no update. Every call reads the guest workspaces and the channel rows from the config the
    caller holds at that moment, so a workspace or a poll block added after construction is polled, as
    `slackpolicy.decide` reads it. The module reads no clock of its own: the caller passes every clock value."""

    def __init__(self, path, committed, local, threads=None):
        self.path = Path(path)
        self.committed, self.local, self.threads = committed or {}, local or {}, threads

    def _load(self):
        try:
            doc = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return doc if isinstance(doc, dict) else {}

    def _save(self, doc):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(doc, indent=1, sort_keys=True) + "\n")

    @contextmanager
    def _locked(self):
        """The write lock of the schedule file: a tick and a poll that note one channel at the same moment each read the
        entry they merge into under this lock, so neither update is lost to the other. The lock file sits in the private
        folder with the mode that folder takes, and a platform without `fcntl` keeps the write with no lock rather than
        fail to import."""
        try:
            import fcntl
        except ImportError:
            fcntl = None
        lock = self.path.with_name(self.path.name + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(lock.parent, DIR_MODE)
        with open(lock, "a+") as handle:
            os.chmod(lock, FILE_MODE)
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def _entry(self, doc, workspace, channel):
        return (doc.get(str(workspace)) or {}).get(str(channel)) or {}

    def _write_entry(self, workspace, channel, **fields):
        with self._locked():
            doc = self._load()
            entry = dict(self._entry(doc, workspace, channel))
            entry.update(fields)
            doc.setdefault(str(workspace), {})[str(channel)] = entry
            self._save(doc)
        return entry

    @staticmethod
    def _step_of(entry):
        """The stored idle step of the channel in seconds, or None when the entry holds none. It is the interval the
        last poll planned, and the next interval reads it rather than a value recomputed from `last_at`."""
        try:
            return float(entry["step"])
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _exp_poll(row):
        """The `floor`, `ceiling`, `factor` and `idle_window` of an exponential channel, in seconds."""
        poll = row.get("poll") if isinstance(row.get("poll"), dict) else {}
        try:
            factor = float(poll.get("factor", _EXP_FACTOR))
        except (TypeError, ValueError):
            factor = _EXP_FACTOR
        return (_duration(poll.get("floor"), _EXP_FLOOR), _duration(poll.get("ceiling"), _EXP_CEILING),
                factor, _duration(poll.get("idle_window"), _EXP_WINDOW))

    @staticmethod
    def _exponential(row):
        poll = row.get("poll") if isinstance(row.get("poll"), dict) else {}
        return str(poll.get("mode") or "two_state") == "exponential"

    def next_due(self, workspace, channel):
        """The time of the next poll of the channel, or None when the schedule never held the channel."""
        return _time_of(self._entry(self._load(), workspace, channel).get("next_due"))

    def last_at(self, workspace, channel):
        """The time of the last message or announced event of the channel, or None."""
        return _time_of(self._entry(self._load(), workspace, channel).get("last_at"))

    def interval(self, workspace, channel, now):
        """The seconds to the next poll of the channel at `now`, from its `poll` block and its state. An open agent thread
        newer than `thread_open` keeps the interval fast; an older one is out of the window, so the channel falls back on
        its own silence. An exponential channel answers the idle step its last poll stored, clamped to the floor and the
        ceiling, so a message after an idle window restarts at the floor and never jumps to the ceiling."""
        entry = self._entry(self._load(), workspace, channel)
        row = _channel_row(self.committed, self.local, workspace, channel)
        if self._exponential(row):
            floor, ceiling, _factor, _window = self._exp_poll(row)
            step = self._step_of(entry)
            if step is None:
                return self._exp_step(workspace, channel, now, _time_of(entry.get("last_at")))
            thread = open_thread_last(self.threads, self.committed, self.local, workspace, channel, now)
            return min(floor, ceiling) if thread is not None else min(max(step, floor), ceiling)
        return self._interval_at(workspace, channel, now, _time_of(entry.get("last_at")))

    def _exp_step(self, workspace, channel, at, last):
        """The idle step of an exponential channel at `at`: the floor times the factor once per idle window since `last`,
        with the ceiling as the bound, and the floor while an agent thread is inside `thread_open`."""
        row = _channel_row(self.committed, self.local, workspace, channel)
        floor, ceiling, factor, window = self._exp_poll(row)
        thread = open_thread_last(self.threads, self.committed, self.local, workspace, channel, at)
        if thread is not None or last is None or window <= 0:
            return min(floor, ceiling)
        steps = max(0, int((_utc(at) - _utc(last)).total_seconds() // window))
        return min(floor * (factor ** steps), ceiling)

    def _interval_at(self, workspace, channel, now, last):
        """The seconds to the next poll at `now`, with `last` as the channel's last activity. A `two_state` channel with
        no message and no open thread is quiet, so it waits `slow`: this rule lives in the schedule, and the shared
        `slackpolicy.poll_interval` keeps its own fast default for a channel with no prior message."""
        row = _channel_row(self.committed, self.local, workspace, channel)
        if self._exponential(row):
            return self._exp_step(workspace, channel, now, last)
        thread = open_thread_last(self.threads, self.committed, self.local, workspace, channel, now)
        poll = row.get("poll") if isinstance(row.get("poll"), dict) else {}
        if last is None and thread is None:
            return _duration(poll.get("slow"), _duration(_POLL_SLOW_DEFAULT, 600.0))
        return slackpolicy.poll_interval(_utc(now), row, {"last_message_at": _utc(last), "thread_last_at": thread})

    def due(self, now):
        """The guest channels whose `next_due` has passed at `now`, as `(workspace, channel)` in file order. A channel
        the schedule never held is due, so its first tick reads it at once. The channels come from the live config the
        caller holds at this call, so a workspace or a channel added after construction is polled on the next tick."""
        doc, at, out = self._load(), _utc(now), []
        for workspace, channel, _row in guest_channels(self.committed, self.local):
            value = _time_of(self._entry(doc, workspace, channel).get("next_due"))
            if value is None or at >= value:
                out.append((workspace, channel))
        return out

    def _advance(self, workspace, channel, at):
        """Set the channel's next due time one interval after `at`. An exponential channel stores the step the interval
        came from, so the state carries the growth and the next read of the interval answers it."""
        at = _utc(at)
        last = self.last_at(workspace, channel)
        interval = self._interval_at(workspace, channel, at, last)
        fields = {"next_due": (at + timedelta(seconds=interval)).isoformat()}
        if self._exponential(_channel_row(self.committed, self.local, workspace, channel)):
            fields["step"] = interval
        self._write_entry(workspace, channel, **fields)

    def note_message(self, workspace, channel, at):
        """A message from another person returns the channel to fast: it is the last activity, and the next poll is one
        interval away. A channel that is already due stays due: its next due time is the earlier of the stored one and
        one interval after the message, and a channel the schedule never held stays due at the message time, so a
        message never delays a poll the tick owes. A message older than the stored activity never moves it backward, so
        a replayed page plans the same next poll it did before. An exponential channel stores the floor as its step, so
        its growth restarts. One write carries every field, so an interrupted note never leaves a last activity with a
        stale due time."""
        at = _utc(at)
        with self._locked():
            doc = self._load()
            entry = dict(self._entry(doc, workspace, channel))
            existing = _time_of(entry.get("last_at"))
            new_last = at if existing is None or at > existing else existing
            anchor = max(at, new_last)
            interval = self._interval_at(workspace, channel, anchor, new_last)
            planned = anchor + timedelta(seconds=interval)
            stored_due = _time_of(entry.get("next_due"))
            entry["last_at"] = new_last.isoformat()
            entry["next_due"] = (min(stored_due, planned) if stored_due is not None else anchor).isoformat()
            row = _channel_row(self.committed, self.local, workspace, channel)
            if self._exponential(row):
                floor, ceiling, _factor, _window = self._exp_poll(row)
                entry["step"] = min(floor, ceiling)
            doc.setdefault(str(workspace), {})[str(channel)] = entry
            self._save(doc)
        return entry

    def note_event(self, workspace, channel, at):
        """An announced event in the channel returns it to fast, as a new message does, so a reaction is read at once."""
        self.note_message(workspace, channel, at)

    def polled(self, workspace, channel, at):
        """The poll of a due channel at `at`: its next due time is one interval away."""
        self._advance(workspace, channel, at)


class Poller:
    """One read of a due `guest` channel, the reread of its open threads, and the policy decision of each new message.

    `connect(channel=..., oldest=..., limit=...)` is the connector's channel read, and `connect(channel=...,
    thread_ts=..., limit=...)` its thread read. `now()` is the caller's clock value. `own` is the list of `ts` of the
    session's own posts, or the `OwnTs` store the writer fills. `owner` names the owner account, `footers` the connector
    footer its texts carry, and `threads` the `RepliedThreads` store. The caller polls one page at a time: the cursor
    advances after each page, so a later poll of the same channel resumes from the next page.

    `shadow` runs the poll in shadow mode (#2711): `poll` records each decision in `self.shadow` and hands nothing out,
    so the caller has no decision to act on and makes no write call.

    `schedule` is the channel's `PollSchedule`: a poll that delivers a new message notes the newest of them on it, so
    the channel's next due time follows the message."""

    def __init__(self, connect, checkpoint, committed, local, now, own=(), limit=100, owner=(), threads=None,
                 footers=(), shadow=False, schedule=None):
        self.connect, self.checkpoint = connect, checkpoint
        self.committed, self.local, self.now = committed, local, now
        self.own, self.limit = own, limit
        self.owner, self.threads = tuple(owner), threads
        self.footers, self.marks = tuple(footers), persona_marks(committed)
        self.schedule = schedule
        # The record lives in the folder the caller chose for the checkpoint, so the private state folder keeps the
        # cursor and the record together and no second path is invented.
        self.shadow = ShadowLog(self.checkpoint.path.with_name(SHADOW_NAME)) if shadow else None

    def clips(self, message):
        """The audio files of one read message, read through `guesstranscribe.audio_clips` (#2714): the file id, the
        mimetype and the name of each. The caller fetches each clip through the connector's read-file tool."""
        return guesstranscribe.audio_clips(message)

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
        returns. A page with no new message ends the walk of the channel for this tick.

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
        self._remember_own(page)
        own = _own_ts(self.own)
        self.checkpoint.record_page(channel, page, own)
        now = self.now()
        # Each message is the channel's activity at its own `ts`, so a page read late plans an old message at the slow
        # interval and a fresh one at the fast interval. The tick's clock is never the activity, and only an open agent
        # thread inside its window turns the thread rule on.
        thread = open_thread_last(self.threads, self.committed, self.local, workspace, channel, now)
        new = [m for m in page if _newer(m.get("ts"), oldest) and not self.is_own(m)]
        pairs = [(m, slackpolicy.decide(self.committed, self.local, event_of(m, workspace, channel, own), now,
                                        {"last_message_at": message_time(m, now), "thread_last_at": thread}))
                 for m in new]
        self._note_delivered(workspace, channel, new, now)
        return pairs + self._reread_threads(workspace, channel, now)

    def _note_delivered(self, workspace, channel, messages, now):
        """Note the newest delivered message on the schedule, so the channel's next poll is one interval from that
        message. A poller with no schedule leaves the schedule to its caller."""
        if self.schedule is None or not messages:
            return
        self.schedule.note_message(workspace, channel,
                                   max(message_time(m, now) for m in messages))

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
        for thread_ts, state in sorted(self.threads.open_threads(channel, workspace=workspace).items()):
            response = self.connect(channel=str(channel), thread_ts=thread_ts, limit=self.limit)
            if read_failed(response):
                continue
            page = messages_of(response)
            self._remember_own(page)
            new = [m for m in page if _newer(m.get("ts"), state["cursor"]) and not self.is_own(m)]
            newest = newest_ts(page)
            last_at = state["last_at"]
            if new or (newest is not None and _newer(newest, state["cursor"])):
                self.threads.record(channel, thread_ts, at=now, ts=newest, workspace=workspace)
                last_at = now
            if last_at is not None and (_utc(now) - _utc(last_at)).total_seconds() > quiet:
                self.threads.close(channel, thread_ts, workspace=workspace)
            if new:
                # A reply read in a thread is that thread's activity, never the newest activity of the whole channel,
                # so the decision reads the timer of the thread the message came from.
                pairs += [(m, slackpolicy.decide(self.committed, self.local,
                                                 event_of(m, workspace, channel, _own_ts(self.own)), now,
                                                 {"last_message_at": message_time(m, now),
                                                  "thread_last_at": last_at}))
                          for m in new]
        return pairs
