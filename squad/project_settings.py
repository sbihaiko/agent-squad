"""The project settings file: `docs/squad/defaults.yaml` under the target overlays the plugin defaults.

Precedence, lowest first: plugin defaults, this file, `roles`, the graph `run` block, the node. A graph or a node may only narrow a cap.
This file is the one layer that may raise a plugin default, except the machine-wide `caps` values, which it may only lower (`squad/caps.py`). It holds only the keys the owner changed, with the key names of the plugin defaults.
The catalog below names every key the file may set. A key outside it, and any credential key, is refused with a message that names the key.

`squad config set|reset|init` write the file: each write is checked by `validate`, and a second `set` of the same value changes nothing.

Guards: `tests/test_project_settings.py`, `tests/test_config_write.py`.
"""
import copy
import hashlib
import re
import subprocess
import sys
from pathlib import Path

import yaml

from squad import defaults as D
from squad.options import OptionError

REL = Path("docs") / "squad" / "defaults.yaml"
CREDENTIAL = re.compile(r"api_?key|secret|password|credential|gateway|base_url|bedrock|vertex|foundry|oauth|auth", re.I)
VALUE_CREDENTIAL = re.compile(CREDENTIAL.pattern + "|tskey-", re.I)     # tskey- is the prefix of a Tailscale auth key
SECRET_VALUE = "credential-shaped value refused: no secret goes in this file"
EMAIL = re.compile(r"[^@\s]+@[^@\s]+")
MIN_CONTEXT = D.MIN_CONTEXT
METHOD = "without-tailnet"   # run_start tag for a hash that leaves the tailnet block out; a record with no tag holds the raw-bytes hash

# key -> (type, low, high, unit, meaning). type: "int", "num", "str", "enum:a|b", "emails", "list". A bound of None is open.
CATALOG = {
    "options.model": ("str", None, None, "model id", "model of a new node"),
    "options.fallback_model": ("str", None, None, "model id", "model used when the first one fails"),
    "options.effort": ("enum:" + "|".join(sorted(D.EFFORTS)), None, None, "level", "reasoning effort"),
    "options.permission_mode": ("enum:" + "|".join(sorted(D.MODES)), None, None, "mode", "permission mode of every call"),
    "options.allowed_tools": ("list", None, None, "tool names", "tools approved with no prompt"),
    "options.disallowed_tools": ("list", None, None, "tool names", "tools no call may use"),
    "limits.max_stalls": ("int", 2, 100, "attempts", "failed attempts at one step with the same failure before a call stops"),
    "limits.max_budget_usd": ("num", 0.01, 100000, "USD", "spend of one call"),
    "limits.max_output_tokens": ("int", 1, 1000000, "tokens", "output tokens of one response"),
    "limits.context_tokens": ("int", MIN_CONTEXT, 10000000, "tokens", "context size before compaction"),
    "limits.deadline_seconds": ("num", 1, 31536000, "seconds", "wall clock of one call"),
    "limits.no_progress_seconds": ("num", 1, 31536000, "seconds", "silence of one call's upstream before its entry ends and the chain moves on"),
    "run.deadline": ("num", 1, 31536000, "seconds", "wall clock of the run"),
    "run.max_parallel": ("int", 1, 1000, "calls", "parallel calls"),
    "run.spend_cap": ("num", 0.01, 1000000, "USD", "spend of the run"),
    "dispatch.max_depth": ("int", 1, 20, "levels", "dispatch depth"),
    "dispatch.max_concurrency": ("int", 1, 1000, "children", "parallel dispatch children"),
    "caps.runs": ("int", 1, 1000, "runs", "live runs on this machine; a project may only lower the plugin value"),
    "caps.children": ("int", 1, 1000, "children", "live dispatch children on this machine; a project may only lower the plugin value"),
    "caps.gates": ("int", 1, 1000, "slots", "gate worker slots on this machine; a project may only lower the plugin value"),
    "caps.reviews": ("int", 1, 1000, "reviews", "live reviews on this machine; a project may only lower the plugin value"),
    "janitor.spec_stall_seconds": ("int", 3600, 31536000, "seconds", "a spec with no dev commit on its open tickets for this long raises one SPEC-STALL alert per bound"),
    "claude_md.max_line_width": ("int", 40, 10000, "characters", "width of one CLAUDE.md line before `squad setup --check` flags it"),
    "claude_md.max_section_lines": ("int", 5, 10000, "lines", "length of one CLAUDE.md section before `squad setup --check` flags it"),
    "tailnet.host": ("str", None, None, "MagicDNS name", "name of this machine on the tailnet"),
    "tailnet.allow": ("emails", None, None, "login names", "logins a tailnet request may come from"),
    "tailnet.terminals_read": ("bool", None, None, "true or false", "the tailnet may read the scrollback of a hosted session of this profile; the grant holds for every allowed login"),
    "tailnet.terminals_attach": ("bool", None, None, "true or false", "the tailnet may attach to and detach from a hosted session of this profile; the grant holds for every allowed login"),
    "tailnet.terminals_send": ("bool", None, None, "true or false", "the phone may send a message to a live session of this profile; the grant holds for every allowed login of the profile"),
    "tailnet.loop_write": ("bool", None, None, "true or false", "the tailnet may start and stop the loop from the hub; off, the switch is read-only from the tailnet"),
    "tailnet.profiler_write": ("bool", None, None, "true or false", "the tailnet may turn the hub's CPU sampling on and off; off, that switch is read-only from the tailnet"),
}
# The board columns a card limit may bound (#2287). `columns.<column>.max_cards` takes a whole number from 1 to 100, or
# none for no limit; `squad/board_limits.py` resolves the value the frontier, the review pass and the hub read.
COLUMN_CARDS = ("Todo", "Doing", "Review", "Landing", "Gating")
for _column in COLUMN_CARDS:
    CATALOG[f"columns.{_column}.max_cards"] = ("intnone", 1, 100, "cards", f"cards the {_column} column holds; none means no limit")
