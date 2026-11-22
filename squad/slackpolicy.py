"""The one home of the Slack file rules (ADR-0177).

`docs/squad/slack.yaml` sits beside the project settings file and is committed. `<profile>/<project>.slack.local.yaml`
sits in the profile folder and never enters a repository. This module loads both files, validates each, and returns one
`Defect` per failing key, so a caller prints a message that names the key. It reads no clock and calls no network.

`squad slack check` imports it, and the hub imports it before it writes a Slack setting. No other file holds a copy of
these rules.

Guards: `tests/test_slack_policy.py`, `tests/test_cli_slack_check.py`, `contrib/squad-goal/scripts/test-commit-guard.sh`.
"""
import os
import re
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
