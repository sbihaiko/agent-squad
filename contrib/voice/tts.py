"""Synthesize a short spoken summary with Gemini TTS and write a WAV file. Never prints, logs or returns a key in output.

Vendored from the `slack-hal9k` skill, so this repository runs and times the pipeline it ships. A caller finds it by name
with `python3 -m squad.skillpath --script tts.py` (squad/skillpath.py) and never by a path.

usage: tts.py <text> [--voice Kore] [--tone STYLE] [--out /tmp/say.wav] [--service <keychain service>]

The key comes from the macOS Keychain first, then GOOGLE_API_KEY in the project's .env, then in a sibling project's .env
— the sibling is named with --sibling-env or SG_TTS_SIBLING (default: the agent-squad checkout beside this skill's
project, which holds the shared Gemini key). The service name defaults to <project>-gemini-key and
<project>-voice-gemini-key. The one lookup is `secretstore.find`; the project is the git repository the caller stands
in, and a .env that git tracks is never a source. No value is ever printed or logged.

Gemini returns raw PCM (16-bit mono, 24 kHz, audio/L16); Slack plays a WAV and not a bare stream, so the bytes are wrapped
here. The spoken script is the caller's: keep it short, the reply's text carries the detail.
"""
import base64
import json
import os
import subprocess
import sys
import wave

import secretstore

TIMEOUT = 60
MODELS = ("gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts")


def gemini_key(project, service, sibling):
    """Keychain, then the project's .env, then the sibling's .env: a project that never stored the key still gets one."""
    names = [service] if service else [f"{project}-gemini-key", f"{project}-voice-gemini-key"]
    return secretstore.find(names, ("GOOGLE_API_KEY", "GEMINI_API_KEY"), sibling)


def wav_from_pcm(pcm, path, rate=24000, width=2, channels=1):
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)


def style_prompt(text, tone=""):
    """The text one TTS request carries: the persona tone as the speaking style, then the script.

    Gemini TTS reads a style instruction at the start of the text part, so the tone goes first and the script follows.
    An empty tone leaves the script alone."""
    tone = (tone or "").strip()
    return f"{tone}\n\n{text}" if tone else text


def synth(key, text, voice, out, tone=""):
    """Synthesize `text` in `voice` and write `out`. A tone goes into the text part through `style_prompt`, so Gemini TTS
    reads it as the speaking style with the script; two tones therefore make two different requests, not only two names.
    `tests/test_voiced_persona.py` proves the tone reaches the request body."""
    body = {
        "contents": [{"parts": [{"text": style_prompt(text, tone)}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    payload = json.dumps(body)
    for model in MODELS:
        cmd = ["curl", "-sS", "--max-time", str(TIMEOUT), "-X", "POST",
               "-H", "Content-Type: application/json", "-d", payload,
               f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"]
        out_bytes = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT + 10).stdout
        try:
            d = json.loads(out_bytes or b"{}")
        except ValueError:
            continue
        if d.get("error"):
            err = (d["error"].get("status") or d["error"].get("message") or "")[:80]
            if "NOT_FOUND" in err or "not found" in err.lower():
                continue
            return None, err
        try:
            part = d["candidates"][0]["content"]["parts"][0]
            data = part["inlineData"]["data"]
        except (KeyError, IndexError, TypeError):
            return None, "no audio in the answer"
        pcm = base64.b64decode(data)
        mime = (part.get("inlineData") or {}).get("mimeType") or ""
        rate = 24000
        for tok in mime.split(";"):
            if tok.strip().startswith("rate="):
                try:
                    rate = int(tok.split("=", 1)[1])
                except ValueError:
                    pass
        wav_from_pcm(pcm, out, rate=rate)
        return out, None
    return None, "no TTS model answered"


def main(argv):
    if not argv:
        print("usage: tts.py <text> [--voice Kore] [--tone STYLE] [--out file.wav] [--project <slug>] [--service S] [--sibling-env PATH]",
              file=sys.stderr)
        return 2
    text, voice, tone, out, project, service, sibling = "", "Kore", "", "/tmp/hal9k-say.wav", "agent-squad", None, None
    rest = list(argv)
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--voice" and i + 1 < len(rest):
            voice = rest[i + 1]; i += 2
        elif a == "--tone" and i + 1 < len(rest):
            tone = rest[i + 1]; i += 2
        elif a == "--out" and i + 1 < len(rest):
            out = rest[i + 1]; i += 2
        elif a == "--project" and i + 1 < len(rest):
            project = rest[i + 1]; i += 2
        elif a == "--service" and i + 1 < len(rest):
            service = rest[i + 1]; i += 2
        elif a == "--sibling-env" and i + 1 < len(rest):
            sibling = rest[i + 1]; i += 2
        else:
            text += (" " if text else "") + a; i += 1
    text = text.strip()
    if not text:
        print("nothing to speak", file=sys.stderr)
        return 2
    sibling = sibling or os.environ.get("SG_TTS_SIBLING") or "~/VSCodeProjects/agent-squad"
    key = gemini_key(project, service, sibling)
    if not key:
        print("no Gemini key: the Keychain has none and no .env names GOOGLE_API_KEY", file=sys.stderr)
        return 1
    path, err = synth(key, text, voice, out, tone=tone)
    if not path:
        print(f"tts refused: {err}", file=sys.stderr)
        return 1
    print(f"{path} {os.path.getsize(path)}B voice={voice}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
