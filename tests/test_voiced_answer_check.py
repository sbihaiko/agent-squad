"""Guard: issue #2948 (SPEC 2946). The daemon checks every answer before it posts it: the answer must answer a pending
inbox line of its channel and thread, its secrets are redacted, every link is removed, and an answer above
`answer_max_chars` is refused.

The seam is the daemon's own outbox path: one outbox line in, the fake `SlackClient` and the fake voice out, and the
audit file beside them. The fake client creates `C0…` ids and the fixture repository is `acme/app`, so no case names a
real workspace, channel or person.
"""
import asyncio
import json

from tests.test_voiced import OWNER, FakeClient, FakeVoice, append_outbox, outbox_daemon, voiced
from tests.test_voiced_guard import DEFAULTS, guarded_channel

QUESTION_TS = "1762500000.000100"


def pending(state, key, ts=QUESTION_TS, text="check the loop"):
    """One pending inbox line the daemon answers: a channel with no inbox line posts no answer."""
    append_outbox(state.inbox_for(key), {"id": f"{key}:{ts}", "ts": ts, "user": OWNER, "channel": key,
                                         "text": text, "language": "en-US"})


def daemon_for(tmp_path, mod, client, voice):
    return outbox_daemon(tmp_path, mod, client, mod.State(str(tmp_path)).channels(), voice)


def audit(daemon):
    try:
        raw = open(daemon.state.audit_path, encoding="utf-8").read()
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def test_an_answer_that_answers_no_pending_inbox_line_is_refused(tmp_path):
    """Criterion (1): an empty inbox answers nothing, so the line stays unposted and writes its refusal."""
    mod = voiced()
    client = FakeClient()
    channel = guarded_channel(tmp_path, mod, client, DEFAULTS)
    daemon = daemon_for(tmp_path, mod, client, FakeVoice(tmp_path))

    append_outbox(daemon.state.outbox_for(channel), {"thread_ts": QUESTION_TS, "text": "Green."})
    asyncio.run(daemon.drain_outboxes())

    assert client.posts() == []
    rows = audit(daemon)
    assert rows[-1]["decision"] == "refused", rows
    assert rows[-1]["reason"] == "unbound", rows


def test_an_answer_threading_under_no_line_of_its_channel_is_refused(tmp_path):
    """Criterion (1), the thread: a pending line of another channel never binds an answer to this one."""
    mod = voiced()
    client = FakeClient()
    channel = guarded_channel(tmp_path, mod, client, DEFAULTS)
    daemon = daemon_for(tmp_path, mod, client, FakeVoice(tmp_path))
    pending(daemon.state, "C0OTHER01")

    append_outbox(daemon.state.outbox_for(channel), {"thread_ts": QUESTION_TS, "text": "Green."})
    asyncio.run(daemon.drain_outboxes())

    assert client.posts() == []


def test_the_binding_rule_lives_in_the_slack_policy_module():
    """Criterion (2): the rule that binds an answer to an inbox line is one function of the policy module."""
    from squad import slackpolicy

    inbox = [{"id": "a", "ts": "1.1"}, {"id": "b", "ts": "2.2"}]
    assert slackpolicy.pending_line(inbox, "2.2")["id"] == "b"
    assert slackpolicy.pending_line(inbox, "3.3") is None
    assert slackpolicy.pending_line([], "1.1") is None
    assert slackpolicy.pending_line(inbox, None)["id"] == "a"


def test_a_clean_answer_under_a_pending_line_posts(tmp_path):
    """The bound is a stop condition, not a target: a clean answer under its question still posts."""
    mod = voiced()
    client = FakeClient()
    channel = guarded_channel(tmp_path, mod, client, DEFAULTS)
    daemon = daemon_for(tmp_path, mod, client, FakeVoice(tmp_path))
    pending(daemon.state, channel)

    append_outbox(daemon.state.outbox_for(channel), {"thread_ts": QUESTION_TS, "text": "Green."})
    asyncio.run(daemon.drain_outboxes())

    assert [p["text"] for p in client.posts()] == ["Green."]


def test_every_link_is_removed_before_the_post(tmp_path):
    """Criterion (4): no URL of an answer reaches the channel, and the audit counts the removed links."""
    mod = voiced()
    client = FakeClient()
    channel = guarded_channel(tmp_path, mod, client, DEFAULTS)
    daemon = daemon_for(tmp_path, mod, client, FakeVoice(tmp_path))
    pending(daemon.state, channel)

    append_outbox(daemon.state.outbox_for(channel),
                  {"thread_ts": QUESTION_TS, "text": "Read https://evil.example/a and http://plant.example/b now."})
    asyncio.run(daemon.drain_outboxes())

    assert [p["text"] for p in client.posts()] == ["Read and now."]
    assert audit(daemon)[-1]["links_removed"] == 2, audit(daemon)


def test_a_secret_in_text_speech_and_persona_is_redacted_before_the_post(tmp_path):
    """Criterion (3): the shared redaction module masks every secret of the answer before the post and the speech."""
    mod = voiced()
    client = FakeClient()
    channel = guarded_channel(tmp_path, mod, client, DEFAULTS)
    voice = FakeVoice(tmp_path)
    daemon = daemon_for(tmp_path, mod, client, voice)
    pending(daemon.state, channel)
    text_token = "xoxb-1234567890-secret"
    speech_token = "sk-ant-abcdefghijklmnopqrstuvwxyz"

    append_outbox(daemon.state.outbox_for(channel),
                  {"thread_ts": QUESTION_TS, "text": f"key {text_token} here.",
                   "speech": f"A token {speech_token}. Done.", "persona": "ghp_" + "a" * 20})
    asyncio.run(daemon.drain_outboxes())

    posts = client.posts()
    assert len(posts) == 1, posts
    assert posts[0]["text"] == "key [redacted] here."
    assert text_token not in posts[0]["text"]
    assert all(speech_token not in script for script, _ in voice.scripts)
    row = audit(daemon)[-1]
    assert row["redacted"] == ["text", "speech", "persona"], row


def test_an_answer_longer_than_answer_max_chars_is_refused(tmp_path):
    """Criterion (5): an answer past the cap posts nothing and writes its refusal with the `size` reason."""
    mod = voiced()
    client = FakeClient()
    channel = guarded_channel(tmp_path, mod, client, {**DEFAULTS, "answer_max_chars": 20})
    daemon = daemon_for(tmp_path, mod, client, FakeVoice(tmp_path))
    pending(daemon.state, channel)

    append_outbox(daemon.state.outbox_for(channel), {"thread_ts": QUESTION_TS, "text": "x" * 21})
    asyncio.run(daemon.drain_outboxes())

    assert client.posts() == []
    rows = audit(daemon)
    assert rows[-1]["decision"] == "refused" and rows[-1]["reason"] == "size", rows


def test_an_answer_at_answer_max_chars_still_posts(tmp_path):
    """Criterion (5), the other half: an answer of exactly `answer_max_chars` passes."""
    mod = voiced()
    client = FakeClient()
    channel = guarded_channel(tmp_path, mod, client, {**DEFAULTS, "answer_max_chars": 20})
    daemon = daemon_for(tmp_path, mod, client, FakeVoice(tmp_path))
    pending(daemon.state, channel)

    append_outbox(daemon.state.outbox_for(channel), {"thread_ts": QUESTION_TS, "text": "x" * 20})
    asyncio.run(daemon.drain_outboxes())

    assert [p["text"] for p in client.posts()] == ["x" * 20]
