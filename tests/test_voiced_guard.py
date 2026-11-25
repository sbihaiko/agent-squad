"""Guard: issue #2947 (SPEC 2946). The daemon refuses a flooding or oversized message before it reaches the inbox, so a
stranger with a channel grant never floods the operator session, and it audits every message with no secret value.

The seam is the daemon's own socket path: one envelope in, the state files out, and the fake `SlackClient` that
`tests/test_voiced.py` already drives. The guard values come from the committed file of the active revision, so each
case installs the guard it proves.
"""
import json
from pathlib import Path

from tests.test_voiced import OWNER, FakeClient, drive_daemon, lines, voiced
from tests.test_voiced_policy import REPO, SHA, configure, slack_files, socket_message

DEFAULTS = {"rate": 6, "max_chars": 4000, "answer_max_chars": 8000, "read_wrappers": []}


def install_guard(tmp_path, mod, channel, guard=None):
    """One active revision whose committed file carries the guard the case proves. A guard of None keeps the file with
    no guard block, so the daemon takes the plugin defaults."""
    committed, local = slack_files(channel)
    if guard is not None:
        committed["guard"] = guard
    mod.State(str(tmp_path)).save_active_revision(
        REPO, {"repo": REPO, "sha": SHA, "revision": committed, "local": local})


def guarded_channel(tmp_path, mod, client, guard=None):
    """The project's channel with the guard installed on its active revision."""
    _, got = configure(tmp_path, mod, client, [REPO])
    channel = got["channels"][REPO]["id"]
    install_guard(tmp_path, mod, channel, guard)
    return channel


def flood(channel, count, start=0, text="check the loop"):
    """`count` distinct messages of one sender in one channel, each with its own id."""
    return [socket_message(channel, text=text, ts=f"1762500{start + i:03d}.0001{start + i:02d}")
            for i in range(count)]


def audit(daemon):
    path = Path(daemon.state.audit_path)
    if not path.exists():
        return []
    return [json.loads(raw) for raw in path.read_text().splitlines() if raw.strip()]


def test_the_seventh_message_of_one_sender_in_a_minute_is_refused(tmp_path):
    """Criterion (4): six messages pass, and the seventh inside the 60-second window is refused."""
    mod = voiced()
    client = FakeClient()
    channel = guarded_channel(tmp_path, mod, client, {**DEFAULTS, "rate": 6})

    _, daemon, _ = drive_daemon(tmp_path, flood(channel, 7), client=client)

    assert len(lines(daemon.state.inbox_for(channel))) == 6
    assert lines(daemon.state.inbox_for(channel))[0]["text"] == "check the loop"


def test_a_message_longer_than_max_chars_is_refused_and_writes_no_inbox_line(tmp_path):
    """Criteria (5) and (6): a message past the cap is refused, and the inbox keeps no line for it."""
    mod = voiced()
    client = FakeClient()
    channel = guarded_channel(tmp_path, mod, client, {**DEFAULTS, "max_chars": 20})

    _, daemon, _ = drive_daemon(tmp_path, [socket_message(channel, text="x" * 21)], client=client)

    assert lines(daemon.state.inbox_for(channel)) == []


def test_a_message_at_max_chars_still_reaches_the_inbox(tmp_path):
    """The cap is a bound, not a target: a message of exactly `max_chars` passes."""
    mod = voiced()
    client = FakeClient()
    channel = guarded_channel(tmp_path, mod, client, {**DEFAULTS, "max_chars": 20})

    _, daemon, _ = drive_daemon(tmp_path, [socket_message(channel, text="x" * 20)], client=client)

    assert len(lines(daemon.state.inbox_for(channel))) == 1


def test_a_refused_sender_gets_one_refusal_reply_per_channel_in_ten_minutes(tmp_path):
    """Criterion (7): two refusals of one sender in one channel post one reply, never one reply per refused message."""
    mod = voiced()
    client = FakeClient()
    channel = guarded_channel(tmp_path, mod, client, {**DEFAULTS, "rate": 1})

    _, daemon, _ = drive_daemon(tmp_path, flood(channel, 3), client=client)

    assert len(lines(daemon.state.inbox_for(channel))) == 1
    assert len(client.posts()) == 1, client.posts()


def test_the_refusal_reply_names_the_rate_reason(tmp_path):
    """Criterion (8): the rate refusal names its reason class."""
    mod = voiced()
    client = FakeClient()
    channel = guarded_channel(tmp_path, mod, client, {**DEFAULTS, "rate": 1})

    drive_daemon(tmp_path, flood(channel, 2), client=client)

    replies = client.posts()
    assert len(replies) == 1
    assert "rate" in replies[0]["text"].lower(), replies


def test_the_refusal_reply_names_the_size_reason(tmp_path):
    """Criterion (8): the size refusal names its reason class."""
    mod = voiced()
    client = FakeClient()
    channel = guarded_channel(tmp_path, mod, client, {**DEFAULTS, "max_chars": 5})

    drive_daemon(tmp_path, [socket_message(channel, text="too many characters")], client=client)

    replies = client.posts()
    assert len(replies) == 1
    assert "size" in replies[0]["text"].lower(), replies


def test_each_message_writes_one_audit_line_with_its_decision_and_reason(tmp_path):
    """Criterion (9): the audit names the time, the sender, the role, the channel, the decision and the reason class."""
    mod = voiced()
    client = FakeClient()
    channel = guarded_channel(tmp_path, mod, client, {**DEFAULTS, "rate": 1})

    _, daemon, _ = drive_daemon(tmp_path, flood(channel, 2), client=client)

    rows = audit(daemon)
    assert len(rows) == 2, rows
    first, second = rows
    for row in rows:
        assert row["time"]
        assert row["user"] == OWNER
        assert row["role"] == "manager"
        assert row["channel"] == channel
    assert first["decision"] == "inbox"
    assert second["decision"] == "refused"
    assert second["reason"] == "rate"


def test_the_audit_line_holds_no_secret_and_at_most_eighty_characters_of_text(tmp_path):
    """Criterion (10): the audit keeps eighty characters of redacted text and never a token value."""
    mod = voiced()
    client = FakeClient()
    channel = guarded_channel(tmp_path, mod, client, {**DEFAULTS, "max_chars": 10})
    token = "xoxb-1234567890-secret"
    text = f"{token} " + "y" * 200

    _, daemon, _ = drive_daemon(tmp_path, [socket_message(channel, text=text)], client=client)

    rows = audit(daemon)
    assert len(rows) == 1, rows
    kept = rows[0]["text"]
    assert len(kept) <= 80, kept
    assert token not in kept
    assert "xoxb-" not in kept
