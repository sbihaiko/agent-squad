"""Facts: observations that a `when` or a proposal may read (ADR-0104, ADR-0106 rule 6).

Only code produces a fact. The registry is closed: a name outside it is refused on read and on write.
A fact carries its instrument, its source, a timestamp, and a severity. A model never mints one.
"""
import operator
import time
from dataclasses import dataclass
from pathlib import Path

from squad.graph import BUILTIN_FACTS

BUILTIN_INSTRUMENT = {
    "run_spend": "code: sum of measured SDK result spend over agent entries (ADR-0108)",
    "run_spend_remaining": "code: run spend cap minus run_spend (ADR-0108)",
    "depth": "code: depth of the child being requested (ADR-0113)",
    "children_count": "code: accepted children of the requesting parent in its entry (ADR-0113)",
    "spawn_rejected_count": "code: spawn_rejected events in this run (ADR-0113)",
    "tier_pin": "code: the tier_pin engine option (ADR-0106 rule 6)",
}
OPS = {"eq": operator.eq, "ne": operator.ne, "gt": operator.gt, "gte": operator.ge, "lt": operator.lt, "lte": operator.le}
NOT_READY = object()

# The tier classifier and the review backstop (ADR-0101 rule 2). A missing estimate is large.
TRIVIAL_MAX = {"files": 2, "added_lines": 50}
MEDIUM_MAX = {"files": 10, "added_lines": 400}
REVIEW_FILES, REVIEW_LINES = 3, 80
SENSITIVE_PATHS = ("auth", "crypto", ".env")
UI_SUFFIXES = (".css", ".html", ".tsx", ".jsx", ".vue", ".svelte")


class FactError(Exception):
    pass


class UnregisteredFact(FactError):
    pass


@dataclass(frozen=True)
class Fact:
    name: str
    value: object
    instrument: str
    source: dict
    timestamp: float
    severity: str = "info"

    def as_dict(self):
        return {"name": self.name, "value": self.value, "instrument": self.instrument, "source": self.source,
                "timestamp": self.timestamp, "severity": self.severity}


def fact_errors(fact, current_pos, latest=None):
    """Return why a fact is refused (ADR-0104 rule 7). An empty list means it is fresh and sourced.

    `latest(source)` gives the newest version of a source. A fact is not refused only because it is old.
    """
    errs = []
    if not fact.source:
        errs.append("no_source")
    if not fact.timestamp:
        errs.append("no_timestamp")
    src = fact.source or {}
    if src.get("kind") == "run_record" and src.get("pos", 0) > current_pos:
        errs.append("future_record_position")
    if latest is not None and src:
        newest = latest(src)
        if newest is not None and newest != src.get("version", src.get("commit")):
            errs.append("source_has_a_newer_version")
    return errs


class FactStore:
    def __init__(self, registry, node_names, run_id="run", clock=time.time):
        self.defs = {f["name"]: f for f in registry}
        self.node_names = set(node_names)
        self.run_id, self.clock = run_id, clock
        self.values = {}  # (name, item) -> Fact

    def registered(self, name):
        if name.startswith("outcome:"):
            return name[8:] in self.node_names
        return name in self.defs or name in BUILTIN_FACTS

    def instrument_of(self, name):
        if name in self.defs:
            return self.defs[name]["instrument"]
        if name.startswith("outcome:"):
            return "code: the node's outcome, derived from its declared artifact (ADR-0105)"
        return BUILTIN_INSTRUMENT[name]

    def produce(self, name, value, source, item=None, timestamp=None, severity="info", instrument=None):
        if not self.registered(name):
            raise UnregisteredFact(f"{name} is not in the facts registry (ADR-0104 rule 1)")
        if not source:
            raise FactError(f"{name}: a fact with no source is refused (ADR-0104 rule 7)")
        ts = self.clock() if timestamp is None else timestamp
        if not ts:
            raise FactError(f"{name}: a fact with no timestamp is refused (ADR-0104 rule 7)")
        f = Fact(name, value, instrument or self.instrument_of(name), source, ts, severity)
        self.values[(name, item)] = f
        return f

    def get(self, name, item=None):
        """Return the fact in the item's scope, else the run scope, else None. An unregistered name raises."""
        if not self.registered(name):
            raise UnregisteredFact(f"{name} is not in the facts registry (ADR-0104 rule 1)")
        return self.values.get((name, item)) or self.values.get((name, None))

    def read(self, name, item=None):
        f = self.get(name, item)
        return None if f is None else f.value

    def view_for(self, sees, names):
        """The facts a proposer sees. A name off its `sees` list is refused, and so is `tier`."""
        out = {}
        for n in names:
            if n not in sees or n == "tier":
                raise FactError(f"{n} is not on the proposer's sees list (ADR-0104 rule 2)")
            f = self.get(n)
            if f is not None:
                out[n] = f.as_dict()
        return out

    def evaluate(self, when, item=None):
        """Evaluate one predicate. Return (truth, reason). An unavailable fact is false with its name as the reason."""
        v = self.read(when["fact"], item)
        if v is None:
            return False, f"fact_unavailable:{when['fact']}"
        return bool(OPS[when["op"]](v, when["value"])), None


