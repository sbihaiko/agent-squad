"""The engine: one scheduler, one node runner per kind, one run record for every flow.

The engine walks the graph and keeps the entry count, fast-forward, dry frontier, and `when` rules in
`exec_node`. Then it looks the node runner up by kind in `squad/runners` (`async run(ctx, node, entry, sc) -> Res`).
An agent node is one fresh session behind the adapter seam (`squad/session.py`). A gate is a deterministic
check. A loop repeats its ordered children under the reserved `next` words. Resume replay lives in
`squad/replay.py`. Node outcomes derive from artifacts and go to the run record; only the verdict module folds
the record into a verdict.
"""
import functools
import math
import subprocess
import time
from pathlib import Path

import anyio

from squad import adapt, git, modelreg, node_commit, session, skills
from squad import killed as K
from squad import options as opt
from squad import project_settings as P
from squad import verdict as V
from squad.adapt import Effective
from squad.artifacts import ArtifactStore
from squad.budget import Budget
from squad.caps import CeilingWaitExpired
from squad.control import Control
from squad.engine_errors import Defect as Defect
from squad.engine_errors import Frontier as Frontier
from squad.engine_errors import Halt as Halt
from squad.engine_errors import ResumeRefused as ResumeRefused
from squad.facts import DeriveCtx, FactStore, refresh
from squad.graph import plan_order
from squad.outcomes import Outcomes
from squad.record import Record, new_run_id
from squad.replay import Replay
from squad.runners import RunCtx, default_registry
from squad.runners.children import Children
from squad.runners.dispatch import Dispatch
from squad.runners.types import Res as Res
from squad.runners.types import Scope as Scope

# The engine option that orders a chain. A resume takes it from `run_start`, never from the invocation that resumes, so a
# launcher that reads a different coding order since then cannot rebuild a different chain (SPEC 1035, ADR-0107). The
# spent ids and the hours windows stay the resuming invocation's: they are facts of the moment, and the record states the
# chain each call had.
CHAIN_ORDER = "model_order"
# The engine option that names the wave ceiling file (#2840), and the time a call leaves before that ceiling so its work
# can still enter the wave: the entry's gates and push run after the call ends.
WAVE_CEILING = "wave_ceiling"
WAVE_ENTRY_RESERVE_SECONDS = 300


def _signals_recorded(fn):
    @functools.wraps(fn)
    async def wrapped(self, *args, **kwargs):
        with K.recording_signals(self.record.path):
            return await fn(self, *args, **kwargs)
    return wrapped


