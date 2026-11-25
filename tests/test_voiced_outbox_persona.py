"""Guard: issue #2935. An outbox line may name its persona, so one line answers with another persona's name, voice and
tone than the channel's.

The seam is the daemon's own outbox path and its one TTS path: a fake Slack client and a fake voice record the post and
the speech request, so no case reaches Slack or Gemini. The personas come from a rendered active revision of a fixture
`slack.yaml`, so each expectation is a value this file writes and never the real project file.
"""
import asyncio

from tests.test_voiced import OWNER, FakeClient, FakeTranscriber, append_outbox, configure, pending, voiced
from tests.test_voiced_persona import PersonaVoice
from tests.test_voiced_policy import REPO

WORKSPACE = "T0TEAM0001"
SHA = "a" * 40
CHANNEL_NAME = "Iara"
CHANNEL_VOICE = "Sulafat"
CHANNEL_TONE = "Warm and patient. Confirms what was asked before answering."
HAL_NAME = "HAL9K"
HAL_VOICE = "Charon"
HAL_TONE = "Voz masculina grave e firme. Ritmo de conversa normal. Calma, cortes e precisa."
ZESTY_NAME = "Zesty"
ZESTY_VOICE = "Kore"
ZESTY_TONE = "Bright and quick. Leads with the result."


def persona_revision(tmp_path, channel):
    """The active revision the daemon loads: a channel under one persona and two more personas the file declares."""
    committed = {
        "version": 1,
        "project": {"language": "en-US", "persona": "iara"},
        "personas": [
            {"id": "iara", "name": CHANNEL_NAME, "voice": CHANNEL_VOICE, "tone": CHANNEL_TONE},
            {"id": "hal", "name": HAL_NAME, "voice": HAL_VOICE, "tone": HAL_TONE},
            {"id": "zesty", "name": ZESTY_NAME, "voice": ZESTY_VOICE, "tone": ZESTY_TONE},
        ],
        "workspaces": [{
            "id": WORKSPACE, "label": "team", "class": "owned", "app": "hal9k",
            "channels": [{"id": channel, "name": "proj-acme-app", "mode": "listen+speak",
                          "language": "en-US", "persona": "iara", "roles": ["manager"]}],
        }],
    }
    voiced().State(str(tmp_path)).save_active_revision(
        REPO, {"repo": REPO, "sha": SHA, "revision": committed, "local": {}})


def outbox_daemon(tmp_path, mod, client, voice, log=None):
    """One daemon whose spoken-answer seams are the fake voice, over the state the revision already sits in."""
    return mod.Daemon(state=mod.State(str(tmp_path)), owner=OWNER, transcriber=FakeTranscriber(),
                      downloader=lambda url: b"", log=log or (lambda line: None), client=client,
                      synthesizer=voice.synth, uploader=voice.upload)


def project_channel(tmp_path, mod, client):
    """The channel the daemon created for one project, before any persona revision loads."""
    _, got = configure(tmp_path, mod, client, [REPO])
    return got["channels"][REPO]["id"]


def test_a_line_that_names_hal_posts_and_speaks_as_HAL9K(tmp_path):
    """Criterion (a): `persona: hal` posts with username HAL9K and speaks with voice Charon and the hal tone."""
    mod = voiced()
    client = FakeClient()
    channel = project_channel(tmp_path, mod, client)
    persona_revision(tmp_path, channel)
    voice = PersonaVoice(tmp_path)
    daemon = outbox_daemon(tmp_path, mod, client, voice)

    pending(daemon.state, channel)

    append_outbox(daemon.state.outbox_for(channel),
                  {"thread_ts": "1762500000.000100", "text": "Green. Then blue.", "persona": "hal"})
    asyncio.run(daemon.drain_outboxes())

    posts = client.posts()
    assert len(posts) == 1, posts
    assert posts[0]["username"] == HAL_NAME
    assert [s["script"] for s in voice.scripts] == ["Green.", "Then blue."]
    assert [s["voice"] for s in voice.scripts] == [HAL_VOICE, HAL_VOICE]
    assert [s["tone"] for s in voice.scripts] == [HAL_TONE, HAL_TONE]


def test_a_line_with_no_persona_keeps_the_channel_persona(tmp_path):
    """Criterion (b): a line with no `persona` field posts and speaks with the channel persona."""
    mod = voiced()
    client = FakeClient()
    channel = project_channel(tmp_path, mod, client)
    persona_revision(tmp_path, channel)
    voice = PersonaVoice(tmp_path)
    daemon = outbox_daemon(tmp_path, mod, client, voice)

    pending(daemon.state, channel)

    append_outbox(daemon.state.outbox_for(channel), {"text": "Green. Then blue."})
    asyncio.run(daemon.drain_outboxes())

    posts = client.posts()
    assert len(posts) == 1, posts
    assert posts[0]["username"] == CHANNEL_NAME
    assert [s["voice"] for s in voice.scripts] == [CHANNEL_VOICE, CHANNEL_VOICE]
    assert [s["tone"] for s in voice.scripts] == [CHANNEL_TONE, CHANNEL_TONE]


def test_a_line_that_names_an_unknown_persona_keeps_the_channel_persona_and_logs_one_line(tmp_path):
    """Criterion (c): an unknown persona id posts and speaks with the channel persona and writes exactly one log line."""
    mod = voiced()
    client = FakeClient()
    channel = project_channel(tmp_path, mod, client)
    persona_revision(tmp_path, channel)
    voice = PersonaVoice(tmp_path)
    lines = []
    daemon = outbox_daemon(tmp_path, mod, client, voice, log=lines.append)

    pending(daemon.state, channel)

    append_outbox(daemon.state.outbox_for(channel),
                  {"thread_ts": "1762500000.000100", "text": "Green. Then blue.", "persona": "ghost"})
    asyncio.run(daemon.drain_outboxes())

    posts = client.posts()
    assert len(posts) == 1, posts
    assert posts[0]["username"] == CHANNEL_NAME
    assert [s["voice"] for s in voice.scripts] == [CHANNEL_VOICE, CHANNEL_VOICE]
    assert len(lines) == 1, lines
    assert "ghost" in lines[0], lines


def test_the_zesty_persona_keeps_its_voice_Kore(tmp_path):
    """Criterion (d): `persona: zesty` speaks with voice Kore, so the voice is the named persona's and not a constant."""
    mod = voiced()
    client = FakeClient()
    channel = project_channel(tmp_path, mod, client)
    persona_revision(tmp_path, channel)
    voice = PersonaVoice(tmp_path)
    daemon = outbox_daemon(tmp_path, mod, client, voice)

    pending(daemon.state, channel)

    append_outbox(daemon.state.outbox_for(channel),
                  {"thread_ts": "1762500000.000100", "text": "Green. Then blue.", "persona": "zesty"})
    asyncio.run(daemon.drain_outboxes())

    assert client.posts()[0]["username"] == ZESTY_NAME
    assert [s["voice"] for s in voice.scripts] == [ZESTY_VOICE, ZESTY_VOICE]
