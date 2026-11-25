"""Guard: issue #2934. The `#proj-zesty` channel takes its persona name and its language from the active revision of the
project's `slack.yaml`, the answer posts under that persona name, and a start names every channel a file declares while
no active revision covers it.

The seam is the daemon's own refresh, its socket path and its one Slack client: a fake client records the post, and the
project is a git checkout whose `origin/dev` holds the Slack file. No case reaches Slack or the network.
"""
import sys
from pathlib import Path

from tests.test_voiced import OWNER, FakeClient, FakeTranscriber, commit, git, git_project, lines, project, revision_daemon, saved_registry
from tests.test_voiced_policy import REPO, SHA, drive_daemon, socket_message

ROOT = Path(__file__).resolve().parent.parent
VOICE_DIR = ROOT / "contrib" / "voice"
if str(VOICE_DIR) not in sys.path:
    sys.path.insert(0, str(VOICE_DIR))

ZESTY_CHANNEL = "C0CFVC68M33"
WORKSPACE = "T0TEAM0001"

SLACK_ZESTY = """version: 1
project:
  language: en-US
personas:
  - id: zesty
    name: Zesty
    voice: Kore
    tone: "Calm and direct. Starts with the result."
roles:
  - id: manager
    actions: [status, logs]
workspaces:
  - id: T0TEAM0001
    label: team
    class: owned
    app: hal9k
    channels:
      - id: C0CFVC68M33
        name: proj-zesty
        mode: listen+speak
        language: pt-BR
        persona: zesty
        roles: [manager]
"""

SLACK_MOVED = SLACK_ZESTY.replace("language: pt-BR", "language: en-US").replace("persona: zesty\n", "")


def local_members(channel=ZESTY_CHANNEL):
    """The local file that gives the owner a role in the channel, so the message reaches the inbox."""
    return {"version": 1,
            "members": [{"workspace": WORKSPACE, "channel": channel, "role": "manager", "users": [OWNER]}]}


def install_active(tmp_path, mod, channel, sha=SHA):
    """One active revision of the project, as a valid refresh leaves it: the committed file and its local file."""
    committed = {
        "version": 1,
        "project": {"language": "en-US"},
        "personas": [{"id": "zesty", "name": "Zesty", "voice": "Kore", "tone": "Calm and direct."}],
        "roles": [{"id": "manager", "actions": ["status", "logs"]}],
        "workspaces": [{
            "id": WORKSPACE, "label": "team", "class": "owned", "app": "hal9k",
            "channels": [{"id": channel, "name": "proj-zesty", "mode": "listen+speak",
                          "language": "pt-BR", "persona": "zesty", "roles": ["manager"]}],
        }],
    }
    mod.State(str(tmp_path)).save_active_revision(
        REPO, {"repo": REPO, "sha": sha, "revision": committed, "local": local_members(channel)})


def daemon_for(tmp_path, mod, client=None, log=None):
    return mod.Daemon(state=mod.State(str(tmp_path)), owner=OWNER, transcriber=FakeTranscriber(),
                      downloader=lambda url: b"", log=(log if log is not None else []).append, client=client)


def test_an_active_revision_gives_the_inbox_line_the_persona_name_and_the_language(tmp_path):
    """Criteria (2) and (3): the channel's active revision names persona `Zesty` and language `pt-BR`, and the inbox
    line carries both."""
    mod = revision_daemon(tmp_path)[0]
    client = FakeClient()
    channel = _project_channel(tmp_path, mod, client)
    install_active(tmp_path, mod, channel)

    _, daemon, _ = drive_daemon(tmp_path, [socket_message(channel)], client=client)

    box = lines(daemon.state.inbox_for(channel))
    assert len(box) == 1, box
    assert box[0]["persona"] == "Zesty", "the persona field holds the persona name, not its id"
    assert box[0]["language"] == "pt-BR", box


def test_the_answer_posts_under_the_persona_name(tmp_path):
    """Criterion (4): a bot answer in the channel carries the persona name as `username`."""
    mod = revision_daemon(tmp_path)[0]
    client = FakeClient()
    channel = _project_channel(tmp_path, mod, client)
    install_active(tmp_path, mod, channel)
    daemon = daemon_for(tmp_path, mod, client=client)

    assert daemon.answer({"channel": channel, "ts": "1762500000.000100"}, "Um recado.") is True

    posts = client.posts()
    assert len(posts) == 1, posts
    assert posts[0]["username"] == "Zesty", posts


