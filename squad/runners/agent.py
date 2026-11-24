"""The agent runner: one fresh session behind the adapter seam, then its proposals."""
import json

import anyio

from squad import adapt, limits, session
from squad.adapter import CallRequest, new_session_id


def prompt(ctx, node, entry, sc, repair=None):
    inputs = {"ledger": ctx.ledger(node["name"])} if ctx.is_dispatch_entry(node["name"]) else ctx.artifacts.view(sc.item)   # a router reads the ledger, never a child's artifact
    doc = {"node": node["name"], "entry": entry, "inputs": inputs}
    if ctx.is_dispatch_entry(node["name"]):
        doc["limits"] = ctx.limits(node["name"])      # the router asks inside these; code still checks every request
    if repair:
        doc["repair"] = repair      # one turn only: the answer was outside the shape, and this is why (#2336)
    body = json.dumps(doc, sort_keys=True, default=str)
    return f"/{node['skill']} {body}" if node.get("skill") else body


def repair_reason(shape):
    """The one reason a dispatch entry's repair turn reads: its answer was outside the shape, and the shape it read."""
    return (f'Your previous answer was outside the shape: it gave no "spawns" list (the shape it read was {shape}). '
            'Answer again: one artifact that holds a "spawns" list, and nothing else changed.')


async def shadow(ctx, node, entry, sc):
    """Run the shadow observer before an agent call and record what it says. A failure or a timeout is no suggestion and never stops the step."""
    obs = ctx.shadow
    if obs is None:
        return
    try:
        with anyio.move_on_after(obs.deadline) as cut:
            payloads = await anyio.to_thread.run_sync(lambda: obs.observe(node, entry, ctx.target), abandon_on_cancel=True)
            for p in payloads or []:
                fields = {"item": sc.item} if sc.item is not None else {}
                ctx.record.append("diagnostic", source=p.get("source", "shadow"), node=node["name"], entry=entry, payload=p, **fields)
        if cut.cancelled_caught:
            raise TimeoutError("shadow_deadline")
    except Exception as e:  # noqa: BLE001  advisory only: no suggestion, and the record says so
        ctx.record.append("diagnostic", source="shadow", node=node["name"], entry=entry,
                          payload={"source": "shadow", "status": "no_suggestion", "reason": type(e).__name__})


def apply_proposals(ctx, node, entry, sc, r):
    """Lift the proposals from an agent's output, validate each, and apply it to the overlay."""
    out = r.output
    if not isinstance(out, dict) or "proposals" not in out:
        return
    props = out["proposals"] if isinstance(out["proposals"], list) else [None]
    for n, p in enumerate(props, 1):
        pid = f"{node['name']}@{entry}#{n}"
        d = p if isinstance(p, dict) else {}
        kind, target = d.get("kind"), d.get("target")
        ctx.record.append("proposed", proposal_id=pid, kind=kind, target=target, requested=d.get("value"), proposer=node["name"])
        try:
            old, rng, cited = adapt.validate(p, node, ctx.nodes, ctx.eff, ctx.facts, sc.item)
        except adapt.Rejected as e:
            ctx.record.append("rejected", proposal_id=pid, kind=kind, target=target, reason=str(e))
            continue
        if not isinstance(kind, str):      # validate refused every other kind (unknown_kind); this narrows the type for the key lookup
            continue
        fields = dict(proposal_id=pid, kind=kind, target=target, old=old, requested=d["value"], range=rng, facts=cited)
        ctx.record.append("validated", **fields)
        ctx.eff.set(target, adapt.KEY[kind], d["value"])
        ctx.record.append("applied", **fields, proposer=node["name"], session=r.session_id)


