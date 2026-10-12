"""Outcomes: the one module that records a node outcome.

An outcome goes to the run record, to `done` (by name, entry, item) and to `last` (by name, item).
The engine and the dispatch children both go through here. Nothing else writes `done` or `last`.
A node and a child differ in two ways:

- A node also makes its `outcome:<name>` fact and refreshes the derived facts. It stops the run when `stop_after` names it.
- A child is no graph node, so the fact registry refuses its `outcome:` fact.
  Readers see a child outcome through `done` and `last`, never through a fact.

A resume rebuilds state from a recorded event with `absorb`. That call writes no event.

Guards: `tests/test_outcomes.py`, which also scans `squad/` for any other writer of `done` or `last`.
"""
from squad.engine_errors import Halt


class Outcomes:
    def __init__(self, record, facts, *, refresh, stop_after=None, source=None):
        """`refresh(item)` re-derives the facts. `source()` names the fact source; the default is the record position."""
        self.record, self.facts, self.refresh, self.stop_after = record, facts, refresh, stop_after
        self.source = source or (lambda: self._source(len(record.events)))
        self.done, self.last = {}, {}

    def _source(self, pos):
        return {"kind": "run_record", "run": self.facts.run_id, "pos": pos}

    def _keep(self, ev, item):
        self.done[(ev["node"], ev["entry"], item)] = ev
        self.last[(ev["node"], item)] = ev["outcome"]

    def recorded(self, name, entry=1, item=None):
        """True when the outcome of this name, entry and item is recorded. Replay asks here and never reads `done`."""
        return (name, entry, item) in self.done

    def event(self, name, entry=1, item=None):
        """The recorded outcome event of this name, entry and item, or None. It answers through `recorded`."""
        return self.done[(name, entry, item)] if self.recorded(name, entry, item) else None

    def node(self, name, entry, item, outcome, **fields):
        """Record a graph node's outcome, make its fact, refresh, and halt when `stop_after` names it."""
        if item is not None:
            fields["item"] = item
        ev = self.record.append("outcome", node=name, entry=entry, outcome=outcome, **fields)
        self._keep(ev, item)
        self.facts.produce(f"outcome:{name}", outcome, self.source(), item=item)
        self.refresh(item)
        if self.stop_after == name:
            raise Halt("stop", name)
        return ev

    def child(self, name, outcome, reasons, artifact, extra, *, parent, depth):
        """Record a dispatch child's outcome. A child has one entry and no item, and makes no fact."""
        fields = {"spend": None, "turns": None, "cap": None, "elapsed": None, **extra}
        ev = self.record.append("outcome", node=name, entry=1, outcome=outcome, reasons=list(reasons), artifact=artifact,
                                parent=parent, depth=depth, **fields)
        self._keep(ev, None)
        return ev

    def absorb(self, ev):
        """Rebuild state from a recorded outcome event on resume. It writes no event."""
        item = ev.get("item")
        self._keep(ev, item)
        if self.facts.registered(f"outcome:{ev['node']}"):
            self.facts.produce(f"outcome:{ev['node']}", ev["outcome"], self._source(ev["pos"]), item=item)
            self.refresh(item)

    def defect(self, root, defect, reasons=None):
        """The fallback when the walk raises a defect, or never starts (`reasons` name why): the root fails.
        Its entry is one past the last recorded entry of the root. A run and a resume use the same rule."""
        entry = max((e for (n, e, it) in self.done if n == root and it is None), default=0) + 1
        ev = self.record.append("outcome", node=root, entry=entry, outcome="fail", reasons=reasons or [f"defect:{defect}"], artifact=None,
                                spend=None, turns=None, cap=None, elapsed=None)
        self._keep(ev, None)
        return ev
