"""The `voiced` daemon: one owner DM becomes one transcribed inbox line, and a stranger's message stays data.

usage: voiced.py [--state DIR] [--owner USER_ID] [--print-plist [--label L] [--python PATH]]

The daemon holds one long-lived Socket Mode connection (`apps.connections.open` with the app token), so Slack pushes
each `message.im` event instead of the operator polling for it. For every event it does one of three things:

- a message from the owner that carries a clip is downloaded, transcribed with Gemini, and appended to the DM's inbox
  `$VOICE_STATE/inboxes/dm.jsonl` as one JSON line (`id`, `ts`, `user`, `channel`, `text`, `clip`), where `text` is the
  transcription. A text message from the owner is appended the same way with `clip` false; an owner message with no
  text at all is recorded in `other.jsonl` with an `error`, never dropped and never left unacknowledged.
- a message from any other user is appended to `$VOICE_STATE/other.jsonl` as data and never to the inbox.
- a message whose id was already recorded is acknowledged and writes nothing: the ids of every appended line live in
  `$VOICE_STATE/seen.jsonl`, so a daemon that crashes and restarts resumes with no duplicate line.

The id is Slack's `client_msg_id`, else `<channel>:<ts>` (Slack omits `client_msg_id` on some events). The inbox line and
its id are fsync'd before the envelope is acknowledged, so a crash after the ack can never lose a line. A clip Gemini
cannot read lands in `other.jsonl` with an `error` and its one-sentence `answer`, never in the inbox as a half line the
operator might read as an instruction; the answer is posted under the clip in its own channel and thread, and the
record keeps the failure so a restart never calls the model that failed again.

Every project the hub lists gets one channel in the dedicated workspace, named `proj-` and its GitHub repository
(lowercased, each dot a hyphen). The daemon creates and joins the channel once, keeps the repository-to-channel map in
`$VOICE_STATE/channels.json`, writes one inbox per channel (`$VOICE_STATE/inboxes/<channel id>.jsonl`, `dm` for the DM)
and posts an answer in the channel its question came from. A queue is keyed by Slack's channel id, never by its name,
so a channel renamed in Slack keeps its inbox, its outbox and its delivery record. A name-keyed file an older build wrote
is moved to its id once, on the version 1 to 2 start, with every pending line, and is left alone when its id file
already exists or when the directory already carries version 2. A name file `channels.json` never names has no
successor any start could give it, so it stays in place and never holds the version at 1. Two repositories whose names
collide after the conversion are refused with one line that names both, and no channel is created for the second. A
message in a channel no project owns is data in `other.jsonl`, never a DM question.

`$VOICE_STATE/schema.json` carries this build's state version. A directory with no version is an older build's and is
migrated once; a version this build does not know stops the daemon with one named line before it writes anything.

The reverse direction is the outbox. An operator session appends one JSON line (`thread_ts`, `text`, an optional
`speech`, and for the DM an optional `channel`) to its own outbox (`$VOICE_STATE/outboxes/<channel id>.jsonl`, or
`$VOICE_STATE/outboxes/dm.jsonl` for the DM), and the daemon answers it with no call from the session:

- the text is posted as the bot at once, threaded under `thread_ts` (else under nothing);
- the spoken script (the `speech` field, else the text) is split into sentences, and the first sentence is synthesized
  in parallel with the text post and posted as one clip, so a slow Slack write never holds the voice;
- the rest is synthesized only once the first clip is out, and posted as one clip.

A line whose text and clips all went out is in `$VOICE_STATE/outbox-seen.jsonl` by a digest of its content, so a restart
posts no second text and speaks no second clip. A line whose text went out and whose clip Slack refused is in
`$VOICE_STATE/outbox-posted.jsonl` with the ts its clips answer, so the next drain speaks the voice again and never posts
the text again. A refused post or a refused voice is a log line, never a daemon that stops answering, and a line refused
three times in one daemon waits for the next daemon instead of spinning at the poll interval.

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
import fcntl
import glob
import hashlib
import json
import os
import re
import shlex
import ssl
import subprocess
import sys
import tempfile
import urllib.request
import xml.sax.saxutils

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import channels  # noqa: E402  the one Slack call path, so this daemon keeps no second copy of it
import keychain  # noqa: E402  the one reader for the three voice secrets
import release_events as releases  # noqa: E402  the one release-event record the promotion pass and a project emit (#2702)

from squad import slackpolicy  # noqa: E402  the one home of the Slack file rules (ADR-0177)

DEFAULT_STATE = os.path.expanduser("~/.local/state/agent-squad/voiced")
ADDRESS = os.path.join(HERE, "..", "squad-goal", "scripts", "address.sh")   # the hub's seam: the one list of its projects
CHANNEL_MAX = 80     # Slack's channel-name cap
PROJ_PREFIX = "proj-"   # every project channel name starts with it, so a reader tells a project channel by its name
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"
BOT_SKIP = ("channel_join", "channel_leave", "bot_message")
AUDIO_TYPES = {"m4a", "mp4", "webm", "ogg", "mp3", "wav", "aac", "flac"}
TIMEOUT = 60
MAX_CLIP_BYTES = 20 * 1024 * 1024     # Gemini's inline_data request cap; a larger clip is a record, never a guess
CHUNK = 1 << 16
DEV_REF = "refs/remotes/origin/dev"   # the tip a project's Slack file is read from, never the working tree
CONFIG_POLL = 60.0                    # how long a moved dev tip waits for its revalidation
VOICE = "Kore"                        # the owner's chosen Gemini TTS voice
OUTBOX_POLL = 0.2                     # how long an answer waits for its post, well inside the 3 s target
MAX_DELIVER_ATTEMPTS = 3              # a refused answer is retried a few times, never spun at the poll interval
SENTENCE = re.compile(r"[^.!?]+(?:[.!?]+|$)")
SCHEMA_FILE = "schema.json"
SCHEMA_VERSION = 2                   # id-keyed queues; 1 is the name-keyed layout an older build wrote
SUPPORTED_SCHEMA = (1, SCHEMA_VERSION)
DM = "dm"                            # the DM is one scope beside the channels, so its queues carry a key too


class SchemaRefused(Exception):
    """A state directory whose schema version this build does not know. The message names the version and the file."""


class ClipTooLarge(Exception):
    """A clip past what one inline Gemini request can carry. The size travels with the error, never the bytes."""

    def __init__(self, size):
        super().__init__(f"clip too large: {size} bytes over the {MAX_CLIP_BYTES} byte cap")
        self.size = size


def one_sentence(text):
    """The failure as one sentence: whitespace collapsed, the first sentence only, and a closing period.

    The daemon builds the sentence the owner reads, so a failure whose text holds several sentences or none still
    yields one clean line under the clip (#1618).
    """
    flat = " ".join(str(text).split())
    first = re.split(r"(?<=[.!?])\s+", flat, maxsplit=1)[0].rstrip(".!?")
    return f"{first}."


def channel_name(repo):
    """The channel for one repository: `proj-` and its name, lowercased, each dot a hyphen; every other character Slack
    refuses becomes a hyphen too, so a GitHub name always yields a valid channel name (#1484). The `proj-` prefix is the
    rule a reader and the check both read: a project channel is told by its name (#1615).

    Slack refuses a name that starts or ends with a hyphen and a run of hyphens; both are collapsed and trimmed. A name
    past the cap keeps a short digest of the whole name, so two long repositories that agree up to the cut never share
    one channel. An empty repository name yields no channel name at all, never a bare prefix.
    """
    name = (repo or "").rstrip("/").rsplit("/", 1)[-1]
    name = re.sub(r"[^a-z0-9_-]", "-", name.lower().replace(".", "-"))
    name = re.sub(r"-{2,}", "-", name).strip("-")
    if not name:
        return ""
    name = PROJ_PREFIX + name
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


def hub_projects(script=None):
    """Every project the hub lists: its `SG_REPO`, the checkout its dev tip is read from, and its local Slack file.

    The seam is the hub's own (ADR-0138), so a project added to the hub needs no second list beside it. A seam that
    refuses or a profile that names no repository contributes nothing, and the daemon keeps the revisions it holds.
    """
    script = script or ADDRESS
    try:
        out = subprocess.run(["bash", script, "--list"], capture_output=True, text=True, timeout=30)
        slugs = [ln.split("\t")[0].strip() for ln in (out.stdout or "").splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        return []
    profiles = os.environ.get("SG_PROFILES_DIR") or os.path.abspath(
        os.path.join(HERE, "..", "squad-goal", "scripts", "profiles"))
    found = []
    for slug in slugs:
        try:
            p = subprocess.run(["bash", script, slug], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        ctx = context_of(p.stdout)
        repo = (ctx.get("SG_REPO") or "").strip()
        if not repo or any(entry["repo"] == repo for entry in found):
            continue
        name = (ctx.get("SG_PROJECT") or "").strip() or repo.rsplit("/", 1)[-1]
        local = slackpolicy.local_path(profiles, name)
        found.append({"repo": repo, "checkout": (ctx.get("SG_CHECKOUT") or "").strip(),
                      "local": str(local) if local else ""})
    return found


def hub_repos(script=None):
    """Every repository the hub lists: the `SG_REPO` of each project `hub_projects` names."""
    return [entry["repo"] for entry in hub_projects(script)]


class TipUnreadable(Exception):
    """A project's dev tip or its Slack file cannot be read: the daemon keeps the revision it holds and warns the owner."""


def git_output(checkout, *args):
    """One git read in a project's checkout, or None when git refuses: a broken checkout is a notice, never a dead daemon."""
    if not checkout:
        return None
    try:
        done = subprocess.run(["git", "-C", checkout, *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def dev_tip(checkout):
    """The `(sha, text)` of a project's committed Slack file at its dev tip, or `TipUnreadable`.

    The tip is read from git's object store, never from the working tree, so an edit in progress in a checkout never
    becomes the active revision (#2685).
    """
    sha = (git_output(checkout, "rev-parse", "--verify", DEV_REF) or "").strip()
    if not sha:
        raise TipUnreadable(f"cannot read the dev tip {DEV_REF} of the checkout {checkout or '(none named)'}")
    name = slackpolicy.COMMITTED.as_posix()
    text = git_output(checkout, "show", f"{sha}:{name}")
    if text is None:
        raise TipUnreadable(f"the dev tip {sha[:12]} holds no {name}")
    return sha, text


class SlackClient:
    """The daemon's one Slack call path with the bot token, injected so a test passes a fake and no token is printed."""

    def __init__(self, token):
        self.token = token

    def api(self, method, params=None, post=False):
        return channels.api(self.token, method, params, post)


def read_schema(directory):
    """The state directory's schema version, or `SchemaRefused` when this build does not know it.

    No file means a directory an older build wrote, which is version 1 and is migrated once. A file this build cannot
    read, or a version outside `SUPPORTED_SCHEMA`, refuses the start before any other file is touched.
    """
    path = os.path.join(directory, SCHEMA_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return 1
    except OSError as x:
        raise SchemaRefused(f"cannot read {path}: {type(x).__name__}") from x
    except ValueError as x:
        raise SchemaRefused(f"{path} is not a JSON object naming a state version") from x
    version = data.get("version") if isinstance(data, dict) else None
    if isinstance(version, bool) or version not in SUPPORTED_SCHEMA:
        raise SchemaRefused(
            f"state schema version {version!r} in {path} is unknown to this build, which reads "
            f"{' and '.join(str(v) for v in SUPPORTED_SCHEMA)}; refusing to start")
    return version


class State:
    """The daemon's state in one directory: the inbox, the record of other users, the seen ids and the owner id."""

    def __init__(self, directory):
        self.dir = directory
        self.version = read_schema(directory)      # before any write: an unknown version stops the start here
        os.makedirs(directory, exist_ok=True)
        self.other_path = os.path.join(directory, "other.jsonl")
        self.seen_path = os.path.join(directory, "seen.jsonl")
        self.owner_path = os.path.join(directory, "owner")
        self.channels_path = os.path.join(directory, "channels.json")
        self.schema_path = os.path.join(directory, SCHEMA_FILE)
        self.inboxes_dir = os.path.join(directory, "inboxes")
        self.outboxes_dir = os.path.join(directory, "outboxes")
        self.delivered_path = os.path.join(directory, "outbox-seen.jsonl")
        self.posted_path = os.path.join(directory, "outbox-posted.jsonl")
        self.release_events_path = os.path.join(directory, releases.RECORD)
        self.slack_dir = os.path.join(directory, "slack")
        self._seen = None
        self._channels = None
        self._registry_read = False
        self._delivered = None
        self._posted = None
        if self.version < SCHEMA_VERSION:
            if self.migrate():     # a name file no registry entry can move yet holds the version, so a later start retries
                self._stamp_schema()

    @property
    def inbox_path(self):
        """The DM's inbox: one scope beside the channels, keyed `dm` like any other."""
        return self.inbox_for(DM)

    @property
    def outbox_path(self):
        """The DM's outbox: the file the owner's session appends its DM answers to."""
        return self.outbox_for(DM)

    def slack_path(self, repo):
        """The file one project's active Slack revision lives in, named by the repository it belongs to."""
        leaf = re.sub(r"[^A-Za-z0-9_.-]", "-", (repo or "").strip()) or "project"
        return os.path.join(self.slack_dir, leaf + ".json")

    def active_revision(self, repo):
        """The Slack revision this daemon accepted for one project, or None. The file survives a restart, so a refused
        revision keeps the previous one active across one (#2685)."""
        try:
            with open(self.slack_path(repo), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and data.get("sha") else None

    def save_active_revision(self, repo, record):
        """The active revision as one atomic write (ADR-0030): a crash leaves the previous file, never half of a new one."""
        os.makedirs(self.slack_dir, exist_ok=True)
        path = self.slack_path(repo)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def release_events(self):
        """Every release event the promotion pass or a project emitted, oldest first (#2702).

        One record per event, in `$VOICE_STATE/release-events.jsonl`, the file `release_events.py emit` appends to. A
        channel that subscribes to the event announces it; a record this build cannot read is skipped.
        """
        return releases.read(self.dir)

    def _stamp_schema(self):
        """One atomic write of this build's version, after the migration it covers (ADR-0030)."""
        tmp = self.schema_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": SCHEMA_VERSION}, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.schema_path)
        self.version = SCHEMA_VERSION

    def migrate(self):
        """Move a queue file keyed by a channel name to the channel id, once, with every pending line.

        The registry names the id each name belongs to. A name file with no id successor is renamed; one whose id file
        already exists is the stale copy of a finished move and is left alone. The DM's queues were two files at the
        root, and they move into the same `dm` key. A file no registry entry names has no known successor and is not
        touched.

        Returns True when this start settles the version 1 layout, so the caller stamps the version and the stamp is the
        one record that the migration ran. A readable registry settles it: every move it names is done, and a name file
        no entry names has no successor any later start could give it, so leaving it in place loses nothing. A registry
        that was missing or unreadable moves nothing, so the version stays 1 and the next start retries; so does a
        directory with no name file at all, which needs no registry to settle.
        """
        moves = []
        for entry in list(self.channels().values()):
            name, cid = (entry.get("name") or "").strip(), (entry.get("id") or "").strip()
            if not name or not cid or name == cid:
                continue
            for directory in (self.inboxes_dir, self.outboxes_dir):
                moves.append((os.path.join(directory, f"{name}.jsonl"), os.path.join(directory, f"{cid}.jsonl")))
        moves.append((os.path.join(self.dir, "inbox.jsonl"), self.inbox_path))
        moves.append((os.path.join(self.dir, "outbox.jsonl"), self.outbox_path))
        for source, target in moves:
            if not os.path.exists(source) or source == target or os.path.exists(target):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.replace(source, target)      # atomic: every pending line travels with the file
        return self._registry_read or not self._name_keyed_queues()

    def _id_keys(self):
        """Every key a queue may carry: `dm` and each id the registry names, never a key's shape on its own."""
        keys = {DM}
        for entry in self.channels().values():
            cid = (entry.get("id") or "").strip()
            if cid:
                keys.add(cid)
        return keys

    def _name_keyed_queues(self):
        """Every queue file whose key is neither `dm` nor an id the registry names: a name no entry has moved yet."""
        known = self._id_keys()
        found = []
        for directory in (self.inboxes_dir, self.outboxes_dir):
            try:
                names = os.listdir(directory)
            except OSError:
                continue
            for name in names:
                if not name.endswith(".jsonl"):
                    continue
                key = name[:-len(".jsonl")]
                if key not in known:
                    found.append(os.path.join(directory, name))
        return found

    def channels(self):
        """repository -> {name, id}: the project channels this daemon created or joined, read from disk or stale-safe.

        The read also records whether the registry was usable: a missing or malformed file leaves the version 1 layout
        for a later start to settle, never stamps a migration it could not finish.
        """
        if self._channels is None:
            self._channels = {}
            self._registry_read = False
            try:
                with open(self.channels_path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._channels = {k: v for k, v in data.items() if isinstance(v, dict)}
                    self._registry_read = True
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
        self._registry_read = True

    def channel_for(self, channel_id):
        """The repository one project channel belongs to, or None for the DM and any channel no project owns."""
        for repo, entry in self.channels().items():
            if entry.get("id") == channel_id:
                return repo
        return None

    def inbox_for(self, key):
        """One channel's inbox, keyed by its id (`dm` for the DM): a rename in Slack never moves it."""
        return os.path.join(self.inboxes_dir, f"{key}.jsonl")

    def outbox_for(self, key):
        """One channel's outbox, keyed by its id (`dm` for the DM): its session names no second path."""
        return os.path.join(self.outboxes_dir, f"{key}.jsonl")

    def delivered(self):
        """Every outbox line already posted, by digest of its content: a restart posts no line a second time."""
        if self._delivered is None:
            self._delivered = {r["digest"] for r in read_lines(self.delivered_path) if r.get("digest")}
        return self._delivered

    def posted(self):
        """Every outbox line whose text is already out, by digest, with the ts its clips answer: a restart speaks the
        voice again without ever posting the text a second time."""
        if self._posted is None:
            self._posted = {r["digest"]: r for r in read_lines(self.posted_path) if r.get("digest")}
        return self._posted

    def remember_posted(self, digest, ts=None):
        """One durable mark of a text already posted, written at once: a refused clip repeats the voice, never the text."""
        record = {"digest": digest, "ts": ts}
        self._append(self.posted_path, record)
        self.posted()[digest] = record

    def remember_clip(self, digest, stage):
        """One durable mark of a clip already out, written at once: a later stage's refusal repeats that stage alone,
        never a clip the owner already heard."""
        record = self.posted().get(digest)
        if record is None:
            return
        done = list(record.get("clips_done") or [])
        if stage in done:
            return
        done.append(stage)
        record = {"digest": digest, "ts": record.get("ts"), "clips_done": done}
        self._append(self.posted_path, record)
        self.posted()[digest] = record      # the last line for a digest wins, so a restart reads the newest stage set

    def remember_delivered(self, digest):
        """One durable mark of an answer fully posted, text and every clip: the ledger is written before the memory that
        trusts it, so an append that fails makes the next drain try again instead of swallowing the answer."""
        self._append(self.delivered_path, {"digest": digest})
        self.delivered().add(digest)

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


def split_sentences(text):
    """One spoken script as its first sentence and the rest, so the owner hears the answer start without its end.

    A script with no sentence mark is one sentence, and the rest is empty. The first sentence keeps its own punctuation,
    because the voice reads it, and the rest is joined back into one clip.
    """
    parts = [part.strip() for part in SENTENCE.findall(text or "") if part.strip()]
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:]).strip()


def outbox_digest(record):
    """One outbox line's identity: its whole content, so two answers that differ by a word are two answers."""
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode("utf-8")).hexdigest()


def spoken_clip(key, script, voice=VOICE):
    """One spoken clip as a WAV path, through the repository's one TTS path (tts.synth); the key never leaves it."""
    import tts  # the vendored TTS beside this file, so the daemon keeps no second copy of it
    fd, path = tempfile.mkstemp(prefix="voiced-", suffix=".wav")
    os.close(fd)
    got, err = tts.synth(key, script, voice, path)
    if not got:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise RuntimeError(err or "no audio")
    return got


def upload_clip(token, path, channel, thread_ts=None, title=None):
    """One clip into one channel through the repository's one upload path (reply.upload); the token never leaves it."""
    import reply  # the vendored reply path beside this file, so the daemon keeps no second copy of the upload
    return reply.upload(token, path, channel, thread_ts=thread_ts, title=title)


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
        # The registry on disk is the source of truth: a repository it already maps keeps that channel, whatever name the
        # conversion would compute now (#1610). The computed name is the default for a repository it does not hold, so an
        # operator who renamed a project channel, or who pointed it at an existing one, is never handed a second channel.
        if entry and entry.get("name") and entry.get("id"):
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


def channel_name_violations(mapping, live):
    """Every mapped channel outside the `proj-` prefix, read against the live names Slack lists (#1615).

    `mapping` is the registry (repository -> {name, id}); `live` is the channel list the caller read from Slack, each
    entry carrying its `id` and `name`. A mapped channel is matched by its id, never by its name: a channel renamed on
    the Slack side keeps its id and its history, and only its live name is reported. A name the registry itself carries
    outside the prefix is reported too, so the file a reader sees stays honest. One line per violation; an empty list
    means every mapped channel complies.
    """
    names = {c.get("id"): (c.get("name") or "") for c in live or () if c.get("id")}
    lines = []
    for repo in sorted(mapping or {}):
        entry = mapping[repo] or {}
        cid = entry.get("id")
        mapped_name = (entry.get("name") or "")
        if not cid:
            lines.append(f"voiced: {repo} maps no channel id")
            continue
        live_name = names.get(cid)
        if live_name is None:
            lines.append(f"voiced: {repo} maps #{mapped_name} ({cid}), which Slack does not list")
            continue
        live_bare = not live_name.startswith(PROJ_PREFIX)
        registry_bare = bool(mapped_name) and not mapped_name.startswith(PROJ_PREFIX)
        if live_bare:
            also = "" if not registry_bare or mapped_name == live_name else f"; the registry names #{mapped_name}"
            lines.append(f"voiced: {repo} maps #{live_name} ({cid}), outside the {PROJ_PREFIX} prefix{also}")
        elif registry_bare:
            lines.append(f"voiced: {repo} names #{mapped_name} in the registry, outside the {PROJ_PREFIX} prefix")
    return lines


def check_channel_names(mapping, client):
    """The `proj-` rule read from Slack's own list, so a rename on the Slack side is caught and never only assumed.

    The client is the one source of live channel names, handed in by the caller; no token is read here. A listing Slack
    refuses yields one line naming the error, never an empty pass over a list the check never read.
    """
    listing = client.api("conversations.list?types=public_channel,private_channel&limit=1000")
    if not listing.get("ok"):
        return [f"voiced: cannot list channels to check their names: {listing.get('error')}"]
    return channel_name_violations(mapping, listing.get("channels") or [])


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

    def __init__(self, state, owner, transcriber, downloader, log=None, client=None, projects=None,
                 synthesizer=None, uploader=None, configs=None):
        self.state = state
        self.owner = owner
        self.transcriber = transcriber
        self.downloader = downloader
        self.log = log or (lambda line: print(line, file=sys.stderr))
        self.client = client
        self.projects = projects
        self.configs = configs     # the project list whose dev tips carry a Slack file; None leaves the revisions alone
        self.synthesizer = synthesizer
        self.uploader = uploader
        self.failed = {}     # digest -> consecutive refused deliveries, so a permanently refused line never spins

    def refresh_slack(self, projects=None):
        """Read each registered project's `slack.yaml` from its dev tip and switch to it only when it is valid.

        A tip that did not move is not read again. A valid revision replaces the active one. A revision that breaks a
        rule, or a tip that cannot be read, keeps the previous revision active, so an invalid edit never changes a live
        channel. The owner gets one DM naming the failing key, and every write is atomic (ADR-0030).
        """
        if projects is None:
            projects = list(self.configs() or []) if self.configs is not None else []
        local_docs, local_defects = self.local_documents(projects)
        results = []
        for entry in projects:
            repo = (entry.get("repo") or "").strip()
            if not repo:
                continue
            results.append(self.refresh_one(repo, entry, local_docs, local_defects))
        return results

    def local_documents(self, projects):
        """Each project's local Slack file read once: `(file name, document)` by repository, and the defects of reading it.

        The local file stays outside the repository, so its defect is one that names the file a person edits. A missing
        local file is no defect, and the committed revision is validated without it.
        """
        docs, defects = {}, {}
        for entry in projects:
            repo = (entry.get("repo") or "").strip()
            path = (entry.get("local") or "").strip()
            if not repo or not path:
                continue
            out = []
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except FileNotFoundError:
                continue
            except OSError as x:
                out.append(slackpolicy.Defect(os.path.basename(path), "(root)",
                                              f"cannot read the file: {x.strerror or x}"))
                docs[repo] = (os.path.basename(path), {})
            else:
                docs[repo] = (os.path.basename(path), slackpolicy.read_document(text, os.path.basename(path), out) or {})
            defects[repo] = out
        return docs, defects

    def refresh_one(self, repo, entry, local_docs, local_defects):
        """One project's revision: read, validate with its local file, then save it or keep the previous one."""
        active = self.state.active_revision(repo)
        try:
            sha, text = dev_tip((entry.get("checkout") or "").strip())
        except TipUnreadable as x:
            return self.refuse_revision(repo, active, [str(x)])
        if active is not None and active.get("sha") == sha:
            return {"repo": repo, "sha": sha, "changed": False, "active": active}
        out = []
        doc = slackpolicy.read_document(text, slackpolicy.COMMITTED.as_posix(), out)
        name, local = local_docs.get(repo, (None, None))
        out.extend(local_defects.get(repo) or [])
        others = [(other_name, other_doc) for other_repo, (other_name, other_doc) in local_docs.items()
                  if other_repo != repo]
        if doc is not None:
            out.extend(slackpolicy.validate(doc, local or {}, others, local_name=name or slackpolicy.LOCAL_SUFFIX))
        if out:
            return self.refuse_revision(repo, active, [str(defect) for defect in out])
        record = {"repo": repo, "sha": sha, "revision": doc}
        try:
            self.state.save_active_revision(repo, record)
        except OSError as x:
            self.log(f"voiced: {repo}: cannot write the Slack revision: {type(x).__name__}: {x.strerror or x}")
            return {"repo": repo, "sha": sha, "changed": False, "active": active}
        self.log(f"voiced: {repo}: slack.yaml revision {sha[:12]} is active")
        return {"repo": repo, "sha": sha, "changed": True, "active": record}

    def refuse_revision(self, repo, active, reasons):
        """Keep the active revision, log each reason, and send the owner one notice that names the failing key."""
        kept = (active or {}).get("sha")
        stays = f"revision {kept[:12]} stays active" if kept else "no revision is active yet"
        self.log(f"voiced: {repo}: slack.yaml refused; {stays}")
        for reason in reasons:
            self.log(f"voiced: {repo}: {reason}")
        self.notice_owner(repo, reasons, active)
        return {"repo": repo, "changed": False, "active": active, "defects": reasons}

    def notice_owner(self, repo, reasons, active):
        """One DM to the owner about a refused revision; a refused DM is a log line, never a lost notice."""
        if self.client is None or not self.owner:
            return False
        kept = (active or {}).get("sha")
        stays = f"revision {kept[:12]} stays active" if kept else "no revision is active yet"
        text = f"voiced: I refused the slack.yaml of {repo} at its dev tip, so {stays}.\n" + "\n".join(reasons)
        try:
            got = self.client.api("chat.postMessage", {"channel": self.owner, "text": text}, post=True)
        except Exception as x:      # noqa: BLE001 - a Slack error is a log line, never a daemon that stops answering
            self.log(f"voiced: {repo}: the Slack notice was not sent: {type(x).__name__}")
            return False
        if not got.get("ok"):
            self.log(f"voiced: {repo}: the Slack notice was refused: {got.get('error')}")
            return False
        return True

    def sync_projects(self):
        """Give every project the hub lists its channel, once; no client or no list leaves the channels as they are.

        The mapped names are then checked against Slack's own list, so a channel renamed on the Slack side is reported
        as a line, never a rule the daemon only assumes (#1615).
        """
        if self.client is None or self.projects is None:
            return {"channels": self.state.channels(), "refused": []}
        got = project_channels(self.state, self.client, self.projects(), self.log)
        for line in check_channel_names(self.state.channels(), self.client):
            self.log(line)
        return got

    def inbox_path_of(self, event):
        """The inbox one message belongs to: its project's when the channel names one, the DM's for an im, else None."""
        repo = self.state.channel_for(event.get("channel"))
        if repo:
            return self.state.inbox_for(self.state.channels()[repo]["id"])
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

    def answer_failed_clip(self, record):
        """One line under a clip that did not work, in its own channel and thread, naming the failure in one sentence.

        The record is already durable when this runs, so a refused post never loses the failure and never makes the
        clip a second model call on the next start (#1618). The sentence comes from `record['answer']`, which the
        recording path builds with `one_sentence`, so the line is the same shape whatever failed.
        """
        if not record.get("channel"):
            self.log(f"voiced: failure answer not posted, the clip names no channel: {record.get('error')}")
            return
        try:
            self.answer(record, f"Your clip arrived, but it did not work: {record['answer']}")
        except Exception as x:      # noqa: BLE001 - a Slack error is a log line, never a lost record or a lost ack
            self.log(f"voiced: failure answer refused: {type(x).__name__}: {x}")

    def outbox_sources(self):
        """Every outbox the daemon answers from, one `(channel, path)` per entry: each project channel's, then the DM's.

        A project outbox names its channel by the registry, so its session never repeats it; the DM outbox has no
        registry entry, so a line there names its own `channel`.
        """
        sources = []
        for entry in self.state.channels().values():
            if entry.get("id"):
                sources.append((entry["id"], self.state.outbox_for(entry["id"])))
        sources.append((None, self.state.outbox_path))
        return sources

    def post_text(self, channel, thread_ts, text):
        """Post the readable answer in its channel; True and its ts when Slack accepts it, so the clips answer it."""
        if self.client is None:
            return False, None
        payload = {"channel": channel, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        got = self.client.api("chat.postMessage", payload, post=True)
        if not got.get("ok"):
            self.log(f"voiced: answer refused: {got.get('error')}")
            return False, None
        return True, got.get("ts")

    def synth_clip(self, script):
        """One spoken clip's path, or None when the voice refused: a missing voice never fails the answer."""
        try:
            return self.synthesizer(script) or None
        except Exception as x:      # noqa: BLE001 - a refused TTS is a log line, and the text answer stands alone
            # The error type only: a provider message can carry request material, and this log is never redacted.
            self.log(f"voiced: speech skipped: {type(x).__name__}")
            return None

    def post_clip(self, path, channel, thread_ts, title):
        """Upload one spoken clip, then drop its temporary file; False when the upload was refused."""
        try:
            if self.uploader is None:
                return False
            fid, err = self.uploader(path, channel, thread_ts, title)
            if not fid:
                self.log(f"voiced: clip refused: {err}")
                return False
            return True
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    async def deliver(self, record, channel=None, digest=None):
        """Answer one outbox line: the text at once, the first spoken sentence with it, then the rest as one clip.

        The first sentence's synthesis starts together with the text post, so a slow Slack write never holds the voice;
        the rest is synthesized only after the first clip is out, so the owner hears the start without waiting for the
        end. Each call runs in a worker thread, because both the Slack write and Gemini take seconds.

        Returns two answers: whether the text is out, and whether every clip it meant to speak went out. A line whose
        text is already out is spoken again and never posted again, and each clip already out is skipped, so a refused
        stage costs the voice that stage alone.
        """
        text = (record.get("text") or "").strip()
        cid = (record.get("channel") or channel or "").strip()
        if not text or not cid:
            self.log("voiced: outbox line names no channel or no text; nothing posted")
            return False, False
        thread = record.get("thread_ts")
        first, rest = split_sentences((record.get("speech") or text).strip())
        posted = self.state.posted().get(digest) if digest else None
        done = set(posted.get("clips_done") or []) if posted else set()
        # The first sentence is synthesized beside the text post when there is one, so the voice never waits for Slack.
        speaking = asyncio.create_task(asyncio.to_thread(self.synth_clip, first)) \
            if (first and self.synthesizer and "first" not in done) else None
        if posted is None:
            ok, ts = await asyncio.to_thread(self.post_text, cid, thread, text)
            if not ok:
                if speaking is not None:
                    await asyncio.gather(speaking, return_exceptions=True)
                return False, False
            if digest:
                # The text is out before its voice: a crash from here repeats only the voice, never the text.
                self.state.remember_posted(digest, ts)
        else:
            ts = posted.get("ts")
        anchor = thread or ts
        if speaking is not None:
            path = await speaking
            if path:
                if not await asyncio.to_thread(self.post_clip, path, cid, anchor, "the answer's first sentence"):
                    return True, False      # the first clip is refused: the next drain speaks it, then the rest
                if digest:
                    self.state.remember_clip(digest, "first")
        if rest and self.synthesizer and "rest" not in done:
            path = await asyncio.to_thread(self.synth_clip, rest)
            if path:
                if not await asyncio.to_thread(self.post_clip, path, cid, anchor, "the answer"):
                    return True, False
                if digest:
                    self.state.remember_clip(digest, "rest")
        return True, True

    async def drain_outboxes(self):
        """Post every outbox line not yet posted, over every outbox, and return how many answers fully went out.

        The digest ledger is read once and extended as answers go fully out, so one drain posts each text once. The text
        is recorded the moment Slack accepts it, so a refused clip repeats the voice and never the text; a line refused
        `MAX_ATTEMPTS` times in one daemon is left to the next one, never spun at the poll interval.
        """
        delivered = self.state.delivered()
        count = 0
        for channel, path in self.outbox_sources():
            for record in read_lines(path):
                digest = outbox_digest(record)
                if digest in delivered:
                    continue
                fails = self.failed.get(digest, 0)
                if fails >= MAX_DELIVER_ATTEMPTS:
                    continue
                posted, voiced = await self.deliver(record, channel, digest)
                if posted and voiced:
                    self.state.remember_delivered(digest)
                    delivered.add(digest)
                    self.failed.pop(digest, None)
                    count += 1
                    continue
                self.failed[digest] = fails + 1
                if fails + 1 == MAX_DELIVER_ATTEMPTS:
                    self.log(f"voiced: answer refused {MAX_DELIVER_ATTEMPTS} times; the next daemon tries it again")
        return count

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
                record["answer"] = one_sentence(record["error"])
                record["bytes"] = x.size
                self.state.append_other(record)
                self.answer_failed_clip(record)
                self.log(f"voiced: {record['error']} for {mid}")
                return "failed"
            except Exception as x:      # noqa: BLE001 - a clip no model could read is data, never an inbox line
                record["error"] = f"transcription failed: {type(x).__name__}"
                record["answer"] = one_sentence(record["error"])
                self.state.append_other(record)
                self.answer_failed_clip(record)
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


async def watch_outboxes(daemon, stop=None, interval=OUTBOX_POLL):
    """Answer each new outbox line as soon as it appears, until `stop` is set.

    The session appends a line and this is the only reader, so no call from the session is needed. The interval bounds
    how long an answer waits for its post; a failed drain is a log line, never a watcher that stops answering.
    """
    while not (stop is not None and stop.is_set()):
        try:
            await daemon.drain_outboxes()
        except Exception as x:      # noqa: BLE001 - a bad line is a log line, never a daemon that stops answering
            daemon.log(f"voiced: outbox drain failed: {type(x).__name__}: {str(x)[:120]}")
        await asyncio.sleep(interval)


async def watch_configs(daemon, stop=None, interval=CONFIG_POLL):
    """Read each project's dev tip and switch Slack revisions, until `stop` is set.

    A refused revision keeps the active one and is a log line, never a watcher that stops reading.
    """
    while not (stop is not None and stop.is_set()):
        try:
            await asyncio.to_thread(daemon.refresh_slack)
        except Exception as x:      # noqa: BLE001 - one project's failure is a log line, never a daemon that stops
            daemon.log(f"voiced: Slack file refresh failed: {type(x).__name__}: {str(x)[:120]}")
        await asyncio.sleep(interval)


async def serve(daemon, app_token, connect=None, open_connection=None, stop=None):
    """Hear Slack and answer the outboxes at once: the connection loop runs beside two watchers.

    The outbox watcher and the Slack-file watcher are tasks of the same loop, so an answer the session appends is posted
    while the socket is open and a moved dev tip is read while the daemon answers. All stop together, and the watchers
    are cancelled with the connection loop.
    """
    outboxes = asyncio.create_task(watch_outboxes(daemon, stop))
    configs = asyncio.create_task(watch_configs(daemon, stop))
    try:
        await hear(daemon, app_token, connect=connect, open_connection=open_connection, stop=stop)
    finally:
        for task in (outboxes, configs):
            task.cancel()
        await asyncio.gather(outboxes, configs, return_exceptions=True)


async def hear(daemon, app_token, connect=None, open_connection=None, stop=None):
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
    try:
        state = State(state_dir)
    except SchemaRefused as x:
        print(f"voiced: {x}", file=sys.stderr)
        return 1
    owner = owner or state.owner()
    if not owner:
        print("voiced: no owner id: set VOICE_OWNER or write one user id to $VOICE_STATE/owner", file=sys.stderr)
        return 2
    if print_plist:
        sys.stdout.write(plist_text(label=label, python=python, script=os.path.abspath(__file__),
                                    state_dir=state_dir, owner=owner,
                                    checkout=os.path.abspath(os.path.join(HERE, "..", ".."))))
        return 0
    lock = open(os.path.join(state_dir, "voiced.lock"), "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"voiced: another daemon holds {state_dir}; this start stands aside", file=sys.stderr)
        lock.close()
        return 1
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
                    client=SlackClient(bot), projects=hub_repos, configs=hub_projects,
                    synthesizer=lambda script: spoken_clip(key, script),
                    uploader=lambda path, channel, thread_ts, title: upload_clip(bot, path, channel, thread_ts, title))
    try:
        asyncio.run(serve(daemon, app))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