class AgentRunner:
    async def run(self, ctx, node, entry, sc):
        await shadow(ctx, node, entry, sc)    # before the deadline and the reservation, so it never eats a node's time
        (deadline, bound_by), eff = ctx.call_deadline(node, sc), ctx.eff.agent(node)
        slot = {"limit_by": bound_by} if bound_by else {}     # call_start names the wave ceiling when it is the bound (#2840)
        ends = ctx.clock() + deadline      # the node's deadline bounds the whole chain, not each entry of it (#1289)
        fin = lambda *a, **kw: ctx.finish(node, entry, sc, *a, **kw)    # noqa: E731
        eff, chain = self.chain(ctx, node, entry, sc, eff)
        log = session.ChainLog(ctx.record, node["name"], entry, limits.write)    # the chain's record: a retry, a silent end, the refusals
        finished, out, who, shape = await self.ask(ctx, node, entry, sc, eff, ends, slot, fin, chain, log)
        return finished if finished is not None else self.settle(ctx, node, entry, sc, out, eff, who, shape=shape)

    async def ask(self, ctx, node, entry, sc, eff, ends, slot, fin, chain, log):
        """Run this entry's call, and its one repair turn when a dispatch entry's answer is outside the spawns-list shape.
        Return `(finished, out, who, shape)`: `finished` is the node's outcome event when a cap, a deadline or a refused
        chain ends it here, else None with the answer and the failed shape a second answer read (#2336)."""
        repair, failed_shape, who = None, None, None
        while True:
            asked = chain if repair is None else ([who["model"]] if who and who.get("model") else chain)   # the repair re-asks the model that answered
            out, tried, who = await session.run_chain(
                asked, lambda m, first, asked=asked, repair=repair: self.attempt(
                    ctx, node, entry, sc, eff, ends, m, slot, fin, chain=asked, repair=repair),
                on_retry=log.retried, on_silent=log.silent_entry)
            log.close(tried)
            if session.exhausted(asked, tried):    # every entry refused to serve: one fail, naming each entry and its error
                return fin("fail", ["adapter_error"], cap=out.res.amount, elapsed=out.elapsed,
                           diagnostic={"error": session.chain_message(tried)}, **session.error_fields(out.error), **who), None, who, None
            if out.kind == "no_funds":
                return fin("fail", ["run_spend_cap"], spend=None, cap=None), None, who, None
            if out.kind == "expired":
                return fin("deadline", ["deadline", slot.get("limit_by") or "run_deadline"]), None, who, None
            if out.kind == "deadline":
                return slot["res"], None, who, None
            if out.kind == "adapter_error":
                return fin("fail", session.adapter_reason(out.error), cap=out.res.amount, elapsed=out.elapsed,
                           diagnostic=session.adapter_diagnostic(out.error), **session.error_fields(out.error), **who), None, who, None
            fault = self.repair_shape(ctx, node, out, eff) if ctx.is_dispatch_entry(node["name"]) else None
            if fault is None:
                return None, out, who, failed_shape
            if repair is not None:      # the second answer is outside the shape too: the run ends naming the shape it read
                return None, out, who, fault
            repair = repair_reason(fault)
            ctx.record.append("answer_repair", node=node["name"], entry=entry, shape=fault, reason=repair)

    @staticmethod
    def repair_shape(ctx, node, out, eff):
        """The shape a dispatch entry's answer read, when a repair turn may answer it again, and None for a sound answer.
        A clean end with budget left and no artifact is `child_ended_early`, and an artifact whose `spawns` value is not
        a list is `spawns_shape:<shape>`: one repair turn answers both (#2336). A cap, a deadline, a session error, and a
        well-formed empty list are the entry's own answers, so they get none (ADR-0113 rule 18)."""
        if out.kind != "result":
            return None
        r, res = out.result, out.res
        _outcome, reasons, _cap = session.agent_outcome(
            node, r, session.cap_reason(r.stop_reason, res.amount, eff["spend_max"]), res.amount)
        if reasons == ["child_ended_early"]:
            return session.spawns_shape(r.output)
        if len(reasons) == 1 and reasons[0].startswith("spawns_shape:"):
            return reasons[0].split(":", 1)[1]
        return None

    def chain(self, ctx, node, entry, sc, eff):
        """The models this entry may answer on, and the effort to name with them. A dispatch entry answers on the envelope's
        `allow.models`, led by the model the node names; SPEC 750 makes it the router's own chain when the envelope holds
        kinds. A provider that refuses to serve one entry never ends the node while the chain still holds an entry nobody
        tried. An entry left out of its window is on the record."""
        dispatch = ctx.is_dispatch_entry(node["name"])
        models = ctx.limits(node["name"]).get("models", []) if dispatch else []
        rchain = ctx.router_chain() if dispatch else []
        at = ctx.now()
        if rchain:
            models = rchain
            eff = {**eff, "model": rchain[0], **({"effort": e} if (e := ctx.router_effort()) else {})}
        chain = session.model_chain(models, eff["model"], ctx.spent_models, ctx.model_hours, at, rchain or ctx.model_order)
        for m, why in session.hours_skipped(models, eff["model"], ctx.spent_models, ctx.model_hours, at):
            ctx.record.append("model_skipped", node=node["name"], entry=entry, model=m, reason=why)
        chain, skipped = session.ctx_skipped(chain, session.estimate_tokens(prompt(ctx, node, entry, sc)), ctx.model_ctx)
        for m, why in skipped:      # an entry whose declared window is under the prompt is on the record, with its size (#1470)
            ctx.record.append("model_skipped", node=node["name"], entry=entry, model=m, reason=why)
        return eff, chain

    async def attempt(self, ctx, node, entry, sc, eff, ends, model, slot, fin, chain=(), repair=None):
        """One model's call for this node entry. The next entry of the chain answers after one that refused to serve."""
        who = {"model": model, "effort": eff["effort"]}      # the attempt's own model: it names the outcome and any stop
        deadline = max(0.0, ends - ctx.clock())              # what is left of the node's deadline; zero ends the chain as expired

        def hit_deadline(res, el):
            slot.update(res=fin("deadline", ["deadline"], spend=None, cap=res.amount, elapsed=el, **who))

        def cancelled(res, el):
            return fin("fail", ["cancelled"], spend=None, cap=res.amount, elapsed=el, **who)

        def make(res):
            return CallRequest(node["name"], entry, sc.item, prompt(ctx, node, entry, sc, repair), model, eff["effort"], eff["tools"],
                               eff["stalls_max"], res.amount, str(ctx.workspace_path(node.get("workspace"), sc.item)), deadline,
                               new_session_id(ctx.run_id), node.get("skill"), node.get("instructions"),
                               ctx.project_limits.get("max_output_tokens"), ctx.project_limits.get("context_tokens"))

        out = await session.run_call(
            lambda: ctx.budget.reserve(eff["spend_max"], node["budget"]["spend"]["min"], node["budget"]["spend"].get("max_tokens")), deadline, make, adapter=ctx.adapter,
            chain=chain, registry=ctx.model_registry,
            record=ctx.record, budget=ctx.budget, clock=ctx.clock, wall=ctx.wall, on_deadline=hit_deadline, on_cancel=cancelled,
            no_progress=ctx.no_progress_window(), limit_by=slot.get("limit_by"))
        return out, who

    def settle(self, ctx, node, entry, sc, out, eff, who, shape=None):
        r, res = out.result, out.res
        outcome, reasons, diag_cap = session.agent_outcome(node, r, session.cap_reason(r.stop_reason, res.amount, eff["spend_max"]), res.amount)
        if shape is not None and not any(str(x).startswith("spawns_shape:") for x in reasons):
            reasons = [*reasons, f"spawns_shape:{shape}"]     # the repair turn's answer is outside the shape too: name the shape it read (#2336)
        rel = ctx.artifacts.put(node["name"], sc.item, entry, r.output, node.get("output_key")) if isinstance(r.output, dict) else None
        extra = {**who, **(r.usage or {}), **({"spend_source": r.spend_source} if r.spend_source else {})}
        diagnostic = {k: v for k, v in (("cap", diag_cap), ("subagents", r.subagents or None), ("session", r.session_id)) if v}
        if not isinstance(r.output, dict) and r.ended:      # an outcome with no artifact says how the session ended
            diagnostic["ended"] = r.ended
        if diagnostic:
            extra["diagnostic"] = diagnostic
        ctx.commit_workspace(node, entry, sc, outcome)     # #84: the engine's commit, before the snapshot a resume compares against
        ws = ctx.ws_snapshot(node, sc)
        if ws:
            extra["workspace"] = ws
        apply_proposals(ctx, node, entry, sc, r)
        return ctx.finish(node, entry, sc, outcome, reasons, rel, spend=r.spend, turns=r.turns, cap=res.amount, elapsed=out.elapsed, **extra)
