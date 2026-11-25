"""Guard: issue #2925. A project may own more than one channel: its active Slack revision names every channel of the
project, and a message in any of them reaches that channel's own inbox, so the project session's Monitor on that inbox
wakes. The registry names only the channel the daemon created for the project.

The seam is the daemon's own socket path, through the fake `SlackClient` of `tests/test_voiced.py`. No case reaches
Slack. Every id is a neutral placeholder.
"""
from tests.test_voiced import OWNER, FakeClient, configure, drive_daemon, lines, voiced
from tests.test_voiced_policy import REPO, SHA, WORKSPACE, slack_files, socket_message

EXTRA = "C0EXTRA001"


def test_a_message_in_a_second_channel_of_the_project_reaches_its_own_inbox(tmp_path):
    mod = voiced()
    client = FakeClient()
    _, got = configure(tmp_path, mod, client, [REPO])
    channel = got["channels"][REPO]["id"]
    committed, local = slack_files(channel)
    committed["workspaces"][0]["channels"].append(
        {"id": EXTRA, "name": "proj-acme-extra", "mode": "listen+speak", "language": "pt-BR", "persona": "hal",
         "roles": ["manager"]})
    local["members"].append({"workspace": WORKSPACE, "channel": EXTRA, "role": "manager", "users": [OWNER]})
    mod.State(str(tmp_path)).save_active_revision(REPO, {"repo": REPO, "sha": SHA, "revision": committed, "local": local})

    _, daemon, _ = drive_daemon(tmp_path, [socket_message(EXTRA)], client=client)

    box = lines(daemon.state.inbox_for(EXTRA))
    assert len(box) == 1, box
    assert box[0]["channel"] == EXTRA and box[0]["persona"] == "HAL9K", box[0]
    assert not lines(daemon.state.inbox_for(channel)), "the line went to the project's first channel"


def test_the_identity_of_a_second_channel_comes_from_its_project(tmp_path):
    """The operator brief reads a channel's language and persona from the revision of the project that names it."""
    mod = voiced()
    client = FakeClient()
    _, got = configure(tmp_path, mod, client, [REPO])
    committed, local = slack_files(got["channels"][REPO]["id"])
    committed["workspaces"][0]["channels"].append(
        {"id": EXTRA, "name": "proj-acme-extra", "mode": "listen+speak", "language": "es-ES", "persona": "hal"})
    mod.State(str(tmp_path)).save_active_revision(REPO, {"repo": REPO, "sha": SHA, "revision": committed, "local": local})
    language, persona = mod.scope_identity(str(tmp_path), EXTRA)
    assert language == "es-ES" and persona is not None and persona.id == "hal", (language, persona)
