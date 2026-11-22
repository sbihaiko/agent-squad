"""Local transcription in a `guest` channel (SPEC 2682, stories 17 and 33; #2714).

The `transcribe` field defaults to `off`, and an `off` channel transcribes no clip. With `transcribe: local`, the clip is
read through the connector's read-file tool and the machine runs faster-whisper on it, in the channel's language, with no
outbound call. The transcript lands in the private state folder. A fake read-file answer and a fake transcriber stand in
for the connector and for the model, so the suite downloads no model and calls no network. Every id and every clip here
is a placeholder.
"""
import socket
import sys
import types
import urllib.request

from squad import guesstranscribe as T
from squad import gueststate as G

CLIP = b"CLIP-BYTES-7f3a"
SENTINEL = "the client says buy the green paint"
TS = "100.000100"


def channel(**over):
    row = {"id": "C0CLISUP01", "name": "support", "mode": "listen+speak", "speak": "draft"}
    row.update(over)
    return row


def read_result(**over):
    """One recorded read result: a page with one message that carries one audio file."""
    message = {"ts": TS, "user": "U0CLIENT01", "text": "",
               "files": [{"id": "F0CLIP001", "mimetype": "audio/mp4", "name": "clip.m4a", "filetype": "m4a"}]}
    message.update(over)
    return {"ok": True, "messages": [message]}


class FakeReader:
    """The connector's read-file tool: `read(file_id)` returns the clip's bytes and records the call."""

    def __init__(self, data=CLIP, fail=False):
        self.data, self.fail, self.calls = data, fail, []

    def __call__(self, file_id):
        self.calls.append(file_id)
        if self.fail:
            raise RuntimeError("the connector returned no file")
        return self.data


class FakeTranscriber:
    """A fake faster-whisper: it answers `text` and records each (audio, language) it was called with."""

    def __init__(self, text=SENTINEL, fail=False):
        self.text, self.fail, self.calls = text, fail, []

    def __call__(self, audio, language=None):
        self.calls.append((audio, language))
        if self.fail:
            raise RuntimeError("the model failed")
        return self.text


def state(tmp_path):
    return G.GuestState(tmp_path / "guest-state", lambda: 1_800_000_000.0)


# --- the field defaults to off -------------------------------------------------------------------------------------

def test_the_transcribe_field_defaults_to_off():
    assert T.transcribe_mode(channel()) == "off"
    assert T.transcribe_mode({"transcribe": None}) == "off"
    assert T.transcribe_mode({"transcribe": "local"}) == "local"


def test_an_off_channel_transcribes_no_clip(tmp_path):
    reader, transcriber = FakeReader(), FakeTranscriber()
    got = T.transcribe_message(read_result()["messages"][0], channel(), state(tmp_path), reader, transcriber,
                               "pt-BR")
    assert got == []
    assert reader.calls == [], "an off channel read no clip"
    assert transcriber.calls == []
    assert state(tmp_path).entries("transcripts") == []


# --- the clip source -----------------------------------------------------------------------------------------------

def test_the_poller_extracts_the_audio_files_of_a_read_message():
    clips = T.audio_clips(read_result()["messages"][0])
    assert [(c.id, c.mimetype, c.name) for c in clips] == [("F0CLIP001", "audio/mp4", "clip.m4a")]


def test_a_file_that_is_not_audio_is_not_a_clip():
    message = {"ts": TS, "files": [{"id": "F0DOC001", "mimetype": "application/pdf", "name": "report.pdf",
                                    "filetype": "pdf"}]}
    assert T.audio_clips(message) == []


# --- the local path ------------------------------------------------------------------------------------------------

def test_a_local_channel_transcribes_the_clip_on_this_machine(tmp_path):
    reader, transcriber = FakeReader(), FakeTranscriber()
    got = T.transcribe_message(read_result()["messages"][0], channel(transcribe="local"), state(tmp_path), reader,
                               transcriber, "pt-BR")
    assert reader.calls == ["F0CLIP001"], "the clip came through the connector's read-file tool"
    assert transcriber.calls == [(CLIP, "pt")], "the machine transcribed the clip's bytes in the channel language"
    assert [e.name for e in got] == [T.transcript_name(TS, T.audio_clips(read_result()["messages"][0])[0])]
    assert state(tmp_path).get("transcripts", got[0].name) == SENTINEL


def test_the_channel_language_guides_the_transcription():
    assert T.whisper_language("pt-BR") == "pt"
    assert T.whisper_language("en-US") == "en"
    assert T.whisper_language("es-ES") == "es"
    assert T.whisper_language(None) is None


def test_the_local_path_makes_no_outbound_call(tmp_path, monkeypatch):
    """The clip never reaches a transcription service: the whole local path runs with every socket and URL call refused."""
    def refuse(*args, **kwargs):
        raise AssertionError("the local path opened an outbound call")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    got = T.transcribe_message(read_result()["messages"][0], channel(transcribe="local"), state(tmp_path),
                               FakeReader(), FakeTranscriber(), "en-US")
    assert got and state(tmp_path).get("transcripts", got[0].name) == SENTINEL


def test_a_gemini_channel_is_not_transcribed_on_the_machine(tmp_path):
    reader, transcriber = FakeReader(), FakeTranscriber()
    assert T.transcribe_message(read_result()["messages"][0], channel(transcribe="gemini"), state(tmp_path),
                                reader, transcriber, "en-US") == []
    assert reader.calls == [] and transcriber.calls == []


def test_the_local_transcriber_loads_a_local_model_only(monkeypatch):
    """`local_transcriber` builds a faster-whisper engine from a local model, so no download is attempted."""
    calls = {}

    class FakeModel:
        def __init__(self, name, **kwargs):
            calls["model"] = (name, kwargs)

        def transcribe(self, audio, language=None):
            calls["transcribe"] = (audio.read(), language)
            return [types.SimpleNamespace(text=" hello "), types.SimpleNamespace(text="world")], None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    run = T.local_transcriber("small")
    assert run(CLIP, "pt") == "hello world"
    assert calls["model"][1]["local_files_only"] is True, "the model is never downloaded"
    assert calls["transcribe"] == (CLIP, "pt")


def test_the_transcript_lives_in_the_private_state_folder(tmp_path):
    g = state(tmp_path)
    got = T.transcribe_message(read_result()["messages"][0], channel(transcribe="local"), g, FakeReader(),
                               FakeTranscriber(), "pt-BR")
    stored = g.path("transcripts", got[0].name)
    assert stored.is_file()
    assert G.is_inside(stored, g.root)
    assert g.get("transcripts", got[0].name) == SENTINEL
