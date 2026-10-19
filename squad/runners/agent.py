"""The agent runner: one fresh session behind the adapter seam, then its proposals."""
import json

import anyio

from squad import adapt, limits, session
from squad.adapter import CallRequest, new_session_id


def prompt(ctx, node, entry, sc):
    inputs = {"ledger": ctx.ledger(node["name"])} if ctx.is_dispatch_entry(node["name"]) else ctx.artifacts.view(sc.item)   # a router reads the ledger, never a child's artifact
    doc = {"node": node["name"], "entry": entry, "inputs": inputs}
    if ctx.is_dispatch_entry(node["name"]):
        doc["limits"] = ctx.limits(node["name"])      # the router asks inside these; code still checks every request
    body = json.dumps(doc, sort_keys=True, default=str)
    return f"/{node['skill']} {body}" if node.get("skill") else body


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
        eff, deadline, slot = ctx.eff.agent(node), ctx.call_deadline(node, sc), {}
        fin = lambda *a, **kw: ctx.finish(node, entry, sc, *a, **kw)    # noqa: E731
        # A dispatch entry answers on the envelope's `allow.models`, led by the model the node names: a provider that
        # refuses to serve one entry must not end the node while the chain still holds an entry nobody tried.
        models = ctx.limits(node["name"])["models"] if ctx.is_dispatch_entry(node["name"]) else []
        hour = ctx.local_hour()
        chain = session.model_chain(models, eff["model"], ctx.spent_models, ctx.model_hours, hour)
        for m, why in session.hours_skipped(models, eff["model"], ctx.spent_models, ctx.model_hours, hour):    # an entry left out of its window is on the record
            ctx.record.append("model_skipped", node=node["name"], entry=entry, model=m, reason=why)
        out, tried, who = await session.run_chain(chain, lambda m, first: self.attempt(ctx, node, entry, sc, eff, deadline, m, slot, fin))
        for m, err in tried:    # every refusal is on the record, exhausted chain or not (#494)
            ctx.record.append("model_refused", node=node["name"], entry=entry, model=m, error=err[:session.CHAIN_LINE])
            limits.write(m, err)    # a refusal that names its reset closes that account until then (#572)
        if session.exhausted(chain, tried):    # every entry refused to serve: one fail, naming each entry and its error
            return fin("fail", ["adapter_error"], cap=out.res.amount, elapsed=out.elapsed,
                       diagnostic={"error": session.chain_message(tried)}, **session.error_fields(out.error), **who)
        if out.kind == "no_funds":
            return fin("fail", ["run_spend_cap"], spend=None, cap=None)
        if out.kind == "expired":
            return fin("deadline", ["deadline", "run_deadline"])
        if out.kind == "deadline":
            return slot["res"]
        if out.kind == "adapter_error":
            return fin("fail", session.adapter_reason(out.error), cap=out.res.amount, elapsed=out.elapsed,
                       diagnostic=session.adapter_diagnostic(out.error), **session.error_fields(out.error), **who)
        return self.settle(ctx, node, entry, sc, out, eff, who)

    async def attempt(self, ctx, node, entry, sc, eff, deadline, model, slot, fin):
        """One model's call for this node entry. The next entry of the chain answers after one that refused to serve."""
        who = {"model": model, "effort": eff["effort"]}      # the attempt's own model: it names the outcome and any stop

        def hit_deadline(res, el):
            slot.update(res=fin("deadline", ["deadline"], spend=None, cap=res.amount, elapsed=el, **who))

        def cancelled(res, el):
            return fin("fail", ["cancelled"], spend=None, cap=res.amount, elapsed=el, **who)

        def make(res):
            return CallRequest(node["name"], entry, sc.item, prompt(ctx, node, entry, sc), model, eff["effort"], eff["tools"],
                               eff["stalls_max"], res.amount, str(ctx.workspace_path(node.get("workspace"), sc.item)), deadline,
                               new_session_id(ctx.run_id), node.get("skill"), node.get("instructions"),
                               ctx.project_limits.get("max_output_tokens"), ctx.project_limits.get("context_tokens"))

        out = await session.run_call(
            lambda: ctx.budget.reserve(eff["spend_max"], node["budget"]["spend"]["min"]), deadline, make, adapter=ctx.adapter,
            record=ctx.record, budget=ctx.budget, clock=ctx.clock, wall=ctx.wall, on_deadline=hit_deadline, on_cancel=cancelled)
        return out, who

    def settle(self, ctx, node, entry, sc, out, eff, who):
        r, res = out.result, out.res
        outcome, reasons, diag_cap = session.agent_outcome(node, r, session.cap_reason(r.stop_reason, res.amount, eff["spend_max"]))
        rel = ctx.artifacts.put(node["name"], sc.item, entry, r.output, node.get("output_key")) if isinstance(r.output, dict) else None
        extra = {**who, **(r.usage or {}), **({"spend_source": r.spend_source} if r.spend_source else {})}
        diagnostic = {k: v for k, v in (("cap", diag_cap), ("subagents", r.subagents or None), ("session", r.session_id)) if v}
        if not isinstance(r.output, dict) and r.ended:      # an artifact_missing outcome says how the session ended
            diagnostic["ended"] = r.ended
        if diagnostic:
            extra["diagnostic"] = diagnostic
        ctx.commit_workspace(node, entry, sc, outcome)     # #84: the engine's commit, before the snapshot a resume compares against
        ws = ctx.ws_snapshot(node, sc)
        if ws:
            extra["workspace"] = ws
        apply_proposals(ctx, node, entry, sc, r)
        return ctx.finish(node, entry, sc, outcome, reasons, rel, spend=r.spend, turns=r.turns, cap=res.amount, elapsed=out.elapsed, **extra)
