"""The gate runner: a deterministic check over the artifacts and facts."""
import os

from squad import gates
from squad.gates import GateInput, run_check


class GateRunner:
    async def run(self, ctx, node, entry, sc):
        t0 = ctx.clock()   # wall time of running the check
        # A gate can run for minutes (ac-verify runs a suite), so its start is recorded the way an agent call's is:
        # the dashboard reads it as in flight until the outcome (#680). `call_start` stays an agent call's own.
        ctx.record.append("gate_start", node=node["name"], entry=entry, item=sc.item, pid=os.getpid(),
                          limit=node.get("deadline"), started_at=ctx.wall())
        inp = GateInput(ctx.artifacts.view(sc.item), {n: f.value for (n, it), f in ctx.facts.values.items() if it in (None, sc.item)},
                        str(ctx.workspace_path(None, sc.item)), fix_run=ctx.fix_run)
        key, tree, hit = gates.memo(node["check"], node["params"], inp, inp.cwd, ctx.record.events)
        if hit is not None:
            # A stage of this run already proved this tree and suite: reuse that verdict, and say so. A silent skip is the
            # defect this replaces, so the record names the tree and the stage it reused (`gate_reuse`).
            ctx.record.append("gate_reuse", node=node["name"], entry=entry, item=sc.item, sha=tree[0], suite=key, source=hit["source"])
            rel = ctx.artifacts.put(node["name"], sc.item, entry, {"ok": True, "findings": [], "outcome": "pass"})
            return ctx.finish(node, entry, sc, "pass", [], rel, elapsed=ctx.clock() - t0, reused_from=hit["source"])
        r = run_check(node["check"], node["params"], inp)
        outcome = r.outcome    # pass, fail or unproven: a check that could not run measured nothing (ADR-0164, #1825)
        rel = ctx.artifacts.put(node["name"], sc.item, entry, {"ok": r.ok, "findings": r.findings, "outcome": outcome})
        if key and tree:     # keep this stage's verdict for a later stage of this run; a red is recorded and never reused
            ctx.record.append("gate_result", node=node["name"], entry=entry, item=sc.item, sha=tree[0], dirt=tree[1], suite=key,
                              outcome=outcome, source=f"{node['name']}.{entry}")
        extra = {"evidence": {f"{node['check']}:{k}": v for k, v in r.evidence.items()}} if r.evidence else {}
        if r.counts:     # the share of the suite each counting command printed (#80): the record holds it, no verdict reads it
            extra["counts"] = r.counts
        reasons = [] if outcome == "pass" else [f"{node['check']}:{f}" for f in r.findings] or [f"{node['check']}:{outcome}"]
        return ctx.finish(node, entry, sc, outcome, reasons, rel, elapsed=ctx.clock() - t0, **extra)
