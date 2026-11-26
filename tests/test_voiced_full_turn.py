"""The owner's message in a `session: full` role runs one turn of the project's session (ADR-0180).

Guards: the owner's message in a full role runs one turn in the project checkout and writes no inbox line; the first turn
records the session and the next turn resumes it; the final text posts in the thread after the secret redaction (#2948);
each turn writes one audit line with its session and exit and no message or answer text; a failed turn posts one sentence;
a member who is not the owner, and the owner of a role with no full session, keep the bounded inbox path.

The stand-in CLI is the one tests/test_full_turn.py drives. No case reaches Slack or a model.
"""
import json

import pytest

from tests.test_full_turn import FIRST_SESSION, MARKER, build_stand_in, calls
from tests.test_voiced import OWNER, FakeClient, FakeTranscriber, lines, project, voiced
from tests.test_voiced_active_revision import WORKSPACE, _project_channel
from tests.test_voiced_policy import REPO, SHA, socket_message


@pytest.fixture
def stand_in(tmp_path, monkeypatch):
    return build_stand_in(tmp_path, monkeypatch)

OTHER = "U0OTHER0001"
SECRET = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
SECRET_MARK = "[redacted]"


def role(session=None):
    row = {"id": "manager", "actions": ["status"]}
    if session is not None:
        row["session"] = session
    return row


def full_revision(tmp_path, mod, channel, row, mode="listen+speak"):
    committed = {
        "version": 1,
        "project": {"language": "en-US"},
        "personas": [{"id": "zesty", "name": "Zesty", "voice": "Kore", "tone": "Calm and direct."}],
        "roles": [row],
        "workspaces": [{
            "id": WORKSPACE, "label": "team", "class": "owned", "app": "hal9k",
            "channels": [{"id": channel, "name": "proj-zesty", "mode": mode, "language": "pt-BR",
                          "persona": "zesty", "roles": [row["id"]]}],
        }],
    }
    local = {"version": 1, "members": [{"workspace": WORKSPACE, "channel": channel, "role": row["id"],
                                        "users": [OWNER, OTHER]}]}
    mod.State(str(tmp_path)).save_active_revision(REPO, {"repo": REPO, "sha": SHA, "revision": committed, "local": local})


def daemon_for(tmp_path, mod, checkout, client, cli):
    """The production shape (#2965 review): `projects` lists repository names (`hub_repos`), and `configs` lists the
    rows that carry each checkout (`hub_projects`)."""
    return mod.Daemon(state=mod.State(str(tmp_path)), owner=OWNER, transcriber=FakeTranscriber(),
                      downloader=lambda url: b"", log=lambda line: None, client=client,
                      projects=lambda: [REPO], configs=lambda: [project(checkout)], full_cli=cli)


def setup(tmp_path, stand, row, mode="listen+speak", cli=None):
    mod = voiced()
    client = FakeClient()
    channel = _project_channel(tmp_path, mod, client)
    full_revision(tmp_path, mod, channel, row, mode)
    checkout = stand["checkout"]
    return mod, client, channel, daemon_for(tmp_path, mod, checkout, client, cli or stand["claude"])


def deliver(daemon, channel, text, ts="1762500100.000100", user=OWNER):
    outcome = daemon.handle(socket_message(channel, user=user, text=text, ts=ts))
    daemon.join_turns()
    return outcome


def test_a_full_message_from_the_owner_runs_one_turn_in_the_project_checkout(tmp_path, stand_in):
    mod, client, channel, daemon = setup(tmp_path, stand_in, role("full"))
    outcome = deliver(daemon, channel, "check the loop")
    assert outcome == "full", outcome
    assert len(calls(stand_in)) == 1
    assert calls(stand_in)[0]["cwd"] == __import__("os").path.realpath(stand_in["checkout"])
    assert lines(daemon.state.inbox_for(channel)) == []


def test_the_first_turn_records_the_session_and_the_next_turn_resumes_it(tmp_path, stand_in):
    mod, client, channel, daemon = setup(tmp_path, stand_in, role("full"))
    deliver(daemon, channel, "first", ts="1762500100.000100")
    deliver(daemon, channel, "second", ts="1762500101.000100")
    later = calls(stand_in)[1]["argv"]
    assert later[later.index("--resume") + 1] == FIRST_SESSION, later
    saved = json.loads((tmp_path / "full-sessions.json").read_text(encoding="utf-8"))
    assert saved[REPO] == FIRST_SESSION, saved


