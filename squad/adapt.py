"""Effective node settings: the graph document plus the proposals applied in this run (ADR-0103).

The overlay lives in the run only. The graph document and its hash never change (ADR-0114 rule 1).
"""


class Effective:
    def __init__(self, nodes):
        self.nodes = nodes
        self.over = {}

    def set(self, name, key, value):
        self.over.setdefault(name, {})[key] = value

    def get(self, name, key, default=None):
        return self.over.get(name, {}).get(key, default)

    def agent(self, node):
        n, o = node, self.over.get(node["name"], {})
        return {"model": o.get("model", n["model"]), "effort": o.get("effort", n.get("effort")),
                "tools": list(o.get("tools", n["tools"])), "turns_max": o.get("turns_max", n["budget"]["turns"]["max"]),
                "spend_max": o.get("spend_max", n["budget"]["spend"]["max"])}

    def loop_max(self, node):
        return self.over.get(node["name"], {}).get("loop_max", node["max"]["value"])


KINDS = ("set_model", "set_effort", "narrow_tools", "shrink_turns", "shrink_spend", "shrink_loop_max")
# The overlay key each kind changes, and the node kind it targets.
KEY = {"set_model": "model", "set_effort": "effort", "narrow_tools": "tools", "shrink_turns": "turns_max",
       "shrink_spend": "spend_max", "shrink_loop_max": "loop_max"}
TARGET_KIND = {k: "agent" for k in KINDS} | {"shrink_loop_max": "loop"}


class Rejected(Exception):
    """A proposal that code refuses whole. The reason goes in the `rejected` event."""


def _current(eff, node, kind):
    if kind == "shrink_loop_max":
        return eff.loop_max(node)
    a = eff.agent(node)
    return a[KEY[kind]]


def validate(p, proposer, nodes, eff, facts, item):
    """Check one proposal against its proposer and its target. Return (old, range, cited). Raise Rejected.

    Nothing is clamped and nothing is applied in part (ADR-0103 rule 8).
    """
    if not isinstance(p, dict):
        raise Rejected("proposal_malformed")
    kind, target, value = p.get("kind"), p.get("target"), p.get("value")
    if kind not in KINDS:
        raise Rejected("unknown_kind")
    if not proposer.get("may_propose"):
        raise Rejected("proposer_not_declared")
    node = nodes.get(target)
    if node is None:
        raise Rejected("unknown_target")
    if node["kind"] == "gate":
        raise Rejected("gate_target")
    if node["kind"] != TARGET_KIND[kind]:
        raise Rejected("kind_does_not_fit_target")
    adapt = node.get("adapt")
    if not adapt or kind not in adapt["kinds"]:
        raise Rejected("kind_not_allowed_on_target")
    names = p.get("facts", [])
    if not isinstance(names, list):
        raise Rejected("proposal_malformed")
    sees = (proposer.get("adapt") or {}).get("sees", [])
    cited = []
    for n in names:
        if n not in sees:
            raise Rejected(f"fact_not_seen:{n}")
        f = facts.get(n, item)
        if f is None:
            raise Rejected(f"fact_unavailable:{n}")
        cited.append(f.as_dict())
    old = _current(eff, node, kind)
    if kind in ("set_model", "set_effort"):
        allowed = adapt["models" if kind == "set_model" else "efforts"]
        if value not in allowed:
            raise Rejected(f"{'model' if kind == 'set_model' else 'effort'}_outside_range")
        return old, {"allowed": allowed}, cited
    if kind == "narrow_tools":
        allowed = adapt["tools"]
        if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
            raise Rejected("proposal_malformed")
        if not set(value) <= set(allowed) or not set(value) <= set(old):
            raise Rejected("tool_outside_range")
        if set(value) == set(old):
            raise Rejected("tools_not_narrowed")
        return old, {"allowed": allowed}, cited
    lo = node["max"]["min"] if kind == "shrink_loop_max" else node["budget"]["turns" if kind == "shrink_turns" else "spend"]["min"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind != "shrink_spend" and not isinstance(value, int)):
        raise Rejected("proposal_malformed")
    if value < lo or value >= old:
        raise Rejected(f"{KEY[kind]}_outside_range")
    return old, {"min": lo, "max": old}, cited


def replay(events, eff, before=None):
    """Put the applied proposals of a record on an overlay, in record order (ADR-0107 rule 1). Return their count."""
    n = 0
    for e in events:
        if e["event"] == "applied" and (before is None or e["pos"] < before):
            eff.set(e["target"], KEY[e["kind"]], e["requested"])
            n += 1
    return n
