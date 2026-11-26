"""The `voiced` daemon: one owner DM becomes one transcribed inbox line, and a stranger's message stays data.

usage: voiced.py [--state DIR] [--owner USER_ID] [--print-plist [--label L] [--python PATH] [--profiles DIR]]
       [--print-profiles-default]

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

- the text is posted as the bot at once under the channel persona's `username` (the `chat:write.customize` scope), and
  threaded under `thread_ts` (else under nothing);
- the spoken script (the `speech` field, else the text) is split into sentences, and the first sentence is synthesized
  in parallel with the text post and posted as one clip, so a slow Slack write never holds the voice;
- the rest is synthesized only once the first clip is out, and posted as one clip;
- every clip uses the persona `voice` and `tone` of the channel, and the clip carries no name, so it leaves under the
  app name until #2726 records what an upload does with a `username`.

Before any of it, the daemon checks the line (#2948): it must answer a pending inbox line of its channel and thread
(`slackpolicy.pending_line`), its `text`, `speech` and `persona` are redacted by the shared redaction module and stripped
of every link, and its text may not pass the channel's `answer_max_chars`. A refused line posts nothing and writes one row
to `$VOICE_STATE/audit.jsonl` with its reason class.

An outbox line whose top-level `kind` is exactly `action-request` is an action request, never an answer (#2939); an
answer that mentions the kind in its text is still an answer. The daemon posts no request line: its action pass (`squad/slackactions.py`) runs in the daemon's own process, outside every session and its
sandbox. The pass checks the request against the policy decision, runs the command the project declares, appends one
reply in the thread and one audit line. It reads only the outbox of a channel a project owns, never the DM outbox, and
it runs no request older than a few minutes. The session never runs a command itself.

Each answered message writes one row to `$VOICE_STATE/answer-latency.jsonl`: `id`, `channel`, `question_ts`,
`answer_ts` and `seconds`. Both ts values are Slack's own epoch stamps, so the latency needs no wall clock: the answer's
question is the inbox line whose ts the answer threads under, else the oldest line the ledger does not answer yet.

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
import threading
import time
import urllib.request
import xml.sax.saxutils
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import channels  # noqa: E402  the one Slack call path, so this daemon keeps no second copy of it
import fullturn  # noqa: E402  one owner message as one turn of the project's own session (ADR-0180)
import keychain  # noqa: E402  the one reader for the three voice secrets
import permrelay  # noqa: E402  the permission relay of a full turn: requests to the owner's DM (SPEC 2964)
import release_events as releases  # noqa: E402  the one release-event record the promotion pass and a project emit (#2702)

from squad import redact as shared_redact  # noqa: E402  the one redaction module, so an answer carries no secret (#2948)
from squad import (  # noqa: E402  a release event as a post in each subscribed owned channel (#2703)
    slackactions,  # the action request pass that runs outside every session (#2939)
    slackannounce,
    slackpolicy,  # the one home of the Slack file rules (ADR-0177)
)

DEFAULT_STATE = os.path.expanduser("~/.local/state/agent-squad/voiced")
# The profiles folder a printed plist names when the caller passes none (#2957): the live copy's own, never the deploy
# checkout's, which holds only `example.sh`. install-agents.sh writes the same default into its own agents.
LIVE_PROFILES = os.path.expanduser("~/.claude/skills/squad-goal/scripts/profiles")
ADDRESS = os.path.join(HERE, "..", "squad-goal", "scripts", "address.sh")   # the hub's seam: the one list of its projects
OPERATORS = os.path.join(HERE, "operators.py")   # the supervisor a new inbox line wakes at once (#2925)
CHANNEL_MAX = 80     # Slack's channel-name cap
PROJ_PREFIX = "proj-"   # every project channel name starts with it, so a reader tells a project channel by its name
# The probable cause a mapped channel Slack does not list leaves open (#2955). A listing answers it by an id alone:
# a channel the bot left, and one that no longer exists, both read as absent from the public list.
NOT_LISTED_CAUSE = "the bot left the channel, or the channel does not exist"
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"
AUDIO_TYPES = {"m4a", "mp4", "webm", "ogg", "mp3", "wav", "aac", "flac"}
TIMEOUT = 60
MAX_CLIP_BYTES = 20 * 1024 * 1024     # Gemini's inline_data request cap; a larger clip is a record, never a guess
CHUNK = 1 << 16
DEV_REF = "refs/remotes/origin/dev"   # the tip a project's Slack file is read from, never the working tree
CONFIG_POLL = 60.0                    # how long a moved dev tip waits for its revalidation
VOICE = "Charon"                       # the owner's chosen Gemini TTS voice
OUTBOX_POLL = 0.2                     # how long an answer waits for its post, well inside the 3 s target
RELEASE_POLL = 30.0                   # how long a release event waits for its notice in the subscribed channels
ACTION_POLL = 1.0                     # how long an action request waits for its run, well inside the answer target
PERMISSION_POLL = 1.0                 # how long a bounded session's permission request waits for its post (#2972)
MAX_DELIVER_ATTEMPTS = 3              # a refused answer is retried a few times, never spun at the poll interval
ACTION_HEARTBEAT = 60                 # empty action passes between two heartbeat log lines (#2939)
BROKEN_LINE_BYTES = 200                # bytes of a line that is no JSON the drain keeps in its skip record
GUARD_WINDOW = 60.0                   # the guard counts one sender's messages in this window (#2947)
GUARD_REPLY_WINDOW = 600.0            # a refused sender hears one refusal reply per channel in ten minutes (#2947)
AUDIT_TEXT_MAX = 80                   # the audit keeps at most this many characters of one message's text (#2947)
SENTENCE = re.compile(r"[^.!?]+(?:[.!?]+|$)")
LINK = re.compile(r"https?://\S+")      # a whole URL, removed from an answer before the post (#2948)
SCHEMA_FILE = "schema.json"
SCHEMA_VERSION = 2                   # id-keyed queues; 1 is the name-keyed layout an older build wrote
SUPPORTED_SCHEMA = (1, SCHEMA_VERSION)
DM = "dm"                            # the DM is one scope beside the channels, so its queues carry a key too
_UNRESOLVED = object()               # no workspace or owner passed in: the callee resolves it itself
SLACK_REGISTRY = "slack-saved.json"  # the hub's saved-sha registry, in the loop state directory (#2694)
SLACK_REGISTRY_ENV = "SG_SLACK_REGISTRY"   # an explicit registry path, for a machine that moves it


class SchemaRefused(Exception):
    """A state directory whose schema version this build does not know. The message names the version and the file."""


class ClipTooLarge(Exception):
    """A clip past what one inline Gemini request can carry. The size travels with the error, never the bytes."""

    def __init__(self, size):
        super().__init__(f"clip too large: {size} bytes over the {MAX_CLIP_BYTES} byte cap")
        self.size = size


FULL_FAILED = "Your message did not reach the project session."
FULL_REASONS = {"deadline": "Your message stopped at the project session deadline.",
                "spend_cap": "Your message stopped at the project session spend cap.",
                "stopped": "Your message stopped when you asked.",
                "session_held": "The project's session is open in a terminal. Close it and send the message again."}
STOP_TEXT = "!stop"


MENTION = re.compile(r"^(?:\s*<@[^>]+>)+")   # one or more leading Slack bot mentions, as a DM or a channel adds


def is_stop(text):
    """Whether one message asks to end its project's running turn (#2968).

    Only the message `!stop` itself stops, after the leading bot mentions Slack puts on a DM or a channel message are
    dropped: `@the-bot !stop` stops too, so a stop never becomes a turn by its mention prefix. Any other word beside
    `!stop` keeps the message an ordinary turn."""
    return MENTION.sub("", str(text or "")).strip() == STOP_TEXT


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
        # The local file sits beside the hub's profile, so the `--list` slug names it first. `SG_PROJECT` and the repo
        # leaf are the fallback, and a generic `slack.local.yaml` the last one. A candidate the profile does not hold
        # never wins, so a missing slug file cannot hide the project's own file.
        local = ""
        for candidate in (slug, name):
            path = os.path.join(profiles, f"{candidate}.slack.local.yaml") if candidate else ""
            if path and os.path.isfile(path):
                local = path
                break
        else:
            generic = os.path.join(profiles, slackpolicy.LOCAL_SUFFIX)
            if os.path.isfile(generic):
                local = generic
        entry = {"repo": repo, "checkout": (ctx.get("SG_CHECKOUT") or "").strip(), "local": local}
        state = (ctx.get("SG_STATE") or "").strip()
        if state:      # the loop state directory holds the hub's saved-sha registry (#2695)
            entry["state"] = state
        found.append(entry)
    return found


def hub_repos(script=None):
    """Every repository the hub lists: the `SG_REPO` of each project `hub_projects` names."""
    return [entry["repo"] for entry in hub_projects(script)]


def slack_registry_path(env=None):
    """The hub's saved-sha registry: one file in the loop state directory, outside every repository (#2694).

    Saving in the hub is the owner's approval (SPEC 2682 story 66). The registry stays off every checkout, so no
    commit under review can list its own sha: an ordinary code change creates no authority. `SG_SLACK_REGISTRY` names
    the file outright, else the loop state directory does; `''` means no registry path is known.
    """
    env = os.environ if env is None else env
    named = (env.get(SLACK_REGISTRY_ENV) or "").strip()
    if named:
        return named
    state = (env.get("SG_STATE") or "").strip()
    return os.path.join(state, SLACK_REGISTRY) if state else ""


def hub_registries(projects=None, env=None):
    """repository -> the registry path of that project's own profile, read from the hub seam's entries (#2961).

    Every profile's hub writes its saves to its own state directory, so one registry per project is the only rule that
    lets a save for one project reach the daemon. `SG_SLACK_REGISTRY` names one file for every project, so it
    short-circuits to that one shared registry, whatever state directory a project names. A project whose profile names
    no state directory is absent, so the saved-sha check stays out of force for it alone.
    """
    env = os.environ if env is None else env
    entries = hub_projects() if projects is None else projects
    repos = [str((entry or {}).get("repo") or "").strip() for entry in entries]
    named = (env.get(SLACK_REGISTRY_ENV) or "").strip()
    if named:      # one named file is one registry for every project, so it never resolves per entry
        return {repo: named for repo in repos if repo}
    out = {}
    for repo, entry in zip(repos, entries, strict=True):
        path = project_registry(entry, env) if repo else ""
        if path:
            out[repo] = path
    return out


def project_registry(entry, env=None):
    """The saved-sha registry of one project: the file in the state directory of that project's own profile (#2961).

    Saving in the hub is the owner's approval (SPEC 2682 story 66), and each profile's hub writes its registry beside its
    own loop state. A project's revision is approved by the registry its own profile wrote, never by another project's.
    `SG_SLACK_REGISTRY` names one file for every project, for a machine that moves it. `''` means this project names no
    registry, so the saved-sha check is out of force for it.
    """
    env = os.environ if env is None else env
    named = (env.get(SLACK_REGISTRY_ENV) or "").strip()
    if named:
        return named
    state = str((entry or {}).get("state") or "").strip()
    return os.path.join(state, SLACK_REGISTRY) if state else ""


def hub_registry_seam(projects=None, env=None):
    """The registry callable `main` hands the daemon, or None when no profile names a registry (#2695, #2961).

    `SG_SLACK_REGISTRY` names one file for every project, so it puts the saved-sha check in force whatever the hub lists.
    Else a project's own state directory puts the check in force for that project alone. A machine that names neither
    keeps the older behaviour with the check out of force, so no channel is refused for want of a save.
    """
    env = os.environ if env is None else env
    if (env.get(SLACK_REGISTRY_ENV) or "").strip():
        return project_registry
    return project_registry if hub_registries(projects, env) else None


def read_registry(path):
    """The hub registry's document, or an empty dict when no path is named or the file is missing or malformed."""
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def listed_revisions(path):
    """The `slack.yaml` shas the hub registry lists, or an empty set when the file is missing or malformed.

    One key per saved file, each value the landed sha of that save. A file this build cannot read lists nothing, so a
    project with no approved revision serves no channel, and a refusal never widens what the daemon serves.
    """
    return {str(entry["sha"]) for entry in read_registry(path).values() if isinstance(entry, dict) and entry.get("sha")}


def registry_owners(path):
    """The owner user id of each workspace the hub registry names, or an empty map when it names none (#2966).

    The owner id is the one the hub route writes, and the daemon reads no other source for it."""
    owners = read_registry(path).get("owners")
    if not isinstance(owners, dict):
        return {}
    return {str(workspace): str(user) for workspace, user in owners.items() if user}


def registry_grants(path):
    """The `{workspace, user}` grants the hub registry holds, or an empty list when it holds none (#2966)."""
    grants = read_registry(path).get("grants")
    if not isinstance(grants, list):
        return []
    return [grant for grant in grants if isinstance(grant, dict)]


class TipUnreadable(Exception):
    """A project's dev tip or its Slack file cannot be read: the daemon keeps the revision it holds and warns the owner."""


class Start:
    """One detached `--ensure` pass: its process, the inbox line that paid for it, and the outcome once it ends.

    `outcome` is None while the pass runs and then `{"started", "session", "code", "reason", "verdict"}`. The watcher
    thread fills it, and `wait` hands it to a caller, so the daemon tells a start that answers the line from a pass
    that started nothing (#2925).
    """

    def __init__(self, proc, repo, line=None):
        self.proc = proc
        self.repo = repo
        self.line = line
        self.outcome = None
        self._done = threading.Event()

    def finished(self, outcome):
        self.outcome = outcome
        self._done.set()

    def wait(self, timeout=None):
        """The pass's outcome, or None when it has not ended within `timeout`. Never blocks the event loop: a caller
        that reads it runs off the loop."""
        self._done.wait(timeout)
        return self.outcome


def pass_output(proc, stream=None):
    """Everything one detached pass printed. `stream` is the file the Popen wrote to; a Popen given a file object puts
    nothing on `proc.stdout`, so the caller hands its own stream too. A fixture's own StringIO on the process answers as
    well. One stream handed for both stdout and stderr is read once, and it is closed after the read."""
    streams = [stream] if stream is not None else []
    for name in ("stderr", "stdout"):
        got = getattr(proc, name, None)
        if got is not None:
            streams.append(got)
    text, seen = [], set()
    for s in streams:
        if id(s) in seen:
            continue
        seen.add(id(s))
        try:
            s.seek(0)
            part = s.read()
        except (OSError, ValueError):
            part = ""
        finally:
            try:
                s.close()
            except OSError:
                pass
        if isinstance(part, bytes):
            part = part.decode("utf-8", "replace")
        if part:
            text.append(part)
    return "\n".join(text)


def verdict_session(row):
    """The session id one `--ensure` verdict line names, or "" when the row is no verdict (#2925).

    A verdict is `operators: ensure <project> started <id>` or `operators: ensure <project> live <id>`, and nothing
    else: the failure line carries words after `started`, so it never reads as a start."""
    words = row.split()
    if len(words) == 5 and words[0] == "operators:" and words[1] == "ensure" and words[3] in ("started", "live"):
        return words[4]
    return ""


def watch_operator_start(proc, repo, log, line=None, finished=None, stream=None):
    """Read one detached supervisor pass and report whether it left the inbox line answered (#2925).

    The pass's `--ensure` prints one verdict line on stdout: `operators: ensure <project> started <id>`, or `... live
    <id>` when a session was already there. Any other outcome, a non-zero exit or a zero exit with no verdict, is a
    start that left the line waiting: the log names that inbox line, so the fault reaches the message that paid for the
    pass. `stream` is the file the pass's output was written to. `finished`, if given, receives the outcome. Runs in its
    own thread: the pass may make several bounded CLI calls, and the event loop never waits for them.
    """
    try:
        code = proc.wait()
    except (OSError, ValueError):
        code = None
    rows = pass_output(proc, stream).strip().splitlines()
    verdict, session = "", ""
    for row in rows:
        named = verdict_session(row)
        if named:
            verdict, session = row, named
    outcome = {"started": bool(verdict), "session": session,
               "code": code, "reason": rows[-1] if rows else "", "verdict": verdict}
    if not outcome["started"]:
        where = f"inbox line {line} for {repo}" if line else f"the operator start for {repo}"
        log(f"voiced: {where} got no operator session: {outcome['reason'] or 'no reason'}")
    if finished is not None:
        finished(outcome)
    return outcome


def start_operator(state_dir, repo, log=None, line=None):
    """Start one project's operator session now when none is live (#2925).

    A message must never wait for the five-minute supervisor pass. operators.py takes its own lock, so a pass in flight
    answers first and a second start stands aside. A start that cannot run is one log line and False, never a lost
    message: the line stays in the inbox and the next pass takes it. Otherwise the return is a `Start` handle whose
    `wait` gives the pass's outcome, so a caller never reads a pass that started nothing as a success.
    """
    log = log or (lambda line: None)
    err = tempfile.TemporaryFile(mode="w+b")
    try:
        proc = subprocess.Popen([sys.executable, OPERATORS, "--state", state_dir, "--ensure", repo],
                                stdout=err, stderr=err, stdin=subprocess.DEVNULL,
                                start_new_session=True, close_fds=True)
    except OSError as x:
        err.close()
        log(f"voiced: the operator start for {repo} failed: {x.strerror or x}")
        return False
    start = Start(proc, repo, line)
    threading.Thread(target=watch_operator_start, args=(proc, repo, log),
                     kwargs={"line": line, "finished": start.finished, "stream": err}, daemon=True).start()
    return start


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


def _rows(value):
    """The mappings of a list, so a file whose shape is wrong still yields the rows it can name."""
    return [row for row in (value or []) if isinstance(row, dict)] if isinstance(value, list) else []


def listed_revision_at(checkout, listed, name=None):
    """The newest revision the hub lists that this checkout holds, as `(sha, text)`, or None (#2934).

    The hub lists the sha of its save. A later commit moves the dev tip, and a stale `origin/dev` ref lags behind it, so
    the tip is often no listed sha. The listed sha is the approval, so the file is read at that revision itself: the
    channel keeps the language and the persona its save declared.
    """
    name = name or slackpolicy.COMMITTED.as_posix()
    best = None
    for sha in sorted(listed or ()):
        if git_output(checkout, "cat-file", "-e", f"{sha}:{name}") is None:
            continue
        stamp = (git_output(checkout, "show", "-s", "--format=%ct", sha) or "").strip()
        try:
            when = int(stamp or 0)
        except ValueError:
            when = 0
        if best is None or when > best[0]:
            best = (when, sha)
    if best is None:
        return None
    text = git_output(checkout, "show", f"{best[1]}:{name}")
    return None if text is None else (best[1], text)


def project_documents_at_start(projects, listed=None):
    """repo -> the Slack document at each project's dev tip, for the app set this start opens sockets for.

    A tip that cannot be read, or a revision that breaks a rule, contributes nothing: the refresh that follows reports
    it, and this start opens only the sockets it can justify. `listed` maps each project to the shas its own registry
    lists (#2961): a tip that project's registry does not list contributes nothing, so no socket opens for a revision
    the owner approved nothing of (#2695).
    """
    docs = {}
    listed = listed or {}
    for entry in projects or ():
        repo = str((entry or {}).get("repo") or "").strip()
        if not repo:
            continue
        try:
            sha, text = dev_tip((entry.get("checkout") or "").strip())
        except TipUnreadable:
            continue
        if repo in listed and sha not in listed[repo]:
            continue
        out = []
        doc = slackpolicy.read_document(text, slackpolicy.COMMITTED.as_posix(), out)
        if doc is not None and not out and not slackpolicy.validate(doc, {}):
            docs[repo] = doc
    return docs


def project_documents(state, projects, listed=None):
    """The active revision of each project: repository -> document; a project with no active revision contributes none.

    `listed` maps each project to the shas its own registry lists (#2961): an active record whose sha that registry does
    not list contributes none, because a revision no save approved is no authority, whatever wrote the record (#2695).
    """
    docs = {}
    listed = listed or {}
    for entry in projects or ():
        repo = str((entry or {}).get("repo") or "").strip()
        if not repo:
            continue
        record = state.active_revision(repo) or {}
        if repo in listed and record.get("sha") not in listed[repo]:
            continue
        revision = record.get("revision")
        if isinstance(revision, dict):
            docs[repo] = revision
    return docs


def effective_documents(state, projects, read_start=None, listed=None):
    """Each project's active revision, or its dev-tip document while it has none: repository -> document.

    The dev tip answers on a fresh start, before the first revision is promoted, so a channel claim is refused from
    the first poll. A tip is read only for a project with no active revision, so a steady daemon reads no extra tip.
    `listed` maps each project to the shas its own registry lists (#2961): a document that project's registry lists no
    save of contributes none, so a project the owner approved nothing for serves no channel (#2695).
    """
    active = project_documents(state, projects, listed=listed)
    missing = []
    for entry in projects or ():
        repo = str((entry or {}).get("repo") or "").strip()
        if repo and repo not in active:
            missing.append(entry)
    read = read_start or (lambda entries: project_documents_at_start(entries, listed=listed))
    docs = dict(read(missing))
    docs.update(active)
    return docs


def channel_apps(documents):
    """channel id -> the app id its workspace names, read from the same documents the revision check reads."""
    apps = {}
    for repo in sorted(documents or {}):
        for ws in _rows((documents[repo] or {}).get("workspaces")):
            app_id = str(ws.get("app") or "").strip()
            for channel in _rows(ws.get("channels")):
                cid = str(channel.get("id") or "").strip()
                if cid and app_id:
                    apps.setdefault(cid, app_id)
    return apps


def app_keychains(documents):
    """app id -> {"bot", "app"}: the Keychain entry names of each app an owned workspace names.

    One entry per app id, so two projects that name one app yield one entry and one socket. The app id keys the entry
    and the Keychain entry names are its values, so an id never becomes a Keychain name.
    """
    apps = {}
    for repo in sorted(documents or {}):
        doc = documents[repo] or {}
        declared = {str(app.get("id")): app for app in _rows(doc.get("apps")) if app.get("id")}
        for ws in _rows(doc.get("workspaces")):
            if str(ws.get("class") or "") != "owned":
                continue
            app_id = str(ws.get("app") or "").strip()
            if not app_id or app_id in apps:
                continue
            entry = (declared.get(app_id) or {}).get("keychain")
            entry = entry if isinstance(entry, dict) else {}
            apps[app_id] = {"bot": str(entry.get("bot") or "").strip(),
                            "app": str(entry.get("app") or "").strip()}
    return apps


def app_tokens(specs, read=None):
    """app id -> {"bot": token, "app": token}, each read from the Keychain entry its name points to.

    The file holds only the entry names, so a token value never enters it. `read` is the one Keychain reader, injected
    so a test proves the names that are read.
    """
    read = read or keychain.read_secret
    return {app_id: {"bot": read(spec["bot"]), "app": read(spec["app"])} for app_id, spec in (specs or {}).items()}


def channel_claims(documents):
    """channel id -> the projects that name it, in sorted project order."""
    claims = {}
    for repo in sorted(documents or {}):
        for ws in _rows((documents[repo] or {}).get("workspaces")):
            for channel in _rows(ws.get("channels")):
                cid = str(channel.get("id") or "").strip()
                if cid:
                    claims.setdefault(cid, []).append(repo)
    return claims


def channel_claim_refusals(documents):
    """One line per channel id that two or more projects claim; the line names both projects and the channel id."""
    claims = channel_claims(documents)
    lines = []
    for cid in sorted(claims):
        repos = claims[cid]
        if len(repos) > 1:
            lines.append(f"voiced: {repos[0]} and {repos[1]} claim the channel {cid}; refused")
    return lines


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


def revision_channel_ids(revision):
    """Every channel id one Slack revision names, across its workspaces (#2925)."""
    ids = []
    for workspace in (revision or {}).get("workspaces") or []:
        for row in (workspace or {}).get("channels") or [] if isinstance(workspace, dict) else []:
            cid = str((row or {}).get("id") or "") if isinstance(row, dict) else ""
            if cid and cid not in ids:
                ids.append(cid)
    return ids


def slack_record_path(state_dir, repo):
    """The file one project's active Slack revision lives in, named by the repository it belongs to.

    The name is a digest of the full repository name, so two distinct repositories never share one record. A name
    built from the repository's own characters would collide (`a/b` and `a-b`) and hand one project another's revision.
    """
    digest = hashlib.sha256((repo or "").strip().encode("utf-8")).hexdigest()[:16]
    return os.path.join(state_dir, "slack", digest + ".json")


def scope_identity(state_dir, scope):
    """The language and persona one scope answers with, from the active revision of the project that owns it.

    The registry names the repository of each channel id, and the active revision sits in the digest-named file beside
    it. A scope no project owns (the DM), a project with no active revision and a registry that cannot be read keep the
    default language and no persona, so the operator session has an instruction before the first revision loads.
    """
    try:
        with open(os.path.join(state_dir, "channels.json"), encoding="utf-8") as f:
            channels = json.load(f)
    except (OSError, ValueError):
        return slackpolicy.DEFAULT_LANGUAGE, None
    if not isinstance(channels, dict):
        return slackpolicy.DEFAULT_LANGUAGE, None
    def record_of(name):
        try:
            with open(slack_record_path(state_dir, name), encoding="utf-8") as f:
                got = json.load(f)
        except (OSError, ValueError):
            return None
        return got if isinstance(got, dict) and got.get("revision") else None

    repo = next((name for name, entry in channels.items()
                 if isinstance(entry, dict) and str(entry.get("id")) == str(scope)), None)
    record = record_of(repo) if repo else None
    if not repo:
        # A channel the registry does not name belongs to the project whose active revision names it (#2925).
        for name in channels:
            got = record_of(name)
            if got and str(scope) in revision_channel_ids(got["revision"]):
                record = got
                break
    if not record:
        return slackpolicy.DEFAULT_LANGUAGE, None
    return slackpolicy.identity(record["revision"], record.get("local") or {}, scope)


class State:
    """The daemon's state in one directory: the inbox, the record of other users, the seen ids and the owner id."""

    def __init__(self, directory):
        self.dir = directory
        self.version = read_schema(directory)      # before any write: an unknown version stops the start here
        os.makedirs(directory, exist_ok=True)
        self.other_path = os.path.join(directory, "other.jsonl")
        self.directory = directory
        self.seen_path = os.path.join(directory, "seen.jsonl")
        self.owner_path = os.path.join(directory, "owner")
        self.channels_path = os.path.join(directory, "channels.json")
        self.schema_path = os.path.join(directory, SCHEMA_FILE)
        self.inboxes_dir = os.path.join(directory, "inboxes")
        self.outboxes_dir = os.path.join(directory, "outboxes")
        self.delivered_path = os.path.join(directory, "outbox-seen.jsonl")
        self.posted_path = os.path.join(directory, "outbox-posted.jsonl")
        self.latency_path = os.path.join(directory, "answer-latency.jsonl")
        self.announced_path = os.path.join(directory, "announced.jsonl")
        self.audit_path = os.path.join(directory, "audit.jsonl")
        self.release_events_path = os.path.join(directory, releases.RECORD)
        self.slack_dir = os.path.join(directory, "slack")
        self._seen = None
        self._channels = None
        self._registry_read = False
        self._delivered = None
        self._posted = None
        self._latencies = None
        self._announced = None
        if self.version < SCHEMA_VERSION:
            if self.migrate():     # a name file no registry entry can move yet holds the version, so a later start retries
                self._stamp_schema()

    def dm_channel(self):
        """The pinned owner DM channel, or an empty string while no DM is pinned (#2949)."""
        from operators import dm_record_path
        try:
            with open(dm_record_path(self.dir), encoding="utf-8") as f:
                return str((json.load(f) or {}).get("dm") or "")
        except (OSError, ValueError):
            return ""

    def pin_dm(self, channel):
        """Pin the owner's DM channel (#2949): the one channel a DM answer may carry. The daemon sees the DM's own
        channel in the owner's event, and a first pin wins, so a later event or an inbox line never moves it."""
        if not channel:
            return
        # One helper names the pinned record (#2949), so the writer, the reader and the refusal spell one path. The
        # import is local because `operators` imports this module at load time.
        from operators import dm_record_path
        path = dm_record_path(self.dir)
        folder = os.path.dirname(path)
        try:
            with open(path, encoding="utf-8") as f:
                if (json.load(f) or {}).get("dm"):
                    return
        except (OSError, ValueError):
            pass
        os.makedirs(folder, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"dm": str(channel)}, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

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
        return slack_record_path(self.dir, repo)

    def refusal_path(self, repo):
        """The file that records the last refused revision of one project, beside its active revision."""
        return self.slack_path(repo)[: -len(".json")] + ".refused.json"

    def _legacy_slack_path(self, repo):
        """The sanitized name an older build gave one project's active revision, before the digest name."""
        leaf = re.sub(r"[^A-Za-z0-9_.-]", "-", (repo or "").strip()) or "project"
        return os.path.join(self.slack_dir, leaf + ".json")

    @staticmethod
    def _read_record(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _adopt_legacy(self, repo):
        """Move one record an older build wrote under the sanitized name to this build's digest name, once.

        A sanitized name collided (`a/b` and `a-b`), so the record is adopted only when it names this repository. A
        collision that names another repository is left where it is, never handed to the wrong project.
        """
        old = self._legacy_slack_path(repo)
        if os.path.abspath(old) == os.path.abspath(self.slack_path(repo)):
            return None
        data = self._read_record(old)
        if data is None or data.get("repo") != repo:
            return None
        try:
            self._atomic_json(self.slack_path(repo), data)
            os.remove(old)
        except OSError:
            return data     # the record is still valid for this read even when the move cannot be written
        return data

    def active_revision(self, repo):
        """The Slack revision this daemon accepted for one project, or None. The file survives a restart, so a refused
        revision keeps the previous one active across one (#2685). A record an older build wrote is adopted once."""
        data = self._read_record(self.slack_path(repo))
        if data is None:
            data = self._adopt_legacy(repo)
        return data if data and data.get("sha") else None

    def save_active_revision(self, repo, record):
        """The active revision as one atomic write (ADR-0030): a crash leaves the previous file, never half of a new one."""
        self._atomic_json(self.slack_path(repo), record)

    def last_refusal(self, repo):
        """The `(sha, reasons)` of the last refused revision this daemon told the owner about, or None.

        The record is what stops an unchanged refusal from becoming one DM per poll: the daemon compares a new refusal
        with it and stays silent when they match.
        """
        try:
            with open(self.refusal_path(repo), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def save_refusal(self, repo, record):
        """Record a refused revision as one atomic write, so a crash leaves the previous record whole (ADR-0030)."""
        self._atomic_json(self.refusal_path(repo), record)

    def clear_refusal(self, repo):
        """Forget the last refused revision after a valid one, so a returning refusal warns the owner again."""
        try:
            os.remove(self.refusal_path(repo))
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _atomic_json(self, path, record):
        """Write one JSON record to its path atomically (ADR-0030): fsync the file, replace it, then fsync the folder.

        The folder sync makes the new name durable, so a crash right after the replace cannot bring the old file back.
        """
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        self._sync_dir(os.path.dirname(path))

    @staticmethod
    def _sync_dir(directory):
        """Persist one folder entry after a replace; a filesystem that refuses the sync is no write failure."""
        try:
            fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

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
        """The repository one project channel belongs to, or None for the DM and any channel no project owns.

        The registry names the channel the daemon created for each project, and a project's active revision may name
        more channels of its own: each of them belongs to that project too, so its message reaches its own inbox (#2925).
        """
        if not channel_id:
            return None
        for repo, entry in self.channels().items():
            if entry.get("id") == channel_id:
                return repo
        for repo in self.channels():
            active = self.active_revision(repo)
            if active and str(channel_id) in revision_channel_ids(active.get("revision")):
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

    def latencies(self):
        """Every recorded answer latency, oldest first: one row per answered message, kept as its Slack ts pair (#2940)."""
        if self._latencies is None:
            self._latencies = read_lines(self.latency_path)
        return self._latencies

    def remember_latency(self, record):
        """One durable latency row, written at once: the Slack ts of the question and the ts of its answer's post."""
        self._append(self.latency_path, record)
        self.latencies().append(record)

    def announced(self):
        """Every `(event key, event id, channel)` notice already posted, so a restart posts no release notice a second
        time, and one key that two events share is two notices, because the event id is part of the mark."""
        if self._announced is None:
            self._announced = {f"{r['key']}|{r['id']}|{r['channel']}" for r in read_lines(self.announced_path)
                               if r.get("key") and r.get("id") and r.get("channel")}
        return self._announced

    def remember_announced(self, key, event_id, channel):
        """One durable mark of a release notice already posted, written at once: the ledger is the one place a restart
        reads the mark from. The event id is part of it, so a later event that reuses a key is a second notice."""
        self._append(self.announced_path, {"key": key, "id": event_id, "channel": channel})
        self.announced().add(f"{key}|{event_id}|{channel}")

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

    def remember_message(self, message_id):
        self._remember(message_id)

    def append_inbox(self, record, path=None):
        self._append(path or self.inbox_path, record)
        self._remember(record["id"])

    def append_other(self, record):
        self._append(self.other_path, record)
        self._remember(record["id"])

    def append_audit(self, record):
        """One audit line per message, with no secret in it (#2947). The audit is not the seen ledger, so it remembers
        no id: a message already recorded stays recorded when the audit line is the only write."""
        self._append(self.audit_path, record)


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


def names_request_kind(text):
    """True when one outbox line that is not JSON names the reserved request kind as its own top-level `kind` key. Only
    a line the parse cannot read reaches this test: a line that parses is read by `slackactions.is_request`, so an
    answer that mentions the kind in plain text is posted, never skipped. A kind inside a string value of the line is a
    mention too, so the line is no request and the drain records it as not JSON.

    The line is walked with a small scanner, because no parse reads it: a string is skipped whole, braces follow the
    depth, and the kind counts only as a key of the object's own top level."""
    depth = 0
    i = 0
    size = len(text)
    wanted = '"' + slackactions.KIND + '"'
    while i < size:
        char = text[i]
        if char == '"':
            j = i + 1
            while j < size:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            if depth == 1 and text[i + 1:j] == "kind":
                k = j + 1
                while k < size and text[k] in " \t":
                    k += 1
                if k < size and text[k] == ":":
                    k += 1
                    while k < size and text[k] in " \t":
                        k += 1
                    if text.startswith(wanted, k):
                        return True
            i = j + 1
            continue
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
        i += 1
    return False


def request_source(text):
    """The source of one request line, read from its raw text, or a word that names why it cannot be read. The drain logs
    it for a line it skips, so the log names the message the request answers."""
    try:
        record = json.loads(text)
    except ValueError:
        return "unreadable"
    if not isinstance(record, dict):
        return "unreadable"
    return str(record.get("source") or "unknown")


def read_outbox_answers(path):
    """The answer lines of one outbox, the raw lines that are action requests, and the raw lines that are no JSON, as
    `(answers, requests, broken)`.

    A line that parses is a request only when `slackactions.is_request` reads the reserved kind on it, so an answer that
    mentions the kind in plain text is still an answer. A top-level kind test covers only a line the parse cannot read,
    so a malformed request line never reaches the channel either. A half line a crash left behind is no request, so its
    first bytes go to `broken` and the drain records why it is not JSON."""
    answers, requests, broken = [], [], []
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                text = raw.strip()
                if not text:
                    continue
                try:
                    record = json.loads(text)
                except ValueError:
                    if names_request_kind(text):
                        requests.append(text)
                    else:
                        broken.append(text[:BROKEN_LINE_BYTES])
                    continue
                if not isinstance(record, dict):
                    continue
                if slackactions.is_request(record):
                    requests.append(text)
                else:
                    answers.append(record)
    except OSError:
        pass
    return answers, requests, broken


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


def question_line(state, cid, thread):
    """The inbox line one answer answers, as `(id, ts)`, or `(None, None)` when the inbox holds none (#2940).

    The answer's thread ts is the question's Slack ts, so a line whose ts it equals is the question. When the answer
    names no thread, or threads under a ts no inbox line holds, the question is the oldest line of the channel that no
    recorded latency answers yet: a record never blames a newer message for an older answer."""
    records = read_lines(state.inbox_for(cid)) if cid else []
    if thread:
        for record in records:
            if record.get("ts") is not None and str(record.get("ts")) == str(thread):
                return record.get("id"), record.get("ts")
    answered = {row.get("id") for row in state.latencies() if row.get("id")}
    for record in records:
        if record.get("ts") is not None and record.get("id") not in answered:
            return record.get("id"), record.get("ts")
    return None, None


def outbox_digest(record):
    """One outbox line's identity: its whole content, so two answers that differ by a word are two answers."""
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode("utf-8")).hexdigest()


# A refused TTS answer as the cause code the log names (#2956): a known phrase of the vendored reader, or a provider
# status such as RESOURCE_EXHAUSTED. The provider's free-text message never becomes a code, because it can carry the
# request material, and this log is never redacted.
SPEECH_CAUSES = {"no audio in the answer": "no_audio", "no TTS model answered": "no_model"}
_SPEECH_CODE = re.compile(r"^[A-Z][A-Z_]{2,39}$")


class SpeechError(RuntimeError):
    """The TTS path refused a clip: `cause` is a short code, never the provider's own message (#2956)."""

    def __init__(self, cause):
        super().__init__(cause)
        self.cause = cause


def speech_cause(err):
    """The safe cause code of a refused TTS answer: a known phrase or a provider status, never its message (#2956).

    A provider message can carry request material, and the daemon's log is never redacted, so the free text stops
    here. An answer that names no known phrase and no status is a plain refusal."""
    text = (err or "").strip()
    if not text:
        return "no_audio"
    if text in SPEECH_CAUSES:
        return SPEECH_CAUSES[text]
    if _SPEECH_CODE.match(text):
        return text.lower()
    return "refused"


def spoken_clip(key, script, voice=VOICE, tone=""):
    """One spoken clip as a WAV path, through the repository's one TTS path (tts.synth); the key never leaves it.

    The voice and the tone are the persona's, so one answer sounds like the channel it answers in. A refusal is a
    `SpeechError` that carries its cause code, and the temporary clip never survives it."""
    import tts  # the vendored TTS beside this file, so the daemon keeps no second copy of it
    fd, path = tempfile.mkstemp(prefix="voiced-", suffix=".wav")
    os.close(fd)
    try:
        got, err = tts.synth(key, script, voice, path, tone=tone)
    except subprocess.TimeoutExpired:
        _drop_clip(path)
        raise SpeechError("timeout") from None
    if not got:
        _drop_clip(path)
        raise SpeechError(speech_cause(err))
    return got


def _drop_clip(path):
    """Remove one temporary clip; a missing file is no error on a failure path."""
    try:
        os.unlink(path)
    except OSError:
        pass


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


def download(url, token, limit=MAX_CLIP_BYTES, context=None):
    """The clip's bytes with the bot token, never the URL's own public form; a clip past the cap is never read whole.

    The call trusts the daemon's one CA bundle (#2933): a python.org build has no default store under launchd."""
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    size = 0
    chunks = []
    with urllib.request.urlopen(request, timeout=TIMEOUT, context=context or daemon_context()) as response:
        while True:
            chunk = response.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise ClipTooLarge(size)
            chunks.append(chunk)
    return b"".join(chunks)


def transcribe(audio, mime, key, context=None):
    """One Gemini transcription of one clip. The key travels in a header, never in the URL or in output.

    The call trusts the daemon's one CA bundle (#2933), as the clip download and the socket do."""
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
    with urllib.request.urlopen(request, timeout=TIMEOUT, context=context or daemon_context()) as response:
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
            lines.append(f"voiced: {repo} maps #{mapped_name} ({cid}), which Slack does not list: {NOT_LISTED_CAUSE}")
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
    refuses yields one line naming the error, never an empty pass over a list the check never read. The listing asks
    for public channels only: an install made before #2816 grants `channels:read` and no `groups:read` (#2767), and that
    listing works on both. The rule reads every project channel because `_ensure_channel` creates each one with `is_private` false;
    a channel converted to private later is Slack's act, and that channel leaves this list.
    """
    listing = client.api("conversations.list?types=public_channel&limit=1000")
    if not listing.get("ok"):
        return [f"voiced: cannot list channels to check their names: {listing.get('error')}"]
    return channel_name_violations(mapping, listing.get("channels") or [])


def _listed_channel(client, name):
    """The channel called `name` in Slack's list: the public type first, then the private one (#2767).

    A `name_taken` error can name a private channel the bot can see, and a `public_channel` listing omits it. The
    second read asks for both types, which Slack refuses when the app grants no `groups:read`; a fresh install then
    searches the public list alone. The return is the channel, or None, and a flag that the private type was refused,
    so the caller names the missing scope only when that refusal is the reason.
    """
    refused = False
    for types in ("public_channel", "public_channel,private_channel"):
        listing = client.api(f"conversations.list?types={types}&limit=1000")
        if not listing.get("ok"):
            refused = refused or "private_channel" in types
            continue
        found = next((c for c in listing.get("channels") or [] if c.get("name") == name), None)
        if found:
            return found, refused
    return None, refused


def _ensure_channel(client, name, channels, log):
    """The channel called `name`: created and joined, else found when Slack already holds the name."""
    for entry in channels.values():
        if entry.get("name") == name and entry.get("id"):
            return dict(entry)
    got = client.api("conversations.create", {"name": name, "is_private": False}, post=True)
    if got.get("ok"):
        channel = got.get("channel") or {}
    elif got.get("error") == "name_taken":
        channel, private_refused = _listed_channel(client, name)
        if not channel:
            hint = "; grant groups:read so a private channel can be found" if private_refused else ""
            log(f"voiced: Slack holds the name {name} and lists no channel for it{hint}")
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
                 synthesizer=None, uploader=None, configs=None,
                 clients=None, bot_tokens=None, downloaders=None, uploaders=None, registry=None, starter=None,
                 full_cli="claude", session_holder=None):
        self.state = state
        self.full_cli = full_cli
        # The pid that holds a project's session now, or None (#2969). None reads the machine's process table; a test
        # injects its own reading, so it never sees a live process it did not start.
        self.session_holder = session_holder or fullturn.session_holder
        self.turns = []
        self.turn_order = fullturn.TurnOrder()
        self.live_turns = fullturn.TurnRegistry()   # the turn each project runs now, for `!stop` (#2968)
        self.full_guard = threading.Lock()
        self.perm_post_guard = threading.Lock()   # one poster of the permission relay at a time (#2972)
        # The callable that starts a project's operator session at once (#2925). None leaves no session started here,
        # so a fixture or a reader never spawns a session; production passes `start_operator`.
        self.starter = starter
        self.owner = owner
        self.transcriber = transcriber
        self.downloader = downloader
        self.log = log or (lambda line: print(line, file=sys.stderr))
        self.client = client
        # One Slack client, token and clip path per app id, so two projects that name different apps never borrow each
        # other's bot. A channel resolves its app from the active revisions; `client`, `downloader` and `uploader` are
        # the fallback when the start named no app for it.
        self.clients = clients or {}
        self.bot_tokens = bot_tokens or {}
        self.downloaders = downloaders or {}
        self.uploaders = uploaders or {}
        self.channel_apps = {}     # channel id -> app id, rebuilt from the documents each refusal pass reads
        self.refused_channels = {}   # channel id -> refusal line, so routing skips a channel two projects claim
        self.projects = projects
        self.configs = configs     # the project list whose dev tips carry a Slack file; None leaves the revisions alone
        # The callable that names one project's saved-sha registry, as the profile seam resolves it (#2961). None leaves
        # the saved-sha check out of force, for a machine that names no registry; a callable puts it in force per
        # project, and a file it cannot read lists nothing.
        self.registry = registry
        # (workspace, repo) -> (registry signature, owner). The owner of a workspace is read once and kept until a
        # registry this daemon reads changes its mtime, so the message path reads no registry twice for one decision.
        self._owner_cache = {}
        self.synthesizer = synthesizer
        self.uploader = uploader
        self.failed = {}     # digest -> consecutive refused deliveries, so a permanently refused line never spins
        self._action_passes = 0   # empty action passes since the last one ran a request, for the heartbeat line
        self._last_refusal = {}   # repo -> the denial signature the owner was told about, held even when no file can hold it
        self._channel_conflicts = set()   # each refused channel claim whose DM went out, pruned when the claim is gone
        self._channel_logged = set()      # each refused channel claim already logged, pruned with the current lines
        self._unowned_logged = set()      # each release channel no app owns, whose skip is already one log line
        self._uncovered_logged = set()    # each channel a file names with no active revision, already one log line
        self._held = set()   # digest -> its hold line was told, so one held answer is not one line per poll
        self._unknown_persona_logged = set()   # each (channel, persona id) one line names that the project omits
        self._speech_logged = set()   # each (error class, cause code) already logged, so one cause is one line (#2956)
        self._guard_hits = {}    # (channel, user) -> monotonic moments of the messages inside the guard window (#2947)
        self._guard_replied = {}  # (channel, user) -> the monotonic moment of the last refusal reply (#2947)
        self._name_lines = set()  # the channel-name lines the last sync reported, so a caught-up sync repeats none (#2955)

    def app_of(self, channel):
        """The app id that owns one channel, or None when no active document names it."""
        return self.channel_apps.get((channel or "").strip())

    def client_for(self, channel):
        """The Slack client of the app that owns `channel`, else the daemon's one fallback client."""
        return self.clients.get(self.app_of(channel)) or self.client

    def bot_for(self, channel):
        """The bot token of the app that owns `channel`, else the first app's token (the daemon's fallback)."""
        app = self.app_of(channel)
        if app in self.bot_tokens:
            return self.bot_tokens[app]
        return next(iter(self.bot_tokens.values()), None)

    def identity_for(self, channel):
        """The language and persona of one channel: from the active revision of its project, by the decision's chain.

        An outbox line names no event, so the daemon reads the identity from the channel id alone. A channel with no
        active revision keeps the default language and no persona, so no post carries a name the file never declared."""
        repo = self.state.channel_for(channel)
        active = self.state.active_revision(repo) if repo else None
        if not active or not active.get("revision"):
            return slackpolicy.DEFAULT_LANGUAGE, None
        return slackpolicy.identity(active["revision"], active.get("local") or {}, channel)

    def addressed_persona(self, channel, thread_ts):
        """The persona a message addressed, when the daemon answers it: the inbox record of the question names the
        persona the policy chose, and the project's active revision holds its row. A line with no such record, or a
        revision with no such persona, answers under the channel persona (issue #2819)."""
        if thread_ts is None:
            return None
        repo = self.state.channel_for(channel)
        active = self.state.active_revision(repo) if repo else None
        if not active or not active.get("revision"):
            return None
        for record in read_lines(self.state.inbox_for(channel)):
            if str(record.get("ts")) == str(thread_ts) and record.get("persona"):
                row = slackpolicy.persona_named(active["revision"], active.get("local") or {},
                                                str(record["persona"]))
                if row is not None:
                    return row
        return None

    def outbox_persona(self, channel, thread_ts, record):
        """The persona one outbox line answers with, and the id its `persona` field names that the project omits.

        A line may carry a `persona` field holding a persona id of the channel project's active revision, so one answer
        posts and speaks under another persona than the channel's. The field wins over the addressed persona. A line
        with no field keeps the addressed persona, else the channel persona. An id the project does not declare keeps
        the channel persona and is returned as the second element, so the caller tells it once."""
        named = str(record.get("persona") or "").strip()
        if named:
            repo = self.state.channel_for(channel)
            active = self.state.active_revision(repo) if repo else None
            if active and active.get("revision"):
                row = slackpolicy.persona_with_id(active["revision"], active.get("local") or {}, named)
                if row is not None:
                    return row, None
        persona = self.addressed_persona(channel, thread_ts) or self.identity_for(channel)[1]
        return persona, (named or None)

    def download_clip(self, channel, url):
        """One clip's bytes, fetched with the token of the app that serves its channel."""
        app = self.app_of(channel)
        if app in self.downloaders:
            return self.downloaders[app](url)
        return self.downloader(url)

    def registry_file(self, repo):
        """The hub registry that holds one project's saved shas, owner ids and grants, or '' when no registry is in force.

        The entry is the configured project of that repository, else a bare entry that names only the repository.
        """
        if self.registry is None:
            return ""
        entry = {"repo": repo}
        rows = list(self.configs() or ()) if self.configs is not None else []
        for row in rows:
            if str((row or {}).get("repo") or "").strip() == repo:
                entry = row
        return self.registry(entry) or ""

    def workspace_owner(self, workspace, repo=None):
        """The owner user id the hub registry names for one Slack workspace, or None when it names none (#2966).

        A project channel reads its own project's registry first, then the other configured projects'. A direct message
        owns no project, so every configured project's registry is read and the first one that names the workspace
        answers. No other source names an owner. A seam that raises, and a registry a build cannot read, name no owner:
        a broken registry never raises into the message path. The answer is kept per workspace until a registry this
        daemon reads changes its mtime, so one decision reads no registry twice.
        """
        workspace = str(workspace or "")
        if not workspace or self.registry is None:
            return None
        try:
            rows = list(self.configs() or ()) if self.configs is not None else []
        except Exception:      # noqa: BLE001 - a configs seam that raises must never drop a message
            rows = []
        entries = []
        if repo:
            own = next((row for row in rows
                        if str((row or {}).get("repo") or "").strip() == repo), None)
            entries.append(own or {"repo": repo})
        entries.extend(rows)
        entries.append({"repo": None})
        seen, sig = set(), []
        for entry in entries:
            try:
                path = self.registry(entry) or ""
            except Exception:      # noqa: BLE001 - a registry seam that raises names no owner
                path = ""
            if path in seen:
                continue
            seen.add(path)
            try:
                mtime = os.stat(path).st_mtime_ns
            except OSError:
                mtime = None
            sig.append((path, mtime))
        signature = tuple(sig)
        key = (workspace, repo or "")
        cached = self._owner_cache.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        owner = None
        for path, _ in signature:
            try:
                owner = registry_owners(path).get(workspace)
            except Exception:      # noqa: BLE001 - an unreadable registry names no owner
                owner = None
            if owner:
                break
        self._owner_cache[key] = (signature, owner)
        return owner

    def listed(self, projects=None):
        """repository -> the shas that project's own registry lists, read fresh on every pass (#2961).

        Saving in the hub is the owner's approval, and a commit author is forgeable, so only a sha the project's own
        profile lists is an authority (#2695). The answer covers the repository each channel registry names and every
        project the configs seam lists, beside this pass's own projects, so a project outside one pass still gets its
        registry read. A daemon wired with no configs seam covers only the channel registry and this pass. A project
        absent from the answer names no registry, so the check is out of force for it alone.
        """
        if self.registry is None:
            return {}
        entries = {}
        for repo in (self.state.channels() or {}):
            repo = str(repo or "").strip()
            if repo:
                entries[repo] = {"repo": repo}
        if self.configs is not None:
            for entry in self.configs() or ():
                repo = str((entry or {}).get("repo") or "").strip()
                if repo:
                    entries[repo] = entry
        for entry in projects or ():
            repo = str((entry or {}).get("repo") or "").strip()
            if repo:
                entries[repo] = entry
        out = {}
        for repo, entry in entries.items():
            path = self.registry(entry)
            if path:
                out[repo] = listed_revisions(path)
        return out

    def refresh_slack(self, projects=None):
        """Read each registered project's `slack.yaml` from its dev tip and switch to it only when it is valid.

        A tip that did not move is not read again. A valid revision replaces the active one. A revision that breaks a
        rule, a tip that cannot be read, or a revision the project's own registry does not list keeps the previous
        revision active, so an invalid edit or an unapproved commit never changes a live channel. The owner gets one DM
        naming the failing key, and every write is atomic (ADR-0030).
        """
        if projects is None:
            projects = list(self.configs() or []) if self.configs is not None else []
        local_docs, local_defects = self.local_documents(projects)
        listed = self.listed(projects)
        results = []
        for entry in projects:
            repo = (entry.get("repo") or "").strip()
            if not repo:
                continue
            results.append(self.refresh_one(repo, entry, local_docs, local_defects, listed))
        self.refuse_channel_claims(projects)
        self.log_uncovered_channels(projects)
        return results

    def log_uncovered_channels(self, projects=None):
        """One line per channel a project's `slack.yaml` names while no active revision covers it (#2934).

        A channel with no active revision answers with the default language and no persona, and its answers post under
        the app name, so the owner reads one line that names the channel and the project instead of a silent default.
        The line is written once, not one per poll."""
        if projects is None:
            projects = list(self.configs() or []) if self.configs is not None else []
        for entry in projects:
            repo = str((entry.get("repo") or "").strip())
            if not repo or self.state.active_revision(repo):
                continue
            try:
                _, text = dev_tip(str((entry.get("checkout") or "").strip()))
            except TipUnreadable:
                continue
            doc = slackpolicy.read_document(text, slackpolicy.COMMITTED.as_posix(), [])
            if doc is None:
                continue
            for workspace in _rows(doc.get("workspaces")):
                for channel in _rows(workspace.get("channels")):
                    cid = str(channel.get("id") or "").strip()
                    if not cid:
                        continue
                    line = (f"voiced: {repo}: channel {cid} has no active Slack revision; its lines keep the default "
                            "language and no persona")
                    if line in self._uncovered_logged:
                        continue
                    self._uncovered_logged.add(line)
                    self.log(line)

    def refuse_channel_claims(self, projects=None):
        """Refuse every channel id two projects claim: one line and one DM that name both projects and the id.

        The claim is read across each project's active revision, or its dev tip while it has none, so a file edit alone
        never widens one project's reach and a fresh start refuses before any socket answers. A refused channel leaves
        the effective routing for both projects, so no answer and no clip reaches it (criterion 4). A conflict already
        told is not told again, and a DM Slack refuses is retried on the next poll; a conflict that is resolved and
        later reintroduced is told again once.
        """
        if projects is None:
            projects = list(self.configs() or []) if self.configs is not None else []
        listed = self.listed(projects)
        documents = effective_documents(self.state, projects, listed=listed)
        unserved = self.unlisted_channel_lines(documents, listed)
        claims = channel_claims(documents)
        lines = channel_claim_refusals(documents) + [line for _, line in unserved]
        self.refused_channels = {cid: f"voiced: {repos[0]} and {repos[1]} claim the channel {cid}; refused"
                                 for cid, repos in claims.items() if len(repos) > 1}
        self.refused_channels.update(dict(unserved))
        if not self.refused_channels:
            self._held.clear()
        self.channel_apps = channel_apps(documents)
        current = set(lines)
        self._channel_logged &= current
        self._channel_conflicts &= current
        for line in lines:
            if line not in self._channel_logged:
                self._channel_logged.add(line)
                self.log(line)
            if line in self._channel_conflicts:
                continue
            if self.notice_channel_conflict(line):
                self._channel_conflicts.add(line)
        return lines

    @staticmethod
    def _unlisted_line(repo, cid):
        """One line for a channel whose project the hub lists no approved revision of (#2695)."""
        return (f"voiced: {repo} has no approved Slack revision in the hub registry; its channel {cid} is not served")

    def unlisted_channel_lines(self, documents, listed=None):
        """`(channel id, line)` of each project channel whose project holds no listed document (#2695, criteria 7, 8).

        `listed` maps each project to the shas its own registry lists (#2961). A project that names no registry serves
        as before. A project that names one, with no listed document, has no revision the hub saved, so no event of its
        channel reaches an inbox and no answer leaves for it.
        """
        if self.registry is None:
            return []
        out = []
        for repo, entry in (self.state.channels() or {}).items():
            cid = str((entry or {}).get("id") or "")
            if cid and repo not in documents and repo in (listed or {}):
                out.append((cid, self._unlisted_line(repo, cid)))
        return out

    def notice_channel_conflict(self, line):
        """One DM to the owner about a channel id two projects claim; a refused DM is a log line, never a lost refusal."""
        if self.client is None or not self.owner:
            return False
        try:
            got = self.client.api("chat.postMessage", {"channel": self.owner, "text": line}, post=True)
        except Exception as x:      # noqa: BLE001 - a Slack error is a log line, never a daemon that stops answering
            self.log(f"voiced: the channel conflict notice was not sent: {type(x).__name__}")
            return False
        if not got.get("ok"):
            self.log(f"voiced: the channel conflict notice was refused: {got.get('error')}")
            return False
        return True

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

    def refresh_one(self, repo, entry, local_docs, local_defects, listed=None):
        """One project's revision: read, validate with its local file, then save it or keep the previous one.

        `listed` maps each project to the shas its own registry lists; `repo` absent from it names no registry, so the
        check is out of force for this project alone (#2961)."""
        active = self.state.active_revision(repo)
        try:
            sha, text = dev_tip((entry.get("checkout") or "").strip())
        except TipUnreadable as x:
            return self.refuse_revision(repo, active, [str(x)], sha=None, tip_failed=True)
        if active is not None and active.get("sha") == sha:
            return {"repo": repo, "sha": sha, "changed": False, "active": active}
        approved = (listed or {}).get(repo)
        if approved is not None and sha not in approved:
            held = listed_revision_at((entry.get("checkout") or "").strip(), approved)
            if held is None:
                return self.refuse_revision(
                    repo, active, [f"the revision {sha[:12]} is not listed in the hub registry, so no save approved it"],
                    sha=sha)
            if active is not None and active.get("sha") == held[0]:
                return {"repo": repo, "sha": held[0], "changed": False, "active": active}
            # The tip moved past the save. The listed sha is the approval, so its file becomes the active revision.
            self.log(f"voiced: {repo}: the dev tip is not a saved revision; reading the listed revision {held[0][:12]}")
            sha, text = held
        tip_defects = []
        doc = slackpolicy.read_document(text, slackpolicy.COMMITTED.as_posix(), tip_defects)
        name, local = local_docs.get(repo, (None, None))
        local_defects = list(local_defects.get(repo) or [])
        local_name = name or slackpolicy.LOCAL_SUFFIX
        others = [(other_name, other_doc) for other_repo, (other_name, other_doc) in local_docs.items()
                  if other_repo != repo]
        if doc is not None:
            registry = self.registry_file(repo)
            for defect in slackpolicy.validate(doc, local or {}, others, local_name=local_name,
                                               owners=registry_owners(registry), grants=registry_grants(registry)):
                # `validate` names the local side with `local_name`; every other defect is the committed file.
                (local_defects if defect.file == local_name else tip_defects).append(defect)
        if tip_defects or local_defects:
            return self.refuse_revision(
                repo, active, [str(defect) for defect in tip_defects + local_defects], sha=sha,
                local_files=sorted({defect.file for defect in local_defects}), tip_failed=bool(tip_defects))
        record = {"repo": repo, "sha": sha, "revision": doc, "local": local or {}}
        try:
            self.state.save_active_revision(repo, record)
        except OSError as x:
            self.log(f"voiced: {repo}: cannot write the Slack revision: {type(x).__name__}: {x.strerror or x}")
            return {"repo": repo, "sha": sha, "changed": False, "active": active}
        self._last_refusal.pop(repo, None)     # a valid revision resets the notice, so the next distinct refusal warns
        self.state.clear_refusal(repo)
        self.log(f"voiced: {repo}: slack.yaml revision {sha[:12]} is active")
        return {"repo": repo, "sha": sha, "changed": True, "active": record}

    def refuse_revision(self, repo, active, reasons, sha=None, local_files=(), tip_failed=True):
        """Keep the active revision and log each reason; send the owner one notice per distinct rejected revision.

        The signature the owner was told about is held in memory, so an unchanged refusal is only a log line even when
        the state directory cannot hold a record. The persisted record is read once, so a restart still keeps the
        owner silent about a notice the previous process already sent.
        """
        kept = (active or {}).get("sha")
        stays = f"revision {kept[:12]} stays active" if kept else "no revision is active yet"
        self.log(f"voiced: {repo}: slack.yaml refused; {stays}")
        for reason in reasons:
            self.log(f"voiced: {repo}: {reason}")
        signature = {"repo": repo, "sha": sha, "reasons": list(reasons)}
        known = self._last_refusal.get(repo)
        if known is None:
            known = self.state.last_refusal(repo)
            if known is not None:
                self._last_refusal[repo] = known
        if known == signature:
            self.log(f"voiced: {repo}: the same revision was refused before; the owner is not told twice")
            return {"repo": repo, "changed": False, "active": active, "defects": reasons}
        if self.notice_owner(repo, reasons, active, local_files, tip_failed):
            self._last_refusal[repo] = signature
            try:
                self.state.save_refusal(repo, signature)
            except OSError as x:
                self.log(f"voiced: {repo}: cannot record the refused revision: {type(x).__name__}: {x.strerror or x}")
        return {"repo": repo, "changed": False, "active": active, "defects": reasons}

    def notice_owner(self, repo, reasons, active, local_files=(), tip_failed=True):
        """One DM to the owner about a refused revision; a refused DM is a log line, never a lost notice.

        The text names the side that fails: the committed file at the dev tip, the local file beside the profile, or
        both. A person who reads it knows which file to edit.
        """
        if self.client is None or not self.owner:
            return False
        kept = (active or {}).get("sha")
        stays = f"revision {kept[:12]} stays active" if kept else "no revision is active yet"
        summary = self._refusal_summary(repo, local_files, tip_failed)
        text = f"voiced: {summary}, so {stays}.\n" + "\n".join(reasons)
        try:
            got = self.client.api("chat.postMessage", {"channel": self.owner, "text": text}, post=True)
        except Exception as x:      # noqa: BLE001 - a Slack error is a log line, never a daemon that stops answering
            self.log(f"voiced: {repo}: the Slack notice was not sent: {type(x).__name__}")
            return False
        if not got.get("ok"):
            self.log(f"voiced: {repo}: the Slack notice was refused: {got.get('error')}")
            return False
        return True

    @staticmethod
    def _refusal_summary(repo, local_files, tip_failed):
        """The sentence before the defect lines: which side failed, the committed dev tip or the local file."""
        parts = []
        if tip_failed:
            parts.append(f"I refused the slack.yaml of {repo} at its dev tip")
        if local_files:
            parts.append(f"the local file {' and '.join(local_files)} of {repo} must be fixed")
        return ", and ".join(parts)

    def sync_projects(self):
        """Give every project the hub lists its channel, once; no client or no list leaves the channels as they are.

        The mapped names are then checked against Slack's own list, so a channel renamed on the Slack side is reported
        as a line, never a rule the daemon only assumes (#1615).
        """
        if self.client is None or self.projects is None:
            return {"channels": self.state.channels(), "refused": []}
        got = project_channels(self.state, self.client, self.projects(), self.log)
        # One line per violation, once per process: the sync runs again on every reconnect, and a channel that stays
        # unlisted is a fact the owner reads once, never one line per pass (#2955). A rule that stops holding leaves
        # the set, so the next break of it is a new line.
        lines = check_channel_names(self.state.channels(), self.client)
        for line in lines:
            if line not in self._name_lines:
                self.log(line)
        self._name_lines = set(lines)
        return got

    def wake_operator(self, repo, line=None):
        """Start the project's operator session at once when its inbox line lands (#2925).

        A project with no live session never waits for the five-minute pass: the message that arrived is the reason the
        session starts. `line` is the inbox line's id, so a pass that started nothing is reported against the message
        still waiting. Returns the starter's handle, or False when no start ran; a refused start is one log line, and
        the line stays in the inbox for the next pass.
        """
        if self.starter is None or not repo:
            return False
        try:
            return self.starter(self.state.dir, repo, log=self.log, line=line)
        except Exception as x:      # noqa: BLE001 - a start that fails is a log line, never a lost inbox line
            self.log(f"voiced: the operator start for {repo} failed: {type(x).__name__}: {x}")
            return False

    def guard_settings(self, channel):
        """The guard block of the project that owns one channel, or the plugin defaults. A channel with no active
        revision, such as the DM, takes the defaults, so the operator session stays bounded whatever the file names."""
        repo = self.state.channel_for(channel)
        active = self.state.active_revision(repo) if repo else None
        committed = active.get("revision") if active else None
        return slackpolicy.guard(committed or {})

    def size_reason(self, channel, text):
        """`size` when one message body is longer than the channel's `max_chars`, else None. The size rule removes the
        `read_wrappers` substrings first, so a wrapper never counts against the sender (#2947)."""
        guard = self.guard_settings(channel)
        body = slackpolicy.readable(text, guard.get("read_wrappers"))
        return "size" if len(body) > int(guard["max_chars"]) else None

    def guard_reason(self, channel, user, text, now=None):
        """`rate` or `size` when the guard refuses one message, else None. The size rule runs first, and the rate rule
        counts the messages of one sender in one channel inside the window; a refused message still counts, so a flood
        stays refused (#2947). `now` is the monotonic moment, so a test passes its own clock."""
        now = time.monotonic() if now is None else now
        reason = self.size_reason(channel, text)
        if reason:
            return reason
        key = (str(channel), str(user))
        hits = [moment for moment in self._guard_hits.get(key, []) if now - moment < GUARD_WINDOW]
        if len(hits) >= int(self.guard_settings(channel)["rate"]):
            self._guard_hits[key] = hits
            return "rate"
        hits.append(now)
        self._guard_hits[key] = hits
        return None

    def answer_refusal(self, record, inbox_key, cid, text):
        """The reason one outbox line may not post, else None (#2948).

        The line must answer a pending inbox line of its channel and thread (`slackpolicy.pending_line`), so a session
        never posts in a channel it was not asked in. The channel's `answer_max_chars` bounds the text it may post."""
        inbox = read_lines(self.state.inbox_for(inbox_key))
        if slackpolicy.pending_line(inbox, record.get("thread_ts")) is None:
            return "unbound"
        if len(text) > int(self.guard_settings(cid)["answer_max_chars"]):
            return "size"
        return None

    def clean_answer(self, record):
        """The answer's `text`, `speech` and `persona` with every secret redacted and every link removed (#2948).

        Returns the cleaned line, the fields the redaction changed and the number of links removed, so the audit tells
        what the checks did. Redaction is a backstop: the session's read bound is what stops a leak."""
        cleaned = dict(record)
        redacted, links = [], 0
        for name in ("text", "speech", "persona"):
            value = cleaned.get(name)
            if not isinstance(value, str):
                continue
            masked = shared_redact.redact(value)
            if masked != value:
                redacted.append(name)
            links += len(LINK.findall(masked))
            cleaned[name] = re.sub(r"[ \t]{2,}", " ", LINK.sub("", masked)).strip()
        return cleaned, redacted, links

    def record_answer_audit(self, cid, text, decision, reason=None, redacted=(), links=0):
        """One audit line for one outbox answer: its channel, the decision, the reason class, and what the redaction
        and the link rule changed (#2948)."""
        self.state.append_audit({
            "time": datetime.now().isoformat(timespec="seconds"),
            "user": None,
            "role": None,
            "channel": cid,
            "decision": decision,
            "reason": reason,
            "text": redacted_text(text),
            "redacted": list(redacted),
            "links_removed": links,
        })

    def answer_refused(self, record, decision, reason):
        """One refusal reply to a refused sender, at most one per channel in ten minutes (#2947). The reply names the
        reason class, `rate` or `size`, and it is one sentence. A `listen` channel receives no post."""
        key = (str(record.get("channel")), str(record.get("user")))
        now = time.monotonic()
        last = self._guard_replied.get(key)
        if last is not None and now - last < GUARD_REPLY_WINDOW:
            return
        if decision is not None and decision.reply is None:
            return
        self._guard_replied[key] = now
        sentence = ("Your message was refused: rate. You sent more messages than this channel accepts in one minute."
                    if reason == "rate" else
                    "Your message was refused: size. Your message is longer than this channel accepts.")
        try:
            self.answer(record, one_sentence(sentence))
        except Exception as x:      # noqa: BLE001 - a Slack error is a log line, never a lost record or a lost ack
            self.log(f"voiced: refusal reply refused: {type(x).__name__}: {x}")

    def refuse(self, record, decision, reason, text):
        """Record one refused message: its audit line first, then the one refusal reply the sender may hear (#2947). A
        refused message writes no inbox line."""
        self.record_audit(record, "refused", text, reason)
        self.answer_refused(record, decision, reason)
        self.log(f"voiced: refused {reason} for {record.get('id')}")

    def record_audit(self, record, decision, text, reason=None):
        """One audit line for one message: its time, sender id, role, channel, decision and reason class, and at most
        `AUDIT_TEXT_MAX` characters of redacted text (#2947)."""
        self.state.append_audit({
            "time": datetime.now().isoformat(timespec="seconds"),
            "user": record.get("user"),
            "role": record.get("role"),
            "channel": record.get("channel"),
            "decision": decision,
            "reason": reason,
            "text": redacted_text(text),
        })

    def inbox_path_of(self, event):
        """The inbox one message belongs to: its own channel's when a project owns the channel, the DM's for an im, else
        None. Each channel of a project has its own inbox, which the project session watches (#2925)."""
        repo = self.state.channel_for(event.get("channel"))
        if repo:
            return self.state.inbox_for(str(event.get("channel")))
        if (event.get("channel_type") or "im") == "im":
            return self.state.inbox_path
        return None

    def answer(self, question, text):
        """Post one answer in the channel its question came from, threaded under it. True when Slack accepts it."""
        if not question.get("channel"):
            return False
        client = self.client_for(question.get("channel"))
        if client is None:
            return False
        payload = {"channel": question["channel"], "text": text}
        persona = self.identity_for(question.get("channel"))[1]
        if persona is not None and persona.name:
            payload["username"] = persona.name     # the scope `chat:write.customize` is what lets Slack accept it
        if question.get("ts"):
            payload["thread_ts"] = question["ts"]
        got = client.api("chat.postMessage", payload, post=True)
        if not got.get("ok"):
            self.log(f"voiced: answer refused: {got.get('error')}")
            return False
        return True

    def answer_failed_clip(self, record, decision=None):
        """One line under a clip that did not work, in its own channel and thread, naming the failure in one sentence.

        The record is already durable when this runs, so a refused post never loses the failure and never makes the
        clip a second model call on the next start (#1618). The sentence comes from `record['answer']`, which the
        recording path builds with `one_sentence`, so the line is the same shape whatever failed. A decision with no
        reply path is a `listen` channel, and it receives no post, not even this one (SPEC 2682).
        """
        if decision is not None and decision.reply is None:
            self.log(f"voiced: failure answer not posted, the channel is listen-only: {record.get('error')}")
            return
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

    def post_text(self, channel, thread_ts, text, persona=None):
        """Post the readable answer in its channel under the persona name; True and its ts when Slack accepts it.

        The `username` is the persona of `persona` when the caller names one, else the channel persona's, so an answer
        to an addressed message carries the addressed persona. A channel with no persona sends no name and the bot
        posts as the app, as it did before the persona existed."""
        client = self.client_for(channel)
        if client is None:
            return False, None
        payload = {"channel": channel, "text": text}
        if persona is None:
            persona = self.identity_for(channel)[1]
        if persona is not None and persona.name:
            payload["username"] = persona.name     # the scope `chat:write.customize` is what lets Slack accept it
        if thread_ts:
            payload["thread_ts"] = thread_ts
        got = client.api("chat.postMessage", payload, post=True)
        if not got.get("ok"):
            self.log(f"voiced: answer refused: {got.get('error')}")
            return False, None
        return True, got.get("ts")

    def announce_releases(self):
        """Post each release event in the `owned` channels that subscribe to it, once per event and channel.

        The record is one line of `$VOICE_STATE/release-events.jsonl` (#2702). A channel gets the template of its
        project's event, or its own override, with the record's fields filled. The post carries the channel persona's
        `username` and no mark, because an `owned` bot speaks as itself. A `listen` channel, a channel that does not
        subscribe and an event with no template get nothing. Only the record of stage S1 is announced: a record another
        stage emitted waits for its own path. The mark is durable, so a restart posts no second notice.
        """
        announced = self.state.announced()
        records = self.state.release_events()
        posted = 0
        for repo in self.state.channels():
            active = self.state.active_revision(repo)
            if not active or not active.get("revision"):
                continue
            committed, local = active["revision"], active.get("local") or {}
            for record in records:
                key, event_id = str(record.get("key") or ""), str(record.get("id") or "")
                if not key or not event_id or slackannounce.stage_of(record) != slackannounce.STAGE:
                    continue
                try:
                    for post in slackannounce.announcements(committed, local, record):
                        mark = f"{key}|{event_id}|{post.channel}"
                        if mark in announced:
                            continue
                        if self.post_announcement(post):
                            self.state.remember_announced(key, event_id, post.channel)
                            announced.add(mark)
                            posted += 1
                except Exception as x:      # noqa: BLE001 - one bad record is a log line, the other records still run
                    self.log(f"voiced: release notice for {repo} {key} failed: {type(x).__name__}")
        return posted

    def post_announcement(self, post):
        """One notice through the client of the app that owns its channel; True when Slack accepts it.

        A channel no app owns is one log line, said once, so a misconfigured channel reaches the operator instead of a
        silent retry on every poll."""
        client = self.client_for(post.channel)
        if client is None:
            if post.channel not in self._unowned_logged:
                self._unowned_logged.add(post.channel)
                self.log(f"voiced: release notice skipped: no app owns channel {post.channel}")
            return False
        payload = {"channel": post.channel, "text": post.text}
        if post.persona:
            payload["username"] = post.persona     # the scope `chat:write.customize` is what lets Slack accept it
        try:
            got = client.api("chat.postMessage", payload, post=True)
        except Exception as x:      # noqa: BLE001 - a Slack error is a log line, never a daemon that stops posting
            self.log(f"voiced: release notice not posted: {type(x).__name__}")
            return False
        if not got.get("ok"):
            self.log(f"voiced: release notice refused: {got.get('error')}")
            return False
        return True

    def synth_clip(self, script, voice=None, tone=None):
        """One spoken clip's path, or None when the voice refused: a missing voice never fails the answer.

        `voice` and `tone` are the persona's, so the clip sounds like the channel; a caller that names neither keeps
        the daemon's default voice."""
        try:
            return self.synthesizer(script, voice=voice, tone=tone) or None
        except Exception as x:      # noqa: BLE001 - a refused TTS is a log line, and the text answer stands alone
            # The error type and its cause code, never the provider's message: that message can carry request
            # material, and this log is never redacted. One cause is one line, so a voice that refuses every answer
            # never floods the log (#2956).
            name, cause = type(x).__name__, getattr(x, "cause", None)
            if (name, cause) not in self._speech_logged:
                self._speech_logged.add((name, cause))
                self.log(f"voiced: speech skipped: {name}: {cause}" if cause else f"voiced: speech skipped: {name}")
            return None

    def upload_for(self, channel):
        """The clip uploader of the app that owns `channel`, else the daemon's one fallback uploader."""
        app = self.app_of(channel)
        if app in self.uploaders:
            return self.uploaders[app]
        return self.uploader

    def post_clip(self, path, channel, thread_ts, title):
        """Upload one spoken clip, then drop its temporary file; False when the upload was refused."""
        uploader = self.upload_for(channel)
        try:
            if uploader is None:
                return False
            fid, err = uploader(path, channel, thread_ts, title)
            if not fid:
                self.log(f"voiced: clip refused: {err}")
                return False
            return True
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def record_latency(self, cid, thread, answer_ts):
        """Write the seconds from the question's Slack ts to the answer's post ts (#2940).

        Slack's ts is epoch seconds, so the answer's own ts is the moment it went out: no wall clock is trusted, and no
        two clocks can disagree. A ts that does not parse, or a channel whose inbox holds no unanswered line, records
        nothing rather than a made-up number."""
        if answer_ts is None:
            return
        question_id, question_ts = question_line(self.state, cid, thread)
        if question_ts is None:
            return
        try:
            seconds = round(float(answer_ts) - float(question_ts), 4)
        except (TypeError, ValueError):
            return
        self.state.remember_latency({"id": question_id or f"{cid}:{question_ts}", "channel": cid,
                                     "question_ts": str(question_ts), "answer_ts": str(answer_ts),
                                     "seconds": seconds})

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
        persona, unknown = self.outbox_persona(cid, thread, record)
        if unknown is not None and (cid, unknown) not in self._unknown_persona_logged:
            self._unknown_persona_logged.add((cid, unknown))
            self.log(f"voiced: outbox line names persona {unknown}, which the channel project does not declare; "
                     "answering under the channel persona")
        voice = persona.voice if persona is not None and persona.voice else None
        tone = persona.tone if persona is not None and persona.tone else None
        posted = self.state.posted().get(digest) if digest else None
        done = set(posted.get("clips_done") or []) if posted else set()
        # The first sentence is synthesized beside the text post when there is one, so the voice never waits for Slack.
        speaking = asyncio.create_task(asyncio.to_thread(self.synth_clip, first, voice, tone)) \
            if (first and self.synthesizer and "first" not in done) else None
        if posted is None:
            ok, ts = await asyncio.to_thread(self.post_text, cid, thread, text, persona)
            if not ok:
                if speaking is not None:
                    await asyncio.gather(speaking, return_exceptions=True)
                return False, False
            if digest:
                # The text is out before its voice: a crash from here repeats only the voice, never the text.
                self.state.remember_posted(digest, ts)
            self.record_latency(cid, thread, ts)
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
            path = await asyncio.to_thread(self.synth_clip, rest, voice, tone)
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
            records, requests, broken = read_outbox_answers(path)
            for raw in requests:
                # An action request is not an answer: the pass runs it and appends its own reply line (#2939). The raw
                # line is named in the log, so a skip is never silent.
                self.log(f"voiced: action request not posted: channel {channel or DM} source {request_source(raw)}")
            for raw in broken:
                # A line no JSON cannot be posted and is no request: the log keeps its first bytes, so a malformed line
                # is never dropped silently.
                self.log(f"voiced: outbox line skipped, the line is not JSON: channel {channel or DM}: {raw}")
            if records and channel and not self.channel_accepts_answer(channel):
                # A `listen` channel receives no post: the answer stays in its outbox and never leaves (SPEC 2682).
                self.log(f"voiced: answer not posted, the channel is listen-only: {channel}")
                continue
            for record in records:
                cid = (record.get("channel") or channel or "").strip()
                refusal = self.refused_channels.get(cid)
                if refusal:      # a channel two projects claim is held, never posted, and is told once
                    digest = outbox_digest(record)
                    if digest not in self._held:
                        self._held.add(digest)
                        self.log(f"voiced: outbox line held: {refusal}")
                    continue
                digest = outbox_digest(record)
                if digest in delivered:
                    continue
                fails = self.failed.get(digest, 0)
                if fails >= MAX_DELIVER_ATTEMPTS:
                    continue
                text = (record.get("text") or "").strip()
                reason, redacted, links = None, [], 0
                if text and cid:
                    reason = self.answer_refusal(record, channel or DM, cid, text)
                    if reason is None:
                        record, redacted, links = self.clean_answer(record)
                    self.record_answer_audit(cid, text, "refused" if reason else "answer", reason, redacted, links)
                if reason:
                    # A refused answer is final: the delivered ledger never retries it, and the outbox keeps the line.
                    self.state.remember_delivered(digest)
                    delivered.add(digest)
                    self.log(f"voiced: answer refused: {reason}")
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

    def action_documents(self, channel):
        """The `(committed, local)` Slack files of the project that owns one channel, from its active revision. A channel
        with no active revision reads as empty files, so its request gets the policy's refusal and no action (#2939)."""
        repo = self.state.channel_for(channel)
        active = self.state.active_revision(repo) if repo else None
        if active and active.get("revision"):
            return active["revision"], active.get("local") or {}
        return {}, {}

    def run_action_requests(self, now=None):
        """Run every action request no earlier pass ran, outside every operator session and its sandbox (#2939).

        The sessions write one request line to an outbox; this pass reads it, asks the policy for the sender's decision,
        checks the request through the executor, runs the command the project declares, appends one reply in the
        thread and one audit line. It is a call in the daemon's own process, so no request reaches a sandboxed session.
        """
        runs = slackactions.run_pass(
            self.state.dir,
            lambda channel: self.state.channel_for(channel) or "",
            self.action_documents,
            now or datetime.now(),
            outbox_for=self.state.outbox_for,
            log=self.log,
        )
        for run in runs:
            self.log(f"voiced: action {run.action or 'none'} for {run.sender} in {run.channel} exited {run.exit}")
        if runs:
            self._action_passes = 0
        else:
            # A pass that ran no request is still visible: one line every ACTION_HEARTBEAT passes says the pass is alive.
            self._action_passes += 1
            if self._action_passes % ACTION_HEARTBEAT == 0:
                self.log(f"voiced: action pass ran no request ({self._action_passes} quiet passes)")
        return len(runs)

    def start_full_turn(self, record, decision):
        """Start one owner message as one turn of its project's session, off the envelope path, so the ack is not held."""
        self.state.remember_message(record["id"])
        repo = self.state.channel_for(record.get("channel"))
        ticket = self.turn_order.arrive(repo)
        worker = threading.Thread(target=self.full_turn, args=(record, decision, repo, ticket), daemon=True)
        with self.full_guard:
            self.turns.append(worker)
        worker.start()

    def join_turns(self):
        with self.full_guard:
            turns = list(self.turns)
        for worker in turns:
            worker.join()

    def stop_full_turn(self, record):
        """End the turn one project runs now, outside the queue (#2968), and write the stop's one audit line.

        A `!stop` never becomes a turn of its own: `handle` reads it before it queues the message. The stop ends
        nothing when the project runs no turn, and the line tells the two apart. The running turn writes its own
        line, a failed turn named `stopped`, so a stop that ends a turn leaves both lines and no lost message.

        The dedup marker is durable before the kill, so a crash between the two drops a redelivered stop as a
        duplicate instead of killing a second time and leaving a second line for one user action (#2968 review). Only
        the requester who started the turn ends it: `TurnRegistry.stop` compares that identity, so a full role handed
        to anyone else clears nothing."""
        repo = self.state.channel_for(record.get("channel"))
        self.state.remember_message(record["id"])
        ended = bool(repo) and self.live_turns.stop(repo, record.get("user"))
        self.state.append_audit({"time": datetime.now().isoformat(timespec="seconds"), "user": record.get("user"),
                                 "role": record.get("role"), "channel": record.get("channel"), "repo": repo,
                                 "decision": "stop", "ended": ended})
        self.log(f"voiced: stop for {repo or 'no project'} ended the running turn: {ended}")
        return "stopped"

    def project_checkout(self, repo):
        """The checkout of one project: from the rows that carry one (`configs`, the hub's project list), then from any
        project row that is a mapping. `projects` lists bare repository names in production (`hub_repos`), so a row
        there names no checkout (#2965 review)."""
        rows = list(self.configs() or []) if self.configs is not None else []
        rows += [row for row in (self.projects() or []) if isinstance(row, dict)]
        for row in rows:
            if isinstance(row, dict) and row.get("repo") == repo and row.get("checkout"):
                return row["checkout"]
        return None

    def permission_relay(self, workspace, timeout=None):
        """The relay of every full turn in this state folder, answered only by the owner in the pinned DM (SPEC 2964)."""
        directory = os.path.join(self.state.directory, "permission")
        os.makedirs(directory, exist_ok=True)
        return permrelay.Relay(directory, owner=self.owner, dm_channel=self.state.dm_channel(), workspace=workspace,
                               timeout=permrelay.TIMEOUT_SECONDS if timeout is None else timeout)

    def post_permission_requests(self, relay):
        """Post each open request of the relay to its DM once: the literal tool call and its working folder.

        A request with no client or no pinned DM reaches no owner, so it is denied at once with its reason, never held
        open until a later turn (#2970 review). One guard belongs to the daemon, so a full turn's poster and the
        bounded watcher never post one request twice (#2972)."""
        with self.perm_post_guard:
            for row in relay.unsent():
                if self.client is None or not row["channel"]:
                    reason = "no client" if self.client is None else "no DM channel"
                    relay.abandon(row["id"], reason)
                    self.log(f"voiced: permission request denied: {reason}")
                    continue
                self.client.api("chat.postMessage", {"channel": row["channel"], "text": row["text"]}, post=True)
                relay.mark_sent(row["id"])

    def post_pending_permissions(self):
        """Post every open request of the shared relay folder once, whatever asked: a full turn or a bounded session.

        A bounded session's request waits while no full turn runs, so the daemon posts it here (#2972)."""
        self.post_permission_requests(self.permission_relay(""))

    def relay_requests_until(self, relay, done):
        while not done.wait(permrelay.POLL_SECONDS):
            self.post_permission_requests(relay)

    def run_relayed_turn(self, record, checkout, repo):
        relay = self.permission_relay(record.get("workspace") or "")
        digest = hashlib.sha256(repo.encode("utf-8")).hexdigest()[:16]
        config = os.path.join(relay.directory, f"mcp-{digest}.json")
        with open(config, "w", encoding="utf-8") as f:
            json.dump(permrelay.mcp_config(sys.executable, permrelay.__file__, relay.directory, self.owner,
                                           relay.dm_channel, relay.workspace, checkout), f)
        done = threading.Event()
        poster = threading.Thread(target=self.relay_requests_until, args=(relay, done), daemon=True)
        poster.start()
        try:
            return fullturn.run_turn(self.full_cli, checkout, self.state.directory, repo, record["text"],
                                     registry=self.live_turns, user=record.get("user"),
                                     relay=(config, permrelay.PROMPT_TOOL), holder=self.session_holder)
        finally:
            done.set()
            poster.join()
            self.post_permission_requests(relay)

    def full_turn(self, record, decision, repo, ticket):
        """Run the turn in the project checkout, one turn at a time per project in arrival order, then post its final
        text in the thread.

        Every turn ends with one audit line and, where the channel takes answers, one post: a turn that raises is a
        failed turn, never a lost message (#2965 review)."""
        turn = fullturn.Turn(None, "", None, True)
        try:
            self.turn_order.wait(repo, ticket)
            try:
                checkout = self.project_checkout(repo)
                if checkout is not None:
                    turn = self.run_relayed_turn(record, checkout, repo)
            finally:
                self.turn_order.leave(repo)
        except Exception as x:      # noqa: BLE001 - the worker thread has no caller: a raise here would lose the message with no trace
            self.log(f"voiced: full turn in {record.get('channel')} raised {type(x).__name__}")
        self.state.append_audit({"time": datetime.now().isoformat(timespec="seconds"), "user": record.get("user"),
                                 "role": record.get("role"), "channel": record.get("channel"), "decision": "full",
                                 **turn.audit()})
        if not self.channel_accepts_answer(record.get("channel")):
            self.log(f"voiced: full turn in {record.get('channel')} kept: the channel is listen only")
            return
        answer = FULL_REASONS.get(turn.reason, FULL_FAILED) if turn.failed else shared_redact.redact(turn.text)
        self.post_text(record.get("channel"), record.get("ts"), answer, persona=decision.persona)

    def policy_decision(self, event):
        """The decision of one socket event, always: from the loaded files of the project that owns its channel.

        A valid active revision and its local file decide the event, so a bot, a stranger and a Slack Connect user stay
        data (SPEC 2682). A project channel whose revision is not active yet, the DM, and every channel no project owns
        run through the daemon's empty policy instead: it names the channel and the owner the hub registry names for the
        workspace as its one member, so the owner's message still reaches the operator while a bot, a stranger and a
        Slack Connect user stay data from the event flags and their missing role alone. The workspace owner is resolved
        once for the whole decision, so its two halves never read two answers (#2966).
        """
        channel = str(event.get("channel") or "")
        repo = self.state.channel_for(channel)
        active = self.state.active_revision(repo) if repo else None
        live = bool(active and active.get("revision"))
        if live:
            committed = active["revision"]
            local = active.get("local") or {}
            workspace = self.workspace_of(event, committed)
        else:
            workspace = self.event_workspace(event)
            committed = self.empty_policy(channel, workspace)
            local = {}
        owner = self.workspace_owner(workspace, repo)
        if not live:
            local = self.baseline_local(workspace, channel, owner)
        return slackpolicy.decide(committed, local,
                                  self.policy_event(event, committed, repo, live, workspace, owner), datetime.now())

    @staticmethod
    def event_workspace(event):
        """The team the socket names for one event, or '' when it names none."""
        return str(event.get("team") or event.get("team_id") or "")

    @staticmethod
    def empty_policy(channel, workspace):
        """The empty policy of a channel with no loaded project files: it names the channel so the decision can classify
        the event. The owner member lives in `baseline_local`, so a bot and a Slack Connect user stay data from the
        event flags alone, whatever the owner is. A channel no project owns keeps the same empty policy; `handle`
        records it as data before it reads the kind, so nothing is granted there."""
        row = {"id": channel, "mode": "listen+speak", "roles": ["owner"]}
        return {"workspaces": [{"id": workspace, "class": "owned", "channels": [row]}],
                "roles": [{"id": "owner", "actions": []}]}

    @staticmethod
    def baseline_local(workspace, channel, owner):
        """The local file of the empty policy: the workspace owner the hub registry names is its one member, so the
        owner's message still reaches the operator while every other caller has no role and stays data."""
        if not owner:
            return {}
        return {"members": [{"workspace": workspace, "channel": channel, "role": "owner", "users": [owner]}]}

    def channel_accepts_answer(self, channel):
        """Whether the daemon may post an answer in one channel: its project's active revision names the channel
        `listen+speak`. A channel with no active revision and a channel the revision does not name keep their answers,
        because no loaded file forbids them."""
        repo = self.state.channel_for(channel)
        active = self.state.active_revision(repo) if repo else None
        if not active or not active.get("revision"):
            return True
        for workspace in active["revision"].get("workspaces") or []:
            for row in workspace.get("channels") or []:
                if str(row.get("id")) == str(channel):
                    return str(row.get("mode") or "listen") == "listen+speak"
        return True

    def policy_event(self, event, committed, repo, live, workspace=_UNRESOLVED, owner=_UNRESOLVED):
        """One socket event as the policy event the decision takes: its workspace, channel, caller, text, and the flags
        that mark a bot, a Slack Connect user, the daemon's own post, a direct message and a live revision. The owner is
        the owner id the hub registry names for the event's workspace (#2966), never the daemon's own setting. The
        decision passes the workspace and owner it already resolved, so this call reads no registry again."""
        workspace = self.workspace_of(event, committed) if workspace is _UNRESOLVED else workspace
        owner = self.workspace_owner(workspace, repo) if owner is _UNRESOLVED else owner
        return {"kind": "message",
                "workspace": workspace,
                "channel": str(event.get("channel") or ""),
                "user": str(event.get("user") or event.get("bot_id") or ""),
                "text": str(event.get("text") or ""),
                "owner": owner is not None and str(event.get("user") or "") == owner,
                "active": live,
                "dm": (event.get("channel_type") or "") == "im",
                "bot": bool(event.get("bot_id") or event.get("subtype") == "bot_message"),
                "external": bool(event.get("is_ext_shared_channel") or event.get("is_external")
                                 or event.get("external")
                                 or (event.get("user_team") and str(event.get("user_team")) != workspace)),
                "own": str(event.get("ts") or "") in self.own_posts()}

    @staticmethod
    def workspace_of(event, committed):
        """The workspace id of one event: the team the socket names, else the workspace whose file holds the channel.
        A socket event may carry no team, and the file still tells which workspace the channel belongs to."""
        team = str(event.get("team") or event.get("team_id") or "")
        if team:
            return team
        channel = str(event.get("channel") or "")
        for workspace in committed.get("workspaces") or []:
            for row in workspace.get("channels") or []:
                if str(row.get("id")) == channel:
                    return str(workspace.get("id") or "")
        return ""

    def own_posts(self):
        """The `ts` of every answer this daemon already posted, so the socket's echo of one is ignored, never recorded."""
        return {str(row.get("ts")) for row in self.state.posted().values() if row.get("ts")}

    def handle(self, envelope):
        """Record one envelope: "inbox", "other", "duplicate", "failed", "ignored", "full" or "stopped". No ack here.

        The policy decision decides what the event becomes: an ignored event writes no line, a bot, a stranger and a
        Slack Connect user stay data, and every line carries the language, the persona and the caller role. A `listen`
        channel never receives a post, neither the failure reply nor the answer. A `!stop` of a full role ends the
        project's running turn before this method queues anything (#2968).
        """
        if (envelope.get("type") or "events_api") != "events_api":
            return "ignored"
        event = (envelope.get("payload") or {}).get("event") or {}
        if event.get("type") != "message":
            return "ignored"
        if event.get("subtype") in ("channel_join", "channel_leave"):
            return "ignored"
        mid = message_id(event)
        if mid in self.state.seen():
            return "duplicate"
        workspace = str(event.get("team") or event.get("team_id") or "")
        answer = permrelay.parse_answer(event.get("text"))
        if answer and self.owner and str(event.get("user") or "") == str(self.owner) \
                and event.get("channel") == self.state.dm_channel():
            outcome = self.permission_relay(workspace).decide(event.get("user"), event.get("channel"), workspace,
                                                            *answer)
            if outcome in ("approved", "denied"):
                self.state.remember_message(mid)
                return "permission"
            # A line that names no open request of this workspace is a message like any other: it reaches the inbox.
        target = self.inbox_path_of(event)
        decision = self.policy_decision(event)
        record = {
            "id": mid,
            "ts": event.get("ts"),
            "user": event.get("user"),
            "channel": event.get("channel"),
            "workspace": workspace,
            "language": decision.language,
            "persona": decision.persona.name if decision.persona else None,
            "tone": decision.tone,
            "role": decision.role,
        }
        if event.get("files"):
            record["files"] = [
                {"id": f.get("id"), "name": f.get("name"), "mimetype": f.get("mimetype")}
                for f in event.get("files") or []
            ]
        text = (event.get("text") or "").strip()
        refusal = self.refused_channels.get((event.get("channel") or "").strip())
        if refusal:
            # A channel two projects claim belongs to neither: its events are data, and no project owns the line.
            record["text"] = text
            record["error"] = refusal
            self.state.append_other(record)
            self.record_audit(record, "other", text)
            return "other"
        if target is None:
            # A channel no project owns is data, never a DM question the operator might answer as if it were one.
            if text:
                record["text"] = text
            record["error"] = "unknown channel: no project owns it"
            self.state.append_other(record)
            self.record_audit(record, "other", text)
            return "other"
        if decision.kind == slackpolicy.IGNORE:
            return "ignored"
        if decision.kind == slackpolicy.DATA:
            if text:
                record["text"] = text
            self.state.append_other(record)
            self.record_audit(record, "data", text)
            return "other"
        # The guard runs before any clip download or inbox write, so a flood or an oversized message never reaches
        # the operator session (#2947).
        reason = self.guard_reason(event.get("channel"), event.get("user"), text)
        if reason:
            self.refuse(record, decision, reason, text)
            return "refused"
        clip = audio_file(event)
        if clip:
            url = clip.get("url_private_download") or clip.get("url_private") or ""
            step = "download"
            try:
                audio = self.download_clip(event.get("channel"), url)
                if len(audio) > MAX_CLIP_BYTES:
                    raise ClipTooLarge(len(audio))
                step = "transcribe"
                text = (self.transcriber(audio, clip.get("mimetype") or "audio/mp4") or "").strip()
            except ClipTooLarge as x:   # a clip past the inline cap is a record with its size
                record["error"] = str(x)
                record["answer"] = one_sentence(record["error"])
                record["bytes"] = x.size
                self.state.append_other(record)
                self.record_audit(record, "failed", text, "size")
                self.answer_failed_clip(record, decision)
                self.log(f"voiced: {record['error']} for {mid}")
                return "failed"
            except Exception as x:      # noqa: BLE001 - a clip no model could read is data, never an inbox line
                kind = "transcription" if step == "transcribe" else "download"
                record["error"] = f"{kind} failed: {type(x).__name__}"
                record["answer"] = one_sentence(record["error"])
                self.state.append_other(record)
                self.record_audit(record, "failed", text)
                self.answer_failed_clip(record, decision)
                # The step and the reason, so a certificate failure reads as a download or a transcription failure and
                # never as one vague type. `safe_reason` drops a URL query and every key-like value (#2933).
                self.log(f"voiced: clip {step} failed: {safe_reason(x)} for {mid}")
                return "failed"
        if not text:
            # An owner message with nothing to transcribe or read is still data, and it is always acknowledged.
            record["error"] = "empty transcript" if clip else "empty message: no clip and no text"
            self.state.append_other(record)
            self.record_audit(record, "other", text)
            return "other"
        # A clip's transcript is measured by the same size rule as the text, so a long transcript is refused too.
        reason = self.size_reason(event.get("channel"), text) if clip else None
        if reason:
            self.refuse(record, decision, reason, text)
            return "refused"
        record["text"] = text
        record["clip"] = bool(clip)
        if decision.session == slackpolicy.SESSION_FULL:
            if is_stop(text):
                return self.stop_full_turn(record)
            self.start_full_turn(record, decision)
            return "full"
        self.state.append_inbox(record, target)
        if target == self.state.inbox_path:
            self.state.pin_dm(record.get("channel"))   # the DM's own channel, seen in the owner's event
        self.record_audit(record, "inbox", text)
        self.wake_operator(self.state.channel_for(event.get("channel")) or (DM if target == self.state.inbox_path else ""),
                           record["id"])
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


def plist_text(label, python, script, state_dir, owner, checkout, profiles=LIVE_PROFILES):
    """The launchd agent that restarts the daemon after a crash. Printing it loads nothing.

    `profiles` is the folder the agent reads its projects from: the live copy's profiles folder, never the deploy's own,
    which holds only `example.sh` (#2957). A caller that names an empty value writes no SG_PROFILES_DIR."""
    esc = lambda v: xml.sax.saxutils.escape(str(v))  # noqa: E731
    profiles_line = (f"<key>SG_PROFILES_DIR</key><string>{esc(profiles)}</string>\n" if profiles else "")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{esc(label)}</string>
<key>ProgramArguments</key><array><string>{esc(python)}</string><string>{esc(script)}</string></array>
<key>WorkingDirectory</key><string>{esc(checkout)}</string>
<key>EnvironmentVariables</key><dict>
<key>VOICE_STATE</key><string>{esc(state_dir)}</string>
<key>VOICE_OWNER</key><string>{esc(owner)}</string>
{profiles_line}</dict>
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


_DAEMON_CONTEXT = None


def daemon_context(log=None):
    """The one SSL context the daemon's HTTPS calls share, built once from `ssl_context()` (#2933).

    The clip download and the Gemini call trust the same bundle as the Socket Mode connection. A python.org build has
    no default store under launchd, so a call without this context fails its own verification.
    """
    global _DAEMON_CONTEXT
    if _DAEMON_CONTEXT is None:
        _DAEMON_CONTEXT = ssl_context(log=log)
    return _DAEMON_CONTEXT


# A whole URL: its query or its path may hold a token, so the log keeps the reason and never the address (#2933).
_URL = re.compile(r"https?://[^\s\"']*")
_KEY_PARAM = re.compile(r"(?i)\b(key|token|api[_-]?key|x-goog-api-key|access_token|sig|signature|secret)=[^\s&,;\"']*")
# A bare secret with a known provider prefix, with no `key=` before it (#2933).
_TOKEN_PREFIX = re.compile(r"\b(?:xox[baprs]-|AIza|ghp_|sk-)[A-Za-z0-9_\-]*")


def redacted_text(text):
    """At most `AUDIT_TEXT_MAX` characters of one message for the audit, with every URL, key-like value and bare token
    removed, so the audit line never carries a secret (#2947)."""
    out = _URL.sub("<url>", str(text or ""))
    out = _KEY_PARAM.sub(lambda m: m.group(1) + "=<redacted>", out)
    out = _TOKEN_PREFIX.sub("<redacted>", out)
    return " ".join(out.split())[:AUDIT_TEXT_MAX]


def safe_reason(error):
    """The reason of one failed HTTPS call, with every URL, query, key-like value and bare token removed (#2933).

    A provider or verification message may carry the URL it failed on, whose query or path holds a secret, or the key
    alone with no `key=` before it. The log keeps the reason and drops the whole URL, every `key=` or `token=` value
    and every known token prefix, so a secret never enters a log line.
    """
    reason = getattr(error, "reason", None)
    text = str(reason if reason is not None else type(error).__name__)
    text = _URL.sub("<url>", text)
    text = _KEY_PARAM.sub(lambda m: m.group(1) + "=<redacted>", text)
    text = _TOKEN_PREFIX.sub("<redacted>", text)
    return (text.strip() or type(error).__name__)[:160]


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


async def watch_actions(daemon, stop=None, interval=ACTION_POLL):
    """Run each new action request as soon as the session writes it, until `stop` is set.

    The pass runs in a worker thread, so a slow declared command never holds the socket loop. One guard belongs to the
    daemon, so only one pass is in flight at a time even with two watchers: a slow declared command never lets a second
    pass start and re-attempt the line the first is still running. A failed pass is a log line, never a watcher that
    stops running actions.
    """
    guard = getattr(daemon, "action_pass_guard", None)
    if guard is None:
        guard = asyncio.Lock()
        daemon.action_pass_guard = guard
    while not (stop is not None and stop.is_set()):
        async with guard:
            try:
                await asyncio.to_thread(daemon.run_action_requests)
            except Exception as x:  # noqa: BLE001 - one bad request is a log line, never a daemon that stops acting
                daemon.log(f"voiced: action pass failed: {type(x).__name__}: {str(x)[:120]}")
        await asyncio.sleep(interval)


async def watch_permissions(daemon, stop=None, interval=PERMISSION_POLL):
    """Post each open permission request as soon as the relay holds it, until `stop` is set.

    A full turn posts its own requests, and this watcher posts the request a bounded session's hook opens when no turn
    runs (#2972). A failed pass is a log line, never a watcher that stops posting."""
    while not (stop is not None and stop.is_set()):
        try:
            await asyncio.to_thread(daemon.post_pending_permissions)
        except Exception as x:  # noqa: BLE001 - one bad pass is a log line, never a daemon that stops posting
            daemon.log(f"voiced: permission post failed: {type(x).__name__}: {str(x)[:120]}")
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


async def watch_releases(daemon, stop=None, interval=RELEASE_POLL):
    """Post each new release event in the `owned` channels that subscribe to it, until `stop` is set.

    The record is read from the state directory the promotion pass writes, so a release announced while the daemon
    runs reaches its channels without a restart. A failed pass is a log line, never a watcher that stops announcing.
    """
    while not (stop is not None and stop.is_set()):
        try:
            await asyncio.to_thread(daemon.announce_releases)
        except Exception as x:      # noqa: BLE001 - one bad record is a log line, never a daemon that stops
            daemon.log(f"voiced: release announcement failed: {type(x).__name__}: {str(x)[:120]}")
        await asyncio.sleep(interval)


async def serve(daemon, apps, connect=None, open_connection=None, stop=None):
    """Hear Slack and answer the outboxes at once: one Socket Mode connection per app, beside three watchers.

    `apps` is one app token, or a mapping of app id to app token for several apps. One connection runs per app, so two
    projects that name one app share one socket. The outbox watcher, the Slack-file watcher and the release watcher are
    tasks of the same loop, so an answer the session appends is posted while a socket is open, a moved dev tip is read
    while the daemon answers, and a release event reaches its channels without a restart. All stop together, and the
    watchers are cancelled with the connection loops.
    """
    if isinstance(apps, str):
        apps = {"app": apps}
    # One context for every HTTPS call of the run, built before the first task names the bundle (#2933). A clip's
    # download and its transcription then trust the same bundle as the sockets, whatever the Python build.
    daemon_context(log=daemon.log)
    outboxes = asyncio.create_task(watch_outboxes(daemon, stop))
    configs = asyncio.create_task(watch_configs(daemon, stop))
    releases_task = asyncio.create_task(watch_releases(daemon, stop))
    actions = asyncio.create_task(watch_actions(daemon, stop))
    permissions = asyncio.create_task(watch_permissions(daemon, stop))
    hearing = [asyncio.create_task(hear(daemon, token, connect=connect, open_connection=open_connection, stop=stop))
               for token in apps.values()]
    try:
        await asyncio.gather(*hearing)
    finally:
        tasks = (outboxes, configs, releases_task, actions, permissions, *hearing)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


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

    context = daemon_context()
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


def main(argv, projects=hub_projects):
    state_dir = os.environ.get("VOICE_STATE") or DEFAULT_STATE
    owner = None
    label = "com.agent-squad.voiced"
    python = sys.executable
    profiles = os.environ.get("SG_PROFILES_DIR") or LIVE_PROFILES   # a plist never names the deploy's example-only folder
    print_plist = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--state", "--owner", "--label", "--python", "--profiles") and i + 1 < len(argv):
            if a == "--state":
                state_dir = argv[i + 1]
            elif a == "--owner":
                owner = argv[i + 1]
            elif a == "--label":
                label = argv[i + 1]
            elif a == "--profiles":
                profiles = argv[i + 1]
            else:
                python = argv[i + 1]
            i += 2
        elif a == "--print-plist":
            print_plist = True
            i += 1
        elif a == "--print-profiles-default":
            # install-agents.sh reads its default from this one constant, so the folder is named in one place (#2957).
            sys.stdout.write(LIVE_PROFILES + "\n")
            return 0
        else:
            print("usage: voiced.py [--state DIR] [--owner USER_ID] [--print-plist [--label L] [--python PATH] [--profiles DIR]]"
                  " [--print-profiles-default]", file=sys.stderr)
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
                                    state_dir=state_dir, owner=owner, profiles=profiles,
                                    checkout=os.path.abspath(os.path.join(HERE, "..", ".."))))
        return 0
    lock = open(os.path.join(state_dir, "voiced.lock"), "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"voiced: another daemon holds {state_dir}; this start stands aside", file=sys.stderr)
        lock.close()
        return 1
    entries = projects()
    # Each project's own registry is the only authority for its Slack revision (#2695, #2961): the app set this start
    # opens sockets for comes from the revisions that project's registry lists alone, and no listed revision means no
    # channel of that project. The same hub seam feeds the daemon's configs and this start's seam, so the two never
    # name different project sets.
    registries = hub_registries(entries)
    listed = {repo: listed_revisions(path) for repo, path in registries.items()}
    specs = app_keychains(project_documents_at_start(entries, listed=listed))
    if not specs:      # a machine with no per-project Slack file keeps one socket on the legacy Keychain entries
        specs = {"legacy": {"bot": keychain.BOT_TOKEN, "app": keychain.APP_TOKEN}}
    try:
        key = keychain.read_secret(keychain.GEMINI_KEY)
        tokens = app_tokens(specs)
    except keychain.SecretMissing as x:
        print(f"voiced: Keychain entry missing: {x}", file=sys.stderr)
        return 1
    bots = {app_id: entry["bot"] for app_id, entry in tokens.items()}
    bot = next(iter(bots.values()))      # the fallback for a channel no active document maps to an app
    apps = {app_id: entry["app"] for app_id, entry in tokens.items()}
    daemon = Daemon(state=state, owner=owner,
                    transcriber=lambda audio, mime: transcribe(audio, mime, key),
                    downloader=lambda url: download(url, bot),
                    client=SlackClient(bot), projects=hub_repos, configs=projects,
                    registry=hub_registry_seam(entries),
                    synthesizer=lambda script, voice=None, tone=None:
                        spoken_clip(key, script, voice=voice or VOICE, tone=tone or ""),
                    uploader=lambda path, channel, thread_ts, title: upload_clip(bot, path, channel, thread_ts, title),
                    clients={app_id: SlackClient(entry) for app_id, entry in bots.items()},
                    bot_tokens=bots,
                    downloaders={app_id: (lambda url, token=entry: download(url, token))
                                 for app_id, entry in bots.items()},
                    uploaders={app_id: (lambda path, channel, thread_ts, title, token=entry:
                                        upload_clip(token, path, channel, thread_ts, title))
                               for app_id, entry in bots.items()},
                    starter=start_operator)
    # Refuse a channel two projects claim before any socket answers, so the first poll already holds it.
    daemon.refuse_channel_claims(entries)
    try:
        asyncio.run(serve(daemon, apps))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
