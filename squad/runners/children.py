"""Dispatch child execution.

`Children` runs the children a dispatch accepted: the `after` order, the parallel limit, one worktree per
writing child, the `checks` gates, and the lift of a child's own spawns. The metered call is `squad/session.py`.
The engine holds one `Children` and keeps no child state.
"""
import time
from types import SimpleNamespace

import anyio

from squad import actions, git, limits, node_commit, session
from squad.adapter import CallRequest, new_session_id
from squad.caps import CeilingWaitExpired
from squad.engine_errors import Frontier
from squad.gates import GateInput, run_check
from squad.graph import WRITE_TOOLS
from squad.runners.types import Scope


class Children:
    def __init__(self, engine):
        self.e = engine

    def isolated(self, d):
        """A dispatch child that may write gets its own worktree, so parallel children never share a directory."""
        return self.e.has_git and bool(WRITE_TOOLS & set(d["tools"]))

    def workspace(self, name, d):
        """The directory of one dispatch child: its own worktree if it may write, else the workspace its template names, else the target."""
        e = self.e
        if not self.isolated(d):
            for dep in d.get("after", []):   # a reader that runs after a writer reads the writer's worktree: that is the work to review
                dd = e.dispatch.children[dep]["definition"]
                if self.isolated(dd) and self.workspace(dep, dd).exists():
                    return self.workspace(dep, dd)
            p = e.workspace_path(d["workspace"], None)
            return p if p.exists() else e.target
        logical = "target" if d["workspace"] in (None, ".") else d["workspace"].replace("/", "_")
        return e.run_dir / "ws" / f"{logical}-{name}"

    async def worktree(self, name, d, timeout):
        """Make the child's worktree from the HEAD of the workspace its template names, else from the target. A path that exists is refused, never reused."""
        e = self.e
        path, base = self.workspace(name, d), e.workspace_path(d["workspace"], None)
        src, branch = (base if base.exists() else e.target), actions.branch_name(e.run_id, path.name, None)
        async with e.wt_lock:    # git takes its own locks, so worktree adds go one at a time, off the event loop
            return await anyio.to_thread.run_sync(actions.worktree_create, src, path, branch, timeout)

    def snapshot(self, name, d):
        if not self.isolated(d):
            return self.e._ws_snapshot({"workspace": d["workspace"]}, Scope(None, 0))
        p = self.workspace(name, d)
        return {"name": d["workspace"], "child": name, "path": str(p), "head": git.head(p), "state": git.state(p)} if p.exists() else None

    def outcome(self, name, outcome, reasons, artifact, extra, entry=1):
        e = self.e
        acc = e.dispatch.children[name]
        res = e.dispatch.held.pop(name, None)
        if res is not None:          # a child that never ran returns its grant
            e.budget.release(res)
        return e.outcomes.child(name, outcome, reasons, artifact, extra, parent=acc["parent"], depth=acc["depth"], entry=entry)

    def run_checks(self, disp, output, cwd):
        """The `checks` gates run after every child. A child cannot omit or edit them."""
        e = self.e
        facts = {n: f.value for (n, it), f in e.facts.values.items() if it is None}
        arts = {**e.artifacts.view(None), **(output if isinstance(output, dict) else {})}
        res = {}
        for g in disp["checks"]:
            gn = e.nodes[g]
            r = run_check(gn["check"], gn["params"], GateInput(arts, facts, cwd))
            res[g] = "pass" if r.ok else "fail"
            if not r.ok:
                res[f"{g}:findings"] = r.findings
                if r.evidence:
                    res[f"{g}:evidence"] = r.evidence
        return res

    async def run_of(self, node, ent, pe, sc, dl_at):
        """Run the accepted top-level children of one dispatch entry. Return (name, outcome, reasons) in request order."""
        e = self.e
        ctx = SimpleNamespace(node=node, dl_at=dl_at, results=[], frontier=[], held=[], cancel=None, waits={},
                              limiter=anyio.CapacityLimiter(max(1, min(node["max_parallel"], e.run_cfg["max_parallel"]))))
        with anyio.CancelScope() as scope:
            ctx.cancel = scope
            await self.run_all(ctx, e.dispatch.accepted(ent, pe))
        if ctx.frontier:
            raise Frontier(ctx.frontier)
        if ctx.held:    # a person paused these children: the ones that could run have run, the rest wait for a resume
            raise e.hold(sorted(ctx.held, key=lambda n: e.dispatch.children[n]["pos"]), node["name"])
        return sorted(ctx.results, key=lambda r: e.dispatch.children[r[0]]["pos"])      # request order, never completion order

    async def run_all(self, ctx, names):
        async with anyio.create_task_group() as tg:
            for n in names:
                tg.start_soon(self.run_one, ctx, n)

    async def run_one(self, ctx, name):
        e = self.e
        done = e.done.get((name, 1, None))
        if done is None:
            if e.dry:
                ctx.frontier.append((name, None))
                return
            try:
                blocked = None if e.control.held(name) else await self.wait_after(ctx, name)      # a child that runs after others waits outside the limiter
                if blocked is not None and e.control.held(name):      # a pause that came while it waited
                    blocked = None
                if blocked is None:                              # held by a person, or it runs after a held child: it does not start
                    ctx.held.append(name)
                    ctx.waits.setdefault(name, anyio.Event()).set()
                    return
                if blocked:
                    done = self.outcome(name, "fail", ["after_failed", *blocked], None, {})
                else:
                    async with ctx.limiter:
                        done = await self.admitted(ctx, name)
            except anyio.get_cancelled_exc_class():
                if (name, 1, None) not in e.done:
                    with anyio.CancelScope(shield=True):
                        self.outcome(name, "fail", ["cancelled", "not_started"], None, {})
                raise
            if done["outcome"] != "pass" and ctx.node["join"] == "first_failure":
                ctx.cancel.cancel()
        ctx.waits.setdefault(name, anyio.Event()).set()
        ctx.results.append((name, done["outcome"], done["reasons"]))
        await self.run_all(ctx, e.dispatch.accepted(name, 1))

    async def fix(self, node, name, entry, review, dl_at, coder=None):
        """One fix round of a child its review asked to fix (#570): entry `entry` of the same child, in its own worktree, with
        the child's caps and the full review as input. `coder` is the model this round answers on first, when the review
        streak moved it (ADR-0151); the child's own model when it is None. Its commits stay on the child's branch.
        Return its outcome event."""
        ctx = SimpleNamespace(node=node, dl_at=dl_at)
        return await self.admitted(ctx, name, entry, review, coder)

    async def admitted(self, ctx, name, entry=1, review=None, coder=None):
        """One child inside one slot of the machine-wide child ceiling, keyed on its `spawn_accepted` name. A full ceiling waits,
        bounded by the child's call deadline; a wait that expires fails the child by name. The slot returns when the child ends."""
        e = self.e
        if not e.ceiling:
            return await self.session(ctx, name, entry, review, coder)
        d = e.dispatch.children[name]["definition"]
        bound = anyio.current_time() + max(0.0, min(d["deadline"] * e.scale, ctx.dl_at - e.clock()))
        try:
            lease = await e.ceiling.take("children", f"{e.run_id}:{name}", bound, on_wait=lambda w: e.record.append("ceiling_wait", **w),
                                         on_tick=lambda w: e.record.append("ceiling_wait", **w))   # the record moves while the wait lasts, so heartbeat never reads a waiting child as wedged
        except CeilingWaitExpired as x:
            return self.outcome(name, "fail", [f"ceiling_wait_expired:{x.ceiling}", f"cap:{x.cap}"], None, {}, entry)
        try:
            return await self.session(ctx, name, entry, review, coder)
        finally:
            e.ceiling.release(lease)

    async def wait_after(self, ctx, name):
        """Wait for the children this one runs after. Return the names that did not pass: this child then fails unrun.
        Return None when one of them is held: it never ends in this run, so this child is held with it."""
        e, bad = self.e, []
        for dep in e.dispatch.children[name]["definition"].get("after", []):
            ev = e.done.get((dep, 1, None))
            if ev is None:
                await ctx.waits.setdefault(dep, anyio.Event()).wait()
                ev = e.done.get((dep, 1, None))
                if ev is None:
                    return None
            if ev["outcome"] != "pass":
                bad.append(dep)
        return bad

    async def session(self, ctx, name, entry=1, review=None, coder=None):
        """One child entry: its worktree, the metered call in `squad/session.py`, then its checks and its spawn lift.
        Entry 1 cuts the worktree; a fix entry (2, 3, ...) works in it again with the review that asked for it, led by
        `coder` when the review streak moved it (ADR-0151)."""
        e, d, slot = self.e, self.e.dispatch.children[name]["definition"], {}
        dl, ws = min(d["deadline"] * e.scale, ctx.dl_at - e.clock()), str(self.workspace(name, d))
        # A child answers on the envelope's `allow.models`, led by the model its request names. A provider that refuses
        # to serve is an outage, not a defect of the work, so the chain moves on; the worktree is cut once and kept.
        spent, hours, hour = e.options.get("spent_models") or [], e.options.get("model_hours") or {}, time.localtime(e.wall()).tm_hour
        first = coder or d["model"]
        chain = session.model_chain(ctx.node["allow"]["models"], first, spent, hours, hour, e.options.get("model_order") or [])
        for m, why in session.hours_skipped(ctx.node["allow"]["models"], first, spent, hours, hour):    # an entry left out of its window is on the record
            e.record.append("model_skipped", node=name, entry=entry, model=m, reason=why)
        out, tried, model = await session.run_chain(chain, lambda m, first: self.attempt(name, d, ws, dl, m, slot, first and entry == 1, entry, review))
        for m, err in tried:    # every refusal is on the record, exhausted chain or not (#494)
            e.record.append("model_refused", node=name, entry=entry, model=m, error=err[:session.CHAIN_LINE])
            limits.write(m, err)    # a refusal that names its reset closes that account until then (#572)
        if out.kind == "deadline":
            return slot["ev"]
        return self.close(ctx, name, d, ws, out, model, session.chain_message(tried) if session.exhausted(chain, tried) else None, entry)

    async def attempt(self, name, d, ws, dl, model, slot, first, entry=1, review=None):
        """One model's call for one child: the worktree is cut for the first attempt, and a later entry reuses it."""
        e = self.e

        def hit_deadline(res, el):
            slot.update(ev=self.outcome(name, "deadline", ["deadline"], None, {"cap": res.amount, "elapsed": el}, entry))

        def cancelled(res, el):
            return self.outcome(name, "fail", ["cancelled"], None, {"cap": res.amount, "elapsed": el}, entry)

        out = await session.run_call(
            lambda: e.dispatch.held.pop(name, None) or e.budget.hold(d["spend"]),   # a resume checks the funding again
            dl, lambda res: self.request(name, d, res, ws, dl, model, entry, review), adapter=e.adapter, record=e.record, budget=e.budget, clock=e.clock,
            wall=e.wall, prepare=(lambda res: self.prepare(name, d, dl)) if first else None,
            on_deadline=hit_deadline, on_cancel=cancelled)
        return out, model

    async def prepare(self, name, d, dl):
        if self.isolated(d):
            made = await self.worktree(name, d, dl)
            if not made.ok:
                return ["child_workspace_failed", *made.reasons]

    def request(self, name, d, res, ws, dl, model=None, entry=1, review=None):
        prompt = f"/{d['skill']['name']} {d['prompt']}" + (fix_input(review) if review else "")
        return CallRequest(name, entry, None, prompt, model or d["model"], d["effort"], d["tools"], min(d["stalls"], self.e.limits.get("max_stalls", d["stalls"])),
                           res.amount, ws, dl, new_session_id(self.e.run_id), d["skill"]["name"], None,
                           self.e.limits.get("max_output_tokens"), self.e.limits.get("context_tokens"))

    def close(self, ctx, name, d, ws, out, model, message=None, entry=1):
        """The outcome of a child whose call ended without a deadline. `message` is the one line an exhausted chain reports."""
        if out.kind == "no_funds":
            return self.outcome(name, "fail", ["run_spend_cap"], None, {}, entry)
        if out.kind == "expired":
            return self.outcome(name, "deadline", ["deadline", "run_deadline"], None, {}, entry)
        if out.kind == "prepare_failed":
            return self.outcome(name, "fail", out.reasons, None, {}, entry)
        if out.kind == "adapter_error":
            return self.outcome(name, "fail", session.adapter_reason(out.error), None,
                                {"model": model, "effort": d["effort"],   # the chain entry it tried: a child has no node-level model
                                 "cap": out.res.amount, "elapsed": out.elapsed,
                                 "diagnostic": session.adapter_diagnostic(out.error, message), **session.error_fields(out.error)}, entry)
        return self.settle(ctx, name, d, ws, out, model, entry)

    def settle(self, ctx, name, d, ws, out, model, entry=1):
        """Derive the outcome from the artifact, run the checks, snapshot the worktree, and lift the child's own spawns."""
        e, disp, r, res = self.e, ctx.node, out.result, out.res
        outcome, reasons, diag_cap = session.agent_outcome({}, r, session.cap_reason(r.stop_reason, res.amount))
        rel, extra = None, {"model": model, "effort": d["effort"], **(r.usage or {}), **({"spend_source": r.spend_source} if r.spend_source else {})}   # the model that answered, not the one the request named first
        if not isinstance(r.output, dict) and r.ended:      # an artifact_missing outcome says how the session ended
            extra["diagnostic"] = {"ended": r.ended}
        if isinstance(r.output, dict):
            rel = e.artifacts.put(name, None, entry, r.output, None)
            outcome, reasons = self.check(disp, r.output, ws, outcome, reasons, extra)
        if diag_cap:      # a cap that left a valid artifact: the child passes, and the cap rides in the record
            extra["diagnostic"] = {**extra.get("diagnostic", {}), "cap": diag_cap}
        if self.isolated(d):    # #84: a child's own worktree is committed by the engine when the child passed, before its snapshot
            node_commit.commit_after(e, node=name, entry=entry, item=None, logical=d["workspace"] or "target", path=ws, outcome=outcome)
        snap = self.snapshot(name, d)
        if snap:
            extra["workspace"] = snap
        if isinstance(r.output, dict) and "spawns" in r.output and entry == 1:      # a fix round fixes its own work: it spawns nothing
            e.dispatch.lift(disp, name, 1, e.dispatch.children[name]["depth"] + 1, r.output["spawns"], d["tools"], max(0.0, res.amount - (r.spend or 0)),
                            min(d["deadline"] - out.elapsed / e.scale, (ctx.dl_at - e.clock()) / e.scale), r.session_id or "unknown")
        return self.outcome(name, outcome, reasons, rel, {"spend": r.spend, "turns": r.turns, "cap": res.amount, "elapsed": out.elapsed, **extra}, entry)

    def check(self, disp, output, ws, outcome, reasons, extra):
        """Run the `checks` gates over a child's artifact. A failed gate fails a passing child. Write the gate results into `extra`."""
        checks = self.run_checks(disp, output, ws)
        failed = [g for g in disp["checks"] if checks[g] == "fail"]
        if failed and outcome == "pass":
            outcome, reasons = "fail", [f"check:{g}:{f}" for g in failed for f in checks[f"{g}:findings"]] or [f"check:{g}" for g in failed]
        extra["checks"] = {k: v for k, v in checks.items() if not k.endswith((":findings", ":evidence"))}
        evidence = {f"{g}:{k}": v for g in disp["checks"] for k, v in checks.get(f"{g}:evidence", {}).items()}
        if evidence:      # the record keeps it; the ledger reads `checks` only, so no router prompt carries it
            extra["evidence"] = evidence
        return outcome, reasons


def fix_input(review):
    """What a fix entry reads after the child's own task: the full review of its last head (#570), never its first line only."""
    return ("\n\nThe review of your work in this worktree asked for changes. Fix every blocking finding here; your commits stay on"
            f" this branch, and the new head is reviewed again. The full review:\n\n{review.get('findings') or ''}")
