"""A channel of an `owned` workspace can carry the `guest` class in the local file (issue #2770).

The local file adds a guest channel under the id of a workspace the committed file declares `owned`, so the owner tests
the guest path in an owned test workspace. The channel's class wins over its workspace's class: the policy decision
polls it and answers with a `proposal`, the guest poller reads it through the connector, and `voiced` posts nothing in
it, though the bot is a member. The one-guest-workspace bound counts the team that holds a guest channel. No real team
id or channel id enters this file: every id here is a placeholder.
"""
import copy
from datetime import UTC, datetime, timedelta

from squad import slackannounce
from squad import slackpolicy as S
from squad import slackpoller as P
from tests.test_slack_announce_owned import FakeClient, daemon_with

NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)
TEAM = "T0TEAM0001"
PROJECT, ALERTS, TRIAL = "C0PROJ0001", "C0ALERT001", "C0TRIAL001"

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal"},
    "personas": [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."}],
    "events": [{"id": "stg_release", "template": "Moved to STG: {items}."}],
    "roles": [{"id": "manager", "actions": ["status"]}],
    "workspaces": [{"id": TEAM, "label": "team", "class": "owned", "app": "hal9k",
                    "channels": [
                        {"id": PROJECT, "name": "proj", "mode": "listen+speak", "persona": "hal",
                         "roles": ["manager"], "announce": ["stg_release"]},
                        {"id": ALERTS, "name": "alerts", "mode": "listen+speak", "persona": "hal",
                         "roles": ["manager"], "announce": ["stg_release"]},
                    ]}],
}

LOCAL = {
    "version": 1,
    "workspaces": [{"id": TEAM, "label": "team",
                    "channels": [
                        {"id": ALERTS, "class": "guest", "mode": "listen+speak", "speak": "draft"},
                        {"id": TRIAL, "name": "trial", "class": "guest", "mode": "listen+speak", "speak": "draft",
                         "persona": "hal", "roles": ["manager"],
                         "poll": {"mode": "two_state", "fast": "1m", "slow": "10m", "quiet_after": "10m",
                                  "thread_open": "30m"}},
                    ]}],
    "members": [{"workspace": TEAM, "channel": TRIAL, "role": "manager", "users": ["U0OWNER001"]}],
}


def files():
    return copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL)


def event(channel=TRIAL, user="U0OWNER001"):
    return {"kind": "message", "workspace": TEAM, "channel": channel, "user": user}


def test_the_local_file_adds_a_guest_channel_under_an_owned_workspace():
    """Criterion (1): the local row carries `class: guest`, and its workspace's row stays `owned` in slack.yaml."""
    committed, local = files()
    assert committed["workspaces"][0]["class"] == "owned"
    row = next(ch for ch in local["workspaces"][0]["channels"] if ch["id"] == TRIAL)
    assert S.channel_class(local["workspaces"][0], row) == "guest"


def test_the_pair_with_a_guest_channel_in_an_owned_workspace_has_no_defect():
    """Criterion (2): the team id is owned, so the guest-channel rule names no defect for the pair of files."""
    committed, local = files()
    assert S.validate(committed, local) == []


def test_a_channel_class_in_the_committed_file_is_refused_with_its_key():
    """Criterion (3): the committed file never names a channel class, and the one defect names the key."""
    committed, _ = files()
    committed["workspaces"][0]["channels"][0]["class"] = "guest"
    named = [d for d in S.validate(committed, {}) if d.key == "workspaces[0].channels[0].class"]
    assert len(named) == 1
    assert named[0].file == "slack.yaml"


def test_the_decision_for_a_guest_channel_in_an_owned_workspace_is_a_proposal():
    """Criterion (4): the channel's class wins, so the decision polls it and answers with a `proposal`, never `inbox`."""
    committed, local = files()
    decision = S.decide(committed, local, event(), NOW)
    assert decision.kind == S.PROPOSAL
    assert decision.kind != S.INBOX
    assert decision.next_poll == NOW + timedelta(minutes=1)
    assert decision.reply == "draft"


def test_a_guests_draft_never_reaches_a_channel_the_bot_serves(tmp_path):
    """Criterion (5): an `owned` channel still gets its notice, and a channel whose local row is `guest` gets none,
    though the app of the workspace serves both."""
    committed, local = files()
    client = FakeClient()
    _, daemon = daemon_with(tmp_path, client, committed["workspaces"][0]["channels"], local=local)

    daemon.announce_releases()

    assert [params["channel"] for method, params, _ in client.calls if method == "chat.postMessage"] == [PROJECT]


def test_the_guest_poller_lists_the_channel_as_due(tmp_path):
    """Criterion (6): the guest poller reads the channel of the owned workspace through the connector."""
    committed, local = files()
    schedule = P.PollSchedule(tmp_path / "schedule.json", committed, local)
    due = schedule.due(NOW)
    assert (TEAM, TRIAL) in due
    assert (TEAM, ALERTS) in due
    assert (TEAM, PROJECT) not in due


def test_the_one_guest_workspace_rule_counts_the_team_that_holds_a_guest_channel():
    """Criterion (7): the team takes the machine's one guest slot, so a second guest workspace is refused."""
    committed, local = files()
    other = {"version": 1, "workspaces": [{"id": "T0OTHER001", "label": "other", "class": "guest",
                                           "channels": []}]}
    named = [d for d in S.validate(committed, local, others=[("other.slack.local.yaml", other)])
             if d.reason == S.SECOND_GUEST_REASON]
    assert [(d.file, d.key) for d in named] == [("other.slack.local.yaml", "workspaces[0].id")]


def test_the_announced_guest_channel_is_skipped_by_the_owned_path():
    """The seam behind criterion (5): the owned path returns no post for a channel whose local row is `guest`."""
    committed, local = files()
    record = {"id": "stg_release", "key": "stg:abc", "items": ["a1b2c3 Fix the door"]}
    posted = [post.channel for post in slackannounce.announcements(committed, local, record)]
    assert posted == [PROJECT]
