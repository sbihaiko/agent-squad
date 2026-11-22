"""The one home of the Slack file rules (ADR-0177).

`docs/squad/slack.yaml` sits beside the project settings file and is committed. `<profile>/<project>.slack.local.yaml`
sits in the profile folder and never enters a repository. This module loads both files, validates each, and returns one
`Defect` per failing key, so a caller prints a message that names the key. It reads no clock and calls no network.

`squad slack check` imports it, and the hub imports it before it writes a Slack setting. No other file holds a copy of
these rules. `decide` maps one event of one channel to one decision; the caller passes the clock value, and the module
reads neither a clock nor the network. A `send_grant` lives in the local file, where only the hub writes it, and it
holds the date of the grant (story 38).

Guards: `tests/test_slack_policy.py`, `tests/test_slack_decision.py`, `tests/test_cli_slack_check.py`,
`contrib/squad-goal/scripts/test-commit-guard.sh`.
"""
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import yaml

COMMITTED = Path("docs") / "squad" / "slack.yaml"
LOCAL_SUFFIX = "slack.local.yaml"       # a file whose name ends in this suffix is refused by the commit guard
ADR_DIR = Path(__file__).resolve().parent.parent / "docs" / "adr"

# A token never enters a file: the file names the Keychain entry instead.
TOKEN_PREFIXES = ("xoxb-", "xapp-", "xoxp-", "tskey-")
# The predefined Gemini TTS voices. A persona voice is one of these names.
GEMINI_VOICES = frozenset((
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe",
    "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia",
    "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi", "Vindemiatrix",
    "Sadachbia", "Sadaltager", "Sulafat",
))
LANGUAGES = ("pt-BR", "en-US", "es-ES")
NAME = re.compile(r"^[A-Za-z0-9 _.\-]{1,32}\Z")     # ASCII letters, digits, space, period, hyphen, underscore; at most 32
TONE_MAX = 300
SECRET_KEY = "secret"


class Defect(NamedTuple):
    """One failing key: the file it lives in, its path in that file, and the rule it breaks."""
    file: str
    key: str
    reason: str

    def __str__(self):
        return f"{self.file}: {self.key}: {self.reason}"


def normalize_language(raw):
    """The BCP 47 tag a written language names, or None when it names none of the three tags."""
    text = str(raw).strip().replace("_", "-")
    if not text:
        return None
    parts = text.split("-")
    if len(parts) == 1:
        tag = "es-ES" if parts[0].lower() == "es" else None
    else:
        tag = f"{parts[0].lower()}-{'-'.join(p.upper() if len(p) == 2 else p for p in parts[1:])}"
    return tag if tag in LANGUAGES else None


def token_at(value):
    """The token prefix a string carries, or None. The test is case-insensitive: a pasted token keeps its shape."""
    low = str(value).lower()
    for prefix in TOKEN_PREFIXES:
        if prefix in low:
            return prefix
    return None


def first_token(data):
    """(key path, prefix) of the first string that carries a token pattern in a document, or None. Keys are checked too."""
    for path, text in strings(data):
        hit = token_at(text)
        if hit:
            return path, hit
    return None


def strings(node, path="", seen=None):
    """Every (key path, string) of a document, nested lists and mappings included; a mapping key counts as a string.
    A key that equals its scalar value is yielded once: one string is one defect."""
    seen = set() if seen is None else seen
    if isinstance(node, str):
        pair = (path or "(root)", node)
        if pair not in seen:
            seen.add(pair)
            yield pair
    elif isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            yield from strings(str(key), here, seen)
            yield from strings(value, here, seen)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from strings(value, f"{path}[{i}]" if path else f"[{i}]", seen)


def _defect(file, key, reason):
    return Defect(file, key, reason)


def _language(file, key, value, out):
    if value is None:
        return
    if normalize_language(value) is None:
        out.append(_defect(file, key, f"language {value!r} is not one of {', '.join(LANGUAGES)}"))


def _personas(file, doc, out):
    for i, persona in enumerate(_rows(doc.get("personas"))):
        base = f"personas[{i}]"
        if persona.get("name") is not None and not NAME.match(str(persona["name"])):
            out.append(_defect(file, f"{base}.name", "a name holds letters, digits, space, period, hyphen and underscore, and at most 32 characters"))
        voice = persona.get("voice")
        if voice is not None and voice not in GEMINI_VOICES:
            out.append(_defect(file, f"{base}.voice", f"voice {voice!r} is not a Gemini voice"))
        tone = persona.get("tone")
        if tone is not None and len(str(tone)) > TONE_MAX:
            out.append(_defect(file, f"{base}.tone", f"a tone holds at most {TONE_MAX} characters ({len(str(tone))} given)"))


