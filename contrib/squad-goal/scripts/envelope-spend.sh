#!/bin/bash
# usage: envelope-spend.sh [workflow]   Prints the phrase a dispatch request must carry as the spend of one child:
#   spend 4 (the dispatch node's budget: a request over it is rejected whole, spend_outside_budget)
# or, when that budget cannot be read, a phrase that names no number at all.
#
# The number is the budget.spend.max of the first dispatch node of the graph the run will start -- what ONE child of that
# run may ask for, and not a penny more. The pass that builds the request writes a number of its own and the envelope
# refuses the whole request when the number is over: a hardcoded 8 against a node whose limit is 4 made the router read
# `spend_outside_budget` in its notes and improvise (#57), and a fix run that cannot spawn at all ends no_dispatch.
# A number the run never set is worse than no number, so an unreadable budget is said, never guessed.
#
# The graph is resolved by id the way squad/workflows.py resolves it: the project's docs/squad/workflows/<id>.graph.json,
# else the owner's default, else the seed in docs/design/. A hand-built path read the seed directory and found nothing.
set -u
unreadable="a spend the run's own dispatch budget allows (this pass could not read that budget: a request over it is rejected whole, spend_outside_budget)"
n=$( cd "${SG_CHECKOUT:-.}" && python3 - "${1:-${SG_WORKFLOW:-dynamic}}" <<'PY' 2>/dev/null
import json, os, sys
sys.path.insert(0, os.environ.get("SG_SQUAD") or os.environ.get("SG_CHECKOUT") or ".")
def budget_spend(o):   # the budget.spend.max of the first dispatch node
    if isinstance(o, dict):
        if o.get("kind") == "dispatch" and isinstance(o.get("budget"), dict):
            s = o["budget"].get("spend")
            if isinstance(s, dict) and isinstance(s.get("max"), (int, float)): return s["max"]
        o = list(o.values())
    if isinstance(o, list):
        for v in o:
            r = budget_spend(v)
            if r is not None: return r
    return None
try:
    from squad import workflows as W
    g = W.resolve(sys.argv[1] or None)
    v = budget_spend(json.loads(g.read_text())) if g.is_file() else None
except Exception:
    v = None
if v is not None:
    print(int(v) if float(v).is_integer() else v)
PY
)
if [ -n "${n:-}" ]; then
  echo "spend $n (the dispatch node's budget: a request over it is rejected whole, spend_outside_budget)"
else
  echo "$unreadable"
fi
