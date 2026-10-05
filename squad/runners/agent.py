"""The agent runner: one fresh session behind the adapter seam, then its proposals (ADR-0102, ADR-0103, ADR-0108)."""
import json

import anyio

from squad import adapt, session
from squad.adapter import CallRequest, new_session_id


def prompt(ctx, node, entry, sc):
    inputs = {"ledger": ctx.ledger(node["name"])} if ctx.is_dispatch_entry(node["name"]) else ctx.artifacts.view(sc.item)   # a router reads the ledger, never a child's artifact (#90)
    doc = {"node": node["name"], "entry": entry, "inputs": inputs}
    if ctx.is_dispatch_entry(node["name"]):
        doc["limits"] = ctx.limits(node["name"])      # the router asks inside these; code still checks every request (ADR-0113)
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
    """Lift the proposals from an agent's output, validate each, and apply it to the overlay (ADR-0103 rules 6 to 8)."""
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
        eff, deadline = ctx.eff.agent(node), ctx.call_deadline(node, sc)
        who, slot = {"model": eff["model"], "effort": eff["effort"]}, {}
        fin = lambda *a, **kw: ctx.finish(node, entry, sc, *a, **kw)    # noqa: E731

        def make(res):
            return CallRequest(node["name"], entry, sc.item, prompt(ctx, node, entry, sc), eff["model"], eff["effort"], eff["tools"],
                               eff["turns_max"], res.amount, str(ctx.workspace_path(node.get("workspace"), sc.item)), deadline,
                               new_session_id(ctx.run_id), node.get("skill"), node.get("instructions"))

        out = await session.run_call(
            lambda: ctx.budget.reserve(eff["spend_max"], node["budget"]["spend"]["min"]), deadline, make, adapter=ctx.adapter,
            record=ctx.record, budget=ctx.budget, clock=ctx.clock, wall=ctx.wall,
            on_deadline=lambda res, el: slot.update(res=fin("deadline", ["deadline"], spend=None, cap=res.amount, elapsed=el, **who)),
            on_cancel=lambda res, el: fin("fail", ["cancelled"], spend=None, cap=res.amount, elapsed=el, **who))
        if out.kind == "no_funds":
            return fin("fail", ["run_spend_cap"], spend=None, cap=None)
        if out.kind == "expired":
            return fin("deadline", ["deadline", "run_deadline"])
        if out.kind == "deadline":
            return slot["res"]
        if out.kind == "adapter_error":
            return fin("fail", ["adapter_error"], spend=None, cap=out.res.amount, elapsed=out.elapsed, diagnostic={"error": str(out.error)}, **who)
        return self.settle(ctx, node, entry, sc, out, eff, who)

    def settle(self, ctx, node, entry, sc, out, eff, who):
        r, res = out.result, out.res
        outcome, reasons, diag_cap = session.agent_outcome(node, r, session.cap_reason(r.stop_reason, res.amount, eff["spend_max"]))
        rel = ctx.artifacts.put(node["name"], sc.item, entry, r.output, node.get("output_key")) if isinstance(r.output, dict) else None
        extra = {**who, **(r.usage or {})}
        diagnostic = {k: v for k, v in (("cap", diag_cap), ("subagents", r.subagents or None), ("session", r.session_id)) if v}
        if diagnostic:
            extra["diagnostic"] = diagnostic
        ws = ctx.ws_snapshot(node, sc)
        if ws:
            extra["workspace"] = ws
        apply_proposals(ctx, node, entry, sc, r)
        return ctx.finish(node, entry, sc, outcome, reasons, rel, spend=r.spend, turns=r.turns, cap=res.amount, elapsed=out.elapsed, **extra)