# The keys the plugin defaults must hold; a missing one is a defect of that file. The rest are optional.
REQUIRED = {"options.effort", "options.permission_mode", "limits.max_stalls", "limits.max_budget_usd", "limits.max_output_tokens", "limits.context_tokens",
            "limits.deadline_seconds", "run.max_parallel", "run.spend_cap"}
ROLE_KEYS = {k.split(".", 1)[1] for k in CATALOG if k.startswith("options.")}
# The keys that reach a call: a request carries the two token limits, and each cap is a ceiling a node can only narrow.
CALL = ("limits.max_output_tokens", "limits.context_tokens", "limits.max_stalls", "limits.max_budget_usd", "limits.deadline_seconds",
        "limits.no_progress_seconds")


# The tailnet block is personal (a machine name, a login), so it lives in the project's gitignored `.env`, never in the tracked file.
ENV = ".env"
ENV_KEYS = {"tailnet.host": "SQUAD_TAILNET_HOST", "tailnet.allow": "SQUAD_TAILNET_ALLOW", "tailnet.terminals_read": "SQUAD_TAILNET_TERMINALS_READ",
            "tailnet.terminals_attach": "SQUAD_TAILNET_TERMINALS_ATTACH", "tailnet.terminals_send": "SQUAD_TAILNET_TERMINALS_SEND",
            "tailnet.loop_write": "SQUAD_TAILNET_LOOP_WRITE", "tailnet.profiler_write": "SQUAD_TAILNET_PROFILER_WRITE"}


def _env_lines(target):
    path = Path(target or ".") / ENV
    return path, (path.read_text().splitlines() if path.is_file() else [])


def env_tailnet(target):
    """The tailnet keys the project's `.env` sets, as a `tailnet` block; empty when it sets none."""
    _, lines = _env_lines(target)
    names = {v: k.split(".", 1)[1] for k, v in ENV_KEYS.items()}
    out = {}
    for line in lines:
        name, sep, raw = line.partition("=")
        if sep and name.strip() in names:
            key, raw = names[name.strip()], raw.strip().strip('"\'')
            out[key] = [x.strip() for x in raw.split(",") if x.strip()] if key == "allow" else yaml.safe_load(raw) if key in ("terminals_send", "loop_write", "profiler_write") else raw
    return out


def _env_write(target, key, value):
    """Set (or, with None, remove) one tailnet key in the project's `.env`. Refuse when git would track that file."""
    path, lines = _env_lines(target)
    if value is not None:
        tracked = subprocess.run(["git", "-C", str(path.parent), "check-ignore", "-q", ENV], capture_output=True).returncode == 1
        if tracked:
            _refuse(key, f"{path} is not in .gitignore; add it there first, so a personal value never reaches git")
    name = ENV_KEYS[key]
    kept = [ln for ln in lines if ln.partition("=")[0].strip() != name]
    if value is not None:
        kept.append(f"{name}={','.join(value) if isinstance(value, list) else str(value).lower() if isinstance(value, bool) else value}")
    if kept == lines:
        return False
    path.write_text("".join(ln + "\n" for ln in kept))
    return True


def path_for(target):
    return Path(target or ".") / REL


