"""The guest thread queue and the owner-addressed path (SPEC 2682, stories 36 and 73; #2713).

A thread holds one draft at a time. A reply for a thread that holds a draft waits in that thread's local queue, and the
owner gets a direct message when a reply waits. A question addressed to the owner by name or by mention never gets a
reply in the channel: the prepared reply goes to the owner direct message only, and the owner decides whether to send it.

A fake connector records every write call, so a test proves that the flow sends nothing by itself. Every id and every
text here is a placeholder.
"""
import copy
from datetime import datetime

from squad import guestqueue as Q
from squad import gueststate as G
from squad import slackpolicy as S
from squad import slackwriter as W

NOW = datetime(2026, 11, 22, 12, 0, 0)
OWNER = Q.Owner(user="U0OWNER001", name="Sandro")
OWNER_DM = "D0OWNERDM1"
QUESTION = "100.000100"
OTHER = "100.000200"
CH = "C0CLISUP01"

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "iara"},
    "personas": [{"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm and patient."}],
    "roles": [{"id": "colleague", "actions": ["status"]}],
    "workspaces": [],
}

LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": "T0CLIENT01", "label": "client-a", "class": "guest", "persona": "iara",
         "channels": [{"id": CH, "name": "support", "mode": "listen+speak", "speak": "draft",
                       "roles": ["colleague"],
                       "poll": {"mode": "two_state", "fast": "1m", "slow": "10m", "quiet_after": "10m"}}]},
    ],
    "members": [{"workspace": "T0CLIENT01", "channel": CH, "role": "colleague", "users": ["U0CLIENT01"]}],
}


class FakeConnector:
    """A fake connector: a direct-message notice, a draft write and a direct send are each recorded, and no call
    reaches Slack. The notice is the one write the flow may make by itself."""

    def __init__(self):
        self.notices, self.drafts, self.sends = [], [], []

    def writes(self):
        """Every write call the flow made: the drafts and the sends, never the direct-message notice."""
        return self.drafts + self.sends

    def notify(self, channel, text):
        self.notices.append({"channel": channel, "text": text})
        return {"ok": True, "channel": channel, "ts": "900.000900"}

    def draft(self, channel, text, thread_ts=None):
        self.drafts.append({"channel": channel, "text": text, "thread_ts": thread_ts})
        return {"ok": True, "channel": channel, "ts": "900.000901"}

    def send(self, channel, text, thread_ts=None):
        self.sends.append({"channel": channel, "text": text, "thread_ts": thread_ts})
        return {"ok": True, "channel": channel, "ts": "900.000902"}


def state(tmp_path):
    return G.GuestState(tmp_path / "guest-state", lambda: NOW.timestamp())


def queue(tmp_path):
    return Q.ThreadQueue(state(tmp_path))


def decision():
    return S.decide(copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL),
                    {"kind": "message", "workspace": "T0CLIENT01", "channel": CH, "user": "U0CLIENT01"}, NOW)


def replies(tmp_path, conn):
    return Q.GuestReplies(queue(tmp_path), conn, OWNER, OWNER_DM)


# --- 1. one local queue for each thread that holds a draft --------------------------------------------------------

def test_a_thread_that_holds_a_draft_keeps_one_local_queue(tmp_path):
    q = queue(tmp_path)
    assert q.holds(CH, QUESTION) is False
    assert q.hold(CH, QUESTION) is True
    assert q.hold(CH, QUESTION) is False               # one queue per thread: a second hold names the same file
    assert q.holds(CH, QUESTION) is True
    files = list(state(tmp_path).kind_dir("queue").glob("*.json"))
    assert len(files) == 1
    # The queue sits in the private state folder, so the 10-day purge reaches it.
    assert [entry.name for entry in state(tmp_path).entries("queue")] == [q.name(CH, QUESTION)]


# --- 2. a reply waits while its thread holds a draft ---------------------------------------------------------------

