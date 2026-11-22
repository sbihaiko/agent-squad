"""Transcription in a `guest` channel (SPEC 2682, stories 33 and 63; #2714 and #2720).

The `transcribe` field of a `guest` channel is `off` by default, and an `off` channel transcribes no clip. With
`transcribe: local`, the poller extracts each audio file of a read message, reads the clip's bytes through the
connector's read-file tool, and the machine runs faster-whisper on it, in the channel's language. The local path calls
no network: the model loads from a local folder only, and the clip reaches no host.

With `transcribe: gemini`, the same clip goes to Gemini through one injected client, in the channel's language, and the
answer's text is the transcript. The client is a callable, so a test supplies a fake and never calls the real service.

Every transcript lands in the private state folder at `gueststate.GuestState`, kind `transcripts`, so the 10-day purge
and the 0600 file mode cover it. A caller passes the clock through `GuestState`, and this module reads no clock of its
own. A test supplies a fake read-file answer, a fake transcriber and a fake Gemini client, so the suite downloads no
model and calls no network.

Guards: `tests/test_guest_transcribe.py`.
"""
import base64
import io
import json
import re
import urllib.request
from typing import NamedTuple

# The audio file types the connector returns. A file is a clip when its mimetype starts with `audio/` or its filetype is
# one of these, so a clip with a generic mimetype still counts.
AUDIO_TYPES = frozenset(("m4a", "mp4", "webm", "ogg", "mp3", "wav", "aac", "flac"))
TRANSCRIBE_MODES = ("off", "local", "gemini")
DEFAULT_MODE = "off"
TRANSCRIPT_KIND = "transcripts"
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"
GEMINI_TIMEOUT = 60.0
GEMINI_PROMPT = "Transcribe this audio exactly in its own language. Reply with the transcript only."
_BAD_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_LANGUAGE = re.compile(r"^[A-Za-z]+")


class Clip(NamedTuple):
    """One audio file of a read message: the connector's file id, its mimetype, and its name."""
    id: str
    mimetype: str
    name: str


def transcribe_mode(channel):
    """The channel's `transcribe` value: `off`, `local` or `gemini`. An absent value, and a value the field does not
    name, reads as `off`, so a misconfiguration never transcribes a clip."""
    mode = str((channel or {}).get("transcribe") or DEFAULT_MODE).strip().lower()
    return mode if mode in TRANSCRIBE_MODES else DEFAULT_MODE


def _is_audio(file):
    mime = str(file.get("mimetype") or "").lower()
    filetype = str(file.get("filetype") or "").lower()
    return mime.startswith("audio/") or filetype in AUDIO_TYPES


def audio_clips(message):
    """The audio files of one read message, as `Clip` rows. A message with no `files` list, and a file that is not
    audio, yields no clip."""
    files = (message or {}).get("files") or []
    if not isinstance(files, list):
        return []
    out = []
    for file in files:
        if not isinstance(file, dict) or not file.get("id") or not _is_audio(file):
            continue
        out.append(Clip(str(file["id"]), str(file.get("mimetype") or ""), str(file.get("name") or "")))
    return out


def whisper_language(tag):
    """The two-letter code faster-whisper takes from a BCP 47 tag: `pt-BR` gives `pt`, `en-US` gives `en`. A missing or
    unreadable tag gives None, so the model detects the language itself."""
    match = _LANGUAGE.match(str(tag).strip()) if tag else None
    return match.group(0).lower() if match else None


def transcript_name(message_ts, clip):
    """The state entry name of one clip's transcript: the message `ts` and the file id, made safe for `GuestState`."""
    safe = _BAD_NAME.sub("_", f"{message_ts}-{clip.id}").strip(".")[:64]
    return safe if safe and safe[0].isalnum() else f"clip-{safe}"[:64]


