"""Answer a mention: the text reply first, then a short spoken summary in the same thread.

Vendored from the `slack-hal9k` skill, so this repository runs and times the pipeline it ships. A caller finds it by name
with `python3 -m squad.skillpath --script reply.py` (squad/skillpath.py) and never by a path.

usage: reply.py <project> <text...> [--speech "<short spoken summary>"] [--thread <ts>] [--channel <id|name>]
                [--voice Charon] [--no-audio] [--bot-service S]

This is the mention pattern the owner asked for: a longer, readable text answer, and a short audio clip that says the
same in a few sentences. The text goes out first because it is what a reader scans; the voice is synthesized after it, so
a slow TTS never delays the answer. Events keep the one-line shape in SKILL.md — this script is for a mention, never for
a log line.

A secret resolves in one order and is never printed or logged: the macOS Keychain (`<project>-slack-bot-token`, also
tried with `-voice-` in the middle), then the project's gitignored .env (secretstore.env_value), then the sibling
project's .env (SKILL.md, Access). The Gemini key follows the same three steps in tts.gemini_key. A tracked .env is
never a source.

The spoken script is the caller's, not the text's: `--speech` names what the clip says, and without it the first 240
characters of the text are spoken.
"""
import os
import subprocess
import sys

TIMEOUT = 30
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import channels  # noqa: E402  the one resolver: the named channel, else the one closest to the repository
import secretstore  # noqa: E402

SIBLING = os.environ.get("SG_TTS_SIBLING", "~/VSCodeProjects/agent-squad")


def api(token, method, params=None, post=False):
    """One Slack call path: channels.api, resolved at call time, so reply.py keeps no second copy of it."""
    return channels.api(token, method, params, post)


def channel_id(token, wanted, project):
    """The channel named, else the one closest to this git repository's name (channels.resolve)."""
    return channels.resolve(token, wanted, project)


def upload(token, path, channel, thread_ts=None, title=None):
    """files.getUploadURLExternal, the bytes, then completeUploadExternal: the one path Slack still supports."""
    size = os.path.getsize(path)
    name = os.path.basename(path)
    d = api(token, f"files.getUploadURLExternal?filename={name}&length={size}")
    if not d.get("ok"):
        return None, d.get("error")
    url, fid = d.get("upload_url"), d.get("file_id")
    up = subprocess.run(["curl", "-sS", "--max-time", str(TIMEOUT), "-X", "POST", "-F", f"file=@{path}", url],
                        capture_output=True, text=True, timeout=TIMEOUT + 5)
    if up.returncode != 0:
        return None, (up.stderr or "upload failed").strip()[:120]
    body = {"files": [{"id": fid, "title": title or name}], "channel_id": channel}
    if thread_ts:
        body["thread_ts"] = thread_ts
    c = api(token, "files.completeUploadExternal", body, post=True)
    if not c.get("ok"):
        return None, c.get("error")
    return fid, None


SPOKEN_MAX = 420   # characters: past this the TTS answer stops mid-sentence, and a cut clip is worse than a short one


def spoken(text):
    """The clip's script: the whole sentence that fits, never a cut word. Markdown is stripped, it is read aloud."""
    import re
    t = re.sub(r"[*_`#>|]+", "", text or "")
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= SPOKEN_MAX:
        return t
    cut = t[:SPOKEN_MAX]
    for mark in (". ", "! ", "? ", "; "):
        i = cut.rfind(mark)
        if i > SPOKEN_MAX * 0.5:
            return cut[:i + 1].strip()
    i = cut.rfind(" ")
    return (cut[:i] if i > 0 else cut).strip() + "."


def main(argv):
    if len(argv) < 2:
        print("usage: reply.py <project> <text...> [--speech ...] [--thread ts] [--channel id] [--voice Charon] [--no-audio]",
              file=sys.stderr)
        return 2
    project, rest = argv[0], list(argv[1:])
    speech = thread = wanted = service = None
    voice, audio = "Charon", True
    words = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("--speech", "--thread", "--channel", "--voice", "--bot-service") and i + 1 < len(rest):
            v = rest[i + 1]
            if a == "--speech":
                speech = v
            elif a == "--thread":
                thread = v
            elif a == "--channel":
                wanted = v
            elif a == "--voice":
                voice = v
            else:
                service = v
            i += 2
        elif a == "--no-audio":
            audio = False; i += 1
        else:
            words.append(a); i += 1
    text = " ".join(words).strip()
    if not text:
        print("nothing to send", file=sys.stderr)
        return 2

    names = [service] if service else [f"{project}-slack-bot-token", f"{project}-voice-slack-bot-token"]
    token = secretstore.find(names, ("SLACK_BOT_TOKEN",), SIBLING)
    if not token:
        print(f"{names[0]}: missing in the Keychain and in the project's .env", file=sys.stderr)
        return 1
    cid, why = channel_id(token, wanted, project)
    if not cid:
        print(why, file=sys.stderr)
        return 1

    msg = {"channel": cid, "text": text, "unfurl_links": False}
    if thread:
        msg["thread_ts"] = thread
    d = api(token, "chat.postMessage", msg, post=True)
    if not d.get("ok"):
        print(f"text refused: {d.get('error')}", file=sys.stderr)
        return 1
    ts = d.get("ts")
    print(f"text {ts} to {cid}")
    if not audio:
        return 0

    said = spoken(speech or text)
    wav = f"/tmp/hal9k-reply-{os.getpid()}.wav"
    t = subprocess.run([sys.executable, os.path.join(HERE, "tts.py"), said, "--voice", voice, "--out", wav,
                        "--project", project, "--sibling-env",
                        os.environ.get("SG_TTS_SIBLING", "~/VSCodeProjects/agent-squad")],
                       capture_output=True, text=True, timeout=180)
    if t.returncode != 0 or not os.path.exists(wav):
        print(f"audio skipped: {(t.stderr or '').strip()[:120] or 'no wav'}", file=sys.stderr)
        return 0   # the text answer stands on its own; a missing voice never fails the reply
    fid, err = upload(token, wav, cid, thread_ts=thread or ts, title=f"{project} summary")
    if not fid:
        print(f"audio upload refused: {err}", file=sys.stderr)
        return 0
    print(f"audio {fid} in thread {thread or ts}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