def test_a_full_answer_posts_after_the_secret_redaction(tmp_path, stand_in):
    mod, client, channel, daemon = setup(tmp_path, stand_in, role("full"))
    deliver(daemon, channel, f"the token is {SECRET} and {MARKER}")
    posts = [p for p in client.posts() if p.get("channel") == channel]
    assert len(posts) == 1, client.posts()
    assert SECRET not in posts[0]["text"], posts[0]
    assert SECRET_MARK in posts[0]["text"], posts[0]
    assert MARKER in posts[0]["text"], posts[0]


def test_each_full_turn_writes_one_audit_line_with_its_session_and_exit(tmp_path, stand_in):
    mod, client, channel, daemon = setup(tmp_path, stand_in, role("full"))
    deliver(daemon, channel, f"secret {SECRET}")
    rows = lines(daemon.state.audit_path)
    full = [r for r in rows if "session" in r and "exit" in r]
    assert len(full) == 1, rows
    assert full[0]["session"] == FIRST_SESSION and full[0]["exit"] == 0, full
    assert SECRET not in open(daemon.state.audit_path, encoding="utf-8").read()


def test_a_failed_turn_posts_one_sentence_and_keeps_its_code_in_the_audit(tmp_path, stand_in, monkeypatch):
    mod, client, channel, daemon = setup(tmp_path, stand_in, role("full"))
    monkeypatch.setenv("STUB_FAIL", "3")
    deliver(daemon, channel, "hello")
    posts = [p for p in client.posts() if p.get("channel") == channel]
    assert len(posts) == 1, client.posts()
    assert posts[0]["text"].count(".") == 1 and posts[0]["text"].endswith("."), posts[0]
    rows = [r for r in lines(daemon.state.audit_path) if r.get("session") is not None or r.get("exit") is not None]
    assert rows and rows[-1]["exit"] == 3 and rows[-1]["failed"] is True, rows


def test_a_member_that_is_not_the_owner_keeps_the_bounded_path(tmp_path, stand_in):
    mod, client, channel, daemon = setup(tmp_path, stand_in, role("full"))
    outcome = deliver(daemon, channel, "a colleague writes", user=OTHER)
    assert outcome == "inbox", outcome
    assert calls(stand_in) == []


def test_an_owner_on_a_role_with_no_full_session_keeps_the_bounded_path(tmp_path, stand_in):
    mod, client, channel, daemon = setup(tmp_path, stand_in, role())
    outcome = deliver(daemon, channel, "check the loop")
    assert outcome == "inbox", outcome
    assert calls(stand_in) == []


def test_a_cli_that_cannot_start_posts_one_sentence_and_writes_its_audit_line(tmp_path, stand_in):
    """A missing CLI raises OSError, not a timeout (#2965 review): the turn still audits and posts the failure sentence."""
    mod, client, channel, daemon = setup(tmp_path, stand_in, role("full"), cli=str(tmp_path / "no-such-claude"))
    deliver(daemon, channel, "hello")
    posts = [p for p in client.posts() if p.get("channel") == channel]
    assert len(posts) == 1 and posts[0]["text"] == mod.FULL_FAILED, client.posts()
    rows = [r for r in lines(daemon.state.audit_path) if r.get("decision") == "full"]
    assert len(rows) == 1 and rows[0]["failed"] is True, rows


def test_a_full_turn_in_a_listen_channel_posts_no_answer(tmp_path, stand_in):
    """A channel the revision names `listen` keeps every answer (#2965 review), a full turn's too; the turn still audits."""
    mod, client, channel, daemon = setup(tmp_path, stand_in, role("full"), mode="listen")
    deliver(daemon, channel, f"hello {MARKER}")
    assert not [p for p in client.posts() if p.get("channel") == channel and MARKER in str(p.get("text"))], client.posts()
    assert [r for r in lines(daemon.state.audit_path) if r.get("decision") == "full"], lines(daemon.state.audit_path)