def without_tailnet(text):
    """The text with the top-level `tailnet` block and the blank lines after it removed. A file with no block comes back unchanged."""
    out, inside = [], False
    for line in text.splitlines(keepends=True):
        if line.startswith("tailnet:"):
            inside = True
            continue
        if inside and (line[:1] in (" ", "\t", "-") or not line.strip()):
            continue
        inside = False
        out.append(line)
    return "".join(out)


def digest(path):
    """The hash `run_start` records: the sha256 of the file without its tailnet block. With no block it is the sha256 of the bytes."""
    return hashlib.sha256(without_tailnet(Path(path).read_bytes().decode()).encode()).hexdigest()


def raw_digest(path):
    """The sha256 of the file bytes: the hash a record holds when it carries no `METHOD` tag."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _flat(data, prefix=""):
    for k, v in data.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and not key.startswith("roles"):
            yield from _flat(v, key + ".")
        else:
            yield key, v


def _refuse(key, why):
    raise OptionError(f"project settings key {key}: {why}")


def value_defect(spec, v, key=""):
    """The reason `v` is unsound for a catalog entry, or None. One rule for the project file, `config check` and the plugin defaults checker."""
    kind, low, high, unit, _ = spec
    if kind.startswith("enum:"):
        if v not in kind[5:].split("|"):
            return f"must be one of {kind[5:].replace('|', ', ')}"
    elif kind == "str":
        if not isinstance(v, str) or not v:
            return "must be a non-empty string"
        if VALUE_CREDENTIAL.search(v):
            return SECRET_VALUE
    elif kind == "emails":
        if not isinstance(v, list) or not all(isinstance(x, str) and EMAIL.fullmatch(x) for x in v):
            return "must be a list of login names in email form"
        if any(VALUE_CREDENTIAL.search(x) for x in v):
            return SECRET_VALUE
    elif kind == "list":
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            return "must be a list of strings"
    elif kind == "bool":
        if not isinstance(v, bool):
            return "must be true or false"
    elif kind == "intnone":
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
            return f"must be an integer from {low} to {high}, or none"
    else:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (kind == "int" and int(v) != v):
            return f"must be {'an integer' if kind == 'int' else 'a number'} ({unit})"
        if (low is not None and v < low) or (high is not None and v > high):
            return f"{v} is out of range: {low} to {high} {unit}" + (f" (a window below {MIN_CONTEXT} is refused)" if key == "limits.context_tokens" else "")
    return None


def _unknown(key):
    return "credential key refused: no secret goes in this file" if CREDENTIAL.search(key) else "not a project setting; see the catalog in squad/project_settings.py"


def defects(data):
    """Return every defect of a project settings mapping as a list of (key, message): unknown key, credential key, bad type, out-of-range value."""
    if not isinstance(data, dict):
        return [("(file)", "project settings must be a mapping")]
    bad = []
    for key, v in _flat(data):
        if key == "roles":
            if not isinstance(v, dict) or not all(isinstance(x, dict) for x in v.values()):
                bad.append((key, "holds one mapping of option keys per role"))
                continue
            for role, over in v.items():
                for k, rv in over.items():
                    rk = f"roles.{role}.{k}"
                    if k not in ROLE_KEYS:
                        bad.append((rk, _unknown(k) if CREDENTIAL.search(k) else f"not a role setting; allowed: {', '.join(sorted(ROLE_KEYS))}"))
                    elif (why := value_defect(CATALOG[f"options.{k}"], rv, rk)):
                        bad.append((rk, why))
        elif key not in CATALOG:
            bad.append((key, _unknown(key)))
        elif (why := value_defect(CATALOG[key], v, key)):
            bad.append((key, why))
    return bad


def validate(data):
    """Raise OptionError naming the key for the first defect: an unknown key, a credential key, or an unsound value."""
    for key, why in defects(data):
        raise OptionError(why if key == "(file)" else f"project settings key {key}: {why}")


def check_file(target):
    """Return (path, defects) for the project file under `target`; defects is a list of (key, message). No file is no defect."""
    path = path_for(target)
    if not path.is_file():
        return path, []
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return path, [("(file)", f"not valid YAML: {e}")]
    return path, defects({} if data is None else data)


def load(target):
    """Return (data, record) for the project file under `target`, or (None, None) when there is none. A bad file raises OptionError."""
    path = path_for(target)
    env = env_tailnet(target)
    if not path.is_file():
        if not env:
            return None, None
        data = {"tailnet": env}
        try:
            validate(data)
        except OptionError as e:
            raise OptionError(f"{Path(target or '.') / ENV}: {e}") from e
        return data, None
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise OptionError(f"{path} is not valid YAML: {e}") from e
    data = {} if data is None else data
    if env:
        data["tailnet"] = {**(data.get("tailnet") or {}), **env}
    try:
        validate(data)
    except OptionError as e:
        raise OptionError(f"{path}: {e}") from e
    return data, {"path": str(path.resolve()), "sha256": digest(path)}


def overlay(cfg, data):
    """Return (merged settings, sources). `sources` maps each flat key of the plugin defaults and the project file to "plugin" or "project"."""
    merged = copy.deepcopy(cfg)
    sources = {k: "plugin" for k, _ in _flat({s: cfg.get(s, {}) for s in ("options", "limits", "run", "dispatch")})}
    for key, v in _flat(data or {}):
        if key == "roles":
            for role, over in v.items():
                merged.setdefault("roles", {}).setdefault(role, {}).update(over)
                sources.update({f"roles.{role}.{k}": "project" for k in over})
        else:
            sec, _, name = key.partition(".")
            merged.setdefault(sec, {})[name] = v
            sources[key] = "project"
    return merged, sources


def call_limits(data):
    """The call-level keys the project set, as {name: value}: max_output_tokens, context_tokens, max_stalls, max_budget_usd, deadline_seconds."""
    flat = dict(_flat(data or {}))
    return {k.split(".", 1)[1]: flat[k] for k in CALL if k in flat}


def _coerce(key, raw):
    """Parse the text of a value: YAML reads a number or a list; a text key keeps its raw text."""
    spec = CATALOG.get("options." + key.split(".")[-1]) if key.startswith("roles.") else CATALOG.get(key)
    if spec and spec[0] == "str":
        return raw
    if spec and spec[0] == "intnone" and isinstance(raw, str) and raw.strip().lower() in ("none", "null"):
        return None
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


def _parts(key):
    parts = key.split(".")
    if parts[0] == "roles":
        if len(parts) != 3:
            _refuse(key, "a role key reads roles.<role>.<option>")
        return parts
    return parts


def _read(target):
    path = path_for(target)
    data, _ = load(target)
    return path, data or {}


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=True, default_flow_style=False))


def set_key(target, key, raw):
    """Write one key. Return True when the file changed. An unknown key, a credential key or an unsound value raises OptionError and writes nothing.
    A tailnet key goes to the project's `.env` instead (ENV_KEYS)."""
    if key in ENV_KEYS:
        value = _coerce(key, raw)
        validate({"tailnet": {key.split(".", 1)[1]: value}})
        return _env_write(target, key, value)
    path, data = _read(target)
    parts = _parts(key)
    new = copy.deepcopy(data)
    node = new
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = _coerce(key, raw)
    validate(new)
    if new == data and path.is_file():
        return False
    _write(path, new)
    return True


