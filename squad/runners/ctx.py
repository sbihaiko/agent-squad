"""The context a runner sees: the run's record, budget, facts, artifacts, workspace paths, and two calls back into the engine."""


class RunCtx:
    def __init__(self, engine):
        self._e = engine    # private: only the runners that still need the engine reach it

    record = property(lambda s: s._e.record)
    budget = property(lambda s: s._e.budget)
    facts = property(lambda s: s._e.facts)
    artifacts = property(lambda s: s._e.artifacts)
    target = property(lambda s: s._e.target)
    run_id = property(lambda s: s._e.run_id)
    clock = property(lambda s: s._e.clock)
    wall = property(lambda s: s._e.wall)
    waits = property(lambda s: s._e.waits)
    answer = property(lambda s: s._e.answer)           # set by resume, so read through
    default_applied = property(lambda s: s._e.default_applied)
    branches = property(lambda s: s._e.branches)
    adapter = property(lambda s: s._e.adapter)
    shadow = property(lambda s: s._e.shadow)
    nodes = property(lambda s: s._e.nodes)
    eff = property(lambda s: s._e.eff)
    run_cfg = property(lambda s: s._e.run_cfg)
    scale = property(lambda s: s._e.scale)
    project_limits = property(lambda s: s._e.limits)   # the project settings file's call limits

    dispatch = property(lambda s: s._e.dispatch)
    runs = property(lambda s: s._e.runs)
    dry = property(lambda s: s._e.dry)

    def entry_count(self, name, item):
        """How many times the node has been entered for the item (0 if never)."""
        return self._e.entries.get((name, item), 0)

    def outcome_event(self, name, entry, item):
        """The recorded outcome event of one node entry, or None."""
        return self._e.outcomes.event(name, entry, item)

    def route(self, node, outcome):
        return self._e._route(node, outcome)

    def is_dispatch_entry(self, name):
        return name in self._e.entry_of

    def limits(self, name):
        """The limits a router may ask within: the dispatch's allow lists and budget, as the envelope checks them."""
        d = self._e.nodes[self._e.entry_of[name]]
        return {"skills": [s["name"] for s in d["allow"]["skills"]], **{k: d["allow"][k] for k in ("models", "efforts", "tools", "workspaces")},
                "stalls": d["budget"]["stalls"], "spend": d["budget"]["spend"], "deadline": d["deadline"],
                "max_children": d["max_children"], "max_parallel": d["max_parallel"], "max_depth": d["max_depth"]}

    def ledger(self, name):
        return self._e.dispatch.ledger(self._e.entry_of[name])

    def workspace_path(self, logical, item):
        return self._e.workspace_path(logical, item)

    def ws_snapshot(self, node, sc):
        return self._e._ws_snapshot(node, sc)

    def recorded_head(self, logical, item):
        return self._e._recorded_head(logical, item)

    def call_deadline(self, node, sc):
        return self._e._call_deadline(node, sc)

    def finish(self, node, entry, sc, outcome, reasons, artifact=None, **kw):
        return self._e._finish(node, entry, sc, outcome, reasons, artifact, **kw)

    async def exec_node(self, name, sc):
        return await self._e.exec_node(name, sc)
