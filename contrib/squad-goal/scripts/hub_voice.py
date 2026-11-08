"""The phone's voice (#1603): one recording becomes one transcript, and one answer becomes one Kore clip.

The two Gemini calls come from the repository's one voice pipeline (`contrib/voice/tts.py` for the speech in Kore's voice,
`contrib/voice/voiced.py` for the transcription), found from this file and never copied here. This module holds the
phone's own rules: which audio a send admits, the clip's cache name, and the one synthesis per answer. The key never
leaves the pipeline's lookup, is never printed, logged or returned, and a hub with no pipeline or no key refuses with a
reason instead of guessing.
"""
import hashlib
import os
import pathlib
import sys

VOICE = "Kore"                 # the owner's chosen Gemini TTS voice (contrib/voice/tts.py)
CLIP_MAX_CHARS = 4000          # one answer's spoken text: the same cap a message carries
VOICE_MAX_BYTES = 12 * 1024 * 1024   # one recording: a two-minute clip of a phone's own audio stays far below it
VOICE_TYPES = {"audio/webm": "webm", "audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/ogg": "ogg",
               "audio/wav": "wav", "audio/x-m4a": "m4a", "audio/aac": "aac"}


def voice_dir():
    """The one voice pipeline beside this hub: this checkout's `contrib/voice`, or None when this copy carries none (the
    live copy under the skills folder has no repository beside it), so a missing pipeline is a named refusal."""
    path = pathlib.Path(__file__).resolve().parents[2] / "voice"
    return path if (path / "tts.py").is_file() and (path / "voiced.py").is_file() else None


def pipeline():
    """The voice pipeline's two modules, loaded from the one directory, so no second copy of either call exists here."""
    folder = voice_dir()
    if folder is None:
        raise RuntimeError("no voice pipeline beside this hub")
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
    import tts, voiced   # noqa: E402  (the vendored pair, imported where it lives)
    return tts, voiced


def key(project="agent-squad"):
    """The Gemini key the pipeline's own lookup finds, or None."""
    tts, _ = pipeline()
    return tts.gemini_key(project, None, os.environ.get("SG_TTS_SIBLING") or "")


def heard(raw, mime):
    """One recording as text, through the pipeline's one transcription. A hub with no key refuses; it never invents text."""
    _, voiced = pipeline()
    found = key()
    if not found:
        raise RuntimeError("no Gemini key for the phone's voice")
    return voiced.transcribe(raw, mime, found)


def spoken(text, out):
    """One answer's words as a WAV in Kore's voice, through the pipeline's one synthesis."""
    tts, _ = pipeline()
    found = key()
    if not found:
        raise RuntimeError("no Gemini key for the phone's voice")
    path, err = tts.synth(found, text[:CLIP_MAX_CHARS], VOICE, out)
    if not path:
        raise RuntimeError(err or "no audio")
    return path


def clip_name(sid, turn_key, text):
    """One answer's clip name: the voice, the turn and the words, so two answers never share a file and the same answer is
    synthesized once."""
    digest = hashlib.sha256(("\n".join((VOICE, sid, turn_key, text))).encode("utf-8")).hexdigest()[:32]
    return digest + ".wav"


def clip_for(state, sid, turn_key, text):
    """One answer's clip file, synthesized once. A file already there is reused; a part file is written and renamed, so an
    interrupted synthesis never serves half a clip."""
    path = pathlib.Path(state) / "voice" / "clips" / sid / clip_name(sid, turn_key, text)
    if path.is_file() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    spoken(text, str(part))
    os.replace(part, path)
    return path


def voice_type(mime):
    """The suffix one recording's type names, or None when the phone sent a type the route does not admit."""
    return VOICE_TYPES.get((mime or "").split(";")[0].strip().lower())
