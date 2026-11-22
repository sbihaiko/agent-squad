"""Guard: issue #2688 (SPEC 2682, stage S1). The bot posts under the channel persona name, the audio uses the persona
Gemini voice and tone, and the clip keeps the app name until #2726 records how an upload treats a name.

The seam is the daemon's own outbox path and its one TTS path: a fake Slack client and a fake voice record the post and
the speech request, so no case reaches Slack or Gemini. The persona and language come from a rendered active revision,
so each expectation is a value this file writes.
"""
import asyncio
import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path

from tests.test_voiced import OWNER, FakeClient, FakeTranscriber, append_outbox, configure, voiced
from tests.test_voiced_policy import REPO

ROOT = Path(__file__).resolve().parent.parent
VOICE_DIR = ROOT / "contrib" / "voice"
if str(VOICE_DIR) not in sys.path:
    sys.path.insert(0, str(VOICE_DIR))

WORKSPACE = "T0TEAM0001"
SHA = "a" * 40
NAME = "Iara"
VOICE_NAME = "Sulafat"
TONE = "Warm and patient. Confirms what was asked before answering."
LANGUAGE = "pt-BR"


def voice_module(name):
    """One vendored script by name, so a broken script names itself and hides no other case."""
    spec = importlib.util.spec_from_file_location(f"voice_{name.removesuffix('.py')}", VOICE_DIR / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def persona_revision(tmp_path, channel, name=NAME, voice=VOICE_NAME, tone=TONE, language=LANGUAGE):
    """The active revision the daemon loads for this project: a channel whose persona names a name, voice and tone."""
    committed = {
        "version": 1,
        "project": {"language": "en-US", "persona": "iara"},
        "personas": [{"id": "iara", "name": name, "voice": voice, "tone": tone}],
        "workspaces": [{
            "id": WORKSPACE, "label": "team", "class": "owned", "app": "hal9k",
            "channels": [{"id": channel, "name": "proj-acme-app", "mode": "listen+speak",
                          "language": language, "persona": "iara", "roles": ["manager"]}],
        }],
    }
    voiced().State(str(tmp_path)).save_active_revision(
        REPO, {"repo": REPO, "sha": SHA, "revision": committed, "local": {}})


class PersonaVoice:
    """The two seams of a spoken answer: a synthesizer that records the voice and tone of each script, and an uploader
    that records every argument it receives, so a name on a clip is visible and never assumed."""

    def __init__(self, tmp_path, started=None):
        self.scripts = []
        self.clips = []
        self.started = started
        self.dir = Path(tmp_path) / "clips"
        self.dir.mkdir(exist_ok=True)

    def synth(self, script, voice=None, tone=None):
        self.scripts.append({"script": script, "voice": voice, "tone": tone})
        if self.started is not None:
            self.started.set()
        path = self.dir / f"clip-{len(self.scripts)}.wav"
        path.write_bytes(b"RIFF")
        return str(path)

    def upload(self, path, channel, thread_ts, title, **extra):
        self.clips.append({"channel": channel, "thread_ts": thread_ts, "title": title, "extra": extra})
        return f"F{len(self.clips):04d}", None


def outbox_daemon(tmp_path, mod, client, voice):
    """One daemon whose spoken-answer seams are the fake voice, over the state the revision already sits in."""
    return mod.Daemon(state=mod.State(str(tmp_path)), owner=OWNER, transcriber=FakeTranscriber(),
                      downloader=lambda url: b"", log=lambda line: None, client=client,
                      synthesizer=voice.synth, uploader=voice.upload)


def project_channel(tmp_path, mod, client):
    """The channel the daemon created for one project, before any persona revision loads."""
    _, got = configure(tmp_path, mod, client, [REPO])
    return got["channels"][REPO]["id"]


def test_the_text_answer_is_posted_with_the_persona_username(tmp_path):
    """Criterion (1): the bot posts the text with the `username` of the channel persona."""
    mod = voiced()
    client = FakeClient()
    channel = project_channel(tmp_path, mod, client)
    persona_revision(tmp_path, channel)
    voice = PersonaVoice(tmp_path)
    daemon = outbox_daemon(tmp_path, mod, client, voice)

    append_outbox(daemon.state.outbox_for(channel),
                  {"thread_ts": "1762500000.000100", "text": "Green. Then blue."})
    asyncio.run(daemon.drain_outboxes())

    posts = client.posts()
    assert len(posts) == 1, posts
    assert posts[0]["username"] == NAME
    assert posts[0]["text"] == "Green. Then blue."


def test_the_text_answer_carries_no_username_when_the_channel_names_no_persona(tmp_path):
    """Criterion (1), the other half: a channel with no persona keeps the app name, so no `username` is sent."""
    mod = voiced()
    client = FakeClient()
    channel = project_channel(tmp_path, mod, client)
    voice = PersonaVoice(tmp_path)
    daemon = outbox_daemon(tmp_path, mod, client, voice)

    append_outbox(daemon.state.outbox_for(channel), {"text": "Green. Then blue."})
    asyncio.run(daemon.drain_outboxes())

    posts = client.posts()
    assert len(posts) == 1, posts
    assert "username" not in posts[0]


def test_the_spoken_answer_uses_the_persona_voice_and_tone(tmp_path):
    """Criterion (4): every clip of the answer is synthesized with the persona voice and its tone."""
    mod = voiced()
    client = FakeClient()
    channel = project_channel(tmp_path, mod, client)
    persona_revision(tmp_path, channel)
    voice = PersonaVoice(tmp_path)
    daemon = outbox_daemon(tmp_path, mod, client, voice)

    append_outbox(daemon.state.outbox_for(channel), {"text": "Green. Then blue."})
    asyncio.run(daemon.drain_outboxes())

    assert [s["script"] for s in voice.scripts] == ["Green.", "Then blue."]
    assert [s["voice"] for s in voice.scripts] == [VOICE_NAME, VOICE_NAME]
    assert [s["tone"] for s in voice.scripts] == [TONE, TONE]


def test_the_clip_carries_no_name_until_the_spike_records_its_result(tmp_path):
    """Criterion (7): an upload carries no `username`, so the clip leaves under the app name (#2726 records the rest)."""
    mod = voiced()
    client = FakeClient()
    channel = project_channel(tmp_path, mod, client)
    persona_revision(tmp_path, channel)
    voice = PersonaVoice(tmp_path)
    daemon = outbox_daemon(tmp_path, mod, client, voice)

    append_outbox(daemon.state.outbox_for(channel), {"text": "Green. Then blue."})
    asyncio.run(daemon.drain_outboxes())

    assert len(voice.clips) == 2, voice.clips
    assert [c["extra"] for c in voice.clips] == [{}, {}], "a clip carried a name before #2726 recorded its result"


def test_the_first_sentence_is_still_synthesized_while_the_persona_text_is_posted(tmp_path):
    """Criterion (8): the first sentence's synthesis starts together with the text post, under the persona voice."""
    mod = voiced()
    client = FakeClient(post_delay=0.2)
    channel = project_channel(tmp_path, mod, client)
    persona_revision(tmp_path, channel)
    started = threading.Event()
    voice = PersonaVoice(tmp_path, started=started)
    daemon = outbox_daemon(tmp_path, mod, client, voice)

    def gate(method, _params):
        if method == "chat.postMessage":
            assert started.wait(5.0), "the first synthesis waited for the text post instead of running with it"

    client.before_post = gate
    append_outbox(daemon.state.outbox_for(channel), {"text": "Green. Then blue."})
    asyncio.run(daemon.drain_outboxes())

    assert voice.scripts[0]["script"] == "Green."
    assert voice.scripts[0]["voice"] == VOICE_NAME


def test_the_tts_request_carries_the_persona_tone_as_the_style_prompt(tmp_path, monkeypatch):
    """Criterion (4): the tone is part of the text Gemini TTS reads, before the script, and the voice is requested."""
    tts = voice_module("tts.py")
    seen = []
    out = tmp_path / "clip.wav"
    answer = b'{"candidates":[{"content":{"parts":[{"inlineData":{"data":"","mimeType":"audio/L16;rate=24000"}}]}}]}'

    def fake_run(cmd, **_kwargs):
        seen.append(json.loads(cmd[cmd.index("-d") + 1]))
        return subprocess.CompletedProcess(cmd, 0, stdout=answer, stderr=b"")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    got, err = tts.synth("a-key", "Green. Then blue.", VOICE_NAME, str(out), tone=TONE)

    assert got and err is None, err
    said = seen[0]["contents"][0]["parts"][0]["text"]
    assert said.startswith(TONE), said
    assert "Green. Then blue." in said
    requested = seen[0]["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"]
    assert requested == VOICE_NAME


def test_the_cli_forwards_the_tone_flag_to_synth(tmp_path, monkeypatch):
    """Criterion (4): the hub calls the script with --tone, and main hands that value to synth, which sends it to Gemini.
    A hub cache key that tells two tones apart is then a real difference in the request, not only in the file name."""
    tts = voice_module("tts.py")
    seen = []

    def fake_synth(key, text, voice, out, tone=""):
        seen.append({"key": key, "text": text, "voice": voice, "tone": tone})
        Path(out).write_bytes(b"RIFF....WAVEfake")
        return str(out), None

    monkeypatch.setattr(tts, "gemini_key", lambda *a, **k: "a-key")
    monkeypatch.setattr(tts, "synth", fake_synth)
    out = tmp_path / "clip.wav"
    rc = tts.main(["Green. Then blue.", "--voice", VOICE_NAME, "--tone", TONE, "--out", str(out)])
    assert rc == 0, rc
    assert seen == [{"key": "a-key", "text": "Green. Then blue.", "voice": VOICE_NAME, "tone": TONE}], seen


def test_the_cli_reads_a_tone_that_begins_with_a_dash(tmp_path, monkeypatch):
    """Criterion (4): the hub writes the tone as one `--tone=<value>` element, so a tone that starts with a dash (a
    bullet is normal typing) is the tone and never a flag the option loop reads or refuses."""
    tts = voice_module("tts.py")
    seen = []

    def fake_synth(key, text, voice, out, tone=""):
        seen.append(tone)
        Path(out).write_bytes(b"RIFF....WAVEfake")
        return str(out), None

    monkeypatch.setattr(tts, "gemini_key", lambda *a, **k: "a-key")
    monkeypatch.setattr(tts, "synth", fake_synth)
    out = tmp_path / "clip.wav"
    rc = tts.main(["Green. Then blue.", "--voice", VOICE_NAME, "--tone=- Calm.", "--out", str(out)])
    assert rc == 0, rc
    assert seen == ["- Calm."], seen
