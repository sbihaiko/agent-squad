"""The `voiced` daemon: one owner DM becomes one transcribed inbox line, and a stranger's message stays data.

usage: voiced.py [--state DIR] [--owner USER_ID] [--print-plist [--label L] [--python PATH]]

The daemon holds one long-lived Socket Mode connection (`apps.connections.open` with the app token), so Slack pushes
each `message.im` event instead of the operator polling for it. For every event it does one of three things:

- a message from the owner that carries a clip is downloaded, transcribed with Gemini, and appended to
  `$VOICE_STATE/inbox.jsonl` as one JSON line (`id`, `ts`, `user`, `channel`, `text`, `clip`), where `text` is the
  transcription. A text message from the owner is appended the same way with `clip` false; an owner message with no
  text at all is recorded in `other.jsonl` with an `error`, never dropped and never left unacknowledged.
- a message from any other user is appended to `$VOICE_STATE/other.jsonl` as data and never to the inbox.
- a message whose id was already recorded is acknowledged and writes nothing: the ids of every appended line live in
  `$VOICE_STATE/seen.jsonl`, so a daemon that crashes and restarts resumes with no duplicate line.

The id is Slack's `client_msg_id`, else `<channel>:<ts>` (Slack omits `client_msg_id` on some events). The inbox line and
its id are fsync'd before the envelope is acknowledged, so a crash after the ack can never lose a line. A clip Gemini
cannot read lands in `other.jsonl` with an `error`, never in the inbox as a half line the operator might read as an
instruction.

Every project the hub lists gets one channel in the dedicated workspace, named after its GitHub repository (lowercased,
each dot a hyphen). The daemon creates and joins the channel once, keeps the repository-to-channel map in
`$VOICE_STATE/channels.json`, writes one inbox per channel (`$VOICE_STATE/inboxes/<channel>.jsonl`) and posts an answer
in the channel its question came from. Two repositories whose names collide after the conversion are refused with one
line that names both, and no channel is created for the second. A message in a channel no project owns is data in
`other.jsonl`, never a DM question.

Secrets come from the one reader the voice pipeline already uses (`keychain.py`): the bot token, the app token and the
Gemini key live in the macOS Keychain. No value is ever printed, logged, or written to a state file. `VOICE_OWNER` (or
the file `$VOICE_STATE/owner`) names the only user the daemon accepts; `VOICE_STATE` names the state directory
(default `~/.local/state/agent-squad/voiced`).

`--print-plist` prints the launchd agent that keeps the daemon alive (`KeepAlive` and `RunAtLoad`): on a crash launchd
starts it again, and the seen ledger makes the restart resume without a duplicate. The command only prints the plist;
loading it into the owner's launchd is the owner's step, and the daemon never runs `launchctl`.
"""
import asyncio
import base64
import glob
import hashlib
import json
import os
import re
import shlex
import ssl
import subprocess
import sys
import urllib.request
import xml.sax.saxutils

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import channels  # noqa: E402  the one Slack call path, so this daemon keeps no second copy of it
import keychain  # noqa: E402  the one reader for the three voice secrets

DEFAULT_STATE = os.path.expanduser("~/.local/state/agent-squad/voiced")
ADDRESS = os.path.join(HERE, "..", "squad-goal", "scripts", "address.sh")   # the hub's seam: the one list of its projects
CHANNEL_MAX = 80     # Slack's channel-name cap
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"
BOT_SKIP = ("channel_join", "channel_leave", "bot_message")
AUDIO_TYPES = {"m4a", "mp4", "webm", "ogg", "mp3", "wav", "aac", "flac"}
TIMEOUT = 60
MAX_CLIP_BYTES = 20 * 1024 * 1024     # Gemini's inline_data request cap; a larger clip is a record, never a guess
CHUNK = 1 << 16


class ClipTooLarge(Exception):
    """A clip past what one inline Gemini request can carry. The size travels with the error, never the bytes."""

    def __init__(self, size):
        super().__init__(f"clip too large: {size} bytes over the {MAX_CLIP_BYTES} byte cap")
        self.size = size


