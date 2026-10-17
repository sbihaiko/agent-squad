#!/usr/bin/env python3
"""usage: fundroom.py [workflow]   Prints how many children ONE run of the workflow can fund, or "-" when the graph does not say.

The engine accepts a child only when the run budget can hold the child's spend (`spend_over_remainder`, #480). A request
that asks for more children than the run can fund leaves the rest unworked, their cards in Doing and their dispatch markers
set. So a dispatcher asks for no more than this: the run's spend cap, less the spend every agent node of the graph may
hold, divided by the spend one child reserves (the first dispatch node's budget.spend.max).

The graph is resolved by id the way squad/workflows.py resolves it, as envelope-spend.sh does. Run from SG_CHECKOUT.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.environ.get("SG_SQUAD") or os.environ.get("SG_CHECKOUT") or ".")


def nodes(o):
    if isinstance(o, dict):
        if isinstance(o.get("kind"), str):
            yield o
        for v in o.values():
            yield from nodes(v)
    elif isinstance(o, list):
        for v in o:
            yield from nodes(v)


def spend_max(n):
    b = n.get("budget")
    s = b.get("spend") if isinstance(b, dict) else None
    m = s.get("max") if isinstance(s, dict) else None
    return m if isinstance(m, (int, float)) else None


def room(doc):
    run = doc.get("run") if isinstance(doc, dict) else None
    cap = run.get("spend_cap") if isinstance(run, dict) else None
    ns = list(nodes(doc))
    child = next((spend_max(n) for n in ns if n.get("kind") == "dispatch" and spend_max(n)), None)
    if not isinstance(cap, (int, float)) or not child:
        return None
    held = sum(spend_max(n) or 0 for n in ns if n.get("kind") == "agent")
    return max(math.floor((cap - held) / child), 0)


if __name__ == "__main__":
    try:
        from squad import workflows as W
        g = W.resolve((sys.argv[1] if len(sys.argv) > 1 else "") or os.environ.get("SG_WORKFLOW") or None)
        r = room(json.loads(g.read_text())) if g.is_file() else None
    except Exception:
        r = None
    print("-" if r is None else r)