def test_a_reply_waits_in_the_queue_while_the_thread_holds_a_draft(tmp_path):
    q = queue(tmp_path)
    conn = FakeConnector()
    r = Q.GuestReplies(q, conn, OWNER, OWNER_DM)
    q.hold(CH, QUESTION)
    held = r.answer(decision(), CH, QUESTION, "The second answer.")
    assert held.kind == Q.WAIT
    assert q.waiting(CH, QUESTION) == ("The second answer.",)
    assert conn.writes() == []                         # a waited reply is never written
    # The owner releases the draft, so the next reply leaves as a new draft.
    assert q.release(CH, QUESTION) is True
    freed = r.answer(decision(), CH, QUESTION, "The third answer.")
    assert freed.kind == Q.DRAFT
    assert freed.draft.thread_ts == QUESTION


# --- 3. the owner gets a DM notice when a reply waits --------------------------------------------------------------

def test_the_owner_gets_a_dm_notice_when_a_reply_waits(tmp_path):
    q = queue(tmp_path)
    conn = FakeConnector()
    q.hold(CH, QUESTION)
    out = Q.GuestReplies(q, conn, OWNER, OWNER_DM).answer(decision(), CH, QUESTION, "The second answer.")
    assert len(conn.notices) == 1
    notice = conn.notices[0]
    assert notice["channel"] == OWNER_DM
    assert CH in notice["text"] and QUESTION in notice["text"]
    assert "The second answer." not in notice["text"]  # the notice never carries the client reply
    assert out.notice == notice["text"]


# --- 4. an owner-addressed question gets no reply in the channel -----------------------------------------------------

def test_a_question_addressed_to_the_owner_gets_no_reply_in_the_channel(tmp_path):
    assert Q.addressed_to_owner("Sandro, can you approve this?", OWNER)
    assert Q.addressed_to_owner("sandro, can you look?", OWNER)
    assert Q.addressed_to_owner("<@U0OWNER001> what do you think?", OWNER)
    assert not Q.addressed_to_owner("Sandra, can you approve this?", OWNER)
    conn = FakeConnector()
    out = Q.GuestReplies(queue(tmp_path), conn, OWNER, OWNER_DM).answer(
        decision(), CH, QUESTION, "I will check it.", question="Sandro, can you confirm the date?")
    assert out.kind == Q.DM
    assert conn.writes() == []                         # nothing reaches the guest channel
    assert out.draft.channel != CH


# --- 5. the prepared reply goes to the owner DM only ----------------------------------------------------------------

def test_the_prepared_reply_goes_to_the_owner_dm_only(tmp_path):
    conn = FakeConnector()
    out = Q.GuestReplies(queue(tmp_path), conn, OWNER, OWNER_DM).answer(
        decision(), CH, QUESTION, "I will check it.", question="<@U0OWNER001> can you confirm the date?")
    assert out.draft.channel == OWNER_DM
    assert out.draft.tool == W.DRAFT_WORD              # Stage 3 prepares a draft, never a send
    assert out.draft.text.startswith("*Iara's Msg*\n_")  # the mark, as on every guest text
    assert out.notice is None


# --- 6. the owner decides; nothing sends by itself -------------------------------------------------------------------

def test_the_owner_decides_and_nothing_sends_automatically(tmp_path):
    conn = FakeConnector()
    r = replies(tmp_path, conn)
    addressed = r.answer(decision(), CH, QUESTION, "I will check it.", question="Sandro, can you confirm?")
    first = r.answer(decision(), CH, OTHER, "The first answer.")
    second = r.answer(decision(), CH, OTHER, "The second answer.")
    assert (addressed.kind, first.kind, second.kind) == (Q.DM, Q.DRAFT, Q.WAIT)
    # No draft and no send reaches the connector. The only write is the one DM notice for the waited reply.
    assert conn.writes() == []
    assert [notice["channel"] for notice in conn.notices] == [OWNER_DM]
    texts = [call["text"] for call in conn.notices + conn.writes()]
    for body in ("I will check it.", "The first answer.", "The second answer."):
        assert body not in texts
    # The prepared reply is a value the caller holds, so a sender decides when and whether it leaves.
    assert addressed.draft.text not in texts
