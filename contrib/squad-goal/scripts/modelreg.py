"""One model registry and one resolver (SPEC 505).

The registry is data: `config/models.registry.json`, shipped byte-identical as
`contrib/squad-goal/scripts/registry.json`. This module is stdlib only and is shipped the same way. It resolves a
raw model spelling to one registry id, one route and the wire string that route sends today. A reader normalizes;
a writer keeps the raw spelling (ADR-0107, opaque resume).

The module never raises from `normalize`: an unknown spelling is data that passes through, not a defect. `load`
is the loud door: a JSON parse error is a named failure, and one bad entry is dropped and named while the other
entries still load.
"""
import argparse
import calendar
import difflib
import functools
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

ROUTES = ("cli", "gateway", "direct")
AUTHS = ("subscription", "api_key", "unverified")
V1_CHAINS = {"coding": "coding_models", "review": "review_models",
             "vision": "vision_models", "router": "router_models",
             # A child whose deliverable is a report or an issue comment, never a tree change (#1574). It runs as a coder runs.
             "probe": "probe_models",
             # A loop defect (ADR-0171): Fable or Astra first, then the review chain. Its one reader is the chain reader below.
             "loop_defect": "loop_defect_models",
             # Editorial text: a commit message, a pull-request body, a status summary. Short, serial, and never on a
             # gate's path, so it may take a small local model first (owner, 2026-11-03).
             "editorial": "editorial_models"}
DEFAULT_RESERVE_PERCENT = 10
# A cache read costs this share of the input rate where a model's display_price names no cache_read. It is the Anthropic
# ratio. The hub's stats price cache reads with it (#791). The token guard does not read it: its cache-read weight is
# SG_TOKEN_CACHE_WEIGHT, default 0 (#495, #793).
CACHE_READ_RATIO = 0.1
DEFAULT_QUOTA_STALE_SECONDS = 6 * 3600   # a quota reading older than this is unknown; SG_QUOTA_STALE_SECONDS overrides
_CTX = re.compile(r"\[([A-Za-z0-9]{1,8})\]$")
_CTX_WINDOW = re.compile(r"^(\d+)([kKmM]?)$")
_EFFORT = re.compile(r"^(.*)-(low|medium|high)$")


class RegistryError(Exception):
    """A registry that cannot be read: a parse error or a shape that is not a registry."""


@dataclass(frozen=True)
class Entry:
    id: str
    label: str
    provider: str
    subscription: str
    efforts: tuple = ()
    routes: dict = field(default_factory=dict)
    capabilities: dict = field(default_factory=dict)
    display_price: dict | None = None
    aliases: tuple = ()
    retired: bool = False
    gateway_only: bool = False


@dataclass(frozen=True)
class Registry:
    path: str
    entries: dict
    aliases: dict
    subscriptions: dict
    dropped: list


@dataclass(frozen=True)
class Ref:
    id: str
    raw: str
    route: str | None
    effort: str | None
    ctx: str | None
    known: bool


@dataclass(frozen=True)
class Inv:
    route: str
    wire: str
    argv: tuple = ()


@dataclass(frozen=True)
class ChainEntry:
    id: str
    raw: str
    route: str | None
    effort: str | None
    ctx: str | None
    on: bool
    hrs: str | None
    timeout_s: float | None
    no_tools: bool
    provider: str | None
    subscription: str | None
    known: bool
    legacy_codex: bool = False   # a v1 spelling the old chain reader ran through Codex: its marker still applies
    model_off: bool = False      # switched off at model level (`model_off`, SPEC 889 #920): off in every chain


@dataclass(frozen=True)
class ReviewRoute:
    route: str       # cli, gateway or direct: how the review pass calls the reviewer
    runner: str      # the CLI program for a cli route (claude, agy, grok, codex); empty for gateway and direct
    wire: str        # the model string that route sends
    tools: bool      # the registry capability: a reviewer with no tools reads the prompt alone


@dataclass(frozen=True)
class Chain:
    name: str
    entries: list
    mixed: bool = False
    stray: list = field(default_factory=list)


@dataclass(frozen=True)
class Eligibility:
    ok: bool
    reason: str | None
    until: int | None


# --- paths ------------------------------------------------------------------------------------------------

def _first_file(candidates):
    for p in candidates:
        if p.is_file():
            return p
    return candidates[-1]


@functools.lru_cache(maxsize=8)
def _registry_path_for(value):
    """The registry a value of SG_MODELS_REGISTRY names, or the shipped one when it names none. The lookup runs `Path.resolve`
    and two `is_file` calls, and the hub asks it on every answer it serves (#2582): one answer per value is kept, so a poll
    costs no filesystem read."""
    if value:
        return Path(value)
    here = Path(__file__).resolve().parent
    return _first_file([here / "registry.json", here.parent / "config" / "models.registry.json"])


def _default_registry_path():
    return _registry_path_for(os.environ.get("SG_MODELS_REGISTRY") or "")


def _default_models_path():
    if os.environ.get("SG_MODELS"):
        return Path(os.environ["SG_MODELS"])
    here = Path(__file__).resolve().parent
    return _first_file([here / "models.json", here.parent / "contrib" / "squad-goal" / "scripts" / "models.json"])


def _default_plans_path():
    if os.environ.get("SG_PLANS"):
        return Path(os.environ["SG_PLANS"])
    here = Path(__file__).resolve().parent
    return _first_file([here / "plans.json", here.parent / "contrib" / "squad-goal" / "scripts" / "plans.json"])


def _shared_dir(shared=None):
    """The one shared directory of every profile: markers and the hub quota cache. SG_SHARED, else the directory the
    seam (address.sh, `sg_shared_dir`) names, as env.sh resolves it; never a profile's SG_STATE (#678). Only with no
    seam beside this module does it fall back to SG_STATE, then the working directory."""
    if shared or os.environ.get("SG_SHARED"):
        return Path(shared or os.environ["SG_SHARED"])
    env = tuple(sorted((k, v) for k, v in os.environ.items() if k.startswith("SG_") or k == "HOME"))
    return Path(_seam_shared(env) or os.environ.get("SG_STATE") or ".")