def _roles(file, doc, out):
    for i, role in enumerate(_rows(doc.get("roles"))):
        deploy = role.get("deploy")
        items = deploy.get("environments") if isinstance(deploy, dict) else deploy
        if isinstance(items, (list, tuple)):
            for env in items:
                if str(env).strip().lower() == "prod":
                    out.append(_defect(file, f"roles[{i}].deploy", "a role never deploys to prod; production stays with the owner"))


def _secret_keys(file, node, path, out):
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if str(key).strip().lower() == SECRET_KEY:
                out.append(_defect(file, here, "a key is never named secret; an app names its Keychain entries"))
            _secret_keys(file, value, here, out)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _secret_keys(file, value, f"{path}[{i}]", out)


def _rows(value):
    """The mappings of a list, so a file whose shape is wrong still yields the keys it can name."""
    return [row for row in (value or []) if isinstance(row, dict)] if isinstance(value, list) else []


def _grant_date(value):
    """True when the value is the date of a grant: `YYYY-MM-DD`, and a day the calendar holds."""
    text = str(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _send_grants(file, doc, out, local):
    """The defects of each `send_grant` of a file. The grant lives in the local file, where only the hub writes it, and
    it holds the date of the grant. A grant in the committed file is refused, and so is a value that is not a date."""
    for i, ws in enumerate(_rows(doc.get("workspaces"))):
        for j, ch in enumerate(_rows(ws.get("channels"))):
            if ch.get("send_grant") is None:
                continue
            key = f"workspaces[{i}].channels[{j}].send_grant"
            if not local:
                out.append(_defect(file, key, "the send_grant lives in the local file, where only the hub writes it"))
            elif not _grant_date(ch["send_grant"]):
                out.append(_defect(file, key, f"send_grant {ch['send_grant']!r} is not the date of the grant "
                                              "(YYYY-MM-DD)"))


def _guest_ids(local):
    """The team ids and channel ids a local file declares as guest."""
    teams, channels = set(), set()
    for ws in _rows(local.get("workspaces")):
        if str(ws.get("class", "")) != "guest":
            continue
        if ws.get("id"):
            teams.add(str(ws["id"]))
        channels.update(str(ch.get("id")) for ch in _rows(ws.get("channels")) if ch.get("id"))
    return teams, channels


def validate(committed=None, local=None, others=(), local_name="slack.local.yaml"):
    """Every defect of the committed file and the local file, one per failing key. `others` are `(file name, document)`
    pairs of the other projects' local files on the machine: the connector reaches one guest workspace at a time, summed
    over all of them, and their token and `secret` rules hold too. `local_name` is the local file's own name, so a
    message names the file a person edits in the profile folder."""
    out = []
    committed = committed or {}
    local = local or {}
    ours = (("slack.yaml", committed), (local_name, local))
    for file, doc in ours:
        hit = first_token(doc)
        if hit:
            out.append(_defect(file, hit[0], f"a value in token format ({hit[1]}) is refused; the file names a Keychain entry"))
        _secret_keys(file, doc, "", out)
    for name, doc in others:
        hit = first_token(doc)
        if hit:
            out.append(_defect(name, hit[0], f"a value in token format ({hit[1]}) is refused; the file names a Keychain entry"))
        _secret_keys(name, doc, "", out)
    for file, doc in ours:
        _personas(file, doc, out)
        _roles(file, doc, out)
    _send_grants("slack.yaml", committed, out, local=False)
    _send_grants(local_name, local, out, local=True)
    _language("slack.yaml", "project.language", (committed.get("project") or {}).get("language"), out)
    for file, doc in ours:
        for i, ws in enumerate(_rows(doc.get("workspaces"))):
            base = f"workspaces[{i}]"
            _language(file, f"{base}.language", ws.get("language"), out)
            for j, ch in enumerate(_rows(ws.get("channels"))):
                _language(file, f"{base}.channels[{j}].language", ch.get("language"), out)
    teams, channels = _guest_ids(local)
    for i, ws in enumerate(_rows(committed.get("workspaces"))):
        if str(ws.get("id")) in teams:
            out.append(_defect("slack.yaml", f"workspaces[{i}].id", "a guest team id stays in the local file"))
        for j, ch in enumerate(_rows(ws.get("channels"))):
            if str(ch.get("id")) in channels:
                out.append(_defect("slack.yaml", f"workspaces[{i}].channels[{j}].id", "a guest channel id stays in the local file"))
    for file, i in _second_guests(local_name, local, others):
        out.append(_defect(file, f"workspaces[{i}].id",
                           "the machine serves one guest workspace at a time; a second guest workspace is refused"))
    return out


def _second_guests(local_name, local, others):
    """Every guest workspace after the first on the machine, as `(file, index)`. The current project's file comes first,
    then the other projects' files by name, so each defect names the file that holds the refused workspace."""
    guests = [(local_name, i) for i, ws in enumerate(_rows(local.get("workspaces"))) if str(ws.get("class", "")) == "guest"]
    for name, doc in sorted(others, key=lambda pair: str(pair[0])):
        guests += [(name, i) for i, ws in enumerate(_rows(doc.get("workspaces"))) if str(ws.get("class", "")) == "guest"]
    return guests[1:]


def read_document(text, file, out=None):
    """The mapping a Slack file's text holds, or None with one defect that names the file. A caller that holds the text
    of a file it did not read from disk uses this: `voiced` validates a `slack.yaml` read from a project's dev tip.
    An empty file is an empty mapping."""
    out = [] if out is None else out
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        out.append(_defect(file, "(root)", f"not valid YAML: {' '.join(str(e).split())}"))
        return None
    if doc is None:
        return {}
    if not isinstance(doc, dict):
        out.append(_defect(file, "(root)", "a Slack file holds one mapping"))
        return None
    return doc


def _load(path, file, out):
    """The document a YAML file holds, or None with one defect that names the file. An absent file is no defect; a file
    the reader cannot open is one, so a broken permission never hides a broken key."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return None
    except OSError as e:
        out.append(_defect(file, "(root)", f"cannot read the file: {e.strerror or e}"))
        return None
    return read_document(text, file, out)


def local_path(profile, project):
    """The local file of one project in a profile folder: `<project>.slack.local.yaml`, else `slack.local.yaml`."""
    if profile is None:
        return None
    named = Path(profile) / f"{project}.slack.local.yaml"
    return named if named.is_file() else (Path(profile) / LOCAL_SUFFIX if (Path(profile) / LOCAL_SUFFIX).is_file() else None)


def project_root(target="."):
    """The project root of a target: the first folder with a `.git` between it and the filesystem root, else the target.
    A command run from a subdirectory still reads the one committed file at the root."""
    start = Path(target).resolve()
    if start.is_file():
        start = start.parent
    for folder in (start, *start.parents):
        if (folder / ".git").exists():
            return folder
    return start


def check(target=".", profile=None, project=None):
    """Every defect of a project's committed file and its local file, read from disk. A file that is absent is no defect."""
    out = []
    root = project_root(target)
    committed = _load(root / COMMITTED, "slack.yaml", out)
    project = project or os.environ.get("SG_PROJECT") or root.name
    profile = profile or os.environ.get("SG_PROFILES_DIR") or None
    path = local_path(profile, project)
    local = _load(path, Path(path).name, out) if path else None
    others = []
    if profile:
        for other in sorted(Path(profile).glob(f"*{LOCAL_SUFFIX}")):
            if path and other.resolve() == Path(path).resolve():
                continue
            doc = _load(other, other.name, out)
            if doc:
                others.append((other.name, doc))
    found = validate(committed, local, others, local_name=Path(path).name if path else "slack.local.yaml")
    return out + found


# One decision of the policy module. The four kinds are the whole set: an event the module does not handle, a message
# that stays data, a command that reaches the inbox, and a request that becomes a proposal to the owner.
IGNORE, DATA, INBOX, PROPOSAL = "ignore", "data", "inbox", "proposal"
DEFAULT_LANGUAGE = "en-US"
# The reply paths that leave from the owner's account, so the reply needs the AI authorship mark (SPEC 2682).
_OWNER_ACCOUNT_REPLIES = ("draft", "send")
_MS = {"s": 1, "m": 60, "h": 3600}
_POLL_DEFAULTS = {"fast": "1m", "slow": "10m", "quiet_after": "10m", "thread_open": "30m"}
_EXP_DEFAULTS = {"floor": "5m", "ceiling": "1h", "factor": 2, "idle_window": "15m", "thread_open": "30m"}


class Persona(NamedTuple):
    """The identity a channel answers with: the bot name, the voice, and the tone. It carries no language, no permission."""
    id: str
    name: str
    voice: str
    tone: str


class Decision(NamedTuple):
    """What the operator session may do with one event: the kind of decision, the caller's role and actions, the
    language and persona of the channel, the reply path (`bot`, `draft` or `send`), the mark flag, and, for a `guest`
    channel, the time of the next poll.

    Field population per kind. `language` and `persona` are always set, from the channel, its workspace or the project.
    `role` is set when the files name one, and `actions` only on `inbox` and `proposal`. `reply` is set only on `inbox`
    and `proposal`, and never in a `listen` channel. `mark` is true exactly when `reply` is `draft` or `send`, because
    that reply leaves from the owner's account; a `bot` reply and a decision with no reply carry no mark. `next_poll` is
    set only for an event of a known `guest` channel, so it is None for an unknown channel and for `ignore`/`data` of a
    non-guest channel."""
    kind: str
    role: str | None
    actions: tuple
    language: str
    persona: Persona | None
    reply: str | None
    mark: bool
    next_poll: datetime | None


def _row_at(rows, key, value):
    for row in _rows(rows):
        if str(row.get("id")) == str(value):
            return row
    return None


def _project(committed, local):
    """The project row: the committed file's project, with a key the local file adds only where the committed file omits
    it. The local file never overrides a committed value, because it may not change a persona (ADR-0177)."""
    merged = dict(local.get("project") or {})
    merged.update(committed.get("project") or {})
    return merged


def _effective_language(committed, local, workspace, channel):
    """The language of a channel: the channel first, then its Slack workspace, then the project, then `en-US`. An absent
    channel or workspace reads as an empty row, so the same chain answers an event of an unknown channel."""
    workspace, channel = workspace or {}, channel or {}
    for value in (channel.get("language"), workspace.get("language"), _project(committed, local).get("language")):
        if value is not None:
            tag = normalize_language(value)
            if tag:
                return tag
    return DEFAULT_LANGUAGE


def _effective_persona(committed, local, workspace, channel):
    """The persona of a channel, resolved in the same order as the language. A channel, a workspace and the project name
    an id; the `Persona` row comes from the committed file, then the local file."""
    workspace, channel = workspace or {}, channel or {}
    wanted = None
    for value in (channel.get("persona"), workspace.get("persona"), _project(committed, local).get("persona")):
        if value is not None:
            wanted = value
            break
    if wanted is None:
        return None
    row = _row_at(committed.get("personas"), "id", wanted) or _row_at(local.get("personas"), "id", wanted)
    if row is None:
        return None
    return Persona(str(row.get("id")), str(row.get("name")), str(row.get("voice")), str(row.get("tone")))


def _member_role(local, workspace_id, channel_id, user, accepted):
    """The role the local file gives this user in this channel, or None. A membership in a role the channel does not
    accept grants nothing, so a stranger's message stays data. `accepted` is None for an unknown channel: no action is
    granted there, so the decision reports the role the file names without an acceptance check."""
    for member in _rows(local.get("members")):
        if (str(member.get("workspace")), str(member.get("channel"))) != (workspace_id, channel_id):
            continue
        if user in {str(u) for u in member.get("users") or []}:
            role = str(member.get("role"))
            if accepted is None or role in accepted:
                return role
    return None


def _role_actions(committed, role):
    for row in _rows(committed.get("roles")):
        if str(row.get("id")) == role:
            return tuple(str(action) for action in row.get("actions") or ())
    return ()


def _reply_path(class_, channel, user):
    """The reply path: `bot` in an `owned` channel, a `draft` by default in a `guest` channel, `send` only with an
    explicit `speak: send` and a `send_grant`, and none in a `listen` channel.

    A Slack Connect channel (`connect: true`) never gets a direct send, because `slack_send_message` does not post there.
    A reply addressed to a person in `draft_for` already leaves as a draft, even with a grant, so the owner reviews what
    goes to that person."""
    if str(channel.get("mode") or "listen") != "listen+speak":
        return None
    if str(class_) != "guest":
        return "bot"
    draft_for = {str(value) for value in channel.get("draft_for") or ()}
    if (str(channel.get("speak") or "draft") == "send" and channel.get("send_grant")
            and not channel.get("connect") and str(user) not in draft_for):
        return "send"
    return "draft"


def _seconds(value):
    """A duration of the file, as seconds: `1m`, `30m`, `1h`, or a number of seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    if text and text[-1] in _MS:
        return float(text[:-1]) * _MS[text[-1]]
    return float(text or 0)


def _elapsed(now, value):
    return (now - value).total_seconds()


def poll_interval(now, channel, thread_state):
    """The interval to the next poll of a `guest` channel, in seconds. `two_state` is fast after a message and slow
    after `quiet_after` of silence, and a channel with no prior message keeps the fast default; `exponential` multiplies
    the floor by the factor at each idle window up to the ceiling. A message in the agent's thread newer than
    `thread_open` keeps the interval fast in both modes. A caller that reads a channel with no message as quiet owns that
    rule itself, so the shared answer never changes under it."""
    poll = channel.get("poll") if isinstance(channel.get("poll"), dict) else {}
    state = thread_state or {}
    last = state.get("last_message_at")
    thread = state.get("thread_last_at")
    if str(poll.get("mode") or "two_state") == "exponential":
        floor = _seconds(poll.get("floor", _EXP_DEFAULTS["floor"]))
        ceiling = _seconds(poll.get("ceiling", _EXP_DEFAULTS["ceiling"]))
        factor = float(poll.get("factor", _EXP_DEFAULTS["factor"]))
        window = _seconds(poll.get("idle_window", _EXP_DEFAULTS["idle_window"]))
        open_ = _seconds(poll.get("thread_open", _EXP_DEFAULTS["thread_open"]))
        if thread is not None and _elapsed(now, thread) <= open_:
            return floor
        if last is None or window <= 0:
            return floor
        steps = max(0, int(_elapsed(now, last) // window))
        return min(floor * (factor ** steps), ceiling)
    fast = _seconds(poll.get("fast", _POLL_DEFAULTS["fast"]))
    slow = _seconds(poll.get("slow", _POLL_DEFAULTS["slow"]))
    quiet = _seconds(poll.get("quiet_after", _POLL_DEFAULTS["quiet_after"]))
    open_ = _seconds(poll.get("thread_open", _POLL_DEFAULTS["thread_open"]))
    if thread is not None and _elapsed(now, thread) <= open_:
        return fast
    if last is not None and _elapsed(now, last) >= quiet:
        return slow
    return fast


def decide(committed, local, event, now, thread_state=None):
    """The one decision for one event of one channel, from the two validated files and the caller's clock value.

    `event` names `workspace`, `channel`, `user` and `kind` (a `message` unless stated), and flags `bot`, `external`
    and `own`. A `kind` other than `message` and an `own` event (the agent's own post) get `ignore`. `thread_state` names
    `last_message_at` and `thread_last_at` of the agent's thread. The module reads no clock and calls no network, so the
    caller passes both answers."""
    committed = committed or {}
    local = local or {}
    workspace_id = str(event.get("workspace") or "")
    channel_id = str(event.get("channel") or "")
    user = str(event.get("user") or "")
    workspace = _row_at(committed.get("workspaces"), "id", workspace_id) or _row_at(local.get("workspaces"), "id", workspace_id)
    channel = _row_at(workspace.get("channels"), "id", channel_id) if workspace else None
    language = _effective_language(committed, local, workspace, channel)
    persona = _effective_persona(committed, local, workspace, channel)
    if channel is None:
        # An unknown channel grants nothing, but the decision still reports the role the files give the caller in it.
        role = _member_role(local, workspace_id, channel_id, user, None) if workspace else None
        return Decision(IGNORE, role, (), language, persona, None, False, None)
    class_ = str(workspace.get("class") or "owned")
    next_poll = now + timedelta(seconds=poll_interval(now, channel, thread_state)) if class_ == "guest" else None
    role = _member_role(local, workspace_id, channel_id, user, {str(name) for name in channel.get("roles") or []})
    if str(event.get("kind") or "message") != "message" or event.get("own"):
        return Decision(IGNORE, role, (), language, persona, None, False, next_poll)
    if event.get("bot") or event.get("external") or role is None:
        return Decision(DATA, role, (), language, persona, None, False, next_poll)
    reply = _reply_path(class_, channel, user)
    kind = PROPOSAL if class_ == "guest" else INBOX
    return Decision(kind, role, _role_actions(committed, role), language, persona, reply, reply in _OWNER_ACCOUNT_REPLIES,
                    next_poll)
