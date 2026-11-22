"""Guard: issue #2703 (SPEC 2682, story 71). `voiced` posts a release event in each `owned` channel that subscribes to
it, in stage S1. Only a subscribed channel gets the template of the event, and a channel override wins over the project
template. A channel that does not subscribe gets no post, and so does a `listen` channel even when it subscribes. The
post carries the channel persona's `username` and no mark, because the bot of an `owned` Slack workspace speaks as
itself.

The seam is the daemon: the event record `release_events.py emit` writes (#2702), one active Slack revision, and the
fake `SlackClient` that `tests/test_voiced.py` already drives, so no case reaches Slack.
"""
from tests.test_voiced import OWNER, FakeClient, voiced

REPO = "acme/app"
WORKSPACE = "T0TEAM0001"
PROJECT = "C0PROJ0001"
ALERTS = "C0ALERT001"
LISTEN = "C0LISTEN01"
SHA = "a" * 40

EVENTS = [
    {"id": "stg_release", "template": "Moved to STG: {items}."},
    {"id": "prod_release", "template": "Released to PROD: {items}."},
]


def committed(channels):
    """One valid committed file: two project events, one persona, and the channels a case declares."""
    return {
        "version": 1,
        "project": {"language": "en-US", "persona": "hal"},
        "personas": [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."}],
        "events": EVENTS,
        "workspaces": [{"id": WORKSPACE, "label": "team", "class": "owned", "app": "hal9k",
                        "channels": channels}],
    }


def channel(channel_id, mode="listen+speak", announce=("stg_release", "prod_release"), **extra):
    row = {"id": channel_id, "name": channel_id.lower(), "mode": mode, "persona": "hal",
           "announce": list(announce)}
    row.update(extra)
    return row


def daemon_with(tmp_path, client, channels, events=("stg_release",), doc=None):
    """A daemon whose one project has an active revision naming `channels`, and one emitted release event."""
    mod = voiced()
    state = mod.State(str(tmp_path))
    state.save_channels({REPO: {"name": "proj-acme-app", "id": PROJECT}})
    state.save_active_revision(REPO, {"repo": REPO, "sha": SHA, "revision": doc or committed(channels),
                                      "local": {}})
    for event in events:
        mod.releases.emit(event, ["a1b2c3 Fix the door"], key=f"{event}:a1b2c3", state_dir=str(tmp_path))
    daemon = mod.Daemon(state=state, owner=OWNER, transcriber=lambda audio, mime: "", downloader=lambda url: b"",
                        log=lambda line: None, client=client)
    return mod, daemon


def test_a_subscribed_owned_channel_gets_the_template_of_the_event(tmp_path):
    """Criterion (1): the channel's `announce` names the event, so it gets the project template, rendered."""
    client = FakeClient()
    _, daemon = daemon_with(tmp_path, client, [channel(PROJECT)])

    daemon.announce_releases()

    posts = client.posts()
    assert len(posts) == 1
    assert posts[0]["channel"] == PROJECT
    assert posts[0]["text"] == "Moved to STG: a1b2c3 Fix the door."


def test_a_channel_template_override_wins_over_the_project_template(tmp_path):
    """Criterion (2): the channel's own `templates` entry replaces the project template for that event."""
    client = FakeClient()
    chips = channel(PROJECT, templates={"stg_release": "STG deploy passed: {items}."})
    _, daemon = daemon_with(tmp_path, client, [chips])

    daemon.announce_releases()

    posts = client.posts()
    assert len(posts) == 1
    assert posts[0]["text"] == "STG deploy passed: a1b2c3 Fix the door."


def test_a_channel_that_does_not_subscribe_gets_no_post(tmp_path):
    """Criterion (3): an empty `announce` list names no event, so the event reaches no channel."""
    client = FakeClient()
    _, daemon = daemon_with(tmp_path, client, [channel(PROJECT, announce=())])

    daemon.announce_releases()

    assert client.posts() == []


def test_a_channel_subscribed_only_to_prod_never_gets_a_stg_notice(tmp_path):
    """Criterion (4): the subscription names one event, so the other event stays out of the channel."""
    client = FakeClient()
    _, daemon = daemon_with(tmp_path, client, [channel(PROJECT, announce=("prod_release",))])

    daemon.announce_releases()

    assert client.posts() == []


def test_a_prod_only_channel_gets_the_prod_notice(tmp_path):
    """Criterion (4), the other side: the event the channel subscribes to does reach it."""
    client = FakeClient()
    _, daemon = daemon_with(tmp_path, client, [channel(PROJECT, announce=("prod_release",))],
                            events=("prod_release",))

    daemon.announce_releases()

    posts = client.posts()
    assert len(posts) == 1
    assert posts[0]["text"] == "Released to PROD: a1b2c3 Fix the door."


def test_the_post_carries_the_channel_persona_name_and_no_mark(tmp_path):
    """Criterion (5): the bot posts under the persona `username`, and the text is the template alone, with no mark."""
    client = FakeClient()
    _, daemon = daemon_with(tmp_path, client, [channel(PROJECT)])

    daemon.announce_releases()

    posts = client.posts()
    assert len(posts) == 1
    assert posts[0]["username"] == "HAL9K"
    assert posts[0]["text"] == "Moved to STG: a1b2c3 Fix the door."


def test_a_listen_channel_gets_no_post_even_when_it_subscribes(tmp_path):
    """Criterion (6): a `listen` channel is read-only, so its subscription is never posted to."""
    client = FakeClient()
    _, daemon = daemon_with(tmp_path, client, [channel(LISTEN, mode="listen")])

    daemon.announce_releases()

    assert client.posts() == []


def test_a_speak_channel_gets_the_notice(tmp_path):
    """Criterion (1) for a writable channel: only `listen` is read-only, so a `speak` channel that subscribes is posted."""
    client = FakeClient()
    _, daemon = daemon_with(tmp_path, client, [channel(PROJECT, mode="speak")])

    daemon.announce_releases()

    posts = client.posts()
    assert len(posts) == 1
    assert posts[0]["channel"] == PROJECT
    assert posts[0]["text"] == "Moved to STG: a1b2c3 Fix the door."


def test_a_channel_with_no_mode_gets_no_post(tmp_path):
    """A row with no `mode` is read-only, so its subscription is never posted to."""
    client = FakeClient()
    row = channel(PROJECT)
    del row["mode"]
    _, daemon = daemon_with(tmp_path, client, [row])

    daemon.announce_releases()

    assert client.posts() == []


def test_a_second_read_of_the_same_event_posts_no_second_notice(tmp_path):
    """One event key and one channel are one notice: a restart that reads the record again posts nothing."""
    client = FakeClient()
    mod, daemon = daemon_with(tmp_path, client, [channel(PROJECT)])

    daemon.announce_releases()
    daemon.announce_releases()
    restarted = mod.Daemon(state=mod.State(str(tmp_path)), owner=OWNER, transcriber=lambda audio, mime: "",
                           downloader=lambda url: b"", log=lambda line: None, client=client)
    restarted.announce_releases()

    assert len(client.posts()) == 1


def test_an_event_with_no_template_posts_nothing(tmp_path):
    """The project names no template for an event and the channel overrides none, so no text is invented."""
    client = FakeClient()
    doc = committed([channel(PROJECT)])
    doc["events"] = [EVENTS[0]]
    _, daemon = daemon_with(tmp_path, client, [channel(PROJECT)], events=("prod_release",), doc=doc)

    daemon.announce_releases()

    assert client.posts() == []