def set_keys(target, items):
    """Write several keys in one atomic write. Return True when the file changed. Every key is checked before any byte is
    written, so a bad value or a failed write leaves the file as it was, never with the first keys changed and the rest
    missing. A tailnet key is refused here: its home is `.env`, and it is set alone."""
    if any(key in ENV_KEYS for key, _ in items):
        _refuse(next(key for key, _ in items if key in ENV_KEYS), "a tailnet key lives in .env and is set alone")
    path, data = _read(target)
    new = copy.deepcopy(data)
    for key, raw in items:
        parts = _parts(key)
        node = new
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = _coerce(key, raw)
    validate(new)
    if new == data and path.is_file():
        return False
    _write(path, new)
    return True


def reset_key(target, key):
    """Remove one key and keep the others; drop a section left empty. Return True when the file changed."""
    if key in ENV_KEYS:
        return _env_write(target, key, None)
    path, data = _read(target)
    parts = _parts(key)
    if key not in CATALOG and parts[0] != "roles":
        _refuse(key, _unknown(key))
    chain, node = [], data
    for p in parts[:-1]:
        if not isinstance(node.get(p), dict):
            return False
        chain.append((node, p))
        node = node[p]
    if parts[-1] not in node:
        return False
    del node[parts[-1]]
    for parent, p in reversed(chain):
        if not parent[p]:
            del parent[p]
    _write(path, data)
    return True


def init(target):
    """Create the project file when there is none, holding only the keys changed so far (none). Return True when it wrote."""
    path, _ = _read(target)
    if path.is_file():
        return False
    _write(path, {})
    return True


def spec_stall_seconds(target):
    """The janitor's spec-stall bound: the project file's value when it sets one, else the plugin default."""
    data, _ = load(target)
    return overlay(D.load(), data)[0]["janitor"]["spec_stall_seconds"]


if __name__ == "__main__":
    print(spec_stall_seconds(sys.argv[2]))