@functools.lru_cache(maxsize=8)
def _seam_shared(_env):
    """`sg_shared_dir` from address.sh, once per process and environment; empty when no seam answers."""
    here = Path(__file__).resolve().parent
    seam = _first_file([here / "address.sh", here.parent / "contrib" / "squad-goal" / "scripts" / "address.sh"])
    if not seam.is_file():
        return ""
    try:
        p = subprocess.run(["bash", "-c", '. "$1" 2>/dev/null; sg_shared_dir', "_", str(seam)],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout.strip()


# --- load: fault isolation -------------------------------------------------------------------------------

def _validate_entry(raw, subscriptions):
    if not isinstance(raw, dict):
        return "not an object"
    for key in ("id", "label", "provider", "subscription"):
        if not isinstance(raw.get(key), str) or not raw[key]:
            return f"missing {key}"
    if raw["subscription"] not in subscriptions:
        return f"unknown subscription {raw['subscription']}"
    for key in ("efforts", "aliases", "served"):
        if key in raw and not isinstance(raw[key], list):
            return f"{key} must be a list"
    for key in ("retired", "gateway_only"):
        if key in raw and not isinstance(raw[key], bool):
            return f"{key} must be a boolean"
    routes = raw.get("routes") or {}
    if not isinstance(routes, dict):
        return "routes must be an object"
    if not routes and not raw.get("retired", False):
        return "no route"
    for name, route in routes.items():
        if name not in ROUTES:
            return f"unknown route {name}"
        if not isinstance(route, dict):
            return f"route {name} is not an object"
        auth = route.get("auth")
        if auth not in AUTHS:
            return f"route {name}: auth {auth!r}"
        if auth == "api_key" and raw["provider"] != "deepseek":
            return f"route {name}: api_key on provider {raw['provider']}"
        served = route.get("served", [])
        if not isinstance(served, list) or not all(isinstance(s, str) and s for s in served):
            return f"route {name}: served must be a list of strings"
        if name in ("gateway", "direct") and not (isinstance(route.get("model"), str) and route["model"]):
            return f"route {name}: missing model"
        if name == "cli":
            argv = route.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
                return "route cli: missing argv"
    if raw.get("gateway_only") and "gateway" not in routes:
        return "gateway_only without a gateway route"
    return None


def _entry_from(raw):
    return Entry(id=raw["id"], label=raw["label"], provider=raw["provider"],
                 subscription=raw["subscription"], efforts=tuple(raw.get("efforts") or ()),
                 routes=raw.get("routes") or {}, capabilities=raw.get("capabilities") or {},
                 display_price=raw.get("display_price"), aliases=tuple(raw.get("aliases") or ()),
                 retired=bool(raw.get("retired", False)), gateway_only=bool(raw.get("gateway_only", False)))


def load(path=None):
    """Read and validate the registry. A parse error raises RegistryError; a bad entry is dropped and named."""
    p = Path(path) if path else _default_registry_path()
    try:
        doc = json.loads(p.read_text())
    except (OSError, ValueError) as exc:
        raise RegistryError(f"registry {p}: invalid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise RegistryError(f"registry {p}: the document must be an object")
    subscriptions = doc.get("subscriptions") or {}
    if not isinstance(subscriptions, dict):
        raise RegistryError(f"registry {p}: `subscriptions` must be an object")
    models = doc.get("models")
    if not isinstance(models, list):
        raise RegistryError(f"registry {p}: `models` must be a list")
    entries, aliases, dropped = {}, {}, []
    for i, raw in enumerate(models):
        name = raw.get("id") if isinstance(raw, dict) and isinstance(raw.get("id"), str) else f"entry[{i}]"
        reason = _validate_entry(raw, subscriptions)
        if reason:
            dropped.append({"id": name, "reason": reason})
            continue
        if name in entries:
            dropped.append({"id": name, "reason": "duplicate id"})
            continue
        entry = _entry_from(raw)
        conflict = next((a for a in entry.aliases if a in aliases and aliases[a] != entry.id), None)
        if conflict:
            dropped.append({"id": name, "reason": f"alias {conflict} already names {aliases[conflict]}"})
            continue
        entries[entry.id] = entry
        for alias in entry.aliases:
            if alias != entry.id:
                aliases[alias] = entry.id
    for alias in list(aliases):                 # a spelling names one entry: an alias may never be another entry's id
        if alias in entries:
            dropped.append({"id": aliases[alias], "reason": f"alias {alias} is another entry's id"})
            del aliases[alias]
    return Registry(path=str(p), entries=entries, aliases=aliases,
                    subscriptions=subscriptions, dropped=dropped)


_CACHE: dict[str, Registry] = {}


def default_registry():
    """The committed registry, read once. A broken file is an error for `load` and a pass-through for `normalize`."""
    key = str(_default_registry_path())
    if key not in _CACHE:
        _CACHE[key] = load(key)
    return _CACHE[key]


# --- normalize and invocation ----------------------------------------------------------------------------

def _route_for(entry, raw):
    for name in ("gateway", "cli", "direct"):
        route = entry.routes.get(name)
        if route and raw in (route.get("served") or []):
            return name
    if "gateway" in entry.routes:
        return "gateway"
    return next(iter(entry.routes), None)


def normalize(raw, reg=None):
    """Resolve one raw spelling. Never raises: an unknown spelling keeps its raw form and says known=False."""
    try:
        if not isinstance(raw, str) or not raw:
            return Ref(id=str(raw), raw=raw if isinstance(raw, str) else "", route=None,
                       effort=None, ctx=None, known=False)
        reg = reg or default_registry()
        original, body, ctx, route = raw, raw, None, None
        match = _CTX.search(body)
        if match:
            ctx = match.group(1)
            body = body[:match.start()]
        if body.startswith("claude:"):
            route = "cli"
            body = body[len("claude:"):]
        entry, effort = None, None
        if body in reg.entries:
            entry = reg.entries[body]
        elif body in reg.aliases:
            entry = reg.entries[reg.aliases[body]]
        else:
            match = _EFFORT.match(body)
            if match and match.group(1) in reg.entries and match.group(2) in reg.entries[match.group(1)].efforts:
                entry, effort = reg.entries[match.group(1)], match.group(2)
        if entry is None:
            return Ref(id=original, raw=original, route=route, effort=None, ctx=ctx, known=False)
        if route is None:
            route = _route_for(entry, body if ctx is None else original)
        return Ref(id=entry.id, raw=original, route=route, effort=effort, ctx=ctx, known=True)
    except Exception:  # noqa: BLE001 - the contract is "never raises": a bad registry passes the spelling through
        return Ref(id=raw if isinstance(raw, str) else "", raw=raw if isinstance(raw, str) else "",
                   route=None, effort=None, ctx=None, known=False)


def model(raw, reg=None):
    """The registry entry a raw spelling names, or None when the registry does not know it."""
    reg = reg or default_registry()
    ref = normalize(raw, reg)
    return reg.entries.get(ref.id) if ref.known else None


_CTX_UNITS = {"": 1, "k": 1_000, "m": 1_000_000}


def ctx_tokens(ctx):
    """A chain entry's declared context window in tokens: `"128k"` -> 128000, `"1m"` -> 1000000, `"32768"` -> 32768.

    The units are decimal: a chain that states `128k` means 128,000 tokens, as providers state their windows. None when
    the value is absent or unreadable: an entry that declares no readable window has no ceiling.
    """
    if isinstance(ctx, bool) or not isinstance(ctx, str):
        return None
    match = _CTX_WINDOW.match(ctx.strip())
    return int(match.group(1)) * _CTX_UNITS[match.group(2).lower()] if match else None


def chain_ids(spellings, reg=None):
    """The registry ids a chain names, in the chain's own order (ADR-0147: a chain is a list of registry ids). An unknown
    spelling stands as written, as `normalize` returns it; no entry is dropped."""
    reg = reg or default_registry()
    return [normalize(raw, reg).id for raw in spellings]


def chain_sha256(ids):
    """The id of one ordered chain: sha256 of its registry ids joined in order by a newline, so the same order hashes the
    same and a different order of the same ids hashes differently (SPEC 1035)."""
    return hashlib.sha256("\n".join(ids).encode()).hexdigest()


def entries_sha256(raws, reg=None):
    """Hash of the entries a list of raw spellings names, and of each spelling the registry cannot resolve.

    The resolved part keeps the shape a record written before this key named unknown spellings used: one entry per
    registry id. An unknown spelling adds one `{raw: {"known": False}}` row, so adding that spelling to the registry
    later changes the hash and a resume refuses instead of resolving the frozen run to a different wire model. A
    record whose graph names only known spellings therefore keeps the hash it wrote.
    """
    reg = reg or default_registry()
    refs = [normalize(raw, reg) for raw in raws]
    doc = {ref.id: asdict(reg.entries[ref.id]) for ref in refs if ref.known}
    doc.update({ref.raw: {"known": False} for ref in refs if not ref.known})
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _wire(route, raw):
    served = route.get("served") or []
    if raw in served:
        return raw
    stripped = raw[len("claude:"):] if raw.startswith("claude:") else raw
    if stripped in served:
        return stripped
    return route.get("model") or stripped


def _argv(route, wire, effort, prompt_file):
    if not route.get("argv"):
        return ()
    values = {"model": wire, "effort": effort or "", "prompt_file": prompt_file or "{prompt_file}"}
    return tuple(a.format(**values) for a in route["argv"])


def invocation(ref_or_raw, use="gateway", effort=None, prompt_file=None, reg=None):
    """The route and the wire string a caller sends for one spelling. An unknown spelling passes through.

    `use="sdk"` is the engine's caller: it sends the spelling through the one route that serves it, so a correct
    spelling keeps its wire string and any other spelling resolves to that route's model.
    """
    reg = reg or default_registry()
    ref = ref_or_raw if isinstance(ref_or_raw, Ref) else normalize(ref_or_raw, reg)
    entry = reg.entries.get(ref.id) if ref.known else None
    route = entry.routes.get(use) if entry else None
    if entry and use == "sdk":
        # The SDK adapter reaches the gateway: the gateway route decides the wire, so a registry id maps to the name
        # that route serves (`gpt-5.6-terra` -> `claude-gpt-5.6-terra`) instead of the CLI route that serves the id bare.
        route = entry.routes.get("gateway") or entry.routes.get(_route_for(entry, ref.raw)) or route
    if not route:
        return Inv(route=use, wire=ref.raw, argv=())
    wire = _wire(route, ref.raw)
    return Inv(route=use, wire=wire, argv=_argv(route, wire, effort or ref.effort, prompt_file))


# --- review route: the runner and the tools of one reviewer -----------------------------------------------

_LEGACY_RUNNERS = (("claude:", "claude"), ("gemini-", "agy"), ("grok-", "grok"))


def _legacy_route(raw):
    for prefix, runner in _LEGACY_RUNNERS:
        if raw.startswith(prefix):
            return ReviewRoute("cli", runner, raw.removeprefix("claude:"), True)
    return ReviewRoute("cli", "codex", raw, True)


def review_route(raw, reg=None, route=None):
    """Where the review pass sends one reviewer: the route, its runner, its wire string and its tools.

    A registry entry decides: a `tools: false` capability picks the direct (or gateway) call, and any other entry
    takes its CLI route, whose first argv word is the runner; a gpt entry with tools and no CLI route keeps Codex, and any other keeps the legacy runner of its raw spelling. A spelling the registry does not know keeps the
    runner the old review script gave it: `claude:`, `gemini-` and `grok-` by prefix, Codex for the rest.

    The review lane reads `review_tools` first: a coding capability says nothing about how a reviewer answers, and a
    model that needs its coding tools can still review through the direct call, thinking off.

    `route="cli"` gives the CLI row of a reviewer that has one: the fallback a streamed no-tools call falls back to
    (#1373). Its tools are the entry's coding capability, because the CLI runs with the repository tools."""
    reg = reg or default_registry()
    ref = normalize(raw, reg)
    entry = reg.entries.get(ref.id) if ref.known else None
    if entry is None:
        return _legacy_route(raw)
    tools = entry.capabilities.get("review_tools", entry.capabilities.get("tools", True)) is not False
    if route == "cli" and "cli" in entry.routes:
        return ReviewRoute("cli", entry.routes["cli"]["argv"][0], invocation(ref, "cli", reg=reg).wire,
                           entry.capabilities.get("tools", True) is not False)
    if tools and "cli" not in entry.routes:
        # a reviewer with tools reads the repository, so it never takes the no-tools call
        if entry.subscription == "gpt":
            return ReviewRoute("cli", "codex", ref.id, True)
        return _legacy_route(raw)
    order = ("cli",) if tools else ("direct", "gateway")
    use = next((name for name in order if name in entry.routes), None)
    if use is None:
        return ReviewRoute("direct", "", ref.raw, tools)
    inv = invocation(ref, use, reg=reg)
    runner = entry.routes["cli"]["argv"][0] if use == "cli" else ""
    return ReviewRoute(use, runner, inv.wire, tools)


# --- chain: v1, v2, and a mixed file ----------------------------------------------------------------------

def _canonical(name):
    key = (name or "").removesuffix("_models")
    if key not in V1_CHAINS:
        raise ValueError(f"unknown chain {name!r}")
    return key


def v1_codex(raw):
    """A v1 file's marker rule: every spelling outside the `claude:`, `grok-` and `gemini-` prefixes ran through Codex.
    A v1 caller reads this rule here, never by parsing the spelling itself (ADR-0147 rule 10)."""
    return not (isinstance(raw, str) and raw.startswith(("claude:", "grok-", "gemini-")))


def v1_agy(raw):
    """A v1 file's second marker rule: a `gemini-` spelling ran through agy. Its one home is here (ADR-0147 rule 10)."""
    return isinstance(raw, str) and raw.startswith("gemini-")


def _default_route(chain_name, entry):
    if entry is None:
        return None
    if chain_name in ("coding", "router") and "gateway" in entry.routes:
        return "gateway"
    for name in ("cli", "gateway", "direct"):
        if name in entry.routes:
            return name
    return None


def _v2_entry(raw, chain_name, reg):
    base = raw.get("raw") or raw.get("id") or ""
    ref = normalize(base, reg)
    mid = raw.get("id") if isinstance(raw.get("id"), str) else ref.id
    entry = reg.entries.get(mid) or (reg.entries.get(ref.id) if ref.known else None)
    if entry is not None:
        mid = entry.id
    return ChainEntry(id=mid, raw=base, route=raw.get("route") or ref.route or _default_route(chain_name, entry),
                      effort=raw.get("effort") or ref.effort, ctx=raw.get("ctx") or ref.ctx,
                      on=bool(raw.get("on", True)), hrs=raw.get("hrs"), timeout_s=raw.get("timeout_s"),
                      no_tools=bool(raw.get("no_tools", False)),
                      provider=entry.provider if entry else None,
                      subscription=entry.subscription if entry else None,
                      known=entry is not None)


def chain(models_json, name, reg=None):
    """One chain, read from a v1 or a v2 file. A mixed file reads `chains`, and `stray` names the v1 keys."""
    reg = reg or default_registry()
    doc = models_json if isinstance(models_json, dict) else json.loads(Path(models_json).read_text())
    canon = _canonical(name)
    stray = [key for key in V1_CHAINS.values() if key in doc] if isinstance(doc.get("chains"), dict) else []
    mixed = bool(stray)
    entries = []
    if isinstance(doc.get("chains"), dict):
        for raw in doc["chains"].get(canon) or []:
            entries.append(_v2_entry(raw, canon, reg))
    else:
        key = V1_CHAINS[canon]
        off = (doc.get("off") or {}).get(key) or {}
        hours = (doc.get("hours") or {}).get(key) or {}
        efforts = (doc.get("efforts") or {}).get(key) or {}
        timeouts = doc.get("review_timeout_seconds_by_model") or {}
        no_tools = doc.get("review_no_tools") or []
        for raw in doc.get(key) or []:
            ref = normalize(raw, reg)
            entry = reg.entries.get(ref.id) if ref.known else None
            entries.append(ChainEntry(
                id=ref.id, raw=raw, route=ref.route, effort=efforts.get(raw) or ref.effort, ctx=ref.ctx,
                on=not bool(off.get(raw)), hrs=hours.get(raw), timeout_s=timeouts.get(raw),
                no_tools=raw in no_tools, provider=entry.provider if entry else None,
                subscription=entry.subscription if entry else None, known=ref.known,
                legacy_codex=v1_codex(raw)))
    moff = doc.get("model_off") if isinstance(doc.get("model_off"), dict) else {}
    entries = [replace(e, model_off=bool(moff.get(e.id))) for e in entries]
    return Chain(name=canon, entries=entries, mixed=mixed, stray=stray)


# --- eligible, quota, mark_limited, display_price ---------------------------------------------------------

# A chain entry's hours window. This module owns the grammar, and every reader reads it here:
#   window := clause (";" clause)*
#   clause := [days] range ("," range)* ["UTC"]
#   days   := day ["-" day] ("," day ["-" day])*      day: mon tue wed thu fri sat sun; a range may wrap the week
#   range  := HH "-" HH                               start 00-23 inclusive, end 00-24 exclusive, wraps midnight
# A clause reads its days and hours in local time, or in UTC with the suffix. The window is open when one clause holds
# the moment. A window that does not parse closes nothing. Example, DeepSeek off-peak:
#   "mon-fri 00-01,04-06,10-24 UTC; sat-sun 00-24 UTC"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY = r"(?:mon|tue|wed|thu|fri|sat|sun)"
_DAYSET = re.compile(rf"^{_DAY}(?:-{_DAY})?(?:,{_DAY}(?:-{_DAY})?)*$")
_RANGES = re.compile(r"^(?:[01]\d|2[0-3])-(?:[01]\d|2[0-4])(?:,(?:[01]\d|2[0-3])-(?:[01]\d|2[0-4]))*$")


def _days(text):
    out = set()
    for item in text.split(","):
        a, _, b = item.partition("-")
        i, j = DAYS.index(a), DAYS.index(b or a)
        out.update(DAYS[(i + k) % 7] for k in range((j - i) % 7 + 1))
    return frozenset(DAYS.index(d) for d in out)


@functools.lru_cache(maxsize=256)
def _clauses(window):
    """((weekdays or None, ((start, end), ...), utc), ...) for a window, or None when it does not parse."""
    if not isinstance(window, str):
        return None
    out = []
    for clause in window.lower().split(";"):
        words = re.sub(r"\s*([,-])\s*", r"\1", clause.strip()).split()
        utc = bool(words) and words[-1] == "utc"
        words = words[:-1] if utc else words
        days = None
        if len(words) == 2 and _DAYSET.match(words[0]):
            days, words = _days(words[0]), words[1:]
        if len(words) != 1 or not _RANGES.match(words[0]):
            return None
        out.append((days, tuple((int(r[:2]), int(r[3:5])) for r in words[0].split(",")), utc))
    return tuple(out)


def window_valid(window):
    """True when `window` parses and no range of it is empty ("07-07"): a window a writer may store."""
    parsed = _clauses(window)
    return parsed is not None and all(a != b for _, ranges, _ in parsed for a, b in ranges)


def _moment(at):
    """A timestamp for `at`: a timestamp, a struct_time (local, or UTC from gmtime) or a datetime (naive is local)."""
    if isinstance(at, time.struct_time):
        return calendar.timegm(at) - at.tm_gmtoff if at.tm_gmtoff is not None else time.mktime(at)
    if hasattr(at, "timestamp"):
        return at.timestamp()
    return float(at)


def in_window(window, at):
    """True when the moment `at` is inside `window` (the grammar above). A window that does not parse closes nothing."""
    parsed = _clauses(window)
    if parsed is None:
        return True
    ts = _moment(at)
    local, utc = time.localtime(ts), time.gmtime(ts)
    for days, ranges, in_utc in parsed:
        t = utc if in_utc else local
        if days is not None and t.tm_wday not in days:
            continue
        h = t.tm_hour
        if any(a <= h < b if a <= b else (h >= a or h < b) for a, b in ranges):
            return True
    return False


def _marker_state(sub, shared, now, reg):
    """(reason, until) while the subscription marker holds; None when the marker is absent or stale."""
    info = reg.subscriptions.get(sub) or {}
    name = info.get("marker")
    if not name:
        return None
    try:
        text = (_shared_dir(shared) / name).read_text().strip()
    except OSError:
        return None
    if not text:
        return None
    if sub == "grok":   # one TTL rule, shared with review.sh (#1417); an expired marker is eligible
        sys.path.insert(0, str(Path(__file__).resolve().parent)) if str(Path(__file__).resolve().parent) not in sys.path else None
        import grokmark  # type: ignore[import-not-found]  # a sibling file, not a package member
        st = grokmark.state(_shared_dir(shared), now)
        return ("balance exhausted", st[1]) if st[0] == "fresh" else None
    try:
        until = float(text)
    except ValueError:
        return ("balance exhausted" if sub == "grok" else "usage limit"), None
    now = time.time() if now is None else now
    return ("usage limit", int(until)) if until > now else None


def _quota_row(sub, shared, reg):
    """One subscription's row of the hub quota cache. A row with no `asof` takes the file time."""
    probe = (reg.subscriptions.get(sub) or {}).get("quota_probe") or sub
    path = _shared_dir(shared) / "hub-quota.json"
    try:
        doc = json.loads(path.read_text())
        row = dict(doc.get(probe) or {})
        if row and not isinstance(row.get("asof"), (int, float)):
            row["asof"] = path.stat().st_mtime
    except (OSError, ValueError, AttributeError):
        return {}
    return row


def _stale_seconds():
    try:
        return float(os.environ.get("SG_QUOTA_STALE_SECONDS") or DEFAULT_QUOTA_STALE_SECONDS)
    except ValueError:
        return DEFAULT_QUOTA_STALE_SECONDS


def _quota_state(sub, shared, now, reg, reserve=None):
    """(ok, reason, until) from the hub quota cache, for one subscription.

    A weekly window at 100% closes the subscription until its reset (the window's `resets`, else the row's `until`);
    for Claude, the reserve closes it at `100 - reserve`. A window's use only grows until it resets, so a reading with a
    reset ahead holds at any age. Any other reading older than the stale bound is unknown: the entry runs, and the
    reason names the stale cache. A reading whose reset has passed is unknown too: the window rolled over.
    """
    row = _quota_row(sub, shared, reg)
    if not row:
        return True, None, None
    age = now - row["asof"]
    stale = age > _stale_seconds()
    weekly = _weekly(row) or {}
    pct = weekly.get("pct")
    resets = weekly.get("resets") or row.get("until")
    ahead = isinstance(resets, (int, float)) and resets > now
    fresh = not stale and (ahead or not isinstance(resets, (int, float)))
    until = int(resets) if ahead else None
    if isinstance(pct, (int, float)) and (ahead or fresh):
        if pct >= 100:
            return False, "weekly quota spent", until
        if reserve is not None and pct >= 100 - reserve:
            return False, "claude weekly reserve", until
    if stale:
        return True, f"quota cache stale ({age / 3600:.0f} h old), read as unknown", None
    return True, None, None


def _weekly(row):
    return next((w for w in (row.get("windows") or []) if w.get("label") == "weekly"), None)


def _reserve_percent(sub, reg):
    if sub != "claude":
        return None
    return int((reg.subscriptions.get("claude") or {}).get("reserve_percent", DEFAULT_RESERVE_PERCENT))


def _plans():
    try:
        return json.loads(_default_plans_path().read_text())
    except (OSError, ValueError):
        return {}


def eligible(entry, shared=None, now=None, hour=None, reg=None):
    """Why one chain entry may not run now: the switch, its window, its subscription marker, a spent weekly window, or
    the Claude reserve. An eligible entry may carry a reason: a stale quota cache, read as unknown (#678).

    A v1 entry keeps the marker rule it had before the registry: its `legacy_codex` flag applies Codex's marker even
    when the registry maps the spelling to another subscription, so a v1 file never changes behavior.
    """
    if getattr(entry, "model_off", False):   # a reader may hand in an entry that predates the model switch
        return Eligibility(False, "off (model)", None)   # the model switch names itself, whatever its chain row says
    if not entry.on:
        return Eligibility(False, "off", None)
    now = time.time() if now is None else now
    at, lt = now, time.localtime(now)
    if hour is not None:   # a caller that names the local hour moves `now` to it, on the same local day
        at = time.mktime((*lt[:3], hour, *lt[4:8], -1))
    if entry.hrs and not in_window(entry.hrs, at):
        return Eligibility(False, f"outside its window {entry.hrs}", None)
    reg = reg or default_registry()
    if entry.legacy_codex:
        marker = _marker_state("gpt", shared, now, reg)
        if marker:
            return Eligibility(False, marker[0], marker[1])
    sub = entry.subscription
    if not sub:
        return Eligibility(True, None, None)
    marker = _marker_state(sub, shared, now, reg)
    if marker:
        return Eligibility(False, marker[0], marker[1])
    return Eligibility(*_quota_state(sub, shared, now, reg, _reserve_percent(sub, reg)))


def spent(raws, shared=None, now=None, reg=None):
    """The spellings of `raws` whose subscription a marker or the quota cache closes now, in their order (#678).

    Only the subscription is read: a switch or a window belongs to a chain, and a spelling with no subscription is
    never spent. The caller skips a spent spelling and keeps the order of the rest.
    """
    reg = reg or default_registry()
    now = time.time() if now is None else now
    out = []
    for raw in raws:
        ref = normalize(raw, reg)
        entry = reg.entries.get(ref.id)
        sub = entry.subscription if entry else None
        if not sub or raw in out or (reg.subscriptions.get(sub) or {}).get("spend_only"):
            continue
        if _marker_state(sub, shared, now, reg) or not _quota_state(sub, shared, now, reg, _reserve_percent(sub, reg))[0]:
            out.append(raw)
    return out


def quota(sub, shared=None, now=None, reserve_percent=None, reg=None):
    """One subscription's state: its marker first, then the probe windows, then the plan, then the Claude reserve."""
    reg = reg or default_registry()
    info = reg.subscriptions.get(sub) or {}
    out = {"sub": sub, "limited": False, "until": None, "reason": None, "windows": [],
           "plan": None, "reserve_percent": None, "spend_only": bool(info.get("spend_only"))}
    if out["spend_only"]:
        return out
    marker = _marker_state(sub, shared, now, reg)
    if marker:
        out.update(limited=True, reason=marker[0], until=marker[1])
    row = _quota_row(sub, shared, reg)
    out["windows"] = list(row.get("windows") or [])
    out["plan"] = _plans().get(sub)
    reserve = _reserve_percent(sub, reg)
    if reserve is not None:
        reserve = reserve_percent if reserve_percent is not None else reserve
        out["reserve_percent"] = reserve
    if not out["limited"]:
        ok, reason, until = _quota_state(sub, shared, time.time() if now is None else now, reg, reserve)
        if not ok:
            out.update(limited=True, reason=reason, until=until)
    return out


def mark_limited(sub, epoch=None, shared=None, reg=None):
    """Write one subscription marker. None means it does not reset by itself (the Grok prepaid balance)."""
    reg = reg or default_registry()
    info = reg.subscriptions.get(sub)
    if not info:
        raise RegistryError(f"unknown subscription {sub!r}")
    name = info.get("marker")
    if not name:
        raise RegistryError(f"{sub} has no marker: it is spend only")
    folder = _shared_dir(shared)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    tmp = folder / (name + ".tmp")
    tmp.write_text(("no-reset" if epoch is None else str(int(epoch))) + "\n")
    os.replace(tmp, path)
    return path


def display_price(entry_or_raw, reg=None):
    """The API-equivalent rate the hub and the dashboard show. The engine never reads it."""
    if isinstance(entry_or_raw, Entry):
        return entry_or_raw.display_price
    reg = reg or default_registry()
    entry = model(entry_or_raw, reg)
    return entry.display_price if entry else None


# --- compatible and migrate: v1 to v2 ---------------------------------------------------------------------

MIGRATED_KEYS = ("off", "hours", "efforts", "review_timeout_seconds_by_model", "review_no_tools")
_V1_TO_CANON = {v1: canon for canon, v1 in V1_CHAINS.items()}


def compatible(mid, chain_name, reg=None):
    """Whether one chain offers and keeps a spelling. A retired or unknown id is never compatible."""
    reg = reg or default_registry()
    canon = _canonical(chain_name)
    ref = normalize(mid, reg)
    entry = reg.entries.get(ref.id) if ref.known else None
    if entry is None:
        return (False, "unknown id")
    if entry.retired:
        return (False, "retired")
    if canon in ("coding", "router", "probe"):
        if "gateway" not in entry.routes:
            return (False, "no gateway route")
        if not entry.capabilities.get("tools"):
            return (False, "gateway model takes no tools")
    elif canon in ("review", "loop_defect"):
        if not any(name in entry.routes for name in ("cli", "direct", "gateway")):
            return (False, "no cli, direct or gateway route")
    elif canon == "vision":
        if not entry.capabilities.get("vision"):
            return (False, "no vision capability")
    return (True, None)


def no_tools_route(mid, reg=None):
    """True when the registry knows the spelling and its coding or router route takes no tools. Unknown passes as data."""
    return compatible(mid, "coding", reg)[1] == "gateway model takes no tools"


def _migrate_route(canon, raw, entry, no_tools):
    """The explicit route an entry runs today: coding and router through the gateway, review as review.sh runs it."""
    if canon in ("coding", "router"):
        return "gateway"
    if canon == "review" and raw.startswith("claude:") and raw in no_tools:
        return "direct"
    return _default_route(canon, entry)


def _migrate_entry(canon, raw, ref, entry, settings):
    out = {"id": ref.id}
    if raw != ref.id:
        out["raw"] = raw
    out["route"] = _migrate_route(canon, raw, entry, settings["no_tools"])
    effort = settings["efforts"].get(raw) or ref.effort
    if effort:
        out["effort"] = effort
    hrs = settings["hours"].get(raw)
    if hrs:
        out["hrs"] = hrs
    if settings["off"].get(raw):
        out["on"] = False
    if canon == "review":
        timeout = settings["timeouts"].get(raw)
        if timeout is not None:
            out["timeout_s"] = timeout
        if raw in settings["no_tools"]:
            out["no_tools"] = True
    return out


def _migration_orphans(doc, kept):
    """Every keyed setting whose spelling survives in no chain: (data, report lines). It is never applied."""
    orphans, lines = {}, []

    def note(setting, path, spelling, value, canon):
        if path is None:
            if isinstance(value, list):
                orphans.setdefault(setting, []).extend(value)
            else:
                orphans.setdefault(setting, {})[spelling] = value
            lines.append(f"orphan {setting}.{spelling}: not in the {canon} chain")
        else:
            orphans.setdefault(setting, {}).setdefault(path, {})[spelling] = value
            lines.append(f"orphan {setting}.{path}.{spelling}: not in the {canon} chain")

    for setting in ("off", "hours", "efforts"):
        section = doc.get(setting)
        if not isinstance(section, dict):
            continue
        for path, rows in section.items():
            if not isinstance(rows, dict):
                continue
            canon = _V1_TO_CANON.get(path)
            if canon is None:
                orphans.setdefault(setting, {})[path] = dict(rows)
                lines.append(f"orphan {setting}.{path}: names no v1 chain")
                continue
            for spelling, value in rows.items():
                if spelling in kept[canon]:
                    continue
                note(setting, path, spelling, value, canon)
    timeouts = doc.get("review_timeout_seconds_by_model")
    if isinstance(timeouts, dict):
        for spelling, value in timeouts.items():
            if spelling not in kept["review"]:
                note("review_timeout_seconds_by_model", None, spelling, value, "review")
    no_tools = doc.get("review_no_tools")
    if isinstance(no_tools, list):
        for spelling in no_tools:
            if spelling not in kept["review"]:
                note("review_no_tools", None, spelling, [spelling], "review")
    return orphans, lines


def _atomic_write(path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _diff(old, new, path):
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"{path} (v1)", tofile=f"{path} (v2)"))


def _live_roots():
    """The live skill directories: ~/.claude/skills, and SG_LIVE_DIR when set. Symlinks resolved."""
    roots = [Path.home() / ".claude" / "skills"]
    if os.environ.get("SG_LIVE_DIR"):
        roots.append(Path(os.environ["SG_LIVE_DIR"]))
    return [r.resolve() for r in roots]


def live_path(path):
    """True when path, symlinks resolved, is under a live skill directory."""
    real = Path(path).resolve()
    return any(real == r or r in real.parents for r in _live_roots())


def migrate(path, dry_run=False, reg=None, live=False):
    """Convert one v1 models.json to the v2 chain shape.

    The write is atomic, keeps a `.v1.bak`, and prints a diff. A second run on a v2 file is a no-op. A mixed
    file is refused. An entry whose chain cannot run it is dropped and named; an orphaned setting is kept under
    `_orphans` and reported, never applied. A write under the live skill copy is the operator's, after landing,
    so it is refused unless `live` is set (#658); a dry run is allowed.
    """
    reg = reg or default_registry()
    p = Path(path)
    if not dry_run and not live and live_path(p):
        raise RegistryError(f"models {p.resolve()}: refused: the file is in the live skill copy; "
                            f"only the operator migrates it, after landing, with --live")
    text = p.read_text()
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise RegistryError(f"models {p}: invalid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise RegistryError(f"models {p}: the document must be an object")
    stray = [key for key in V1_CHAINS.values() if key in doc]
    if isinstance(doc.get("chains"), dict):
        if stray:
            raise RegistryError(f"models {p}: mixed file: it carries both v1 chains ({', '.join(stray)}) "
                                f"and `chains`")
        return {"path": str(p), "changed": False, "mixed": False, "dropped": [], "orphans": {},
                "dropped_lines": [], "orphan_lines": [], "diff": ""}
    chains, kept, dropped, dropped_lines = {}, {}, [], []
    for canon, v1key in V1_CHAINS.items():
        settings = {
            "off": (doc.get("off") or {}).get(v1key) or {},
            "hours": (doc.get("hours") or {}).get(v1key) or {},
            "efforts": (doc.get("efforts") or {}).get(v1key) or {},
            "timeouts": doc.get("review_timeout_seconds_by_model") or {},
            "no_tools": doc.get("review_no_tools") or [],
        }
        entries, surviving = [], set()
        for raw in doc.get(v1key) or []:
            ok, reason = compatible(raw, canon, reg)
            if not ok:
                dropped.append({"chain": canon, "raw": raw, "reason": reason})
                dropped_lines.append(f"dropped {canon} {raw}: {reason}")
                continue
            ref = normalize(raw, reg)
            surviving.add(raw)
            entries.append(_migrate_entry(canon, raw, ref, reg.entries.get(ref.id), settings))
        chains[canon] = entries
        kept[canon] = surviving
    orphans, orphan_lines = _migration_orphans(doc, kept)
    out = {"schema_version": 2, "chains": chains}
    for key, value in doc.items():
        if key in V1_CHAINS.values() or key in MIGRATED_KEYS:
            continue
        out[key] = value
    if orphans:
        out["_orphans"] = orphans
    new_text = json.dumps(out, indent=2) + "\n"
    result = {"path": str(p), "changed": True, "mixed": False, "dropped": dropped, "orphans": orphans,
              "dropped_lines": dropped_lines, "orphan_lines": orphan_lines,
              "diff": _diff(text, new_text, p)}
    if not dry_run:
        _atomic_write(p.with_name(p.name + ".v1.bak"), text)
        _atomic_write(p, new_text)
    return result


def write_chains(path, chains, doc=None):
    """Write one v2 models.json from complete chains: `{canonical chain name: [entry, ...]}`.

    Every top-level key of `doc` other than the v1 chains, the flat setting maps and the entry-shape line is kept, so
    a caller changes the chains and nothing else. The entry shape is the one `migrate` writes. The write is atomic.
    Return the document written.

    A caller that passes no `doc` keeps the file's own keys: it reads the file on disk. No `doc` never means "a file of
    two keys", because that drops the review timeout, the review line cap and the stall budget. A file that is absent
    or unreadable is the empty document it always was.

    A `doc` still in the v1 shape is migrated whole, never partly: a keyed setting for a model that survives in no
    chain goes under `_orphans` as `migrate` keeps it, and the input file is saved as `<name>.v1.bak` first, so a
    caller's Save can never drop a timeout or a tool rule in silence."""
    src = Path(path)
    out_chains = {_canonical(name): list(entries) for name, entries in chains.items()}
    if not isinstance(doc, dict):
        try:
            doc = json.loads(src.read_text())
        except (OSError, ValueError):
            doc = {}
    if not isinstance(doc, dict):
        doc = {}
    kept = {key: value for key, value in doc.items()
            if key not in V1_CHAINS.values() and key not in MIGRATED_KEYS and key not in ("schema_version", "chains")}
    out = {"schema_version": 2, "chains": out_chains, **kept}
    if any(key in doc for key in V1_CHAINS.values()):
        surviving = {canon: set() for canon in V1_CHAINS}
        surviving.update({canon: {e.get("raw") or e["id"] for e in entries} for canon, entries in out_chains.items()})
        orphans, _ = _migration_orphans(doc, surviving)
        if orphans:
            out["_orphans"] = orphans
        try:
            _atomic_write(src.with_name(src.name + ".v1.bak"), src.read_text())
        except OSError:
            pass
    _atomic_write(src, json.dumps(out, indent=2) + "\n")
    return out


def migrate_lines(result):
    """The report a migrate prints: one line per dropped entry, one per orphan, and a no-op line."""
    lines = result["dropped_lines"] + result["orphan_lines"]
    if not result["changed"] and not result["mixed"]:
        lines.append(f"already v2: {result['path']}")
    return lines


# --- check and CLI ----------------------------------------------------------------------------------------

def check(path=None, models=None):
    """The registry's own report: dropped entries, unknown chain ids, and a mixed models file."""
    try:
        reg = load(path)
    except RegistryError as exc:
        return {"ok": False, "error": str(exc), "dropped": [], "mixed": [], "unknown": []}
    unknown, mixed = [], []
    if models:
        for name in V1_CHAINS:
            try:
                c = chain(models, name, reg)
            except (OSError, ValueError) as exc:
                return {"ok": False, "error": str(exc), "dropped": reg.dropped, "mixed": mixed, "unknown": unknown}
            if c.mixed:
                mixed.append(name)
            unknown += [f"{name}: {e.raw}" for e in c.entries if not e.known]
    return {"ok": not reg.dropped and not unknown, "error": None,
            "dropped": reg.dropped, "mixed": mixed, "unknown": unknown}


def _check_text(result, reg=None):
    if result["error"]:
        return [f"error: {result['error']}"]
    lines = [f"dropped {d['id']}: {d['reason']}" for d in result["dropped"]]
    lines += [f"unknown {u}" for u in result["unknown"]]
    lines += [f"mixed: {name} carries both shapes; `chains` wins" for name in result["mixed"]]
    if reg is not None:
        lines.append(f"ok: {len(reg.entries)} models")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(prog="modelreg", description="Resolve model ids through one registry.")
    ap.add_argument("--registry", default=None, help="registry file (default: the committed one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("chain", help="print one chain")
    c.add_argument("name")
    c.add_argument("--models", default=None)
    c.add_argument("--eligible", action="store_true")
    c.add_argument("--tsv", action="store_true")
    c.add_argument("--shared", default=None)
    c.add_argument("--now", type=float, default=None)
    r = sub.add_parser("review-route", help="print one reviewer's route, runner, wire string and tools as a tab row")
    r.add_argument("id")
    r.add_argument("--route", choices=("cli",), default=None, help="the CLI row of a reviewer, its streamed call's fallback")
    a = sub.add_parser("argv", help="print argv, NUL-separated")
    a.add_argument("id")
    a.add_argument("--use", default="gateway", choices=ROUTES)
    a.add_argument("--effort", default=None)
    a.add_argument("--prompt-file", default=None)
    m = sub.add_parser("mark-limited", help="write a subscription marker")
    m.add_argument("sub")
    m.add_argument("--until", type=float, default=None)
    m.add_argument("--shared", default=None)
    k = sub.add_parser("check", help="validate the registry and the chains")
    k.add_argument("--models", default=None)
    g = sub.add_parser("migrate", help="convert a v1 models.json to the v2 chain shape")
    g.add_argument("models")
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--live", action="store_true", help="allow a write under the live skill copy (operator only)")
    ns = ap.parse_args(argv)
    try:
        reg = load(ns.registry)
    except RegistryError as exc:
        print(exc, file=sys.stderr)
        return 1
    if ns.cmd == "check":
        result = check(ns.registry, ns.models)
        for line in _check_text(result, reg):
            print(line)
        return 0 if result["ok"] else 1
    if ns.cmd == "chain":
        rows = chain(ns.models or _default_models_path(), ns.name, reg).entries
        if ns.eligible:
            rows = [e for e in rows if eligible(e, shared=ns.shared, now=ns.now, reg=reg).ok]
        if ns.tsv:   # column 8: 1 when the entry's hours window holds `--now` (default: the clock), read by the shell callers
            at = time.time() if ns.now is None else ns.now
            for e in rows:
                print("\t".join([e.id, e.raw, e.route or "", e.effort or "", e.hrs or "",
                                 "" if e.timeout_s is None else str(e.timeout_s), "1" if e.on else "0",
                                 "1" if not e.hrs or in_window(e.hrs, at) else "0"]))
        else:
            for e in rows:
                print(e.id)
        return 0
    if ns.cmd == "review-route":
        rr = review_route(ns.id, reg, ns.route)
        print("\t".join([rr.route, rr.runner or "-", rr.wire, "1" if rr.tools else "0"]))
        return 0
    if ns.cmd == "argv":
        inv = invocation(ns.id, ns.use, effort=ns.effort, prompt_file=ns.prompt_file, reg=reg)
        if not inv.argv:
            print(f"modelreg: {ns.id} has no {ns.use} route", file=sys.stderr)
            return 2
        sys.stdout.write("\0".join(inv.argv))
        return 0
    if ns.cmd == "migrate":
        try:
            result = migrate(ns.models, dry_run=ns.dry_run, reg=reg, live=ns.live)
        except (OSError, RegistryError) as exc:
            print(f"modelreg: {exc}", file=sys.stderr)
            return 1
        for line in migrate_lines(result):
            print(line)
        if result["diff"]:
            sys.stdout.write(result["diff"])
        return 0
    if ns.cmd == "mark-limited":
        try:
            print(mark_limited(ns.sub, ns.until, shared=ns.shared, reg=reg))
        except RegistryError as exc:
            print(exc, file=sys.stderr)
            return 2
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