class Engine:
    def __init__(self, graph, adapter, run_dir, *, options=None, target=None, time_scale=1.0, clock=time.monotonic,
                 wall=time.time, run_id=None, invoker="cli", graph_path=None, defaults=None, shadow=None, vendor=None, project=None, autonomy=None, ceiling=None, state_binding="board", registry=None, router_overlay=None, kind_chains=None, kind_efforts=None):
        self.shadow = shadow     # advisory observer: its output is recorded as `diagnostic` and read by nothing
        self.graph_path = graph_path
        self.g, self.adapter, self.run_dir = graph, adapter, Path(run_dir)
        self.options, run_cfg = opt.resolve(graph, options)
        self.target = Path(target or ".").resolve()
        self.scale, self.clock, self.wall, self.invoker = time_scale, clock, wall, invoker
        self.run_id = run_id or new_run_id()
        self.run_cfg = run_cfg
        self.nodes = dict(graph.nodes)
        self.record = Record(self.run_dir / "record.jsonl")
        self.control = Control(self.run_dir)   # pause and resume requests from a person
        self.autonomy = autonomy       # {enabled, max_resumes}: recorded in run_start; a resume refuses when it differs
        self.project = project or {}    # {record: {path, sha256}, limits: {...}}: the project settings file
        self.limits = self.project.get("limits", {})
        self.eff = Effective(self.nodes, self.limits)
        self.budget = Budget(self.run_cfg["spend_cap"], graph.doc["run"].get("spend_cap_tokens"))
        self.facts = FactStore(graph.doc["facts"], self.nodes, self.run_id, clock=wall)
        self.artifacts = ArtifactStore(self.run_dir / "artifacts")
        self.entries = {}
        self.outcomes = Outcomes(self.record, self.facts, refresh=lambda item: self._refresh(Scope(item, 0)),
                                 stop_after=self.options.get("stop_after"), source=self._src)   # the one writer of done and last
        self.done, self.last = self.outcomes.done, self.outcomes.last   # read-only views for the engine and the runners
        self.has_git = git.available(self.target)
        self.dry, self.forced, self.from_node = False, False, None
        self.waits, self.answer, self.default_applied = {}, None, []
        self._ceiling_missing = False   # one event says the wave ceiling file the run names is gone (#2840)
        self.branches = {}
        self.replay = Replay(self)   # the replay half of resume
        self.runs = Children(self)   # dispatch child execution
        self.dispatch = Dispatch(self)   # spawn decisions and landing; owns children, held, landed, ac_before
        self.entry_of = {n["entry"]: n["name"] for n in self.nodes.values() if n["kind"] == "dispatch"}
        self.wt_lock = anyio.Lock()
        self.vendor = Path(vendor) if vendor else skills.VENDOR   # where VERSIONS.json lives
        self.ceiling = ceiling         # squad.caps.Ceiling: the machine-wide run and child slots; None admits without one
        self.defaults = defaults       # {path, sha256, engine}: the per-invocation engine-level defaults, recorded in run_start and resume
        self.state_binding = state_binding  # the issue-state carrier, fixed for this run and checked on resume
        self.registry = registry       # the model registry path; None is the committed registry (SPEC 505)
        self.router_overlay = dict(router_overlay or {})   # {dispatch node: models kept}: recorded at run_start, applied at dispatch
        self.kind_chains = {k: list(v) for k, v in (kind_chains or {}).items()}   # {kind: models in chain order} (SPEC 750): recorded at run_start, read at dispatch
        self.kind_efforts = {k: dict(v) for k, v in (kind_efforts or {}).items()}  # {kind: {model: effort}}: the panel's per-entry effort
        self._reg = None               # the loaded registry, read once
        self.models_resolve = False    # run_start asks the SDK adapter to resolve a spelling; an old record stays opaque
        if hasattr(adapter, "bind"):
            adapter.bind(lambda kind, **f: self.record.append(kind, **f))
        self.item_workspaces = self._item_workspaces()
        self.runners = default_registry()   # kind -> runner; a test may wrap one entry
        self.ctx = RunCtx(self)

    def _item_workspaces(self):
        """Logical workspaces that a worktree_create makes inside an `each`: one per item."""
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
    def _registry(self):
        """The registry this engine resolves through. A path is read once; None is the committed registry."""
        if self._reg is None:
            self._reg = modelreg.load(self.registry) if self.registry else modelreg.default_registry()
        return self._reg

    def _model_names(self):
        """Every model spelling the graph names: a node's pick, a dispatch's allow list, and an adaptation range."""
        out = []
        for n in self.nodes.values():
            if isinstance(n.get("model"), str):
                out.append(n["model"])
            out += [m for m in (n.get("allow") or {}).get("models") or [] if isinstance(m, str)]
            out += [m for m in (n.get("adapt") or {}).get("models") or [] if isinstance(m, str)]
        return out

    def _resolve_opted_in(self):
        """A graph asks for resolution with `models.resolve: true`; a graph without the key keeps its raw spellings.

        ADR-0147 rule 8: resolution is recorded at run start and applied only when the run opts in, so a graph that
        did not ask sends exactly the string it carries.
        """
        blk = self.g.doc.get("models")
        return isinstance(blk, dict) and blk.get("resolve") is True

    def _set_resolve(self, on):
        """Record the resolution setting and hand it to an adapter that can resolve a spelling (the SDK adapter).

        The engine registry is the one the adapter resolves through: a run never records a model id from one registry
        and sends the wire model of another.
        """
        self.models_resolve = bool(on)
        if hasattr(self.adapter, "resolve"):
            self.adapter.resolve = self.models_resolve
            if hasattr(self.adapter, "registry"):
                self.adapter.registry = self.registry
                if hasattr(self.adapter, "_reg"):
                    self.adapter._reg = None

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
        if isinstance(extra.get("model"), str) and self.models_resolve:     # an outcome keeps the graph spelling and names the registry id it resolved to
            raw, reg = extra["model"], self._registry()
            extra["route_model"] = modelreg.invocation(raw, "sdk", reg=reg).wire
            ref = modelreg.normalize(raw, reg)
            if ref.known:
                extra["model_id"] = ref.id
        routed = routed or self._route(node, outcome)
        fields = dict(reasons=list(reasons), artifact=artifact, spend=spend, turns=turns, cap=cap, elapsed=elapsed, routed=routed, **extra)
        self.outcomes.node(name, entry, sc.item, outcome, **fields)
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
    def no_progress_window(self):
        """The silence one call may keep before its entry ends and the chain moves on (#1289). A project may set it; the
        scale applies as it does to the deadlines, so a test that compresses time compresses this window too."""
        return self.limits.get("no_progress_seconds", session.NO_PROGRESS_SECONDS) * self.scale

    def _call_deadline(self, node, sc):
        """(seconds, by) of one call: the node's own deadline, the project's per-call ceiling, the run's remaining scope
        and the wave's age cap. `by` names the bound that shortened the call below its own deadline: `run_deadline`,
        `project_deadline` or `wave_ceiling`; None when the node's own deadline is the limit (#2840)."""
        own = node.get("deadline", self.run_cfg["deadline"])     # null run deadline: no run limit, the node's own deadline is the cap
        own = math.inf if own is None else own * self.scale
        bounds = [(own, None),
                  (self.limits.get("deadline_seconds", math.inf) * self.scale, "project_deadline"),    # the project's per-call ceiling
                  (sc.deadline_at - self.clock(), "run_deadline")]
        ceiling = self.wave_ceiling_at(notify=True)
        if ceiling is not None:
            bounds.append((ceiling - self.wall(), "wave_ceiling"))
        return min(bounds, key=lambda b: b[0])

    def wave_ceiling_epoch(self, notify=False):
        """The absolute epoch the loop wrote as the wave's age cap, or None with no ceiling option. `notify` writes one
        first-class `wave_ceiling_lost` event when the run names a ceiling file that is missing or unreadable: the lost
        bound is on the record as an event, not a passing diagnostic (#2840)."""
        path = (self.record.events[0].get("engine_options") or {}).get(WAVE_CEILING) if self.record.events else None
        path = path or self.options.get(WAVE_CEILING)   # a resume reads the file the start recorded (ADR-0106)
        if not path:
            return None
        try:
            return float(Path(path).read_text().split()[0])
        except (OSError, ValueError, IndexError) as x:
            if notify and not self._ceiling_missing:
                self._ceiling_missing = True
                self.record.append("wave_ceiling_lost", source="wave_ceiling", node=None, entry=None,
                                   path=str(path), error=str(x)[:200])
            return None

    def wave_ceiling_at(self, notify=False):
        """The absolute epoch a call must end by: the wave's age cap less the entry reserve, so a call that ends at the
        bound can still enter its wave. A running call reads this again, so a ceiling the loop rewrites under a live wave
        bounds the call it already runs (#2840). None with no ceiling."""
        epoch = self.wave_ceiling_epoch(notify)
        return None if epoch is None else epoch - WAVE_ENTRY_RESERVE_SECONDS

    def wave_bound(self, own, by=None):
        """(seconds, by): `own` seconds, or the time left to the wave's age cap when that is less (#2840). `by` names the
        bound `own` came from and is kept when the ceiling does not shorten it. The ceiling is one absolute epoch the loop
        writes when it cuts the wave and removes with the wave; it already carries every pause the age cap took. The time
        left keeps an entry reserve, so a call that ends at the bound can still enter its wave. Zero or less is a spent
        deadline. No option, no file or an unreadable file: no ceiling."""
        ceiling = self.wave_ceiling_at()
        if ceiling is None:
            return own, by
        left = ceiling - self.wall()
        return (left, "wave_ceiling") if left < own else (own, by)

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
        """Record where the run stands and return the `Halt` that ends the run here: a person paused these nodes."""
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
        """The workspace state after a node: what a later resume compares against."""
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
        """A graph with action nodes needs git. A missing git is a defect, never a swap."""
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
        if bad := skills.dirty(self.vendor):   # refuse before run_start, no record
            raise opt.OptionError(f"vendor/skills is not clean: {'; '.join(bad)}. Nothing ran")
        self.preflight = self._preflight_start()
        resolve = self._resolve_opted_in()
        self._set_resolve(resolve)
        models = {"resolve": resolve}
        if resolve:
            models["entries_sha256"] = modelreg.entries_sha256(self._model_names(), self._registry())
        self.record.append("run_start", run_id=self.run_id, graph_name=self.g.doc["name"], graph_hash=self.g.hash,
                           models=models, **({"router_overlay": self.router_overlay} if self.router_overlay else {}), **({"kind_chains": self.kind_chains} if self.kind_chains else {}), **({"kind_efforts": self.kind_efforts} if self.kind_efforts else {}),
                           engine_options=self.options, root=self.g.root, plan=plan_order(self.g.doc), preflight=self.preflight, target=str(self.target), **({"target_branch": b} if (b := self._target_branch()) else {}), **self._target_owned(),
                           **({"target_head": git.head(self.target)} if self.options.get("fix_run") and self.has_git else {}),
                           **({"graph_path": str(self.graph_path)} if self.graph_path else {}), **({"defaults": self.defaults} if self.defaults else {}),
                           **({"project_settings": self.project["record"], "project_settings_method": P.METHOD} if self.project.get("record") else {}), **({"autonomy": self.autonomy} if self.autonomy else {}),
                           state_binding=self.state_binding,
                           **({"caps": self.ceiling.caps} if self.ceiling else {}),
                           skills=skills.snapshot(self.g.doc, self.vendor), skills_commit=skills.commit(self.vendor))

    def _keep_chain_config(self):
        """A resume keeps the order the run built its chains with: `model_order` comes from `run_start`, so a launcher that
        reads a different coding order since then cannot rebuild a different chain (SPEC 1035). A record that never held the
        key keeps no order, as the run started."""
        start = self.record.events[0].get("engine_options") or {}
        if CHAIN_ORDER in start:
            self.options[CHAIN_ORDER] = start[CHAIN_ORDER]
        else:
            self.options.pop(CHAIN_ORDER, None)

    @property
    def fix_run(self):
        """True when this run repairs a work branch (#1374): the engine option `fix_run` recorded at run start.

        The record, not the live options, so a resume reads the answer the start recorded (ADR-0106). A fix run's child commits
        on the work branch, never in the target cut from the base, so its acceptance commands prove in the child's tree."""
        start = self.record.events[0] if self.record.events else {}
        return bool((start.get("engine_options") or {}).get("fix_run"))

    @property
    def fix_cut(self):
        """The target tip a fix run was cut at (#2561), from `run_start`, or None. Only a target still on that tip may be replaced
        by a rebased child: a sibling's landing or a node commit moved it, and that work is never dropped."""
        start = self.record.events[0] if self.record.events else {}
        return start.get("target_head") if self.fix_run else None

    def _target_branch(self):
        """The branch the target stands on at run start, or None (no git, a detached HEAD): a resume refuses a target on another branch."""
        if not self.has_git:
            return None
        try:
            return git.branch(self.target, node_commit.PROBE_DEADLINE_S)
        except subprocess.TimeoutExpired:
            return None

    def _target_owned(self):
        """`target_owned` for `run_start`: whether the target was a linked worktree the run owns. A run started in the owner's checkout
        records False, so its resume goes on there; a record with no marker is an old one, and a resume refuses its checkout target."""
        if not self.has_git:
            return {}
        try:
            return {"target_owned": git.owned_worktree(self.target, node_commit.PROBE_DEADLINE_S)}
        except subprocess.TimeoutExpired:
            return {}

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

    # ------------------------------------------------------------ resume
    @_signals_recorded
    async def resume(self, *, answer=None, from_node=None, release=False):
        """Continue one run from its record under the same run id. Return the verdict. The replay is `squad/replay.py`.
        `release`: the person who runs the command releases every hold the control file still holds."""
        self.run_id, self.answer, self.from_node = self.replay.refuse(from_node), answer, from_node
        self._set_resolve((self.record.events[0].get("models") or {}).get("resolve"))   # a record without the key stays opaque
        self._keep_chain_config()
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
        await self._admitted_walk()
        return self._conclude()

    async def _admitted_walk(self):
        """Walk the graph inside one slot of the machine-wide run ceiling. A full ceiling waits, bounded by the run deadline;
        a wait that expires fails the root by name. The slot returns when the walk ends."""
        lease = None
        if self.ceiling:
            dl = self.run_cfg["deadline"]
            try:
                lease = await self.ceiling.take("runs", self.run_id, None if dl is None else anyio.current_time() + dl * self.scale,
                                                on_wait=lambda w: self.record.append("ceiling_wait", **w),
                                                on_tick=lambda w: self.record.append("ceiling_wait", **w))   # the record moves while the wait lasts, so heartbeat never reads a waiting run as wedged
            except CeilingWaitExpired as x:
                self.outcomes.defect(self.g.root, x, reasons=[f"ceiling_wait_expired:{x.ceiling}", f"cap:{x.cap}"])
                return
        try:
            await self._walk()
        except Defect as d:
            self.outcomes.defect(self.g.root, d)
        finally:
            if self.ceiling:
                self.ceiling.release(lease)

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
        """The target observation that the verdict module folds: what the run will not overwrite."""
        conflict = []
        if self.has_git:
            conflict += [f"unmerged:{p}" for p in git.unmerged(self.target)]
        return {"conflict": conflict}

    def _conclude(self):
        v = V.fold(self.record.events, self.observe_target(), self.run_id, self.run_dir.resolve())
        self.record.append("verdict", **v.event_fields())
        if v.verdict == "completed" and self.has_git:
            self.dispatch.prune()      # a landed child's branch and worktree are clutter once the run passed (a failed run keeps them for resume, and a run the loop started keeps them for its wave entry)
        return v

    @_signals_recorded
    async def run(self):
        self._start()
        if self.preflight["status"] != "ok":
            return self._conclude()
        self._seed_facts()
        await self._admitted_walk()
        return self._conclude()
