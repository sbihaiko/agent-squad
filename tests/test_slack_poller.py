"""The guest poller: the read-only hook and the checkpoint that code writes (SPEC 2682, #2707).

A fake connector stands for the claude.ai Slack connector. A read takes a channel and an `oldest` value, and it returns
a page of messages. Every id and every `ts` here is a placeholder.
"""
import asyncio
import json
from datetime import datetime, timedelta

from squad import slackpolicy as S
from squad import slackpoller as P

OWNER = "U0OWNER001"
CLIENT = "U0CLIENT01"
NOW = datetime(2026, 11, 22, 12, 0, 0)
THREAD = "100.000100"
FOOTER = "O envio foi feito usando Claude"

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
         "channels": [{"id": "C0CLISUP01", "name": "support", "mode": "listen+speak", "speak": "draft",
                       "roles": ["colleague"],
                       "poll": {"mode": "two_state", "fast": "1m", "slow": "10m", "quiet_after": "10m"}}]},
    ],
    "members": [{"workspace": "T0CLIENT01", "channel": "C0CLISUP01", "role": "colleague", "users": [CLIENT]}],
}

WS, CH = "T0CLIENT01", "C0CLISUP01"


def message(ts, user=CLIENT, text="hello", **over):
    return {"ts": ts, "user": user, "text": text, **over}


class FakeConnector:
    """The connector's calls: one read of a channel from `oldest` (inclusive, as Slack does), one read of a thread, and
    one post. A channel read returns the messages that stand alone, and a thread read returns the thread's root and its
    replies, as the connector does."""

    def __init__(self, messages=(), fail_threads=False):
        self.messages = list(messages)
        self.reads = []
        self.thread_reads = []
        self.posts = []
        self.fail_threads = fail_threads          # a transient connector error on every thread read

    def read(self, channel, oldest=None, limit=100, thread_ts=None):
        if thread_ts is not None:
            self.thread_reads.append({"channel": channel, "thread_ts": thread_ts, "limit": limit})
            if self.fail_threads:
                return {"ok": False, "error": "rate_limited"}
            page = [m for m in self.messages if m["ts"] == thread_ts or m.get("thread_ts") == thread_ts]
        else:
            self.reads.append({"channel": channel, "oldest": oldest, "limit": limit})
            page = [m for m in self.messages if not m.get("thread_ts") or m.get("thread_ts") == m["ts"]]
            page = [m for m in page if oldest is None or float(m["ts"]) >= float(oldest)]
        page.sort(key=lambda m: float(m["ts"]))
        return {"ok": True, "messages": page[:limit], "has_more": len(page) > limit}

    def post(self, channel, text, user=OWNER, ts="900.000900"):
        msg = message(ts, user=user, text=text)
        self.messages.append(msg)
        self.posts.append(msg)
        return {"ok": True, "channel": channel, "ts": ts}


def poller(conn, path, own=(), limit=100, **over):
    return P.Poller(conn.read, P.Checkpoint(path), COMMITTED, LOCAL, lambda: NOW, own=own, limit=limit, **over)


def decision_ts(pairs):
    return [(m["ts"], d.kind) for m, d in pairs]


# --- the read-only hook -------------------------------------------------------------------------------------------

def test_a_read_tool_passes_and_every_other_tool_is_denied():
    allowed = ["mcp__slack__slack_read_channel", "mcp__slack__slack_read_thread", "slack_search_public"]
    refused = ["mcp__slack__slack_send_message", "mcp__slack__slack_send_message_draft",
               "mcp__slack__slack_get_file_upload_url", "Bash", "Write", "mcp__slack__slack_list_channels_upload"]
    for name in allowed:
        assert P.read_denial(name) is None, name
    for name in refused:
        reason = P.read_denial(name)
        assert reason and "read" in reason, (name, reason)


