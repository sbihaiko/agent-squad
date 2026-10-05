"""The engine: one scheduler, one node runner per kind, one run record for every flow (ADR-0100 to ADR-0114).

The engine walks the graph and keeps the entry count, fast-forward, dry frontier, and `when` rules in
`exec_node`. Then it looks the node runner up by kind in `squad/runners` (`async run(ctx, node, entry, sc) -> Res`).
An agent node is one fresh session behind the adapter seam (`squad/session.py`). A gate is a deterministic
check. A loop repeats its ordered children under the reserved `next` words. Resume replay lives in
`squad/replay.py`. Node outcomes derive from artifacts and go to the run record; only the verdict module folds
the record into a verdict.
"""
import math
import time
import uuid
from pathlib import Path

import anyio

from squad import adapt, git, skills
from squad import options as opt
from squad import verdict as V
from squad.adapt import Effective
from squad.artifacts import ArtifactStore
from squad.budget import Budget
from squad.control import Control
from squad.engine_errors import Defect as Defect
from squad.engine_errors import Frontier as Frontier
from squad.engine_errors import Halt as Halt
from squad.engine_errors import ResumeRefused as ResumeRefused
from squad.facts import DeriveCtx, FactStore, refresh
from squad.graph import plan_order
from squad.record import Record
from squad.replay import Replay
from squad.runners import RunCtx, default_registry
from squad.runners.children import Children
from squad.runners.dispatch import Dispatch
from squad.runners.types import Res as Res
from squad.runners.types import Scope as Scope


