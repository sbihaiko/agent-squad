"""The one home of the Slack file rules (ADR-0177).

`docs/squad/slack.yaml` sits beside the project settings file and is committed. `<profile>/<project>.slack.local.yaml`
sits in the profile folder and never enters a repository. This module loads both files, validates each, and returns one
`Defect` per failing key, so a caller prints a message that names the key. It reads no clock and calls no network.

`squad slack check` imports it, and the hub imports it before it writes a Slack setting. No other file holds a copy of
these rules. `decide` maps one event of one channel to one decision; the caller passes the clock value, and the module
reads neither a clock nor the network. A `send_grant` lives in the local file, where only the hub writes it, and it
holds the date of the grant (story 38). A channel row carries `class: guest` in the local file only, and the channel's
class wins over its workspace's class (#2770).

Guards: `tests/test_slack_policy.py`, `tests/test_slack_decision.py`, `tests/test_cli_slack_check.py`,
`tests/test_hub_slack_read.py`, `tests/test_slack_migrate.py`, `tests/test_slack_channel_class.py`,
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
USER_ID = re.compile(r"^[UW][A-Z0-9]{2,}\Z")   # a Slack user id: `U` or `W`, then uppercase letters and digits
# The two refusals the hub editor shows beside the field: the text lives here so the editor and the validator never
# state a rule twice (ADR-0177 decision 3).
GUEST_TEAM_REASON = "a guest team id stays in the local file"
SECOND_GUEST_REASON = "the machine serves one guest workspace at a time; a second guest workspace is refused"


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


def _deploy_identity(file, doc, out):
    """The deploy target of the project identity (#2724): one environment and one command, both non-empty, and never
    `prod`, because production stays with the owner. The requester supplies neither, so the identity declares both."""
    row = (doc.get("project") or {}).get("deploy")
    if row is None:
        return
    if not isinstance(row, dict):
        out.append(_defect(file, "project.deploy", "the deploy target names an environment and a command"))
        return
    environment = str(row.get("environment") or "").strip()
    command = str(row.get("command") or "").strip()
    if not environment:
        out.append(_defect(file, "project.deploy.environment", "the project identity names the deploy environment"))
    elif environment.lower() == "prod":
        out.append(_defect(file, "project.deploy.environment",
                           "the owner alone deploys to production; the project identity never declares prod"))
    if not command:
        out.append(_defect(file, "project.deploy.command", "the project identity names the deploy command"))


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
        if isinstance(role.get("actions"), (list, tuple)):
            actions = role_actions()
            for j, action in enumerate(role["actions"]):
                if str(action) not in actions:
                    out.append(_defect(file, f"roles[{i}].actions[{j}]",
                                       f"action {action!r} is not one of {', '.join(actions)}"))


def _members(file, doc, out, local):
    """The members of a role live in the local file (ADR-0177 decision 11). A member names its Slack workspace, its
    channel, its role and its user ids, and a user id is a Slack user id: `U` or `W`, then uppercase letters and
    digits (issue #2699)."""
    for i, member in enumerate(_rows(doc.get("members"))):
        base = f"members[{i}]"
        if not local:
            out.append(_defect(file, "members", "the members of a role live in the local file"))
            return
        for key in ("workspace", "channel", "role"):
            if not str(member.get(key) or "").strip():
                out.append(_defect(file, f"{base}.{key}", f"a member names its {key}"))
        if "alias" in member and not isinstance(member["alias"], str):
            out.append(_defect(file, f"{base}.alias", "a member alias is a string"))
        users = member.get("users")
        if not isinstance(users, list) or not users:
            out.append(_defect(file, f"{base}.users", "a member names at least one user id"))
            continue
        for j, user in enumerate(users):
            if not USER_ID.match(str(user)):
                out.append(_defect(file, f"{base}.users[{j}]",
                                   f"user {user!r} is not a Slack user id (U... or W...)"))


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


def _announce(file, doc, events, out):
    """The defects of each channel's `announce` list. The list names only the events the committed file declares, so a
    typo never subscribes a channel to nothing. A guest channel lives in the local file and names the same events."""
    for i, ws in enumerate(_rows(doc.get("workspaces"))):
        for j, ch in enumerate(_rows(ws.get("channels"))):
            announce = ch.get("announce")
            if announce is None:
                continue
            key = f"workspaces[{i}].channels[{j}].announce"
            if not isinstance(announce, list):
                out.append(_defect(file, key, "announce is a list of event ids"))
                continue
            for k, ev in enumerate(announce):
                if str(ev) not in events:
                    declared = ", ".join(sorted(events)) if events else "no event"
                    out.append(_defect(file, f"{key}[{k}]", f"announce names the event {str(ev)!r}, and the project "
                                                            f"declares {declared}"))


def channel_class(workspace, channel):
    """The class one channel takes: the channel's own `class`, then its workspace's class, else `owned`. A channel row
    carries `class: guest` in the local file only, so the owner tests the guest path in an owned workspace (#2770)."""
    return str((channel or {}).get("class") or (workspace or {}).get("class") or "owned")


def channel_row(local, workspace_id, channel_id):
    """The local file's row of one channel, or None when it names none. A caller merges it over the committed row, so
    the channel's class and its other keys read from the file that declares the guest channel."""
    workspace = _row_at((local or {}).get("workspaces"), "id", workspace_id)
    return _row_at(workspace.get("channels"), "id", channel_id) if workspace is not None else None


def _channel_classes(file, doc, out, local):
    """A channel class lives in the local file (ADR-0177), and it is `guest`: the local file narrows a channel and never
    widens it. The committed file never names one."""
    for i, ws in enumerate(_rows(doc.get("workspaces"))):
        for j, ch in enumerate(_rows(ws.get("channels"))):
            if ch.get("class") is None:
                continue
            key = f"workspaces[{i}].channels[{j}].class"
            if not local:
                out.append(_defect(file, key, "a channel class lives in the local file"))
            elif str(ch["class"]) != "guest":
                out.append(_defect(file, key, "a local channel class is guest"))


def _channel_replies(file, doc, out):
    """A channel's `reply` is `all` or `addressed`, and the default is `all`, so a value that names neither is a defect
    and never reads as `all` by accident (issue #2819)."""
    for i, ws in enumerate(_rows(doc.get("workspaces"))):
        for j, ch in enumerate(_rows(ws.get("channels"))):
            reply = ch.get("reply")
            if reply is not None and str(reply) not in FIELD_REPLY:
                out.append(_defect(file, f"workspaces[{i}].channels[{j}].reply",
                                   f"reply {reply!r} is one of {', '.join(FIELD_REPLY)}"))


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
        _members(file, doc, out, local=(file == local_name))
        _channel_classes(file, doc, out, local=(file == local_name))
        _channel_replies(file, doc, out)
    _send_grants("slack.yaml", committed, out, local=False)
    _send_grants(local_name, local, out, local=True)
    _language("slack.yaml", "project.language", (committed.get("project") or {}).get("language"), out)
    _deploy_identity("slack.yaml", committed, out)
    events = {str(row.get("id")) for row in _rows(committed.get("events")) if row.get("id") is not None}
    for file, doc in ours:
        _announce(file, doc, events, out)
        for i, ws in enumerate(_rows(doc.get("workspaces"))):
            base = f"workspaces[{i}]"
            _language(file, f"{base}.language", ws.get("language"), out)
            for j, ch in enumerate(_rows(ws.get("channels"))):
                _language(file, f"{base}.channels[{j}].language", ch.get("language"), out)
    teams, channels = _guest_ids(local)
    for i, ws in enumerate(_rows(committed.get("workspaces"))):
        if str(ws.get("id")) in teams:
            out.append(_defect("slack.yaml", f"workspaces[{i}].id", GUEST_TEAM_REASON))
        for j, ch in enumerate(_rows(ws.get("channels"))):
            if str(ch.get("id")) in channels:
                out.append(_defect("slack.yaml", f"workspaces[{i}].channels[{j}].id", "a guest channel id stays in the local file"))
    for file, i in _second_guests(local_name, local, others):
        out.append(_defect(file, f"workspaces[{i}].id", SECOND_GUEST_REASON))
    return out


def _guest_workspace(ws):
    """True when the connector reaches this workspace, so it takes the machine's one guest slot: the workspace is
    `guest`, or one of its channels carries the guest class (#2770)."""
    if str(ws.get("class", "")) == "guest":
        return True
    return any(str(ch.get("class") or "") == "guest" for ch in _rows(ws.get("channels")))


def _second_guests(local_name, local, others):
    """Every guest workspace after the first on the machine, as `(file, index)`. The current project's file comes first,
    then the other projects' files by name, so each defect names the file that holds the refused workspace."""
    guests = [(local_name, i) for i, ws in enumerate(_rows(local.get("workspaces"))) if _guest_workspace(ws)]
    for name, doc in sorted(others, key=lambda pair: str(pair[0])):
        guests += [(name, i) for i, ws in enumerate(_rows(doc.get("workspaces"))) if _guest_workspace(ws)]
    return guests[1:]


FIELD_CLASSES = ("owned", "guest")
FIELD_MODES = ("listen", "listen+speak")
FIELD_SPEAK = ("draft", "send")
FIELD_REPLY = ("all", "addressed")     # the default is `all`
FIELD_TRANSCRIBE = ("off", "local", "gemini")
FIELD_POLL_MODES = ("two_state", "exponential")
POLL_DEFAULT_MODE = "two_state"     # the mode a guest channel polls with when its row names none (#2845)


def _key(path):
    """A key path as the validator names it: `workspaces[0].channels[0].speak`."""
    out = ""
    for step in path:
        out += f"[{step}]" if isinstance(step, int) else (f".{step}" if out else str(step))
    return out


def _ids(doc, name):
    return {str(row.get("id")) for row in _rows((doc or {}).get(name)) if isinstance(row, dict) and row.get("id") is not None}


def validate_field(documents, doc, path, field, value):
    """The one failing key of one editor value, as `(key, reason)`, or None when the value keeps its rule. `documents`
    holds both files, `doc` names the file the edit writes, and `path` is the key path of the edited field. The hub
    editor asks this function, so a refusal it shows lives here (ADR-0177 decision 3), never in the view or the hub."""
    documents = documents or {}
    committed = documents.get("committed") or {}
    key = _key(path)

    def bad(reason):
        return key, reason

    if field == "class" and "channels" in path:
        if str(value) != "guest":
            return bad("a local channel class is guest")
        return None
    if field == "class" and str(value) not in FIELD_CLASSES:
        return bad(f"the class is one of {', '.join(FIELD_CLASSES)}")
    if field == "mode" and str(value) not in FIELD_MODES:
        return bad(f"the mode is one of {', '.join(FIELD_MODES)}")
    if field == "language" and str(value) != "" and normalize_language(value) is None:
        return bad(f"language {value!r} is not one of {', '.join(LANGUAGES)}")
    if field == "persona" and str(value) != "":
        known = _ids(committed, "personas")
        if known and str(value) not in known:
            return bad(f"persona {value!r} is not one of the project's personas")
    if field == "roles":
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            return bad("the roles are a list of role ids")
        known = _ids(committed, "roles")
        unknown = [x for x in value if known and x not in known]
        if unknown:
            return bad(f"role {unknown[0]!r} is not one of the project's roles")
    if field == "announce":
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            return bad("the announce list is a list of event ids")
        known = _ids(committed, "events")
        unknown = [x for x in value if known and x not in known]
        if unknown:
            return bad(f"event {unknown[0]!r} is not one of the project's events")
    if field == "speak" and str(value) not in FIELD_SPEAK:
        return bad(f"the answer is one of {', '.join(FIELD_SPEAK)}")
    if field == "reply" and str(value) not in FIELD_REPLY:
        return bad(f"the reply is one of {', '.join(FIELD_REPLY)}")
    if field == "transcribe" and str(value) not in FIELD_TRANSCRIBE:
        return bad(f"the transcription is one of {', '.join(FIELD_TRANSCRIBE)}")
    if field == "voice_reply" and not isinstance(value, bool):
        return bad("the voice reply is true or false")
    if field == "poll" and not isinstance(value, dict):
        return bad("the poll is a mapping")
    if field == "poll.mode" and str(value) not in FIELD_POLL_MODES:
        return bad(f"the poll mode is one of {', '.join(FIELD_POLL_MODES)}")
    if field == "draft_for" and (not isinstance(value, list) or not all(isinstance(x, str) for x in value)):
        return bad("draft for is a list of user ids")
    return None


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
    """The local file of one project in a profile folder: `<project>.slack.local.yaml`, else `slack.local.yaml`, else the
    name this project's first save writes. Only a profile folder that resolves answers None, so the first save of a
    project writes the file it builds (#2762)."""
    if profile is None:
        return None
    named = Path(profile) / f"{project}.slack.local.yaml"
    return named if named.is_file() else (Path(profile) / LOCAL_SUFFIX if (Path(profile) / LOCAL_SUFFIX).is_file() else named)


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

    Field population per kind. `language` and `persona` are always set, from the channel, its workspace or the project,
    and a leading `@<name>` of the event text selects the persona of that name. In a `reply: addressed` channel, a
    message that addresses no persona has no reply path.
    `role` is set when the files name one, and `actions` only on `inbox` and `proposal`. `reply` is set only on `inbox`
    and `proposal`, and never in a `listen` channel. `mark` is true exactly when `reply` is `draft` or `send`, because
    that reply leaves from the owner's account; a `bot` reply and a decision with no reply carry no mark. `next_poll` is
    set only for an event of a known `guest` channel, so it is None for an unknown channel and for `ignore`/`data` of a
    non-guest channel. `voice_reply` is true exactly when the `guest` channel asks for the reply also in audio, so a
    channel that does not ask carries no clip (story 70). `deploy` is the tuple of environments the caller's role lists
    for the `deploy` action, and it is empty for a decision with no role (#2724)."""
    kind: str
    role: str | None
    actions: tuple
    language: str
    persona: Persona | None
    reply: str | None
    mark: bool
    next_poll: datetime | None
    voice_reply: bool = False
    deploy: tuple = ()


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


def project_identity(committed, local):
    """The project row the deploy action reads its target from: the same merged row the effective chain ends on, so the
    executor and the writer resolve the project identity in one place."""
    return _project(committed or {}, local or {})


def deploy_target(identity):
    """The `(environment, command)` the project identity declares for deploy, or `("", "")` when it declares none. The
    environment and the command both live in the identity, and the requester supplies neither (SPEC 2682 S5, #2724)."""
    row = (identity or {}).get("deploy") if isinstance(identity, dict) else None
    if not isinstance(row, dict):
        return "", ""
    return str(row.get("environment") or "").strip(), str(row.get("command") or "").strip()


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


def mark(persona):
    """The authorship mark that opens every text sent from the owner's account: `*<name>'s Msg*`, alone and bold. A
    persona with no name, and a missing persona, carry no mark, so a channel never wears another persona's name."""
    name = getattr(persona, "name", "") if persona is not None else ""
    return f"*{name}'s Msg*" if name else ""


def effective_persona(committed, local, workspace, channel):
    """The persona of a channel, resolved in the same order as the language. A channel, a workspace and the project name
    an id; the `Persona` row comes from the committed file, then the local file. The announcement writer reads it too."""
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


def persona_named(committed, local, name):
    """The persona a name declares, or None. A name is a field of a persona row of the project's two files, and the
    match ignores case. Slack has no user for a persona, so the address is plain text, never a native mention."""
    wanted = str(name or "").strip().lower()
    if not wanted:
        return None
    rows = _rows((committed or {}).get("personas")) + _rows((local or {}).get("personas"))
    for row in rows:
        if str(row.get("name") or "").strip().lower() == wanted:
            return Persona(str(row.get("id")), str(row.get("name")), str(row.get("voice")), str(row.get("tone")))
    return None


def addressed(committed, local, text):
    """The persona a leading `@<name>` of a message names, or None. The match ignores case, and only a persona the
    project declares answers, so a name no persona declares reads as no address. The longest declared name wins, so a
    name with a space still matches. A native mention (`<@U...>`) is no address: the message keeps the channel persona.
    A leading Slack quote marker (`>` and its run) is stripped first, so a quoted reply that addresses a persona still
    addresses it."""
    body = str(text or "").lstrip()
    while body.startswith(">"):
        body = body[1:].lstrip()
    if not body.startswith("@"):
        return None
    rest = body[1:]
    low = rest.lower()
    best = None
    for name in {str(row.get("name") or "") for row in
                 _rows((committed or {}).get("personas")) + _rows((local or {}).get("personas"))}:
        if not name or not low.startswith(name.lower()):
            continue
        after = rest[len(name):]
        if after and (after[0].isalnum() or after[0] in "._-"):
            continue        # the name runs into a longer word, so it is no address of this persona
        if best is None or len(name) > len(best):
            best = name
    return persona_named(committed, local, best) if best is not None else None


def identity(committed, local, channel_id):
    """The language and persona one channel answers with, from its id alone: the same chain `decide` uses.

    The daemon reads them for a channel that no event names, such as the channel of an outbox line. The committed file
    comes first, then the local file, and a channel neither file names falls back to the project default, as an event of
    an unknown channel does. The persona is None when no file names one."""
    committed = committed or {}
    local = local or {}
    channel_id = str(channel_id or "")
    workspace = channel = None
    for document in (committed, local):
        for ws in _rows(document.get("workspaces")):
            row = _row_at(ws.get("channels"), "id", channel_id)
            if row is not None:
                workspace, channel = ws, row
                break
        if channel is not None:
            break
    return _effective_language(committed, local, workspace, channel), effective_persona(committed, local, workspace, channel)


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


def _role_deploy(committed, role):
    """The environments the role lists for deploy, in file order: the written grant of the owner for each one. A role
    that names none, and a role the file does not hold, list nothing (SPEC 2682 S5, #2724)."""
    for row in _rows(committed.get("roles")):
        if str(row.get("id")) == role:
            deploy = row.get("deploy")
            items = deploy.get("environments") if isinstance(deploy, dict) else deploy
            if isinstance(items, (list, tuple)):
                return tuple(str(environment) for environment in items)
            return ()
    return ()


def can_speak(channel):
    """True when the channel's `mode` lets the agent post. One mode name answers every writer: the reply path and the
    announcement writer both read it here, so a new writable mode is added in one place."""
    return str(channel.get("mode") or "listen") == "listen+speak"


def _reply_path(class_, channel, user):
    """The reply path: `bot` in an `owned` channel, a `draft` by default in a `guest` channel, `send` only with an
    explicit `speak: send` and a `send_grant`, and none in a `listen` channel.

    A Slack Connect channel (`connect: true`) never gets a direct send, because `slack_send_message` does not post there.
    A reply addressed to a person in `draft_for` already leaves as a draft, even with a grant, so the owner reviews what
    goes to that person."""
    if not can_speak(channel):
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


def poll_defaults(mode=None):
    """The poll settings a guest channel reads when its row names none, for `mode` or the default mode (#2845). The hub
    draws these values and holds no copy of them; a mode the policy does not know answers the two-state settings, as
    `poll_interval` reads it."""
    return dict(_EXP_DEFAULTS if str(mode or POLL_DEFAULT_MODE) == "exponential" else _POLL_DEFAULTS)


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


def role_actions():
    """The actions a role may list, in name order: the executor runs them, and the editor offers them (issue #2699).
    The import runs here, not at module import, so `squad.slackexecutor` imports this module first without a cycle
    (#2747). One vocabulary, never a copy."""
    from .slackexecutor import ACTIONS
    return tuple(sorted(ACTIONS))


def rows(value):
    """The row list a key of a Slack file holds: its mappings only, in file order, so a reader never breaks on a bad
    shape the validator already names."""
    return _rows(value)


def documents(committed_path, local_path):
    """The two documents of one read, and one `Defect` per file that cannot be read. An absent file is an empty mapping
    and no defect. The hub's read view asks this, so it holds no copy of the loader (ADR-0177)."""
    out = []
    committed = _load(committed_path, Path(committed_path).name, out) if committed_path else None
    local = _load(local_path, Path(local_path).name, out) if local_path else None
    return committed or {}, local or {}, out


def project_settings(committed, local):
    """The project row the effective chain ends on: the language as a tag (`en-US` when the files name none or an
    unknown one) and the persona id, or None when the files name none. `stored_language` is the tag the file names, or
    None when it names none, so the project's select shows `Not set` and not the default (#2845)."""
    row = _project(committed or {}, local or {})
    raw = row.get("language")
    stored = normalize_language(raw) if raw is not None else None
    return {"language": stored or DEFAULT_LANGUAGE, "persona": row.get("persona"), "stored_language": stored}


def effective(committed, local, workspace, channel=None):
    """The language and the persona one channel row answers with, in the channel, workspace, project order. The channel
    is None for a workspace row, which resolves in the workspace, project order. The persona is a `Persona` row or None."""
    committed, local = committed or {}, local or {}
    return (_effective_language(committed, local, workspace, channel),
            effective_persona(committed, local, workspace, channel))


def resolution(committed, local, workspace, channel=None):
    """The language and the persona a row answers with, and the level each one comes from: `channel`, `workspace`,
    `project` or `default`. The chain is the one `decide` reads (#2824). A level that names a value the tag rule
    refuses is skipped, as `_effective_language` skips it, so a broken value never becomes the source."""
    workspace, channel = workspace or {}, channel or {}
    project = _project(committed or {}, local or {})
    language = language_source = None
    for level, row in (("channel", channel), ("workspace", workspace), ("project", project)):
        if row.get("language") is None:
            continue
        tag = normalize_language(row.get("language"))
        if tag:
            language, language_source = tag, level
            break
    if language is None:
        language, language_source = DEFAULT_LANGUAGE, "default"
    wanted = None
    for level, row in (("channel", channel), ("workspace", workspace), ("project", project)):
        if wanted is None and row.get("persona") is not None:
            wanted = row.get("persona")
            persona_source = level
    if wanted is None:
        persona_source = "default"
    return {"language": language, "language_source": language_source,
            "persona": effective_persona(committed, local, workspace, channel), "persona_source": persona_source}


def resolved_members(local, workspace_id, channel_id, accepted):
    """Every user the local file gives a role this channel accepts, in file order. A role the channel does not accept
    grants nothing, so its users are absent, as they are in `_member_role`."""
    out = []
    for member in _rows((local or {}).get("members")):
        if (str(member.get("workspace")), str(member.get("channel"))) != (str(workspace_id), str(channel_id)):
            continue
        if str(member.get("role")) in {str(name) for name in accepted or ()}:
            out += [str(u) for u in member.get("users") or []]
    return out


def guest_workspaces(doc):
    """The indices of the workspaces the connector reaches: class `guest`, or one channel with the guest class
    (#2770). The machine serves one guest workspace, so the validator refuses every one after the first."""
    return [i for i, ws in enumerate(_rows((doc or {}).get("workspaces"))) if _guest_workspace(ws)]


def destination_label(decision):
    """The destination of an eligible member's message, in the words of the settings view: `inbox` in an `owned`
    channel, `proposal` in a `guest` channel, `read as data` when no member holds an accepted role, and `none` for a
    kind the channel ignores."""
    return {INBOX: "inbox", PROPOSAL: "proposal", DATA: "read as data"}.get(decision.kind, "none")


def reply_label(channel, decision):
    """The reply path the settings view prints: `bot`, `draft`, `send`, `send, except N listed`, `never posts` for a
    channel whose mode posts nothing, or `none` when the channel may reply and no member holds an accepted role."""
    channel = channel or {}
    if decision.reply:
        listed = len([value for value in channel.get("draft_for") or ()])
        if decision.reply == "send" and listed:
            return f"send, except {listed} listed"
        return decision.reply
    return "never posts" if not can_speak(channel) else "none"


def channel_note(channel, members, announcement):
    """The one plain sentence the settings view prints under a channel's Now tiles, naming how a message resolves. A
    channel with no eligible member says a message stays data; a listen channel with a member names the proposal path;
    a channel that speaks and has a member needs no note. The view draws this sentence and holds no copy of it."""
    if not members:
        return ("Announcements only: no member holds a role this channel accepts, so a message is read as data and gets "
                "no reply.") if announcement else \
               "No member holds a role this channel accepts, so a message is read as data and gets no reply."
    if str((channel or {}).get("mode") or "listen") == "listen":
        return ("Listen mode: an eligible member's message becomes a proposal in your DM with the role's actions; "
                "nothing is posted here.")
    return ""


def transport(workspace, channel):
    """The badge a channel carries in the settings tree for its transport: `bot` when the app posts in an `owned`
    channel, `send` when a `guest` channel posts from the owner's account (directly or as a draft), and `listen` when
    the channel posts nothing."""
    if not can_speak(channel):
        return "listen"
    return "bot" if channel_class(workspace, channel) != "guest" else "send"


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
    local_channel = channel_row(local, workspace_id, channel_id)
    if channel is None:
        channel = local_channel
    elif local_channel is not None:
        channel = {**channel, **local_channel}
    language = _effective_language(committed, local, workspace, channel)
    # A leading `@<name>` of the text addresses the message to that persona of the project; a name no persona declares,
    # and no address at all, keep the channel persona (SPEC 2682, issue #2819).
    named = addressed(committed, local, event.get("text"))
    persona = named or effective_persona(committed, local, workspace, channel)
    if channel is None:
        # An unknown channel grants nothing, but the decision still reports the role the files give the caller in it.
        role = _member_role(local, workspace_id, channel_id, user, None) if workspace else None
        return Decision(IGNORE, role, (), language, persona, None, False, None)
    class_ = channel_class(workspace, channel)
    next_poll = now + timedelta(seconds=poll_interval(now, channel, thread_state)) if class_ == "guest" else None
    # Only a `guest` channel has `voice_reply`: an `owned` channel already speaks audio through the bot.
    voice = class_ == "guest" and bool(channel.get("voice_reply"))
    role = _member_role(local, workspace_id, channel_id, user, {str(name) for name in channel.get("roles") or []})
    if str(event.get("kind") or "message") != "message" or event.get("own"):
        return Decision(IGNORE, role, (), language, persona, None, False, next_poll, voice)
    if event.get("bot") or event.get("external") or role is None:
        return Decision(DATA, role, (), language, persona, None, False, next_poll, voice)
    reply = _reply_path(class_, channel, user)
    if named is None and str(channel.get("reply") or "all") == "addressed":
        # A `reply: addressed` channel answers only a message that addresses a persona. The decision keeps its role
        # and its data; only the reply path is absent.
        reply = None
    kind = PROPOSAL if class_ == "guest" else INBOX
    return Decision(kind, role, _role_actions(committed, role), language, persona, reply, reply in _OWNER_ACCOUNT_REPLIES,
                    next_poll, voice, _role_deploy(committed, role))


# --- the one-time migration of the old per-profile Slack file (SPEC 2682 #2700, story 46) -------------
# The old file named five events, two roles, and one row per channel. This maps each old name to its id in the new
# files, following the spec's field table. `Manager` and `Colleague` carry `status` only, which is what S1 delivers.
MIGRATED_EVENTS = {"stg": "stg_release", "prod": "prod_release", "dev": "dev_release",
                   "bug": "bug_report", "fix": "fix_report"}
MIGRATED_ROLES = {"Manager": "manager", "Colleague": "colleague"}
MIGRATION_ACTIONS = ("status",)


class Migration(NamedTuple):
    """The migration's answer: the two documents it builds, the channels still waiting for a Slack id, the channels
    that have an id but no Slack workspace to place them in, and the report of what it carried over and what it
    dropped. `pending` is the id-ask list and holds only a channel with no id, so no caller asks the owner for an id
    the channel already carries. It reads no file and writes none, so the hub hands the pair to its usual save path and
    the owner approves it there."""
    committed: dict
    local: dict
    pending: list
    unplaced: list
    report: list


def _label(name):
    return str(name).strip().lstrip("#")


def _after_mark(template):
    """The template without the `prefix` line: the persona mark replaces the prefix, and the writer adds the mark at
    the post, so the template never holds it."""
    text = str(template)
    head = "<prefix>\n"
    if text.startswith(head):
        return text[len(head):]
    return "\n".join(line for line in text.splitlines() if line.strip() != "<prefix>")


def _minutes(value, default):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def migrate(old, workspace=None, ids=None):
    """The one-time migration of the old per-profile Slack file, by the spec's field table.

    `old` is the document the old hub wrote: `lang`, `prefix`, `tick`, `max`, `quiet`, `other`, `dm`, `audio`,
    `channels` and `people`, with a template per release event. `workspace` names the Slack workspace the old file
    posted in, `{"id", "label"}`, because the old file never held a team id. `ids` maps an old channel name to its
    Slack channel id, from the answer the hub asks for. A channel with no id stays in `pending` and enters neither
    document; the hub asks for the id before it writes. The answer's `report` names each field carried over and each
    field dropped."""
    old = old if isinstance(old, dict) else {}
    workspace = workspace if isinstance(workspace, dict) else {}
    given = {}
    for name, channel_id in (ids or {}).items():
        if str(channel_id or "").strip():
            given[_label(name)] = str(channel_id).strip()
    report = []
    raw_lang = old.get("lang")
    language = (normalize_language(raw_lang) if raw_lang is not None else None) or DEFAULT_LANGUAGE
    committed = {"version": 1, "project": {"language": language}}
    local = {"version": 1, "workspaces": []}
    if raw_lang is not None:
        report.append(f"carried: language {raw_lang} becomes project.language {language}")
    # The project's events come from the old templates, and each template loses the prefix line.
    templates = old.get("templates") if isinstance(old.get("templates"), dict) else {}
    declared = {}
    for old_id, new_id in MIGRATED_EVENTS.items():
        text = templates.get(old_id)
        if isinstance(text, str) and text.strip():
            declared[new_id] = _after_mark(text)
    if declared:
        committed["events"] = [{"id": event_id, "template": text} for event_id, text in declared.items()]
        for old_id, new_id in MIGRATED_EVENTS.items():
            if new_id in declared:
                report.append(f"carried: event {old_id} becomes {new_id} with its template, after the mark")
    # Both roles exist under the new id, each with only `status`.
    committed["roles"] = [{"id": role_id, "actions": list(MIGRATION_ACTIONS)}
                          for role_id in MIGRATED_ROLES.values()]
    for old_role, new_role in MIGRATED_ROLES.items():
        report.append(f"carried: role {old_role} becomes {new_role} with action status")
    fast = f"{_minutes(old.get('tick'), 5)}m"
    slow = f"{_minutes(old.get('max'), 30)}m"
    written, pending = [], []
    for row in _rows(old.get("channels")):
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        if row.get("watch") is not True:
            report.append(f"dropped: channel {name} (watch is off)")
            continue
        label = _label(name)
        poll = {"mode": "two_state", "fast": fast, "slow": slow}
        channel = {"name": label, "mode": "listen+speak", "poll": poll}
        channel_lang = normalize_language(row["lang"]) if row.get("lang") is not None else None
        if channel_lang:
            channel["language"] = channel_lang
        events_on = row.get("events") if isinstance(row.get("events"), dict) else {}
        announce = [new_id for old_id, new_id in MIGRATED_EVENTS.items()
                    if events_on.get(old_id) is True and new_id in declared]
        if announce:
            channel["announce"] = announce
        report.append(f"carried: channel {name} becomes {label}, {channel.get('language', 'project language')}")
        report.append(f"carried: channel {name} poll fast {fast} slow {slow} (two_state)")
        if announce:
            report.append(f"carried: channel {name} announce {', '.join(announce)}")
        channel_id = given.get(label)
        if channel_id:
            written.append({"id": channel_id, **channel})
            report.append(f"carried: channel {name} Slack id {channel_id}")
        else:
            pending.append(channel)
            report.append(f"pending: channel {label} (no Slack id; the hub asks for the id before it writes)")
    team = str(workspace.get("id") or "").strip()
    unplaced = []
    if team:
        row = {"id": team, "label": str(workspace.get("label") or team), "class": "guest"}
        if written:
            row["channels"] = written
        local["workspaces"] = [row]
        report.append(f"carried: workspace {team} becomes guest of the local file")
    else:
        unplaced = written
        for channel in written:
            report.append(f"unplaced: channel {channel['name']} (Slack id {channel['id']} is given, but the "
                          "workspace has no team id; the hub asks for it before it writes)")
        report.append("pending: the Slack workspace has no team id; the hub asks for it before it writes")
    report += [
        "dropped: prefix (the persona mark replaces it)",
        "dropped: thread and mention (every reply goes in the thread)",
        "dropped: people (a role needs Slack user ids, and the hub keeps the names)",
        "dropped: quiet, dm, audio (the new file has no quiet hours and no owner inbox)",
        "dropped: other (a message from someone without a role is data)",
    ]
    return Migration(committed, local, pending, unplaced, report)