def channel_name(repo):
    """The channel for one repository: its name, lowercased, each dot a hyphen; every other character Slack refuses
    becomes a hyphen too, so a GitHub name always yields a valid channel name (#1484).

    Slack refuses a name that starts or ends with a hyphen and a run of hyphens; both are collapsed and trimmed. A name
    past the cap keeps a short digest of the whole name, so two long repositories that agree up to the cut never share
    one channel.
    """
    name = (repo or "").rstrip("/").rsplit("/", 1)[-1]
    name = re.sub(r"[^a-z0-9_-]", "-", name.lower().replace(".", "-"))
    name = re.sub(r"-{2,}", "-", name).strip("-")
    if len(name) > CHANNEL_MAX:
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        name = name[:CHANNEL_MAX - len(digest) - 1].strip("-") + "-" + digest
    return name


def context_of(text):
    """The KEY=value context `address.sh` prints, one key per line.

    Each line is read as its own shell words, so an optional leading `export`, a quoted value and a value holding a
    space all resolve to the value the seam meant, never to the last word of it.
    """
    ctx = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, rest = line.partition("=")
        key = key.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        try:
            ctx[key] = " ".join(shlex.split(rest.strip()))
        except ValueError:
            ctx[key] = rest.strip()
    return ctx


def hub_repos(script=None):
    """Every repository the hub lists: the profile names `address.sh --list` prints, each resolved to its SG_REPO.

    The seam is the hub's own (ADR-0138), so a project added to the hub needs no second list beside it. A seam that
    refuses or a profile that names no repository contributes nothing, and the daemon keeps the channels it already has.
    """
    script = script or ADDRESS
    try:
        out = subprocess.run(["bash", script, "--list"], capture_output=True, text=True, timeout=30)
        slugs = [ln.split("\t")[0].strip() for ln in (out.stdout or "").splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        return []
    repos = []
    for slug in slugs:
        try:
            p = subprocess.run(["bash", script, slug], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        repo = (context_of(p.stdout).get("SG_REPO") or "").strip()
        if repo and repo not in repos:
            repos.append(repo)
    return repos


class SlackClient:
    """The daemon's one Slack call path with the bot token, injected so a test passes a fake and no token is printed."""

    def __init__(self, token):
        self.token = token

    def api(self, method, params=None, post=False):
        return channels.api(self.token, method, params, post)


class State:
    """The daemon's state in one directory: the inbox, the record of other users, the seen ids and the owner id."""

    def __init__(self, directory):
        self.dir = directory
        os.makedirs(directory, exist_ok=True)
        self.inbox_path = os.path.join(directory, "inbox.jsonl")
        self.other_path = os.path.join(directory, "other.jsonl")
        self.seen_path = os.path.join(directory, "seen.jsonl")
        self.owner_path = os.path.join(directory, "owner")
        self.channels_path = os.path.join(directory, "channels.json")
        self.inboxes_dir = os.path.join(directory, "inboxes")
        self._seen = None
        self._channels = None

    def channels(self):
        """repository -> {name, id}: the project channels this daemon created or joined, read from disk or stale-safe."""
        if self._channels is None:
            self._channels = {}
            try:
                with open(self.channels_path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._channels = {k: v for k, v in data.items() if isinstance(v, dict)}
            except (OSError, ValueError):
                pass
        return self._channels

    def save_channels(self, mapping):
        """The registry as one atomic write (ADR-0030): a crash leaves the old file, never half of a new one."""
        tmp = self.channels_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(mapping, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.channels_path)
        self._channels = {k: dict(v) for k, v in mapping.items()}

    def channel_for(self, channel_id):
        """The repository one project channel belongs to, or None for the DM and any channel no project owns."""
        for repo, entry in self.channels().items():
            if entry.get("id") == channel_id:
                return repo
        return None

    def inbox_for(self, name):
        """One project channel's inbox: one file per channel, so a project's session watches only its own."""
        return os.path.join(self.inboxes_dir, f"{name}.jsonl")

    def owner(self):
        """The one accepted user: VOICE_OWNER wins, else the owner file written once beside the inbox."""
        env = (os.environ.get("VOICE_OWNER") or "").strip()
        if env:
            return env
        try:
            with open(self.owner_path, encoding="utf-8") as f:
                return f.read().strip() or None
        except OSError:
            return None

    def seen(self):
        """Every message id already recorded. Every inbox is read too, so a crash between the line and the ledger loses none."""
        if self._seen is None:
            ids = set()
            paths = [self.seen_path, self.inbox_path, self.other_path]
            try:
                paths += sorted(glob.glob(os.path.join(self.inboxes_dir, "*.jsonl")))
            except OSError:
                pass
            for path in paths:
                for record in read_lines(path):
                    if record.get("id"):
                        ids.add(record["id"])
            self._seen = ids
        return self._seen

    def _append(self, path, record):
        """One durable line: written, flushed and fsync'd before the caller returns.

        A crash can leave a half line with no newline; it gets one first, so the new record is its own line and the
        half line stays the skipped record `read_lines` already ignores.
        """
        line = json.dumps(record, sort_keys=True) + "\n"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a+b") as f:
            f.seek(0, os.SEEK_END)
            if f.tell():
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    line = "\n" + line
            f.write(line.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())

    def _remember(self, message_id):
        self.seen().add(message_id)
        self._append(self.seen_path, {"id": message_id})

    def append_inbox(self, record, path=None):
        self._append(path or self.inbox_path, record)
        self._remember(record["id"])

    def append_other(self, record):
        self._append(self.other_path, record)
        self._remember(record["id"])


def read_lines(path):
    """The JSON lines of one state file, skipping a half line a crash left behind."""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    out.append(record)
    except OSError:
        pass
    return out


def message_id(event):
    """Slack's own id for one message: client_msg_id, else channel and ts, which name it once."""
    client_msg_id = event.get("client_msg_id")
    if client_msg_id:
        return client_msg_id
    return f"{event.get('channel') or 'dm'}:{event.get('ts') or ''}"


def audio_file(event):
    """The clip a message carries, or None: an audio mimetype or an audio filetype."""
    for f in event.get("files") or []:
        mime = (f.get("mimetype") or "").lower()
        filetype = (f.get("filetype") or "").lower()
        if mime.startswith("audio/") or filetype in AUDIO_TYPES:
            return f
    return None


def download(url, token, limit=MAX_CLIP_BYTES):
    """The clip's bytes with the bot token, never the URL's own public form; a clip past the cap is never read whole."""
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    size = 0
    chunks = []
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        while True:
            chunk = response.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise ClipTooLarge(size)
            chunks.append(chunk)
    return b"".join(chunks)


def transcribe(audio, mime, key):
    """One Gemini transcription of one clip. The key travels in a header, never in the URL or in output."""
    body = {
        "contents": [{"parts": [
            {"text": "Transcribe this audio exactly in its own language. Reply with the transcript only."},
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(audio).decode()}},
        ]}],
    }
    request = urllib.request.Request(
        GEMINI_URL % GEMINI_MODEL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        answer = json.loads(response.read() or b"{}")
    parts = ((answer.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    text = " ".join(part.get("text") or "" for part in parts).strip()
    if not text:
        raise RuntimeError("gemini returned no transcript")
    return text


def project_channels(state, client, repos, log=None):
    """Give every repository one channel, created and joined once. Returns {"channels": {repo: {name, id}}, "refused": [...]}.

    A repository whose converted name collides with an earlier one is refused with one line that names both, and no
    channel is created for it. The registry on disk makes a restart create nothing a second time.
    """
    log = log or (lambda line: None)
    channels = {repo: dict(entry) for repo, entry in state.channels().items()}
    refused, order, by_name = [], [], {}
    # A channel already on disk keeps its repository: a newcomer that converts to that name is refused, never handed
    # the first project's channel and inbox.
    for repo in sorted(channels):
        name = (channels[repo].get("name") or "").strip()
        if not name:
            continue
        if name in by_name:
            refused.append(_collision_line(repo, by_name[name], name))
            continue
        by_name[name] = repo
    for repo in repos:
        name = channel_name(repo)
        if not name:
            continue
        holder = by_name.get(name)
        if holder is not None and holder != repo:
            refused.append(_collision_line(repo, holder, name))
            continue
        by_name[name] = repo
        order.append((repo, name))
    changed = False
    for repo, name in order:
        entry = channels.get(repo)
        if entry and entry.get("name") == name and entry.get("id"):
            continue
        got = _ensure_channel(client, name, channels, log)
        if got is None:
            continue
        channels[repo] = got
        changed = True
    if changed:
        state.save_channels(channels)
    for line in refused:
        log(line)
    return {"channels": channels, "refused": refused}


def _collision_line(repo, holder, name):
    """The one line that refuses a repository whose converted name another repository already holds."""
    return f"voiced: {repo} and {holder} share the channel name {name}; no channel is created for {repo}"


def _ensure_channel(client, name, channels, log):
    """The channel called `name`: created and joined, else found when Slack already holds the name."""
    for entry in channels.values():
        if entry.get("name") == name and entry.get("id"):
            return dict(entry)
    got = client.api("conversations.create", {"name": name, "is_private": False}, post=True)
    if got.get("ok"):
        channel = got.get("channel") or {}
    elif got.get("error") == "name_taken":
        listing = client.api("conversations.list?types=public_channel,private_channel&limit=1000")
        channel = next((c for c in listing.get("channels") or [] if c.get("name") == name), None)
        if not channel:
            log(f"voiced: Slack holds the name {name} and lists no channel for it")
            return None
    else:
        log(f"voiced: cannot create the channel {name}: {got.get('error')}")
        return None
    cid = channel.get("id")
    if not cid:
        log(f"voiced: the channel {name} has no id")
        return None
    joined = client.api("conversations.join", {"channel": cid}, post=True)
    if not joined.get("ok") and joined.get("error") not in ("already_in_channel", "is_archived"):
        log(f"voiced: cannot join the channel {name}: {joined.get('error')}")
    return {"name": name, "id": cid}


class Daemon:
    """One handler for one event; the acknowledging loop is `serve` and `handle_envelope` beside it."""

    def __init__(self, state, owner, transcriber, downloader, log=None, client=None, projects=None):
        self.state = state
        self.owner = owner
        self.transcriber = transcriber
        self.downloader = downloader
        self.log = log or (lambda line: print(line, file=sys.stderr))
        self.client = client
        self.projects = projects

    def sync_projects(self):
        """Give every project the hub lists its channel, once; no client or no list leaves the channels as they are."""
        if self.client is None or self.projects is None:
            return {"channels": self.state.channels(), "refused": []}
        return project_channels(self.state, self.client, self.projects(), self.log)

    def inbox_path_of(self, event):
        """The inbox one message belongs to: its project's when the channel names one, the DM's for an im, else None."""
        repo = self.state.channel_for(event.get("channel"))
        if repo:
            return self.state.inbox_for(self.state.channels()[repo]["name"])
        if (event.get("channel_type") or "im") == "im":
            return self.state.inbox_path
        return None

    def answer(self, question, text):
        """Post one answer in the channel its question came from, threaded under it. True when Slack accepts it."""
        if self.client is None or not question.get("channel"):
            return False
        payload = {"channel": question["channel"], "text": text}
        if question.get("ts"):
            payload["thread_ts"] = question["ts"]
        got = self.client.api("chat.postMessage", payload, post=True)
        if not got.get("ok"):
            self.log(f"voiced: answer refused: {got.get('error')}")
            return False
        return True

    def handle(self, envelope):
        """Record one envelope: "inbox", "other", "duplicate", "failed" or "ignored". No ack happens here."""
        if (envelope.get("type") or "events_api") != "events_api":
            return "ignored"
        event = (envelope.get("payload") or {}).get("event") or {}
        if event.get("type") != "message":
            return "ignored"
        if event.get("subtype") in BOT_SKIP or event.get("bot_id"):
            return "ignored"
        mid = message_id(event)
        if mid in self.state.seen():
            return "duplicate"
        record = {
            "id": mid,
            "ts": event.get("ts"),
            "user": event.get("user"),
            "channel": event.get("channel"),
        }
        if event.get("files"):
            record["files"] = [
                {"id": f.get("id"), "name": f.get("name"), "mimetype": f.get("mimetype")}
                for f in event.get("files") or []
            ]
        target = self.inbox_path_of(event)
        if target is None:
            # A channel no project owns is data, never a DM question the operator might answer as if it were one.
            record["text"] = (event.get("text") or "").strip()
            record["error"] = "unknown channel: no project owns it"
            self.state.append_other(record)
            return "other"
        text = (event.get("text") or "").strip()
        if event.get("user") != self.owner:
            record["text"] = text
            self.state.append_other(record)
            return "other"
        clip = audio_file(event)
        if clip:
            url = clip.get("url_private_download") or clip.get("url_private") or ""
            try:
                audio = self.downloader(url)
                if len(audio) > MAX_CLIP_BYTES:
                    raise ClipTooLarge(len(audio))
                text = (self.transcriber(audio, clip.get("mimetype") or "audio/mp4") or "").strip()
            except ClipTooLarge as x:   # a clip past the inline cap is a record with its size
                record["error"] = str(x)
                record["bytes"] = x.size
                self.state.append_other(record)
                self.log(f"voiced: {record['error']} for {mid}")
                return "failed"
            except Exception as x:      # noqa: BLE001 - a clip no model could read is data, never an inbox line
                record["error"] = f"transcription failed: {type(x).__name__}"
                self.state.append_other(record)
                self.log(f"voiced: {record['error']} for {mid}")
                return "failed"
        if not text:
            # An owner message with nothing to transcribe or read is still data, and it is always acknowledged.
            record["text"] = text
            record["error"] = "empty transcript" if clip else "empty message: no clip and no text"
            self.state.append_other(record)
            return "other"
        record["text"] = text
        record["clip"] = bool(clip)
        self.state.append_inbox(record, target)
        return "inbox"


async def handle_envelope(daemon, envelope, ack):
    """Record one envelope off the event loop, then acknowledge it only after its outcome is durable.

    The whole per-envelope path lives here, so production and the tests drive one copy of it. `daemon.handle` runs in a
    worker thread: the Gemini call takes up to `TIMEOUT` seconds, and the loop must keep answering websocket pings.
    An unexpected failure leaves the envelope unacknowledged, which is how Slack is told to deliver it again.
    """
    try:
        outcome = await asyncio.to_thread(daemon.handle, envelope)
    except Exception as x:      # noqa: BLE001 - no ack: Slack delivers it again, and the id keeps any later line single
        daemon.log(f"voiced: {type(x).__name__}: {str(x)[:120]}")
        return None
    if envelope.get("envelope_id"):
        await ack(envelope["envelope_id"])
    return outcome


def plist_text(label, python, script, state_dir, owner, checkout):
    """The launchd agent that restarts the daemon after a crash. Printing it loads nothing."""
    esc = lambda v: xml.sax.saxutils.escape(str(v))  # noqa: E731
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{esc(label)}</string>
<key>ProgramArguments</key><array><string>{esc(python)}</string><string>{esc(script)}</string></array>
<key>WorkingDirectory</key><string>{esc(checkout)}</string>
<key>EnvironmentVariables</key><dict>
<key>VOICE_STATE</key><string>{esc(state_dir)}</string>
<key>VOICE_OWNER</key><string>{esc(owner)}</string>
</dict>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>ThrottleInterval</key><integer>10</integer>
<key>StandardOutPath</key><string>{esc(os.path.join(state_dir, "voiced.out"))}</string>
<key>StandardErrorPath</key><string>{esc(os.path.join(state_dir, "voiced.err"))}</string>
</dict></plist>
"""


def certifi_bundle():
    """certifi's bundle, or None where it is not installed."""
    try:
        import certifi
    except ImportError:
        return None
    return certifi.where()


def ca_bundle():
    """The CA bundle path the daemon trusts, or None for the default system store.

    certifi comes before `/etc/ssl/cert.pem`: the launchd environment is minimal, and a Python without Homebrew's
    bundle directory must still find a complete store.
    """
    for ca in (os.environ.get("SSL_CERT_FILE"), certifi_bundle(), "/etc/ssl/cert.pem"):
        if ca and os.path.exists(ca):
            return ca
    return None


def ssl_context(log=None):
    """The one CA bundle the daemon trusts, and the line that names it: under launchd a silent guess is a TLS loop."""
    ca = ca_bundle()
    if log:
        log(f"voiced: trusting CA bundle {ca}" if ca else "voiced: trusting the default system CA store")
    return ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()


async def serve(daemon, app_token, connect=None, open_connection=None, stop=None):
    """Keep the Socket Mode connection open, reconnecting with a growing wait; each envelope is handled, then acked.

    `connect` and `open_connection` are the two seams a test injects; `stop` ends the loop when it is set. Production
    passes none of the three, so the loop it runs is the loop the tests exercise.
    """
    if connect is None:
        import websockets
        connect = websockets.connect
    if open_connection is None:
        open_connection = lambda: channels.api(app_token, "apps.connections.open", {}, post=True)  # noqa: E731

    context = ssl_context(log=daemon.log)
    wait = 1
    while not (stop is not None and stop.is_set()):
        try:
            # A project that entered the hub gets its channel before the connection is renewed; a failure to create
            # one is a log line, never a daemon that stops hearing the DM.
            await asyncio.to_thread(daemon.sync_projects)
        except Exception as x:      # noqa: BLE001 - the DM keeps working even when the channel sync cannot
            daemon.log(f"voiced: project sync failed: {type(x).__name__}: {str(x)[:120]}")
        answer = await asyncio.to_thread(open_connection)
        url = answer.get("url")
        if not url:
            daemon.log(f"voiced: socket mode refused: {answer.get('error')}")
            await asyncio.sleep(wait)
            wait = min(wait * 2, 60)
            continue
        try:
            async with connect(url, ping_interval=20, ssl=context) as ws:
                wait = 1

                async def ack(envelope_id):
                    await ws.send(json.dumps({"envelope_id": envelope_id}))

                async for raw in ws:
                    envelope = json.loads(raw)
                    kind = envelope.get("type")
                    if kind == "disconnect":
                        break
                    if kind != "events_api":
                        continue
                    await handle_envelope(daemon, envelope, ack)
        except Exception as x:      # noqa: BLE001 - a dropped socket is retried, never fatal: launchd also restarts the process
            daemon.log(f"voiced: {type(x).__name__}: {str(x)[:120]}")
        if stop is not None and stop.is_set():
            return
        await asyncio.sleep(wait)
        wait = min(wait * 2, 60)


def main(argv):
    state_dir = os.environ.get("VOICE_STATE") or DEFAULT_STATE
    owner = None
    label = "com.agent-squad.voiced"
    python = sys.executable
    print_plist = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--state", "--owner", "--label", "--python") and i + 1 < len(argv):
            if a == "--state":
                state_dir = argv[i + 1]
            elif a == "--owner":
                owner = argv[i + 1]
            elif a == "--label":
                label = argv[i + 1]
            else:
                python = argv[i + 1]
            i += 2
        elif a == "--print-plist":
            print_plist = True
            i += 1
        else:
            print("usage: voiced.py [--state DIR] [--owner USER_ID] [--print-plist [--label L] [--python PATH]]",
                  file=sys.stderr)
            return 2
    state = State(state_dir)
    owner = owner or state.owner()
    if not owner:
        print("voiced: no owner id: set VOICE_OWNER or write one user id to $VOICE_STATE/owner", file=sys.stderr)
        return 2
    if print_plist:
        sys.stdout.write(plist_text(label=label, python=python, script=os.path.abspath(__file__),
                                    state_dir=state_dir, owner=owner,
                                    checkout=os.path.abspath(os.path.join(HERE, "..", ".."))))
        return 0
    try:
        bot = keychain.read_secret(keychain.BOT_TOKEN)
        app = keychain.read_secret(keychain.APP_TOKEN)
        key = keychain.read_secret(keychain.GEMINI_KEY)
    except keychain.SecretMissing as x:
        print(f"voiced: Keychain entry missing: {x}", file=sys.stderr)
        return 1
    daemon = Daemon(state=state, owner=owner,
                    transcriber=lambda audio, mime: transcribe(audio, mime, key),
                    downloader=lambda url: download(url, bot),
                    client=SlackClient(bot), projects=hub_repos)
    try:
        asyncio.run(serve(daemon, app))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