class Engine:
    def __init__(self, graph, adapter, run_dir, *, options=None, target=None, time_scale=1.0, clock=time.monotonic,
                 wall=time.time, run_id=None, invoker="cli", graph_path=None, defaults=None, shadow=None, vendor=None):
        self.shadow = shadow     # advisory observer (ticket #55): its output is recorded as `diagnostic` and read by nothing
        self.graph_path = graph_path
        self.g, self.adapter, self.run_dir = graph, adapter, Path(run_dir)
        self.options, run_cfg = opt.resolve(graph, options)
        self.target = Path(target or ".").resolve()
        self.scale, self.clock, self.wall, self.invoker = time_scale, clock, wall, invoker
        self.run_id = run_id or f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self.run_cfg = run_cfg
        self.nodes = dict(graph.nodes)
        self.record = Record(self.run_dir / "record.jsonl")
        self.control = Control(self.run_dir)   # pause and resume requests from a person (ticket #107, ADR-0128)
        self.eff = Effective(self.nodes)
        self.budget = Budget(self.run_cfg["spend_cap"])
        self.facts = FactStore(graph.doc["facts"], self.nodes, self.run_id, clock=wall)
        self.artifacts = ArtifactStore(self.run_dir / "artifacts")
        self.done, self.entries, self.last = {}, {}, {}
        self.has_git = git.available(self.target)
        self.dry, self.forced, self.from_node = False, False, None
        self.waits, self.answer, self.default_applied = {}, None, []
        self.branches = {}
        self.replay = Replay(self)   # the replay half of resume (ticket #126)
        self.runs = Children(self)   # dispatch child execution (ticket #125)
        self.dispatch = Dispatch(self)   # spawn decisions and landing; owns children, held, landed, ac_before (ticket #124)
        self.entry_of = {n["entry"]: n["name"] for n in self.nodes.values() if n["kind"] == "dispatch"}
        self.wt_lock = anyio.Lock()
        self.vendor = Path(vendor) if vendor else skills.VENDOR   # where VERSIONS.json lives (ADR-0130)
        self.defaults = defaults       # {path, sha256, engine}: the per-invocation engine-level defaults, recorded in run_start and resume (ticket #46)
        if hasattr(adapter, "bind"):
            adapter.bind(lambda kind, **f: self.record.append(kind, **f))
        self.item_workspaces = self._item_workspaces()
        self.runners = default_registry()   # kind -> runner; a test may wrap one entry (ticket #121)
        self.ctx = RunCtx(self)

    def _item_workspaces(self):
        """Logical workspaces that a worktree_create makes inside an `each`: one per item (ADR-0102 rule 2)."""
        out = set()
        for n in self.nodes.values():
            if n["kind"] != "each":
                continue
            stack = list(n["children"])
            while stack:
                c = self.nodes[stack.pop()]
                if c["kind"] == "action" and c["effect"] == "worktree_create":
                    out.add(c.get("workspace"))
                stack += c.get("children", [])
        return out

    # ------------------------------------------------------------ record helpers
    def _src(self):
        return {"kind": "run_record", "run": self.run_id, "pos": len(self.record.events)}

    def _route(self, node, outcome):
        return (node.get("next") or {}).get(outcome) or V.DEFAULT_ROUTE[outcome]

    def _next_entry(self, name, item):
        n = self.entries.get((name, item), 0) + 1
        if self.forced:   # `--from` re-runs a node: its new entry follows every recorded one
            n = max(n, max((e for (nm, e, it) in self.done if nm == name and it == item), default=0) + 1)
        self.entries[(name, item)] = n
        return n

    def _finish(self, node, entry, sc, outcome, reasons, artifact=None, *, spend=None, turns=None, cap=None, elapsed=None, routed=None, **extra):
        name = node["name"]
        routed = routed or self._route(node, outcome)
        fields = dict(node=name, entry=entry, outcome=outcome, reasons=list(reasons), artifact=artifact, spend=spend,
                      turns=turns, cap=cap, elapsed=elapsed, routed=routed, **extra)
        if sc.item is not None:
            fields["item"] = sc.item
        ev = self.record.append("outcome", **fields)
        self.done[(name, entry, sc.item)] = ev
        self.last[(name, sc.item)] = outcome
        self.facts.produce(f"outcome:{name}", outcome, self._src(), item=sc.item)
        self._refresh(sc)
        if self.options.get("stop_after") == name:
            raise Halt("stop", name)
        return Res(outcome, list(reasons), routed)

    def _produce_builtin(self):
        src = self._src()
        self.facts.produce("run_spend", round(self.budget.run_spend, 6), src)
        self.facts.produce("run_spend_remaining", round(self.budget.run_spend_remaining, 6), src)

    def _refresh(self, sc):
        self._produce_builtin()
        c = DeriveCtx(self.artifacts.get, lambda n, i: (n, i) in self.last or (n, None) in self.last, sc.item, self.target,
                      self.options, self.has_git, self.facts, len(self.record.events))
        refresh(self.facts, c)

    # ------------------------------------------------------------ deadlines
    def _call_deadline(self, node, sc):
        own = node.get("deadline", self.run_cfg["deadline"])     # null run deadline: no run limit, the node's own deadline is the cap
        own = math.inf if own is None else own * self.scale
        return min(own, sc.deadline_at - self.clock())

    # ------------------------------------------------------------ the walk
    LEAVES = ("agent", "gate", "action", "wait")

    async def exec_node(self, name, sc):
        node = self.nodes[name]
        if name == self.from_node and not self.forced:
            self.forced = True
        entry = self._next_entry(name, sc.item)
        ev = None if self.forced else self.done.get((name, entry, sc.item))
        if ev is not None:
            self._fast_forward(name)
            return Res(ev["outcome"], list(ev["reasons"]), ev.get("routed"))
        if self.dry and node["kind"] in self.LEAVES:
            raise Frontier([(name, sc.item)])
        if not self.dry and self.control.held(name):
            raise self.hold([self._label(name, entry, sc.item)], name, sc.item)
        if "when" in node:
            ok, why = self.facts.evaluate(node["when"], sc.item)
            if not ok:
                if self.dry:
                    return Res("pass", ["skipped"])
                return self._finish(node, entry, sc, "pass", ["skipped"] + ([why] if why else []))
        return await self.runners[node["kind"]].run(self.ctx, node, entry, sc)

    def hold(self, labels, node, item=None):
        """Record where the run stands and return the `Halt` that ends the run here: a person paused these nodes (ADR-0128)."""
        completed = list(dict.fromkeys(x["node"] for x in self.record.events if x["event"] == "outcome"))
        extra = {} if item is None else {"item": item}
        self.record.append("paused", node=node, scope=self.control.scope(), held=labels, completed=completed, **extra)
        return Halt("pause", node)

    def _label(self, name, entry, item):
        return name if item is None else f"{name}#{item}"

    def subtree(self, name):
        """A node and everything under it, including the children a dispatch spawned."""
        out, stack = set(), [name]
        while stack:
            n = stack.pop()
            if n in out or n not in self.nodes:
                continue
            out.add(n)
            nd = self.nodes[n]
            stack += nd.get("children", [])
            if nd["kind"] == "dispatch":
                stack += [nd["entry"], *nd["checks"]]
        out |= {k for k in self.nodes if k.startswith(f"{name}.")}
        return out

    def _fast_forward(self, name):
        """A recorded container returns its recorded outcome: the entry counters of its descendants catch up with the record."""
        if self.nodes[name]["kind"] in self.LEAVES:
            return
        sub = self.subtree(name)
        for (n, e, it) in self.done:
            if n in sub and n != name:
                self.entries[(n, it)] = max(self.entries.get((n, it), 0), e)

    def workspace_path(self, logical, item):
        """A logical workspace name maps to a directory. `.` is the target. A worktree made per `each` item gets one per item."""
        if logical in (None, "."):
            return self.target
        tag = f"-{item}" if item is not None and logical in self.item_workspaces else ""
        return self.run_dir / "ws" / (logical.replace("/", "_") + tag)

    def _ws_snapshot(self, node, sc):
        """The workspace state after a node: what a later resume compares against (ADR-0107 rule 5)."""
        logical = node.get("workspace")
        if logical in (None, ".") or not self.has_git:
            return None
        p = self.workspace_path(logical, sc.item)
        return {"name": logical, "path": str(p), "head": git.head(p), "state": git.state(p)} if p.exists() else None

    def _recorded_head(self, logical, item):
        """The HEAD that the last outcome event of a worktree_create or commit in this workspace recorded."""
        for e in reversed(self.record.events):
            w = e.get("workspace") if e["event"] == "outcome" else None
            if w and w["name"] == logical and e.get("item") == item:
                return w["head"]
        return None

    # ------------------------------------------------------------ run
    def _preflight_start(self):
        """A graph with action nodes needs git. A missing git is a defect, never a swap (ADR-0106 rule 4)."""
        acts = [n for n in self.nodes.values() if n["kind"] == "action"]
        if not acts:
            return {"status": "ok"}
        if not self.has_git:
            return {"status": "failed", "reason": "git_missing"}
        touches_target = any(n["effect"] == "merge_land" or (n["effect"] == "commit" and n.get("workspace") in (None, ".")) for n in acts)
        dirty = git.tracked_changes(self.target) if touches_target else []
        if dirty:
            return {"status": "conflict", "reason": f"dirty_base:{len(dirty)}_paths"}
        return {"status": "ok"}

    def _start(self):
        if bad := skills.dirty(self.vendor):   # refuse before run_start, no record (ticket #140, ADR-0130)
            raise opt.OptionError(f"vendor/skills is not clean: {'; '.join(bad)}. Nothing ran (ADR-0130)")
        self.preflight = self._preflight_start()
        self.record.append("run_start", run_id=self.run_id, graph_name=self.g.doc["name"], graph_hash=self.g.hash,
                           engine_options=self.options, root=self.g.root, plan=plan_order(self.g.doc), preflight=self.preflight, target=str(self.target),
                           **({"graph_path": str(self.graph_path)} if self.graph_path else {}), **({"defaults": self.defaults} if self.defaults else {}),
                           skills=skills.snapshot(self.g.doc, self.vendor), skills_commit=skills.commit(self.vendor))

    def _seed_facts(self):
        if self.options.get("tier_pin"):
            self.facts.produce("tier_pin", self.options["tier_pin"], self._src())
        self._refresh(Scope(None, 0))

    async def _walk(self):
        sc = Scope(None, self.clock() + (math.inf if self.run_cfg["deadline"] is None else self.run_cfg["deadline"] * self.scale))
        try:
            await self.exec_node(self.g.root, sc)
        except Halt as h:
            return h
        return None

    # ------------------------------------------------------------ resume (ADR-0107)
    async def resume(self, *, answer=None, from_node=None, release=False):
        """Continue one run from its record under the same run id. Return the verdict. The replay is `squad/replay.py`.
        `release`: the person who runs the command releases every hold the control file still holds (ADR-0128)."""
        self.run_id, self.answer, self.from_node = self.replay.refuse(from_node), answer, from_node
        if release and self.control.requests():
            self.control.append("resume", source="cli")
        self.facts.run_id = self.run_id
        missing = self.replay.absorb_all()
        self.replay.mark_crashed_children()
        adapt.replay(self.record.events, self.eff, self.replay.cut(from_node))
        self._seed_facts()
        pre, frontier = {"status": "ok"}, []
        if missing:
            pre = {"status": "failed", "reason": f"artifact_missing:{missing[0]}"}
        else:
            frontier = await self._dry_walk()
            pre = self.replay.preflight_workspaces(frontier)
        self.record.append("resume", **self.replay.resume_fields(frontier, pre, answer, from_node))
        if pre["status"] != "ok":
            return self._conclude()
        try:
            await self._walk()
        except Defect as d:
            self.record.append("outcome", node=self.g.root, entry=99, outcome="fail", reasons=[f"defect:{d}"], artifact=None,
                               spend=None, turns=None, cap=None, elapsed=None)
        return self._conclude()

    async def _dry_walk(self):
        """Walk the graph without calls up to the first node that has not run. Return that frontier."""
        frontier, self.dry = [], True
        try:
            await self._walk()
        except Frontier as f:
            frontier = f.positions
        self.dry, self.forced, self.entries = False, False, {}
        return frontier

    def observe_target(self):
        """The target observation that the verdict module folds: what the run will not overwrite (ADR-0105 rule 4)."""
        conflict = []
        if self.has_git:
            conflict += [f"unmerged:{p}" for p in git.unmerged(self.target)]
        return {"conflict": conflict}

    def _conclude(self):
        v = V.fold(self.record.events, self.observe_target(), self.run_id)
        self.record.append("verdict", **v.event_fields())
        return v

    async def run(self):
        self._start()
        if self.preflight["status"] != "ok":
            return self._conclude()
        self._seed_facts()
        try:
            await self._walk()
        except Defect as d:
            self.record.append("outcome", node=self.g.root, entry=1, outcome="fail", reasons=[f"defect:{d}"], artifact=None,
                               spend=None, turns=None, cap=None, elapsed=None)
        return self._conclude()
