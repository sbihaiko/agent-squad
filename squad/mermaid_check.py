"""A parse check for the Mermaid view of a graph. It reads text and renders nothing, so it needs no browser.

It covers structure: the header, balanced subgraphs, closed quotes, one declaration per node, edge and class
targets that exist, and every graph node drawn. It does not cover layout or Mermaid's own lexer.
"""
import re

DECL = re.compile(r'^(?P<id>\w+)(?:\["[^"\n]*"\]|\{\{"[^"\n]*"\}\}|\(\["[^"\n]*"\]\)|\(\([^()\n]*\)\))$')
SUB = re.compile(r'^subgraph (?P<id>\w+)\["[^"\n]*"\]$')
EDGE = re.compile(r'^(?P<a>\w+) (?:-->|-\. [^\n]*? \.->) (?P<b>\w+)$')
CLASS = re.compile(r'^class (?P<id>\w+) (?P<cls>\w+)$')


def check(text, graph_nodes=None):
    """Return a list of problems. An empty list means the file parses. graph_nodes: names that must be drawn."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    errs = []
    if not lines or lines[0] != "flowchart TD":
        return ["the first line must be `flowchart TD`"]
    decl, edges, classes, depth = set(), [], [], 0
    for n, ln in enumerate(lines[1:], 2):
        if ln.startswith("classDef ") or ln == "direction TB":
            continue
        if m := SUB.match(ln):
            depth += 1
            ids = m["id"]
        elif ln == "end":
            depth -= 1
            if depth < 0:
                errs.append(f"line {n}: `end` with no subgraph")
            continue
        elif m := EDGE.match(ln):
            edges.append((n, m["a"], m["b"]))
            continue
        elif m := CLASS.match(ln):
            classes.append((n, m["id"]))
            continue
        elif m := DECL.match(ln):
            ids = m["id"]
        else:
            errs.append(f"line {n}: cannot parse {ln!r}")
            continue
        if ids in decl:
            errs.append(f"line {n}: {ids} is declared twice")
        decl.add(ids)
    if depth != 0:
        errs.append("subgraph and end do not balance")
    errs += [f"line {n}: edge names undeclared {x}" for n, a, b in edges for x in (a, b) if x not in decl]
    errs += [f"line {n}: class names undeclared {x}" for n, x in classes if x not in decl]
    errs += [f"graph node {x} is not drawn" for x in (graph_nodes or []) if x not in decl]
    return errs
