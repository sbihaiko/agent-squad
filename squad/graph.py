"""Load and validate a graph document; compute the hash that a layout change never moves.

Rules: ADR-0100 to ADR-0111, ADR-0113. The JSON Schema in docs/design states the shape.
The cross-reference rules a schema cannot state live here. Every error names its node.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator

from squad.gates import GATE_REGISTRY

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "docs" / "design" / "graph.schema.json"
KINDS = ("agent", "gate", "wait", "loop", "each", "action", "dispatch")
BUILTIN_FACTS = {"depth", "children_count", "spawn_rejected_count", "run_spend", "run_spend_remaining", "tier_pin"}
SEES_FORBIDDEN = {"tier"}  # ADR-0104 rule 3: a `when` may read tier, a proposer may not
WRITE_TOOLS = {"Edit", "Write", "Bash", "NotebookEdit"}
FORBIDDEN_NAME = re.compile(r"(pass_rate|finding|trend|self_report|judge|confidence|effectiveness|learning_count)")
FORBIDDEN_INSTRUMENT = re.compile(r"(\bmodel\b|\bjudge\b|self-report|self report|confidence|pass rate|finding count|\btrend\b|\b(another|other|previous|past|earlier) run\b|adaptation report)", re.I)


@dataclass(frozen=True)
class GraphError:
    node: str | None
    message: str

    def __str__(self):
        return f"{self.node}: {self.message}" if self.node else self.message


class InvalidGraph(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("\n".join(str(e) for e in errors))


def canon_hash(doc):
    """Hash of the canonical graph document. Layout lives in a sibling file and is never part of it."""
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _schema():
    return json.loads(SCHEMA_PATH.read_text())


def _schema_errors(doc, schema):
    top = json.loads(json.dumps(schema))
    top["properties"]["nodes"] = {"type": "array", "minItems": 1}
    errs = [GraphError(None, f"schema: {'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}") for e in Draft202012Validator(top).iter_errors(doc)]
    return errs


def _node_schema_errors(node, schema):
    kind = node.get("kind")
    name = node.get("name") if isinstance(node.get("name"), str) else None
    if kind not in KINDS:
        return [GraphError(name, f"kind {kind!r} is not one of the seven kinds: {', '.join(KINDS)} (ADR-0100 rule 1)")]
    explicit, stripped = [], dict(node)
    if kind == "gate":
        for key, msg in (("when", "a gate never carries a when (ADR-0100 rule 3)"), ("adapt", "a gate never adapts (ADR-0103 rule 1)"),
                         ("deadline", "a gate carries no deadline (ADR-0102 rule 3)")):
            if key in stripped:
                explicit.append(GraphError(name, msg))
                del stripped[key]
        nxt = stripped.get("next") or {}
        if isinstance(nxt, dict) and nxt.get("fail") not in (None, "repeat", "exit:fail"):
            explicit.append(GraphError(name, f"a gate's fail never names a node; got {nxt['fail']!r} (ADR-0101 rule 2)"))
            stripped["next"] = {k: v for k, v in nxt.items() if k != "fail"}
    if kind == "loop" and "deadline" in stripped:
        explicit.append(GraphError(name, "a loop carries no deadline (ADR-0102 rule 3)"))
        del stripped["deadline"]
    sub = {"$ref": f"#/$defs/{kind}", "$defs": schema["$defs"]}
    errs = [GraphError(name, f"{'/'.join(map(str, e.absolute_path)) or kind}: {e.message}") for e in Draft202012Validator(sub).iter_errors(stripped)]
    return explicit + errs


def _when_errors(nm, when, nodes, known, fact_defs):
    f = when["fact"]
    if f.startswith("outcome:"):
        return [] if f[8:] in nodes else [GraphError(nm, f"when names unknown node {f[8:]}")]
    if f not in known:
        return [GraphError(nm, f"when reads unregistered fact {f} (ADR-0104 rule 1)")]
    d, v = fact_defs.get(f), when["value"]
    if d and d["type"] == "enum" and v not in d.get("values", []):
        return [GraphError(nm, f"when value {v!r} is not a value of fact {f}")]
    if d and d["type"] == "bool" and not isinstance(v, bool):
        return [GraphError(nm, f"when value for bool fact {f} must be a bool")]
    if d and d["type"] in ("int", "number") and (isinstance(v, bool) or not isinstance(v, (int, float))):
        return [GraphError(nm, f"when value for numeric fact {f} must be a number")]
    return []


def _fact_errors(doc):
    errs, seen = [], set()
    for f in doc["facts"]:
        n = f["name"]
        if n in seen:
            errs.append(GraphError(None, f"fact {n}: registered twice"))
        seen.add(n)
        if FORBIDDEN_NAME.search(n):
            errs.append(GraphError(None, f"fact {n}: a gate rate, a finding count or trend, a self-report, or a judge score never triggers (ADR-0104 rule 5)"))
        if not f["instrument"].startswith("code:"):
            errs.append(GraphError(None, f"fact {n}: instrument must be code, not a model (ADR-0104 rule 4)"))
        elif FORBIDDEN_INSTRUMENT.search(f["instrument"]):
            errs.append(GraphError(None, f"fact {n}: instrument names a model, a judge, a cross-run source, or the adaptation report (ADR-0104 rule 4, ADR-0109, ADR-0110)"))
    return errs


def _subtree(nodes, name):
    out, stack = [], [name]
    while stack:
        n = nodes[stack.pop()]
        for c in n.get("children", []):
            if c in nodes:
                out.append(c)
                stack.append(c)
    return out


def _each_workspace_errors(nm, each, nodes, run):
    if each["mode"] != "parallel" or min(each.get("max_parallel", run["max_parallel"]), run["max_parallel"]) < 2:
        return []
    sub = [nodes[c] for c in _subtree(nodes, nm)]
    created = {}
    for n in sub:
        if n["kind"] == "action" and n["effect"] == "worktree_create":
            created.setdefault(n.get("workspace"), []).append(n["name"])
    errs = [GraphError(nm, f"parallel items share workspace {w!r}: created by {', '.join(ns)} (ADR-0102 rule 2)") for w, ns in created.items() if len(ns) > 1]
    for n in sub:
        ws = n.get("workspace")
        writes = n["kind"] == "action" and n["effect"] != "worktree_create" or (n["kind"] == "agent" and WRITE_TOOLS & set(n["tools"]))
        if n["kind"] not in ("agent", "action") or not writes:
            continue
        if ws is None or ws == "." or ws not in created:
            errs.append(GraphError(nm, f"parallel items share workspace {ws!r} at {n['name']}: no worktree_create makes one per item (ADR-0102 rule 2)"))
    return errs


def _file_errors(nodes):
    """A declared input file is one a person keeps: no other node may write it (ADR-0110 rule 2)."""
    writers = {}
    for n in nodes.values():
        for f in n.get("writes", []):
            writers.setdefault(f, []).append(n["name"])
    return [GraphError(n["name"], f"reads {f!r}, which {', '.join(w)} writes: a declared input is never written by the graph (ADR-0110 rule 2)")
            for n in nodes.values() for f in n.get("reads", []) if (w := [x for x in writers.get(f, []) if x != n["name"]])]


def validate(doc, gate_registry=GATE_REGISTRY):
    """Return every error in a graph document. An empty list means the graph is valid."""
    if not isinstance(doc, dict):
        return [GraphError(None, "a graph document is one JSON object")]
    schema = _schema()
    errs = _schema_errors(doc, schema)
    if not isinstance(doc.get("nodes"), list):
        return errs
    bad_nodes = False
    for n in doc["nodes"]:
        e = _node_schema_errors(n, schema) if isinstance(n, dict) else [GraphError(None, "a node is one JSON object")]
        bad_nodes |= bool(e)
        errs += e
    if errs:
        return errs
    nodes, parents = {}, {}
    for n in doc["nodes"]:
        if n["name"] in nodes:
            errs.append(GraphError(n["name"], "duplicate node name"))
        nodes[n["name"]] = n
    fact_defs = {f["name"]: f for f in doc["facts"]}
    known = set(fact_defs) | BUILTIN_FACTS
    errs += _fact_errors(doc)
    gate_ids = {g["id"]: set(g["params"]) for g in doc["gates"]}
    for gid, params in gate_ids.items():
        if gid not in gate_registry:
            errs.append(GraphError(None, f"gates registry: check id {gid} is outside the engine registry (ADR-0111 rule 7)"))
        elif params != set(gate_registry[gid]):
            errs.append(GraphError(None, f"gates registry: params of {gid} differ from the engine registry"))
    if doc["root"] not in nodes or nodes[doc["root"]]["kind"] != "loop":
        errs.append(GraphError(doc["root"], "root must name a loop"))
    for n in nodes.values():
        for c in n.get("children", []):
            if c not in nodes:
                errs.append(GraphError(n["name"], f"unknown child {c}"))
            elif c in parents:
                errs.append(GraphError(c, "two parents"))
            parents[c] = n["name"]
    entries = {}
    for n in nodes.values():
        if n["kind"] != "dispatch":
            continue
        for c in [n["entry"]] + n["checks"]:
            if c not in nodes:
                errs.append(GraphError(n["name"], f"unknown entry or check {c}"))
                continue
            if c in parents:
                errs.append(GraphError(c, "two parents"))
            parents[c] = n["name"]
        entries[n["entry"]] = n["name"]
    run = doc["run"]
    min_spend = [n["budget"]["spend"]["min"] for n in nodes.values() if n["kind"] in ("agent", "dispatch")]
    if min_spend and run["spend_cap"] < max(min_spend):
        errs.append(GraphError(None, f"run spend cap {run['spend_cap']} is below an agent's spend minimum {max(min_spend)} (ADR-0108 rule 9)"))
    for n in nodes.values():
        nm, k = n["name"], n["kind"]
        if nm != doc["root"] and nm not in parents:
            errs.append(GraphError(nm, "orphan: no parent"))
        if "deadline" in n and k != "wait" and run["deadline"] is not None and n["deadline"] > run["deadline"]:
            errs.append(GraphError(nm, "deadline above the run deadline"))
        if n.get("next") and "repeat" in n["next"].values() and nodes.get(parents.get(nm), {}).get("kind") != "loop":
            errs.append(GraphError(nm, "repeat is valid only on a child of a loop"))
        if "when" in n:
            errs += _when_errors(nm, n["when"], nodes, known, fact_defs)
        if k == "gate":
            if n["check"] not in gate_ids:
                errs.append(GraphError(nm, f"unknown check {n['check']} (ADR-0111 rule 7)"))
            elif set(n["params"]) != gate_ids[n["check"]]:
                errs.append(GraphError(nm, "params differ from the gates registry"))
        elif k == "each":
            if n["items"] not in fact_defs and n["items"] not in BUILTIN_FACTS:
                errs.append(GraphError(nm, "items is not a registered fact"))
            errs += _each_workspace_errors(nm, n, nodes, run)
        elif k == "agent":
            for key in ("turns", "spend"):
                if n["budget"][key]["min"] > n["budget"][key]["max"]:
                    errs.append(GraphError(nm, f"{key} min above max"))
            if n.get("may_spawn") and nm not in entries:
                errs.append(GraphError(nm, "may_spawn on a node that is not a dispatch entry"))
            if n.get("may_propose") and "adapt" not in n:
                errs.append(GraphError(nm, "may_propose without an adapt block"))
        elif k == "loop":
            if n["max"]["min"] > n["max"]["value"]:
                errs.append(GraphError(nm, "max min above value"))
            if "when" in n and not any(nodes[c]["kind"] in ("agent", "each", "action", "dispatch") for c in n["children"] if c in nodes):
                errs.append(GraphError(nm, "a loop with a when must hold an agent, each, action, or dispatch child (ADR-0101 rule 3)"))
        elif k == "dispatch":
            errs += _dispatch_errors(doc, n, nodes)
        a = n.get("adapt")
        if a:
            errs += _adapt_errors(nm, n, a, known)
    return errs + _file_errors(nodes)


def _dispatch_errors(doc, n, nodes):
    nm, errs = n["name"], []
    e = nodes.get(n["entry"])
    if e is not None:
        if e["kind"] != "agent":
            errs.append(GraphError(nm, f"entry {n['entry']} is not an agent"))
        elif not e.get("may_spawn"):
            errs.append(GraphError(nm, f"entry {n['entry']} lacks may_spawn"))
        if "when" in e:
            errs.append(GraphError(nm, f"entry {n['entry']} carries a when, which would bypass the dispatch"))
    for c in n["checks"]:
        if c in nodes and nodes[c]["kind"] != "gate":
            errs.append(GraphError(nm, f"check {c} is not a gate (ADR-0113 rule 2)"))
    for key in ("turns", "spend"):
        if n["budget"][key]["min"] > n["budget"][key]["max"]:
            errs.append(GraphError(nm, f"child {key} min above max"))
    if n["max_parallel"] > doc["run"]["max_parallel"]:
        errs.append(GraphError(nm, "max_parallel above the run block"))
    names = [x["name"] for x in n["allow"]["skills"]]
    if len(names) != len(set(names)):
        errs.append(GraphError(nm, "duplicate skill in allow"))
    return errs


def _adapt_errors(nm, n, a, known):
    k, errs = n["kind"], []
    if k not in ("agent", "loop"):
        errs.append(GraphError(nm, f"adapt on a {k}"))
    for x in a["kinds"]:
        if x == "shrink_loop_max" and k != "loop":
            errs.append(GraphError(nm, "shrink_loop_max on a non-loop"))
        if x != "shrink_loop_max" and k != "agent":
            errs.append(GraphError(nm, f"{x} on a non-agent"))
    for kind, key in (("set_model", "models"), ("set_effort", "efforts"), ("narrow_tools", "tools")):
        if kind in a["kinds"] and not a.get(key):
            errs.append(GraphError(nm, f"{kind} needs a non-empty {key} list in adapt"))
    if a.get("tools") is not None and not set(a["tools"]) <= set(n.get("tools", [])):
        errs.append(GraphError(nm, "adapt tools widen the node's tools"))
    if a.get("models") and n.get("model") not in a["models"]:
        errs.append(GraphError(nm, "adapt models exclude the node's own model"))
    if a.get("efforts") and n.get("effort") and n["effort"] not in a["efforts"]:
        errs.append(GraphError(nm, "adapt efforts exclude the node's own effort"))
    for s in a.get("sees", []):
        if s in SEES_FORBIDDEN:
            errs.append(GraphError(nm, f"sees lists {s}: a proposer may not see it (ADR-0104 rule 3)"))
        elif s not in known and not s.startswith("outcome:"):
            errs.append(GraphError(nm, f"sees unregistered fact {s} (ADR-0104 rule 2)"))
    if n.get("may_propose") is None and a and k == "agent":
        pass
    return errs


def check_node_names(doc, names):
    """An engine option may name only nodes of the graph (ADR-0106 rule 2). Return the errors."""
    have = {n["name"] for n in doc["nodes"]}
    return [GraphError(None, f"unknown node name {x!r}") for x in names if x not in have]


@dataclass
class Graph:
    doc: dict
    hash: str
    nodes: dict
    layout: dict | None = None

    @property
    def root(self):
        return self.doc["root"]

    @property
    def run(self):
        return self.doc["run"]


def from_doc(doc, layout=None):
    errs = validate(doc)
    if errs:
        raise InvalidGraph(errs)
    return Graph(doc, canon_hash(doc), {n["name"]: n for n in doc["nodes"]}, layout)


def layout_path(path):
    p = Path(path)
    return p.with_name(p.name.removesuffix(".graph.json").removesuffix(".json") + ".layout.json")


def load(path):
    """Load a graph file. Raise InvalidGraph with every error. The sibling layout file never changes the hash."""
    p = Path(path)
    try:
        doc = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise InvalidGraph([GraphError(None, f"cannot read graph: {e}")]) from e
    lp = layout_path(p)
    return from_doc(doc, json.loads(lp.read_text()) if lp.exists() else None)


def plan_order(doc):
    """Node names in walk order: a parent, then its children, then a dispatch's entry and checks."""
    nodes = {n["name"]: n for n in doc["nodes"]}
    out = []

    def walk(name):
        n = nodes[name]
        out.append(name)
        for c in n.get("children", []):
            walk(c)
        if n["kind"] == "dispatch":
            for c in [n["entry"], *n["checks"]]:
                walk(c)

    walk(doc["root"])
    return out
