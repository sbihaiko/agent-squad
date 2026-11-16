"""Dispatch child execution.

`Children` runs the children a dispatch accepted: the `after` order, the parallel limit, one worktree per
writing child, the `checks` gates, and the lift of a child's own spawns. The metered call is `squad/session.py`.
The engine holds one `Children` and keeps no child state.
"""
from types import SimpleNamespace

import anyio

from squad import actions, gates, git, limits, node_commit, session
from squad import verdict as V
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

    def ledger_held(self, name):
        """The cause of a child its issue ledger held, and "" for any other child: the issue is frozen for the owner
        (ADR-0149 rule 4, ADR-0029). A dependent of such a child inherits the hold, so a chain of dependents stays a hold
        and `first_failure` reads it too, and a hold never turns into a failure."""
        done = self.e.done.get((name, 1, None))
        if done and done["outcome"] == "needs-human" and done["reasons"][:1] in (["ledger_held"], ["after_held"]):
            return " ".join(str(r) for r in done["reasons"][1:]) or "ledger_held"
        return ""

    @staticmethod
    def held_by_ledger(done):
        """True when this outcome is a ledger hold, the child's own or the one a dependent inherited: it asks the owner,
        so it cancels no sibling and fails no dependent."""
        return (done["outcome"] == "needs-human" and bool(done["reasons"])
                and done["reasons"][0] in ("ledger_held", "after_held"))

    def run_checks(self, disp, output, cwd, child=None, entry=1):
        """The `checks` gates run after every child. A child cannot omit or edit them.

        A check that runs a suite also takes part in the run's verdict memo: a green result is kept for its tree and
        suite, and a later stage of this run reuses it instead of running the same suite again."""
        e = self.e
        facts = {n: f.value for (n, it), f in e.facts.values.items() if it is None}
        arts = {**e.artifacts.view(None), **(output if isinstance(output, dict) else {})}
        res = {}
        for g in disp["checks"]:
            gn = e.nodes[g]
            inp = GateInput(arts, facts, cwd)
            key, tree, hit = gates.memo(gn["check"], gn["params"], inp, cwd, e.record.events)
            if hit is not None:
                e.record.append("gate_reuse", node=g, entry=entry, item=None, sha=tree[0], suite=key, source=hit["source"])
                res[g] = "pass"
                continue
            r = run_check(gn["check"], gn["params"], inp)
            res[g] = r.outcome     # a check that could not run is unproven, never a failed child (ADR-0164, #1825)
            if key and tree:
                e.record.append("gate_result", node=g, entry=entry, item=None, sha=tree[0], dirt=tree[1], suite=key,
                                outcome=res[g], source=f"{g}:{child}.{entry}" if child else f"{g}.{entry}")
            if r.outcome != "pass":     # a red's findings and an unproven check's findings both land in the record (#1825)
                res[f"{g}:findings"] = r.findings
                if r.evidence:
                    res[f"{g}:evidence"] = r.evidence
        return res

    async def run_of(self, node, ent, pe, sc, dl_at, reviews=None):
        """Run the accepted top-level children of one dispatch entry. Return (name, outcome, reasons) in request order.
        `reviews` is the task group that owns the children's reviews. A child's review starts the moment the child passes,
        so the reviews of several passing children overlap; the group is opened outside this entry's cancel scope, because
        a child that passed is finished work and a sibling's failure never cancels its review. A review takes a slot of
        `review_slots`, never one of `limiter`: a reading beside a sibling's work is never a child's place in the queue."""
        e = self.e
        room = max(1, min(node["max_parallel"], e.run_cfg["max_parallel"]))
        ctx = SimpleNamespace(node=node, dl_at=dl_at, deadline_at=sc.deadline_at, results=[], frontier=[], held=[],
                              cancel=None, waits={}, reviews=reviews,
                              limiter=anyio.CapacityLimiter(room), review_slots=anyio.CapacityLimiter(room))
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
                held = "" if blocked else e.dispatch.ledger_hold(e.dispatch.children[name]["definition"])
                holding = [(dep, cause) for dep in (blocked or []) if (cause := self.ledger_held(dep))]
                if holding:           # a dependent of a held issue asks the owner with it, never fails `after_failed` (ADR-0029)
                    done = self.outcome(name, "needs-human",
                                        ["after_held", *[dep for dep, _ in holding], *dict.fromkeys(c for _, c in holding)], None, {})
                elif blocked:
                    done = self.outcome(name, "fail", ["after_failed", *blocked], None, {})
                elif held:                                      # a held issue ledger starts no session: the owner is asked (ADR-0149 rule 4, ADR-0029)
                    done = self.outcome(name, "needs-human", ["ledger_held", held], None, {})
                else:
                    async with ctx.limiter:
                        done = await self.admitted(ctx, name)
                        self.start_review(ctx, name, done)
            except anyio.get_cancelled_exc_class():
                if (name, 1, None) not in e.done:
                    with anyio.CancelScope(shield=True):
                        self.outcome(name, "fail", ["cancelled", "not_started"], None, {})
                raise
            if done["outcome"] != "pass" and ctx.node["join"] == "first_failure" and not self.held_by_ledger(done):
                ctx.cancel.cancel()
        ctx.waits.setdefault(name, anyio.Event()).set()
        ctx.results.append((name, done["outcome"], done["reasons"]))
        await self.run_all(ctx, e.dispatch.accepted(name, 1))

    def start_review(self, ctx, name, done):
        """Start a passing child's reading now, so it overlaps its siblings' work. Only a passing write child that can
        land is read: no other child reaches the review in `Dispatch.land`. A dry walk starts none, and a round the
        record already holds is replayed by `land`, never read again (ADR-0107)."""
        e = self.e
        d = e.dispatch.children[name]["definition"]
        path = self.workspace(name, d)
        if (ctx.reviews is None or e.dry or not V.delivers(done["outcome"]) or not ctx.node.get("review")
                or not e.dispatch.landable(name) or not path.exists() or e.dispatch.last_round(name) >= 1):
            return
        e.dispatch.pending[name] = {"event": anyio.Event(), "got": None}
        ctx.reviews.start_soon(self.review_child, ctx, name, d, path)

    async def review_child(self, ctx, name, d, path):
        """One passed child's reading, beside its siblings. It takes a slot of the review limiter, never a child's: a
        review's wall time is never taken from a waiting child's call deadline (ADR-0051). Once the reading ends, the child
        lands at once (`Dispatch.stream`, #1520), one landing at a time; a child another child runs after lands in the
        settle pass instead (ADR-0113 rule 11). A fix round the review asks for is funded there, against the budget the
        run holds at that moment (ADR-0108), and a fix the run cannot fund yet is deferred to the settle pass.
        `Dispatch.land` waits for this result and replays the record when the task died before writing one."""
        e = self.e
        got = e.dispatch.pending[name]
        try:
            async with ctx.review_slots:
                got["got"] = await e.dispatch.first_round(ctx.node, name, d, path, e.dispatch.review_base(path))
        except Exception as x:  # noqa: BLE001 - a task that broke mid-read leaves the land to replay what the record holds
            e.record.append("diagnostic", source="review", node=name, entry=1, payload={"error": str(x)[:500]})
            got["got"] = None
        finally:
            got["event"].set()
        if e.dispatch.streams(name):
            await e.dispatch.stream(name)

    async def fix(self, node, name, entry, review, dl_at, coder=None, repeated=False):
        """One fix round of a child its review asked to fix (#570): entry `entry` of the same child, in its own worktree, with
        the child's caps and the full review as input. `coder` is the model this round answers on first, when the review
        streak moved it (ADR-0151); the child's own model when it is None. `repeated` is the review's reading of the same
        finding twice, which adds one sentence to the prompt (#882). Its commits stay on the child's branch.
        Return its outcome event."""
        ctx = SimpleNamespace(node=node, dl_at=dl_at)
        return await self.admitted(ctx, name, entry, review, coder, repeated)

    async def admitted(self, ctx, name, entry=1, review=None, coder=None, repeated=False):
        """One child inside one slot of the machine-wide child ceiling, keyed on its `spawn_accepted` name. A full ceiling waits,
        bounded by the child's call deadline; a wait that expires fails the child by name. The slot returns when the child ends."""
        e = self.e
        if not e.ceiling:
            return await self.session(ctx, name, entry, review, coder, repeated)
        d = e.dispatch.children[name]["definition"]
        bound = anyio.current_time() + max(0.0, min(d["deadline"] * e.scale, ctx.dl_at - e.clock()))
        try:
            lease = await e.ceiling.take("children", f"{e.run_id}:{name}", bound, on_wait=lambda w: e.record.append("ceiling_wait", **w),
                                         on_tick=lambda w: e.record.append("ceiling_wait", **w))   # the record moves while the wait lasts, so heartbeat never reads a waiting child as wedged
        except CeilingWaitExpired as x:
            return self.outcome(name, "fail", [f"ceiling_wait_expired:{x.ceiling}", f"cap:{x.cap}"], None, {}, entry)
        try:
            return await self.session(ctx, name, entry, review, coder, repeated)
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

    async def session(self, ctx, name, entry=1, review=None, coder=None, repeated=False):
        """One child entry: its worktree, the metered call in `squad/session.py`, then its checks and its spawn lift.
        Entry 1 cuts the worktree; a fix entry (2, 3, ...) works in it again with the review that asked for it, led by
        `coder` when the review streak moved it (ADR-0151), and told to test the premise of the last fix when the
        finding repeated (#882)."""
        e, d, slot = self.e, self.e.dispatch.children[name]["definition"], {}
        dl, ws = min(d["deadline"] * e.scale, ctx.dl_at - e.clock()), str(self.workspace(name, d))
        ends = e.clock() + dl      # the child's deadline bounds the whole chain, not each entry of it (#1289)
        # A child answers on the envelope's `allow.models`, led by the model its request names. A provider that refuses
        # to serve is an outage, not a defect of the work, so the chain moves on; the worktree is cut once and kept.
        # A requeued issue starts its first entry where the issue ledger says its last attempt stopped (ADR-0151 rule 2).
        spent, hours, at = e.options.get("spent_models") or [], e.options.get("model_hours") or {}, e.wall()
        first = coder or (e.dispatch.moved(ctx.node, d) if entry == 1 else d["model"])
        # A child of a kind falls back along its own kind's chain (SPEC 750). The chain is read from this entry's node,
        # so the router overlay still closes a model the run may not use (SPEC 505).
        km = (ctx.node["allow"].get("kind_models") or {}).get(d.get("kind")) if d.get("kind") else None
        models = km or ctx.node["allow"]["models"]
        chain = session.model_chain(models, first, spent, hours, at, km or e.options.get("model_order") or [])
        for m, why in session.hours_skipped(models, first, spent, hours, at):    # an entry left out of its window is on the record
            e.record.append("model_skipped", node=name, entry=entry, model=m, reason=why)
        chain = ctx_skip(e, name, entry, chain, request_prompt(d, review, repeated))   # an entry under the prompt is on the record, with its size (#1470)
        log = session.ChainLog(e.record, name, entry, limits.write)
        out, tried, model = await session.run_chain(
            chain, lambda m, first: self.attempt(name, d, ws, ends, m, slot, first and entry == 1, entry, review, repeated, chain=chain),
            on_retry=log.retried, on_silent=log.silent_entry)
        log.close(tried)
        if out.kind == "deadline":
            return slot["ev"]
        return self.close(ctx, name, d, ws, out, model, session.chain_message(tried) if session.exhausted(chain, tried) else None, entry)

    async def attempt(self, name, d, ws, ends, model, slot, first, entry=1, review=None, repeated=False, chain=()):
        """One model's call for one child: the worktree is cut for the first attempt, and a later entry reuses it."""
        e = self.e
        dl = max(0.0, ends - e.clock())      # what is left of the child's deadline (#1289)

        def hit_deadline(res, el):
            slot.update(ev=self.outcome(name, "deadline", ["deadline"], None, {"cap": res.amount, "elapsed": el}, entry))

        def cancelled(res, el):
            return self.outcome(name, "fail", ["cancelled"], None, {"cap": res.amount, "elapsed": el}, entry)

        out = await session.run_call(
            lambda: e.dispatch.held.pop(name, None) or e.budget.hold(d["spend"], d.get("spend_tokens")),   # a resume checks the funding again
            dl, lambda res: self.request(name, d, res, ws, dl, model, entry, review, repeated), adapter=e.adapter, chain=chain, registry=e._registry(),
            record=e.record, budget=e.budget, clock=e.clock,
            wall=e.wall, prepare=(lambda res: self.prepare(name, d, dl)) if first else None,
            on_deadline=hit_deadline, on_cancel=cancelled, no_progress=e.no_progress_window())
        return out, model

    async def prepare(self, name, d, dl):
        if self.isolated(d):
            made = await self.worktree(name, d, dl)
            if not made.ok:
                return ["child_workspace_failed", *made.reasons]

    def request(self, name, d, res, ws, dl, model=None, entry=1, review=None, repeated=False):
        prompt = request_prompt(d, review, repeated)
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
        if out.kind in ("adapter_error", "no_progress"):      # a silent entry that ended the chain names its window in the message (#1289)
            return self.outcome(name, "fail", session.adapter_reason(out.error), None,
                                {"model": model, "effort": d["effort"],   # the chain entry it tried: a child has no node-level model
                                 "cap": out.res.amount, "elapsed": out.elapsed,
                                 "diagnostic": session.adapter_diagnostic(out.error, message), **session.error_fields(out.error)}, entry)
        return self.settle(ctx, name, d, ws, out, model, entry)

    def settle(self, ctx, name, d, ws, out, model, entry=1):
        """Derive the outcome from the artifact, run the checks, snapshot the worktree, and lift the child's own spawns."""
        e, disp, r, res = self.e, ctx.node, out.result, out.res
        outcome, reasons, diag_cap = session.agent_outcome({}, r, session.cap_reason(r.stop_reason, res.amount), res.amount)
        rel, extra = None, {"model": model, "effort": d["effort"], **(r.usage or {}), **({"spend_source": r.spend_source} if r.spend_source else {})}   # the model that answered, not the one the request named first
        if not isinstance(r.output, dict) and r.ended:      # an outcome with no artifact says how the session ended
            extra["diagnostic"] = {"ended": r.ended}
        if isinstance(r.output, dict):
            rel = e.artifacts.put(name, None, entry, r.output, None)
            outcome, reasons = self.check(disp, r.output, ws, outcome, reasons, extra, name, entry)
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

    def check(self, disp, output, ws, outcome, reasons, extra, child=None, entry=1):
        """Run the `checks` gates over a child's artifact. A failed gate fails a passing child; a gate that could not run
        makes it unproven, never failed (ADR-0164, #1825). Write the gate results into `extra`."""
        checks = self.run_checks(disp, output, ws, child, entry)
        failed = [g for g in disp["checks"] if checks[g] == "fail"]
        if failed and outcome == "pass":
            outcome, reasons = "fail", [f"check:{g}:{f}" for g in failed for f in checks[f"{g}:findings"]] or [f"check:{g}" for g in failed]
        unproven = [g for g in disp["checks"] if checks[g] == "unproven"]
        if unproven and outcome == "pass":     # a red beats it above: a measured failure still fails the child
            outcome, reasons = "unproven", [f"check:{g}:{f}" for g in unproven for f in checks.get(f"{g}:findings", [])] or [f"check:{g}" for g in unproven]
        extra["checks"] = {k: v for k, v in checks.items() if not k.endswith((":findings", ":evidence"))}
        evidence = {f"{g}:{k}": v for g in disp["checks"] for k, v in checks.get(f"{g}:evidence", {}).items()}
        if evidence:      # the record keeps it; the ledger reads `checks` only, so no router prompt carries it
            extra["evidence"] = evidence
        return outcome, reasons


def ctx_skip(e, name, entry, chain, prompt):
    """The chain without the entries whose declared context window is under `prompt`, each skip on the record (#1470)."""
    kept, skipped = session.ctx_skipped(chain, session.estimate_tokens(prompt), e.options.get("model_ctx") or {})
    for m, why in skipped:
        e.record.append("model_skipped", node=name, entry=entry, model=m, reason=why)
    return kept


def request_prompt(d, review=None, repeated=False):
    """The prompt one child entry sends: its skill and task, and the review it answers when it is a fix entry."""
    return f"/{d['skill']['name']} {d['prompt']}" + (fix_input(review, repeated) if review else "")


def fix_input(review, repeated=False):
    """What a fix entry reads after the child's own task: the full review of its last head (#570), never its first line only.
    A finding that repeats the one before it adds one sentence: the coder names the premise its last fix rested on and tests
    that premise before it attempts a third fix (#882, ADR-0151)."""
    return ("\n\nThe review of your work in this worktree asked for changes. Fix every blocking finding here; your commits stay on"
            f" this branch, and the new head is reviewed again. The full review:\n\n{review.get('findings') or ''}"
            + ("\n\nName the premise the last fix rested on, and test that premise before you attempt a third fix."
               if repeated else ""))
