"""Deadlock check over the native dependency graph (#1983): one pure validator and one read-only audit.

A key names an issue by its repository and number, "owner/repo#12". An edge (blocked, blocker) makes `blocked` wait on
`blocker`. A parent waits on each child it has not delivered, so a child that waits on its parent closes a cycle.
Nothing here calls GitHub: every answer comes from the graph the caller already read.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RANK = {"invalid": 0, "unknown": 1, "held": 2, "valid": 3}


def key(repo, number):
    return f"{repo}#{number}"


def node(kind="task", open=True, delivered=False, parent=None, blockers=(), complete=True, read=True, hold=None,
         outside_loop=False):
    return {"kind": kind, "open": open, "delivered": delivered, "parent": parent, "blockers": list(blockers),
            "complete": complete, "read": read, "hold": hold, "outside_loop": outside_loop}


def _explicit(graph, proposed=()):
    return [(n, b) for n, nd in graph.items() for b in nd["blockers"]] + [tuple(p) for p in proposed]


def _done(nd):
    return nd is not None and not nd["open"] and nd["delivered"]


def _implicit(graph):
    return [(nd["parent"], c) for c, nd in graph.items() if nd["parent"] and not _done(nd)]


def _component(edges, seeds):
    adj = {}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    comp, todo = set(), list(seeds)
    while todo:
        k = todo.pop()
        if k in comp:
            continue
        comp.add(k)
        todo.extend(adj.get(k, ()))
    return comp


def _cycle(waits):
    done = set()

    def visit(n, stack):
        if n in stack:
            return stack[stack.index(n):] + [n]
        if n in done:
            return None
        stack.append(n)
        for m in sorted(waits.get(n, ())):
            found = visit(m, stack)
            if found:
                return found
        stack.pop()
        done.add(n)
        return None

    for n in sorted(waits):
        found = visit(n, [])
        if found:
            return found
    return None


def _finding(status, cause, reason, witness=(), actor=None):
    return {"status": status, "cause": cause, "reason": reason, "witness": list(witness), "actor": actor}


def _findings(graph, explicit, comp):
    out = []
    for k in sorted(comp):
        nd = graph.get(k)
        if nd is None:
            out.append(_finding("unknown", f"unknown:{k}", f"{k} is not in the read: its read failed or it was never read"))
        elif not nd["read"]:
            out.append(_finding("unknown", f"unknown:{k}", f"the read of {k} failed"))
        elif not nd["complete"]:
            out.append(_finding("unknown", f"unknown:{k}", f"a page of the blockers of {k} is missing"))
    for a, b in explicit:
        if a == b:
            out.append(_finding("invalid", f"invalid:{a} > {a}", f"{a} is blocked by itself", [a, a]))
    waits = {}
    for a, b in explicit + _implicit(graph):
        if a in comp and b in comp and a != b and not _done(graph.get(b)):
            waits.setdefault(a, set()).add(b)
    cycle = _cycle(waits)
    if cycle:
        path = " > ".join(cycle)
        out.append(_finding("invalid", f"invalid:{path}", f"the blocking edges form a cycle: {path}", cycle))
    for a, b in explicit:
        na, nb = graph.get(a), graph.get(b)
        if na and nb and na["kind"] == "loop-bug" and na["open"] and nb["kind"] != "loop-bug" and nb["open"] and not nb["outside_loop"]:
            out.append(_finding("invalid", f"invalid:{a} > {b}", f"loop-bug {a} waits on {b}, work only dispatch can run", [a, b]))
    for a, b in explicit:
        nb = graph.get(b)
        if nb is None:
            continue
        if not nb["open"]:
            if not nb["delivered"]:
                out.append(_finding("held", f"held:owner:{b}", f"{a} waits on {b}, closed without delivery", actor="owner"))
        elif nb["hold"]:
            out.append(_finding("held", f"held:{nb['hold']}:{b}", f"{a} waits on {b}, held by {nb['hold']}", actor=nb["hold"]))
    return out


def _verdict(findings):
    if not findings:
        return {"status": "valid", "witness": [], "actor": None, "reasons": []}
    first = min(findings, key=lambda f: RANK[f["status"]])
    return {"status": first["status"], "witness": first["witness"], "actor": first["actor"],
            "reasons": [f["reason"] for f in findings]}


def validate(graph, proposed=()):
    """The verdict for the component the proposed edges touch: invalid beats unknown, unknown beats held, held beats valid."""
    proposed = [tuple(p) for p in proposed]
    explicit = _explicit(graph, proposed)
    seeds = {k for p in proposed for k in p}
    comp = _component(explicit + _implicit(graph), seeds)
    return _verdict(_findings(graph, explicit, comp))


def graph_of(issues):
    """The graph of the reads a pass holds: one node per listed issue, one per edge target it cannot list."""
    graph = {}
    for i in issues:
        labels = {label.get("name") for label in i.get("labels") or [] if isinstance(label, dict)}
        kind = "loop-bug" if any(str(name).startswith("loop-bug") for name in labels) else "bug" if "bug" in labels else "spec" if "spec" in labels else "task"
        k = key(i["repo"], i["number"])
        graph[k] = node(
            kind=kind, open=i.get("state") == "open", delivered=i.get("state") != "open",
            parent=key(i["repo"], i["parent"]) if i.get("parent") else None,
            blockers=[key(e["repo"], e["number"]) for e in i.get("blocked_by_edges") or []],
            complete=i.get("blocked_by_complete") is True, hold="owner" if "frozen:owner" in labels else None,
        )
    for i in issues:
        for e in i.get("blocked_by_edges") or []:
            k = key(e["repo"], e["number"])
            if k not in graph:
                closed = e.get("state") != "open"
                graph[k] = node(open=not closed, delivered=closed, read=closed)
    return graph


def audit(issues):
    """Every cause that makes an edge invalid or unknown, one entry per cause; a pass runs it on the reads it holds."""
    graph = graph_of(issues)
    explicit = _explicit(graph)
    seen, out = set(), []
    for k in sorted(graph):
        comp = _component(explicit + _implicit(graph), {k})
        for f in _findings(graph, explicit, comp):
            if f["status"] in ("invalid", "unknown") and f["cause"] not in seen:
                seen.add(f["cause"])
                out.append(f)
    return out


def verdict_of(issues, repo, number):
    """The verdict for the component that holds one issue, read from the pass's own issue list."""
    graph = graph_of(issues)
    explicit = _explicit(graph)
    comp = _component(explicit + _implicit(graph), {key(repo, number)})
    return _verdict(_findings(graph, [e for e in explicit if e[0] in comp], comp))


def new_causes(causes, seen):
    return [c for c in causes if c["cause"] not in seen]


def main(argv):
    command, path, *args = argv
    issues = json.loads(Path(path).read_text())
    if command == "verdict":
        print(json.dumps(verdict_of(issues, *args)))
    elif command == "audit":
        for cause in audit(issues):
            print(f"ALERT {cause['reason']}")


if __name__ == "__main__":
    main(sys.argv[1:])
