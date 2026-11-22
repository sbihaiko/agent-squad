"""The guest thread queue and the owner-addressed path (SPEC 2682, stories 36 and 73; #2713).

A thread holds one draft at a time. While the owner reviews that draft, a new reply for the thread waits in the
thread's local queue, and the owner gets one direct-message notice. The queue lives in the private `guest` state folder
(`gueststate.GuestState`, kind `queue`), so the 10-day purge reaches it and no client text enters a repository.

A question addressed to the owner by name or by mention never gets a reply in the channel. The flow prepares that reply
for the owner's direct message only, in the shape every guest text carries, and it sends nothing. The owner decides
whether the reply leaves, so the prepared reply is a value the caller holds (`slackwriter.Draft`).

The module reads no clock and calls no network. `connect` is the connector the caller supplies, and the flow calls one
method of it, `notify`, for the direct-message notice.

Guards: `tests/test_guest_queue.py`.
"""
import json
import re
from typing import NamedTuple

from . import slackwriter

DRAFT, WAIT, DM, NONE = "draft", "wait", "dm", "none"
KIND = "queue"
NOTICE = "A guest reply waits in thread {thread_ts} of channel {channel}."
DEFAULT_NAME = "the owner"


class Owner(NamedTuple):
    """The owner as a guest message names the owner: the Slack user id of a mention, and the name words of a text."""
    user: str = ""
    name: str = ""


class Outcome(NamedTuple):
    """What one reply became: `DRAFT` with a prepared channel draft, `WAIT` with the notice the owner got, `DM` with the
    reply prepared for the owner direct message, or `NONE` when the decision names no reply."""
    kind: str
    draft: object | None
    notice: str | None


def _word(text, name):
    """True when the name appears in the text as whole words, in either letter case."""
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE) is not None


def addressed_to_owner(text, owner):
    """True when the text addresses the owner: a mention of the owner's user id, or the owner's name as whole words.
    The address is the signal, so a question mark is not needed."""
    text = str(text or "")
    user = str(getattr(owner, "user", "") or "")
    if user and (f"<@{user}>" in text or f"<@{user}|" in text):
        return True
    name = str(getattr(owner, "name", "") or "").strip()
    return bool(name) and _word(text, name)


def _persona_name(decision):
    persona = getattr(decision, "persona", None)
    name = getattr(persona, "name", None) if persona is not None else None
    return str(name) if name else ""


def queue_name(channel, thread_ts):
    """The name of a thread's one queue file: its channel and the `ts` of the thread."""
    return f"{channel}-{thread_ts}"


class ThreadQueue:
    """One local queue for each thread that holds a draft. Each queue is one entry of the private state folder, so a
    second hold of the same thread names the same file and never starts a second queue."""

    def __init__(self, state):
        self.state = state

    def name(self, channel, thread_ts):
        return queue_name(channel, thread_ts)

    def load(self, channel, thread_ts):
        """The queue document of one thread. An unreadable entry reads as an empty queue, so a broken file never stops
        a reply."""
        try:
            doc = json.loads(self.state.get(KIND, self.name(channel, thread_ts)) or "{}")
        except (TypeError, ValueError):
            return {}
        return doc if isinstance(doc, dict) else {}

    def _save(self, channel, thread_ts, doc):
        self.state.put(KIND, self.name(channel, thread_ts), json.dumps(doc, sort_keys=True))

    def holds(self, channel, thread_ts):
        """True while the thread holds a draft the owner has not released."""
        return bool(self.load(channel, thread_ts).get("draft"))

    def hold(self, channel, thread_ts):
        """Mark the thread as holding a draft and return True. Returns False when it held one already, so a thread keeps
        one queue."""
        channel, thread_ts = str(channel), str(thread_ts)
        doc = self.load(channel, thread_ts)
        if doc.get("draft"):
            return False
        doc.update({"draft": True, "channel": channel, "thread_ts": thread_ts, "waiting": []})
        self._save(channel, thread_ts, doc)
        return True

    def release(self, channel, thread_ts):
        """Clear the draft hold, so the next reply leaves as a new draft. The waiting replies stay queued. Returns False
        when the thread held no draft."""
        channel, thread_ts = str(channel), str(thread_ts)
        doc = self.load(channel, thread_ts)
        if not doc.get("draft"):
            return False
        doc["draft"] = False
        self._save(channel, thread_ts, doc)
        return True

    def wait(self, channel, thread_ts, text):
        """Put one reply in the queue of a thread that holds a draft, and return its position, from 1. Returns 0, and
        stores nothing, when the thread holds no draft: a reply waits only while a draft holds the thread."""
        channel, thread_ts = str(channel), str(thread_ts)
        doc = self.load(channel, thread_ts)
        if not doc.get("draft"):
            return 0
        doc.setdefault("waiting", []).append({"text": str(text)})
        self._save(channel, thread_ts, doc)
        return len(doc["waiting"])

    def waiting(self, channel, thread_ts):
        """The replies that wait in the thread's queue, oldest first."""
        entries = self.load(channel, thread_ts).get("waiting") or []
        return tuple(str(entry.get("text")) for entry in entries if isinstance(entry, dict))


def _dm_draft(dm, body, name):
    """The reply prepared for the owner direct message, in the shape every guest text carries, or None when no persona
    names the mark. It is a draft: the owner decides whether it leaves."""
    if not name:
        return None
    return slackwriter.Draft(slackwriter.DRAFT_WORD, str(dm), "", slackwriter.compose(name, body), name)


class GuestReplies:
    """The route of one reply of one `guest` message. `queue` is the thread queue, `connect` the connector (`notify`
    sends the owner one direct message), `owner` the owner as a message names the owner, and `owner_dm` the owner's
    direct-message channel."""

    def __init__(self, queue, connect, owner, owner_dm):
        self.queue = queue
        self.connect = connect
        self.owner = owner
        self.owner_dm = str(owner_dm)

    def answer(self, decision, channel, thread_ts, body, question=""):
        """Route one reply. An owner-addressed question yields a draft for the owner direct message, and no reply in the
        channel. A thread that holds a draft queues the reply and sends the owner one notice. Any other reply is a
        channel draft, and the thread starts to hold it."""
        channel, thread_ts = str(channel), str(thread_ts)
        if addressed_to_owner(question, self.owner):
            draft = _dm_draft(self.owner_dm, body, _persona_name(decision))
            return Outcome(DM, draft, None) if draft is not None else Outcome(NONE, None, None)
        if self.queue.holds(channel, thread_ts):
            self.queue.wait(channel, thread_ts, body)
            notice = NOTICE.format(thread_ts=thread_ts, channel=channel)
            self.connect.notify(self.owner_dm, notice)
            return Outcome(WAIT, None, notice)
        draft = slackwriter.plan(decision, channel, thread_ts, body)
        if draft is None:
            return Outcome(NONE, None, None)
        self.queue.hold(channel, thread_ts)
        return Outcome(DRAFT, draft, None)


__all__ = ["DM", "DRAFT", "GuestReplies", "NONE", "NOTICE", "Outcome", "Owner", "ThreadQueue", "WAIT",
           "addressed_to_owner", "queue_name"]