def clip_bytes(response):
    """The clip's bytes of one read-file answer: bytes, or a mapping with `data` as bytes or base64 text. An answer that
    carries no bytes is refused, so a failed read never reaches the model as empty audio."""
    if isinstance(response, (bytes, bytearray)):
        return bytes(response)
    if isinstance(response, dict):
        data = response.get("data")
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        if isinstance(data, str):
            return base64.b64decode(data)
    if isinstance(response, str):
        return response.encode()
    raise ValueError("the read-file answer carries no clip bytes")


def local_transcriber(model="small"):
    """A transcriber that runs faster-whisper on this machine, from a local model folder only (`local_files_only`), so
    the model is never downloaded and the clip never leaves the machine. `transcribe(audio, language)` answers the
    transcript text. The import is lazy, so a machine with no faster-whisper still runs the rest of the poller."""
    from faster_whisper import WhisperModel  # type: ignore[import-not-found]  # an optional local model, absent in CI

    engine = WhisperModel(model, device="cpu", compute_type="int8", local_files_only=True)

    def transcribe(audio, language=None):
        segments, _ = engine.transcribe(io.BytesIO(audio), language=language)
        return " ".join(segment.text.strip() for segment in segments).strip()

    return transcribe


def gemini_prompt(language):
    """The prompt of one Gemini transcription. A language code, when given, names the language so the model holds the
    channel's language; with no code the model detects the language itself."""
    return f"{GEMINI_PROMPT} The language is {language}." if language else GEMINI_PROMPT


def gemini_text(answer):
    """The transcript of one Gemini answer: the text of its candidate parts, joined. An answer with no text is refused,
    so a failed call never stores an empty transcript."""
    parts = ((answer.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    text = " ".join(part.get("text") or "" for part in parts).strip()
    if not text:
        raise RuntimeError("gemini returned no transcript")
    return text


def gemini_client(key, model=GEMINI_MODEL, timeout=GEMINI_TIMEOUT):
    """A Gemini client that sends one clip to `model` and answers the parsed response. The key travels in a header,
    never in the URL or in output. `client(audio, mime, prompt)` is the callable shape `gemini_transcriber` takes."""
    def client(audio, mime, prompt):
        body = {"contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(audio).decode()}},
        ]}]}
        request = urllib.request.Request(
            GEMINI_URL % model,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")
    return client


def gemini_transcriber(client):
    """A transcriber that sends one clip to Gemini through `client(audio, mime, prompt)` and answers its text in the
    channel's language. `transcribe(audio, language, mime)` is the shape `transcribe_message` calls."""
    def transcribe(audio, language=None, mime="audio/mp4"):
        return gemini_text(client(audio, mime, gemini_prompt(language)))
    return transcribe


def transcribe_message(message, channel, state, read_file, transcriber, language, gemini=None):
    """Transcribe each audio clip of one read message and store each transcript in the private state folder. An `off`
    channel transcribes nothing and reads no clip. A `local` channel transcribes through `transcriber(audio, language)`.
    A `gemini` channel sends the clip through `gemini(audio, language, mime)`; with no `gemini` callable it transcribes
    nothing. `read_file(file_id)` is the connector's read-file tool and `state` the `GuestState` of the folder. Returns
    the stored `gueststate.Entry` rows."""
    mode = transcribe_mode(channel)
    if mode not in ("local", "gemini") or (mode == "gemini" and gemini is None):
        return []
    code = whisper_language(language)
    stored = []
    for clip in audio_clips(message):
        audio = clip_bytes(read_file(clip.id))
        if mode == "local":
            text = transcriber(audio, code)
        else:
            text = gemini(audio, code, clip.mimetype or "audio/mp4")
        stored.append(state.put(TRANSCRIPT_KIND, transcript_name(message.get("ts"), clip), str(text or "")))
    return stored


__all__ = ["AUDIO_TYPES", "Clip", "TRANSCRIBE_MODES", "audio_clips", "clip_bytes", "gemini_client", "gemini_prompt",
           "gemini_text", "gemini_transcriber", "local_transcriber", "transcribe_message", "transcribe_mode",
           "transcript_name", "whisper_language"]
