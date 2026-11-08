"""Print the messages in a channel that ask the bot something and have not been answered yet.

Vendored from the `slack-hal9k` skill, so this repository runs and times the pipeline it ships. A caller finds it by name
with `python3 -m squad.skillpath --script watch.py` (squad/skillpath.py) and never by a path.

usage: watch.py <project> [--channel <id|name>] [--all] [--peek] [--bot-service S]

The cursor is one file per channel under ~/.cache/slack-hal9k/, holding the ts of the newest message this script has
printed. Only a message newer than the cursor is printed, and the cursor moves only for messages printed — a run that
dies leaves the message for the next one. `--peek` prints without moving it.

What counts: a message that carries `<@BOT_USER_ID>` (Slack writes a mention as that token, or `<!subteam>` for a group),
and, with `--all`, every message from a human. The bot's own messages and joins are never printed.

The line is `<ts>\\t<user>\\t<text>`, one per message, so a caller answers each and posts into its thread. A channel with
nothing new prints nothing and exits 0 — a watcher runs every minute, so silence must be silent.

A secret resolves in one order and is never printed or logged: the macOS Keychain, then the project's gitignored .env,
then the sibling project's .env (SKILL.md, Access); the Slack token is a Keychain entry, and a tracked .env is never a
source.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import channels  # noqa: E402

CURSOR_DIR = os.path.expanduser("~/.cache/slack-hal9k")


def keychain(service):
    out = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                         capture_output=True, text=True, timeout=15)
    return out.stdout.strip() or None if out.returncode == 0 else None


def keychain_any(names):
    for n in names:
        v = keychain(n)
        if v:
            return v
    return None


def main(argv):
    if not argv:
        print("usage: watch.py <project> [--channel id] [--all] [--peek] [--bot-service S]", file=sys.stderr)
        return 2
    project, rest = argv[0], list(argv[1:])
    wanted = service = None
    show_all = peek = False
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("--channel", "--bot-service") and i + 1 < len(rest):
            if a == "--channel":
                wanted = rest[i + 1]
            else:
                service = rest[i + 1]
            i += 2
        elif a == "--all":
            show_all = True; i += 1
        elif a == "--peek":
            peek = True; i += 1
        else:
            i += 1

    service = service or next((s for s in (f"{project}-slack-bot-token", f"{project}-voice-slack-bot-token")
                               if keychain(s)), f"{project}-slack-bot-token")
    token = keychain(service)
    if not token:
        print(f"{service}: missing in the Keychain", file=sys.stderr)
        return 1
    cid, why = channels.resolve(token, wanted, project)
    if not cid:
        print(why, file=sys.stderr)
        return 1
    me = channels.api(token, "auth.test")
    bot_user = me.get("user_id")
    if not bot_user:
        print(f"cannot read the bot's own id: {me.get('error')}", file=sys.stderr)
        return 1
    bot_id = me.get("bot_id")

    os.makedirs(CURSOR_DIR, exist_ok=True)
    cf = os.path.join(CURSOR_DIR, f"{cid}.ts")
    try:
        cursor = open(cf, encoding="utf-8").read().strip() or "0"
    except OSError:
        cursor = "0"
    q = f"conversations.history?channel={cid}&limit=50&oldest={cursor}"
    d = channels.api(token, q)
    if not d.get("ok"):
        print(f"cannot read the channel: {d.get('error')}", file=sys.stderr)
        return 1
    msgs = sorted(d.get("messages") or [], key=lambda m: float(m.get("ts") or 0))
    # The thread roots are read without the cursor: a root older than the cursor never comes back from the oldest-bounded
    # read, and its replies are where a person answers the bot. Only the roots are used here, so the extra read costs one call.
    whole = channels.api(token, f"conversations.history?channel={cid}&limit=50")
    roots = whole.get("messages") or [] if whole.get("ok") else msgs
    # A reply in a thread is a question too: Slack keeps thread replies out of the channel history, and a person answering
    # the bot's own message rarely types its mention, so each live thread is read as well (#a mention in a thread).
    thread_msgs = []
    for m in roots:
        tt = m.get("thread_ts")
        if not (tt and m.get("reply_count")):
            continue
        r = channels.api(token, f"conversations.replies?channel={cid}&ts={tt}&limit=60&oldest={cursor}")
        if not r.get("ok"):
            continue
        replies = [x for x in (r.get("messages") or []) if x.get("ts") != tt]
        # A thread the bot answers is a conversation: a person replying there is talking to it, and rarely types a mention.
        if any(x.get("user") == bot_user or x.get("bot_id") for x in replies) or m.get("user") == bot_user:
            for x in replies:
                x["_thread"] = True
            thread_msgs.extend(replies)
    msgs = sorted(msgs + thread_msgs, key=lambda m: float(m.get("ts") or 0))
    out, newest = [], cursor
    for m in msgs:
        ts = m.get("ts") or ""
        if float(ts or 0) <= float(cursor or 0) or ts == cursor:
            continue
        if float(ts) > float(newest or 0):
            newest = ts
        if m.get("user") == bot_user or m.get("bot_id"):
            continue
        text = (m.get("text") or "").strip()
        # A voice clip carries the audio and no text: read as empty it is skipped, and the owner's clip goes unanswered.
        files = m.get("files") or []
        if not text and files:
            names = ", ".join(f"{f.get('name') or f.get('title') or 'file'} [{f.get('id')}]" for f in files[:3])
            text = f"[file: {names}]"
        if not text or text.startswith("<@" + bot_user + "> entrou") or text.endswith("entrou no canal"):
            continue
        # Slack autocompletes an app by its bot id as often as by its user id: both spellings are a mention of the bot.
        mention = (f"<@{bot_user}>" in text or (bot_id and f"<@{bot_id}>" in text) or "<!subteam" in text)
        if not (mention or show_all or m.get("_thread")):
            continue
        out.append(f"{ts}\t{m.get('user')}\t{text.replace(chr(10), ' | ')}")
    if out:
        print("\n".join(out))
        if not peek:
            tmp = cf + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(newest)
            os.replace(tmp, cf)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
