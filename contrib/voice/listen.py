"""Print the messages a person sends to the bot as Slack pushes them, one line each: Socket Mode, no polling.

Vendored from the `slack-hal9k` skill, so this repository runs and times the pipeline it ships. A caller finds it by name
with `python3 -m squad.skillpath --script listen.py` (squad/skillpath.py) and never by a path.

usage: listen.py <project> [--bot-service S] [--app-service S]

`watch.py` asks Slack every 45 seconds whether something new arrived. This script does not ask: it opens the app's
Socket Mode connection (`apps.connections.open` with the app token) and Slack pushes each event to it, so a mention or a
clip reaches the caller within a second and nothing is missed between two polls.

Each printed line keeps the shape of `watch.py` — `<ts>\\t<user>\\t<text>`, and `[file: <name> [<id>]]` for a clip with
no text — so the caller answers it the same way. The bot's own messages, joins and empty events print nothing.

The connection is long-lived and Slack closes it after about an hour or on a redeploy: a `disconnect` event reconnects,
and a dropped socket is retried with a growing wait. A secret resolves in one order and is never printed or logged: the
macOS Keychain, then the project's gitignored .env (secretstore.env_value), then the sibling project's .env
(SKILL.md, Access); a tracked .env is never a source.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import channels  # noqa: E402
import secretstore  # noqa: E402

BOT_SKIP = ("channel_join", "channel_leave", "bot_message")
SIBLING = os.environ.get("SG_TTS_SIBLING", "~/VSCodeProjects/agent-squad")


def open_url(app_token):
    """One connection URL from apps.connections.open. The URL carries a ticket; it is never printed."""
    d = channels.api(app_token, "apps.connections.open", {}, post=True)
    if not d.get("ok"):
        return None, d.get("error")
    return d.get("url"), None


def line_of(ev, bot_user):
    """The one line a message event prints, or None when there is nothing to answer."""
    if ev.get("type") != "message":
        return None
    if ev.get("subtype") in BOT_SKIP or ev.get("bot_id"):
        return None
    if ev.get("user") == bot_user:
        return None
    text = (ev.get("text") or "").strip()
    files = ev.get("files") or []
    if not text and files:
        names = ", ".join(f"{f.get('name') or f.get('title') or 'file'} [{f.get('id')}]" for f in files[:3])
        text = f"[file: {names}]"
    if not text:
        return None
    return f"{ev.get('ts')}\t{ev.get('user')}\t{text.replace(chr(10), ' | ')}"


def main(argv):
    if not argv:
        print("usage: listen.py <project> [--bot-service S] [--app-service S]", file=sys.stderr)
        return 2
    project, rest = argv[0], list(argv[1:])
    bot_service = app_service = None
    i = 0
    while i < len(rest):
        if rest[i] in ("--bot-service", "--app-service") and i + 1 < len(rest):
            if rest[i] == "--bot-service":
                bot_service = rest[i + 1]
            else:
                app_service = rest[i + 1]
            i += 2
        else:
            i += 1
    bot_names = [bot_service] if bot_service else [f"{project}-slack-bot-token", f"{project}-voice-slack-bot-token"]
    app_names = [app_service] if app_service else [f"{project}-slack-app-token", f"{project}-voice-slack-app-token"]
    bot = secretstore.find(bot_names, ("SLACK_BOT_TOKEN",), SIBLING)
    app = secretstore.find(app_names, ("SLACK_APP_TOKEN",), SIBLING)
    if not bot:
        print(f"{bot_names[0]}: missing in the Keychain and in the project's .env", file=sys.stderr)
        return 1
    if not app:
        print(f"{app_names[0]}: missing in the Keychain and in the project's .env", file=sys.stderr)
        return 1
    me = channels.api(bot, "auth.test")
    bot_user = me.get("user_id")
    if not bot_user:
        print(f"cannot read the bot's own id: {me.get('error')}", file=sys.stderr)
        return 1

    try:
        import asyncio
        import ssl

        import websockets
    except ImportError:
        print("listen.py needs the `websockets` package", file=sys.stderr)
        return 1
    # The macOS interpreter often carries no CA bundle, and the socket would fail with "certificate verify failed" —
    # an error that reads as "Slack is down" while the token is fine. One bundle, resolved once.
    ctx = None
    for ca in (os.environ.get("SSL_CERT_FILE"), "/etc/ssl/cert.pem"):
        if ca and os.path.exists(ca):
            ctx = ssl.create_default_context(cafile=ca)
            break
    if ctx is None:
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = ssl.create_default_context()

    async def run():
        wait = 1
        while True:
            url, err = open_url(app)
            if not url:
                print(f"socket mode refused: {err}", file=sys.stderr)
                await asyncio.sleep(wait)
                wait = min(wait * 2, 60)
                continue
            try:
                async with websockets.connect(url, ping_interval=20, ssl=ctx) as ws:
                    wait = 1
                    async for raw in ws:
                        d = json.loads(raw)
                        if d.get("envelope_id"):
                            await ws.send(json.dumps({"envelope_id": d["envelope_id"]}))
                        t = d.get("type")
                        if t == "disconnect":
                            break
                        if t != "events_api":
                            continue
                        ev = d.get("payload", {}).get("event") or {}
                        line = line_of(ev, bot_user)
                        if line:
                            print(line, flush=True)
            except Exception as x:      # a dropped socket is retried, never fatal: a watcher that dies is a missed question
                print(f"listen.py: {type(x).__name__}: {str(x)[:120]}", file=sys.stderr)
            await asyncio.sleep(wait)
            wait = min(wait * 2, 60)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