def test_a_start_names_each_channel_a_file_declares_with_no_active_revision(tmp_path):
    """Criterion (5): a channel a `slack.yaml` names, with no active revision covering it, is one start log line naming
    the channel id and the project."""
    repo = git_project(tmp_path, SLACK_ZESTY, name="zesty-agent")
    empty_registry = saved_registry(tmp_path)          # the hub lists no save, so no revision becomes active
    _, daemon, log = revision_daemon(tmp_path, registry=empty_registry)

    daemon.refresh_slack(projects=[project(repo)])

    assert daemon.state.active_revision("acme/app") is None
    uncovered = [line for line in log if ZESTY_CHANNEL in line]
    assert uncovered, log
    assert any("acme/app" in line for line in uncovered), uncovered


def test_a_saved_revision_takes_hold_when_the_dev_tip_has_moved_past_it(tmp_path):
    """The root cause: the hub lists the saved sha, and a later commit moves the dev tip. The daemon must read the file
    at the listed revision, so the channel keeps the language and the persona its save declared."""
    repo = git_project(tmp_path, SLACK_ZESTY, name="zesty-agent")
    saved = git(repo, "rev-parse", "refs/remotes/origin/dev")
    registry = saved_registry(tmp_path, saved)
    mod, daemon, log = revision_daemon(tmp_path, registry=registry)
    newer = commit(repo, SLACK_MOVED)                  # the tip moves, and the hub lists no save of this commit
    assert newer != saved

    daemon.refresh_slack(projects=[project(repo)])

    active = daemon.state.active_revision("acme/app")
    assert active is not None, log
    assert active["sha"] == saved, "the daemon activated a revision the hub never listed"
    assert active["revision"]["workspaces"][0]["channels"][0]["language"] == "pt-BR"
    assert any(saved[:12] in line for line in log), log


def _project_channel(tmp_path, mod, client):
    """The channel this project's registry entry names; the active revision is written over it by the case."""
    state = mod.State(str(tmp_path))
    got = mod.project_channels(state, client=client, repos=[REPO], log=lambda line: None)
    return got["channels"][REPO]["id"]


def test_the_registry_key_of_a_saved_file_does_not_name_the_revision(tmp_path):
    """A registry that lists a sha the checkout does not hold approves nothing: the daemon refuses and activates no
    revision, rather than guessing the file from the tip."""
    repo = git_project(tmp_path, SLACK_ZESTY, name="zesty-agent")
    registry = saved_registry(tmp_path, "f" * 40)      # listed, yet no such commit sits in this checkout
    mod, daemon, log = revision_daemon(tmp_path, registry=registry)

    daemon.refresh_slack(projects=[project(repo)])

    assert daemon.state.active_revision("acme/app") is None, log
    assert any("not listed" in line for line in log), log


def test_the_uncovered_channel_line_is_written_once(tmp_path):
    """A steady daemon writes the line once, not one per poll."""
    repo = git_project(tmp_path, SLACK_ZESTY, name="zesty-agent")
    registry = saved_registry(tmp_path)
    _, daemon, log = revision_daemon(tmp_path, registry=registry)

    daemon.refresh_slack(projects=[project(repo)])
    daemon.refresh_slack(projects=[project(repo)])

    assert sum(1 for line in log if "no active Slack revision" in line) == 1, log


def test_the_registry_is_read_as_json_not_assumed(tmp_path):
    """The saved-sha registry stays the one authority: a malformed file lists nothing, so no revision activates."""
    repo = git_project(tmp_path, SLACK_ZESTY, name="zesty-agent")
    registry = tmp_path / "loop-state" / "slack-saved.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text("{not json")
    mod, daemon, log = revision_daemon(tmp_path, registry=registry)

    daemon.refresh_slack(projects=[project(repo)])

    assert daemon.state.active_revision("acme/app") is None
    assert any("not listed" in line for line in log), log