def test_the_pre_hook_returns_one_denial_answer():
    hook = P.pre_read_tool_use()
    denied = asyncio.run(hook({"tool_name": "mcp__slack__slack_send_message", "tool_input": {}}, "t1", None))
    assert denied["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert asyncio.run(hook({"tool_name": "mcp__slack__slack_read_channel", "tool_input": {}}, "t2", None)) == {}


# --- the PostToolUse checkpoint -----------------------------------------------------------------------------------

def test_a_read_result_advances_the_checkpoint_to_its_newest_ts(tmp_path):
    ck = P.Checkpoint(tmp_path / "checkpoint.json")
    hook = P.checkpoint_hook(ck)
    result = {"ok": True, "messages": [message("100.000100"), message("100.000300"), message("100.000200")]}
    asyncio.run(hook({"tool_name": "mcp__slack__slack_read_channel",
                      "tool_input": {"channel_id": CH}, "tool_response": result}, "t1", None))
    assert ck.get(CH) == "100.000300"


def test_a_read_that_returns_an_own_post_advances_to_the_newest_other_ts(tmp_path):
    ck = P.Checkpoint(tmp_path / "checkpoint.json")
    hook = P.checkpoint_hook(ck, own=("900.000900",))
    result = {"ok": True, "messages": [message("100.000200"), message("900.000900", user=OWNER)]}
    asyncio.run(hook({"tool_name": "mcp__slack__slack_read_channel",
                      "tool_input": {"channel_id": CH}, "tool_response": result}, "t1", None))
    assert ck.get(CH) == "100.000200"


def test_a_post_result_never_advances_the_checkpoint(tmp_path):
    ck = P.Checkpoint(tmp_path / "checkpoint.json")
    ck.advance(CH, "100.000100")
    hook = P.checkpoint_hook(ck)
    asyncio.run(hook({"tool_name": "mcp__slack__slack_send_message", "tool_input": {"channel_id": CH},
                      "tool_response": {"ok": True, "ts": "900.000900"}}, "t1", None))
    assert ck.get(CH) == "100.000100"


def test_an_own_post_ts_never_advances_the_checkpoint(tmp_path):
    ck = P.Checkpoint(tmp_path / "checkpoint.json")
    assert ck.advance(CH, "900.000900", own=("900.000900",)) is False
    assert ck.get(CH) is None


def test_the_checkpoint_never_moves_backward(tmp_path):
    ck = P.Checkpoint(tmp_path / "checkpoint.json")
    ck.advance(CH, "100.000500")
    ck.advance(CH, "100.000200")
    assert ck.get(CH) == "100.000500"


# --- reads move forward with `oldest` ------------------------------------------------------------------------------

def test_the_read_passes_the_checkpoint_as_oldest(tmp_path):
    conn = FakeConnector([message("100.000100"), message("100.000200")])
    p = poller(conn, tmp_path / "checkpoint.json")
    p.poll(WS, CH)
    p.poll(WS, CH)
    assert [r["oldest"] for r in conn.reads] == [None, "100.000200"]


def test_each_page_advances_the_checkpoint_and_delivers_no_repeat(tmp_path):
    conn = FakeConnector([message("100.000100"), message("100.000200"), message("100.000300"),
                          message("100.000400")])
    p = poller(conn, tmp_path / "checkpoint.json", limit=2)
    delivered = []
    for _ in range(4):
        delivered += [m["ts"] for m, _ in p.poll(WS, CH)]
    assert delivered == ["100.000100", "100.000200", "100.000300", "100.000400"]
    assert p.checkpoint.get(CH) == "100.000400"


def test_a_new_session_resumes_from_the_checkpoint_with_no_gap_and_no_repeat(tmp_path):
    path = tmp_path / "checkpoint.json"
    conn = FakeConnector([message("100.000100"), message("100.000200"), message("100.000300"),
                          message("100.000400")])
    first = poller(conn, path, limit=3).poll(WS, CH)
    resumed = poller(conn, path, limit=3).poll(WS, CH)
    assert [m["ts"] for m, _ in first] == ["100.000100", "100.000200", "100.000300"]
    assert [m["ts"] for m, _ in resumed] == ["100.000400"]


def test_the_checkpoint_is_readable_between_processes(tmp_path):
    path = tmp_path / "checkpoint.json"
    conn = FakeConnector([message("100.000100")])
    poller(conn, path).poll(WS, CH)
    saved = json.loads(path.read_text())
    assert saved[CH] == "100.000100"


# --- the policy hand-off -------------------------------------------------------------------------------------------

def test_each_delivered_message_reaches_the_policy(tmp_path):
    conn = FakeConnector([message("100.000100"), message("100.000200", user="U0STRANGER1")])
    got = poller(conn, tmp_path / "checkpoint.json").poll(WS, CH)
    assert decision_ts(got) == [("100.000100", S.PROPOSAL), ("100.000200", S.DATA)]


def test_an_own_message_is_not_delivered_and_never_moves_the_checkpoint(tmp_path):
    path = tmp_path / "checkpoint.json"
    conn = FakeConnector([message("100.000100", user=OWNER)])
    got = poller(conn, path, own=("100.000100",)).poll(WS, CH)
    assert got == []
    assert not path.exists()


# --- own-message detection and thread rereads (#2708) --------------------------------------------------------------

def test_a_ts_in_the_own_store_is_never_delivered(tmp_path):
    own = P.OwnTs(tmp_path / "own.json")
    own.add("900.000900")
    conn = FakeConnector([message("100.000100"), message("900.000900", user=OWNER)])
    got = poller(conn, tmp_path / "checkpoint.json", own=own).poll(WS, CH)
    assert [m["ts"] for m, _ in got] == ["100.000100"]
    assert P.Checkpoint(tmp_path / "checkpoint.json").get(CH) == "100.000100"


def test_an_owner_message_that_starts_with_a_persona_mark_is_never_delivered(tmp_path):
    marked = message("100.000900", user=OWNER, text="*Iara's Msg*\n_hello_")
    unmarked = message("100.001000", user=OWNER, text="hello")
    conn = FakeConnector([marked, unmarked])
    got = poller(conn, tmp_path / "checkpoint.json", owner=(OWNER,)).poll(WS, CH)
    assert [m["ts"] for m, _ in got] == ["100.001000"]


def test_the_persona_mark_comparison_ignores_the_connector_footer(tmp_path):
    marked = message("100.000900", user=OWNER, text=f"*Iara's Msg*\n_hello_\n\n{FOOTER}")
    plain = message("100.001000", user=OWNER, text=f"hello\n\n{FOOTER}")
    conn = FakeConnector([marked, plain])
    got = poller(conn, tmp_path / "checkpoint.json", owner=(OWNER,), footers=(FOOTER,)).poll(WS, CH)
    assert [m["ts"] for m, _ in got] == ["100.001000"]


def test_a_thread_where_the_agent_replied_is_reread(tmp_path):
    first, second = THREAD, "100.000200"
    threads = P.RepliedThreads(tmp_path / "threads.json")
    conn = FakeConnector([message(first, text="question"), message(second, text="other question"),
                          message("100.000700", text="first answer", thread_ts=first),
                          message("100.000800", text="second answer", thread_ts=second),
                          message("100.001200", text="more on the first", thread_ts=first),
                          message("100.001300", text="more on the second", thread_ts=second)])
    p = poller(conn, tmp_path / "checkpoint.json", threads=threads)
    assert [m["ts"] for m, _ in p.poll(WS, CH)] == [first, second]
    threads.record(CH, first, at=NOW, ts="100.000900")
    threads.record(CH, second, at=NOW, ts="100.001000")
    got = p.poll(WS, CH)
    assert [(r["channel"], r["thread_ts"]) for r in conn.thread_reads] == [(CH, first), (CH, second)]
    assert sorted(m["ts"] for m, _ in got) == ["100.001200", "100.001300"]


def test_a_quiet_thread_is_no_longer_reread(tmp_path):
    threads = P.RepliedThreads(tmp_path / "threads.json")
    conn = FakeConnector([message(THREAD, text="question")])
    clock = [NOW]
    p = P.Poller(conn.read, P.Checkpoint(tmp_path / "checkpoint.json"), COMMITTED, LOCAL, lambda: clock[0],
                 threads=threads)
    threads.record(CH, THREAD, at=NOW, ts="100.000900")
    clock[0] = NOW + timedelta(minutes=31)
    p.poll(WS, CH)
    assert len(conn.thread_reads) == 1        # one last read collects a reply that arrived before it closed
    p.poll(WS, CH)
    assert len(conn.thread_reads) == 1        # quiet: the thread is never reread again


def test_a_thread_recorded_without_last_at_is_reread_not_dropped(tmp_path):
    path = tmp_path / "threads.json"
    # A writer stored the cursor only. The thread is still one where the agent replied, so the poller must reread it.
    path.write_text(json.dumps({CH: {THREAD: {"cursor": "100.000100"}}}))
    threads = P.RepliedThreads(path)
    assert THREAD in threads.open_threads(CH)
    conn = FakeConnector([message(THREAD, text="question"),
                          message("100.001200", text="the late reply", thread_ts=THREAD)])
    got = poller(conn, tmp_path / "checkpoint.json", threads=threads).poll(WS, CH)
    assert [m["ts"] for m, _ in got] == ["100.000100", "100.001200"]


def test_activity_in_a_thread_refreshes_its_quiet_clock(tmp_path):
    threads = P.RepliedThreads(tmp_path / "threads.json")
    conn = FakeConnector([message(THREAD, text="question"),
                          message("100.001200", user=OWNER, text="*Iara's Msg*\n_still here_", thread_ts=THREAD)])
    clock = [NOW + timedelta(minutes=31)]
    p = P.Poller(conn.read, P.Checkpoint(tmp_path / "checkpoint.json"), COMMITTED, LOCAL, lambda: clock[0],
                 owner=(OWNER,), threads=threads)
    threads.record(CH, THREAD, at=NOW, ts="100.001100")
    p.poll(WS, CH)                              # an own reply moves the thread's activity to now
    clock[0] = NOW + timedelta(minutes=45)      # fourteen minutes later, not yet quiet
    p.poll(WS, CH)
    assert len(conn.thread_reads) == 2


def test_a_failed_thread_read_leaves_the_thread_open(tmp_path):
    threads = P.RepliedThreads(tmp_path / "threads.json")
    conn = FakeConnector([message(THREAD, text="question")], fail_threads=True)
    clock = [NOW + timedelta(minutes=31)]
    p = P.Poller(conn.read, P.Checkpoint(tmp_path / "checkpoint.json"), COMMITTED, LOCAL, lambda: clock[0],
                 threads=threads)
    P.Checkpoint(tmp_path / "checkpoint.json").advance(CH, "100.000100")
    threads.record(CH, THREAD, at=NOW, ts="100.000900")
    assert p.poll(WS, CH) == []
    assert THREAD in threads.open_threads(CH)   # a failed read never closes the thread
    conn.fail_threads = False
    clock[0] = NOW + timedelta(minutes=45)
    conn.messages.append(message("100.001200", text="the late reply", thread_ts=THREAD))
    assert [m["ts"] for m, _ in p.poll(WS, CH)] == ["100.001200"]


def test_an_own_reply_in_a_thread_is_never_delivered(tmp_path):
    threads = P.RepliedThreads(tmp_path / "threads.json")
    conn = FakeConnector([message(THREAD, text="question"),
                          message("100.001200", user=OWNER, text=f"*Iara's Msg*\n_answer_\n\n{FOOTER}",
                                  thread_ts=THREAD),
                          message("100.001300", text="thanks", thread_ts=THREAD)])
    p = poller(conn, tmp_path / "checkpoint.json", owner=(OWNER,), footers=(FOOTER,), threads=threads)
    P.Checkpoint(tmp_path / "checkpoint.json").advance(CH, "100.000100")
    threads.record(CH, THREAD, at=NOW, ts="100.000100")
    got = p.poll(WS, CH)
    assert [m["ts"] for m, _ in got] == ["100.001300"]
    assert conn.thread_reads == [{"channel": CH, "thread_ts": THREAD, "limit": 100}]


def test_a_persona_marked_post_the_read_returns_never_moves_the_checkpoint(tmp_path):
    own = P.OwnTs(tmp_path / "own.json")
    path = tmp_path / "checkpoint.json"
    conn = FakeConnector([message("100.000100"), message("100.000500", user=OWNER, text="*Iara's Msg*\n_hi_")])
    got = poller(conn, path, own=own, owner=(OWNER,)).poll(WS, CH)
    assert [m["ts"] for m, _ in got] == ["100.000100"]
    assert own.has("100.000500")               # the poller records the own post it observed
    assert P.Checkpoint(path).get(CH) == "100.000100"


# --- the acceptance test -------------------------------------------------------------------------------------------

def test_a_message_posted_between_the_read_and_the_post_is_delivered_by_the_next_read(tmp_path):
    path = tmp_path / "checkpoint.json"
    conn = FakeConnector([message("100.000100", text="first")])
    p = poller(conn, path)
    first = p.poll(WS, CH)
    assert [m["ts"] for m, _ in first] == ["100.000100"]
    # Another person writes a report while the agent reads and prepares its post. The writer keeps the post's ts in the
    # own list (#2708), so the poller never treats it as read.
    conn.messages.append(message("100.000500", text="the bug report"))
    post = conn.post(CH, "a draft reply", ts="900.000900")
    p.own = (post["ts"],)
    assert json.loads(path.read_text())[CH] == "100.000100"
    second = p.poll(WS, CH)
    assert [m["ts"] for m, _ in second] == ["100.000500"]
    assert second[0][0]["text"] == "the bug report"
    assert json.loads(path.read_text())[CH] == "100.000500"