# ---------------------------------------------------------------- derived facts

@dataclass
class DeriveCtx:
    """What a derivation may read: artifacts in scope, which nodes exited, the target, and the options."""
    artifact: object       # callable (key, item) -> payload or None
    ran: object            # callable (node, item) -> bool
    item: int | None
    target: Path
    options: dict
    has_git: bool
    facts: FactStore
    pos: int = 0


def _size(est):
    if not isinstance(est, dict) or not all(isinstance(est.get(k), int) for k in ("files", "added_lines")):
        return "large"
    if est["files"] <= TRIVIAL_MAX["files"] and est["added_lines"] <= TRIVIAL_MAX["added_lines"]:
        return "trivial"
    if est["files"] <= MEDIUM_MAX["files"] and est["added_lines"] <= MEDIUM_MAX["added_lines"]:
        return "medium"
    return "large"


def derive_tier(c):
    pin = c.facts.read("tier_pin")
    if pin:
        return pin
    if not c.ran("scout", None):
        return NOT_READY
    return _size(c.artifact("size_estimate", None))


def derive_sensitive(c):
    if not c.ran("scout", None):
        return NOT_READY
    paths = (c.artifact("scout", None) or {}).get("paths", [])
    return any(s in p for p in paths for s in SENSITIVE_PATHS)


def derive_visual(c):
    if not c.ran("scout", None):
        return NOT_READY
    paths = (c.artifact("scout", None) or {}).get("paths", [])
    return any(p.endswith(UI_SUFFIXES) for p in paths)


def derive_git(c):
    return c.has_git


def work_item_list(wi):
    """The decompose artifact is a list, or an object with an `items` list."""
    if isinstance(wi, list):
        return wi
    return wi["items"] if isinstance(wi, dict) and isinstance(wi.get("items"), list) else None


def derive_work_items(c):
    items = work_item_list(c.artifact("work_items", None))
    if items is not None:
        return len(items)
    return 1 if c.ran("decompose", None) or c.ran("plan", None) else NOT_READY   # `plan` skipped by its `when` still ran


def derive_review_required(c):
    diff = c.artifact("diff", c.item)
    if not isinstance(diff, dict):
        return NOT_READY
    files = [f["path"] for f in diff.get("files", [])]
    sensitive = any(s in p for p in files for s in SENSITIVE_PATHS)
    tier = c.facts.read("tier")
    return bool(sensitive or len(files) > REVIEW_FILES or diff.get("added_lines", 0) > REVIEW_LINES or tier == "large")


def derive_blocking_learnings(c):
    import json
    p = c.target / "learnings.json"
    if not p.exists():
        return 0
    return sum(1 for e in json.loads(p.read_text()).get("entries", []) if e.get("blocking"))


def derive_architecture_issues(c):
    crit = c.artifact("critic", c.item)
    if not c.ran("critic", c.item):
        return NOT_READY
    return len((crit or {}).get("architecture_issues", []))


# name -> (function, scope): item-scoped derivations are recomputed in each item
DERIVED = {
    "tier": (derive_tier, False), "sensitive": (derive_sensitive, False), "visual": (derive_visual, False),
    "git": (derive_git, False), "work_items": (derive_work_items, False), "review_required": (derive_review_required, True),
    "blocking_learnings": (derive_blocking_learnings, False), "architecture_issues": (derive_architecture_issues, True),
}


def refresh(store, c):
    """Recompute every registered derived fact that its inputs can now give. Return the names produced."""
    made = []
    for name, (fn, scoped) in DERIVED.items():
        if name not in store.defs or (scoped and c.item is None):
            continue
        v = fn(c)
        if v is NOT_READY:
            continue
        item = c.item if scoped else None
        old = store.values.get((name, item))
        if old is None or old.value != v:
            store.produce(name, v, {"kind": "run_record", "run": store.run_id, "pos": c.pos}, item=item)
            made.append(name)
    return made
