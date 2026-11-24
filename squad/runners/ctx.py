"""The context a runner sees: the run's record, budget, facts, artifacts, workspace paths, and two calls back into the engine."""

from squad import node_commit


class RunCtx:
    def __init__(self, engine):
        self._e = engine    # private: only the runners that still need the engine reach it

    record = property(lambda s: s._e.record)
    budget = property(lambda s: s._e.budget)
    facts = property(lambda s: s._e.facts)
    artifacts = property(lambda s: s._e.artifacts)
    target = property(lambda s: s._e.target)
    fix_run = property(lambda s: s._e.fix_run)          # the run option `run_start` recorded (#1374)
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

    @property
    def spent_models(self):
        return self._e.options.get("spent_models") or []

    @property
    def model_hours(self):
        return self._e.options.get("model_hours") or {}

    @property
    def model_order(self):
        return self._e.options.get("model_order") or []

    @property
    def model_ctx(self):
        """The context window each chain entry declares (`{spelling: "128k"}`): a prompt larger than it skips the entry (#1470)."""
        return self._e.options.get("model_ctx") or {}

    @property
    def model_registry(self):
        """The registry this run resolves model spellings through: a call states its chain in these ids (SPEC 1035)."""
        return self._e._registry()

    def now(self):
        """The run's wall moment, a timestamp: an hours window reads its weekday and its UTC hour from it."""
        return self._e.wall()

    def is_dispatch_entry(self, name):
        return name in self._e.entry_of

    def dispatch_node(self, node):
        """A dispatch node with this run's router overlay applied. The authored graph, and its hash, stay unchanged."""
        models = self._e.router_overlay.get(node.get("name")) if node.get("kind") == "dispatch" else None
        if node.get("kind") == "dispatch" and "kinds" in (node.get("allow") or {}):
            # SPEC 750: the envelope holds kinds; each kind's models are the chain of that kind, read from run_start.
            # The overlay still narrows it: a model it does not name is closed for every kind (SPEC 505).
            chains = self._e.kind_chains
            km = {k: [m for m in (chains.get(k) or []) if models is None or m in models] for k in node["allow"]["kinds"]}
            union = list(dict.fromkeys(m for v in km.values() for m in v))
            return {**node, "allow": {**node["allow"], "models": union, "kind_models": km}}
        return node if models is None else {**node, "allow": {**(node.get("allow") or {}), "models": list(models)}}

    def router_chain(self):
        """The router agent's models, in chain order (SPEC 750): empty for a graph whose envelope still lists models."""
        return list(self._e.kind_chains.get("router") or [])

    def router_effort(self):
        """The effort the router chain names for the entry it answers on (SPEC 750), or None: the panel draws one
        effort per row, so the router's effort is the chain's, never the node's."""
        chain = self._e.kind_chains.get("router") or []
        return (self._e.kind_efforts.get("router") or {}).get(chain[0]) if chain else None

    def limits(self, name):
        """The limits a router may ask within: the dispatch's allow lists and budget, as the envelope checks them."""
        d = self.dispatch_node(self._e.nodes[self._e.entry_of[name]])
        # an envelope of kinds names the kinds a request may ask for, never a model id (SPEC 750)
        pick = ("kinds" if "kind_models" in d["allow"] else "models", "efforts", "tools", "workspaces")
        return {"skills": [s["name"] for s in d["allow"]["skills"]], **{k: d["allow"][k] for k in pick},
                "stalls": d["budget"]["stalls"], "spend": d["budget"]["spend"], "deadline": d["deadline"],
                "max_children": d["max_children"], "max_parallel": d["max_parallel"], "max_depth": d["max_depth"]}

    def ledger(self, name):
        return self._e.dispatch.ledger(self._e.entry_of[name])

    def workspace_path(self, logical, item):
        return self._e.workspace_path(logical, item)

    def ws_snapshot(self, node, sc):
        return self._e._ws_snapshot(node, sc)

    def commit_workspace(self, node, entry, sc, outcome, *, path=None):
        """The engine commits a passing node's dirty declared workspace before its snapshot (#84). Returns the record event or None."""
        logical = node.get("workspace")
        return node_commit.commit_after(self._e, node=node["name"], entry=entry, item=sc.item, logical=logical,
                                        path=path or self._e.workspace_path(logical, sc.item), outcome=outcome)

    def recorded_head(self, logical, item):
        return self._e._recorded_head(logical, item)

    def call_deadline(self, node, sc):
        """(seconds, reason): the call's limit, and `wave_ceiling` when the wave ceiling set it (#2840)."""
        return self._e._call_deadline(node, sc)

    def wave_bound(self, own):
        return self._e.wave_bound(own)

    def no_progress_window(self):
        return self._e.no_progress_window()

    def finish(self, node, entry, sc, outcome, reasons, artifact=None, **kw):
        return self._e._finish(node, entry, sc, outcome, reasons, artifact, **kw)

    async def exec_node(self, name, sc):
        return await self._e.exec_node(name, sc)
