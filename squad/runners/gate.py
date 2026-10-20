"""The gate runner: a deterministic check over the artifacts and facts."""
import os

from squad.gates import GateInput, run_check


class GateRunner:
    async def run(self, ctx, node, entry, sc):
        t0 = ctx.clock()   # wall time of running the check
        # A gate can run for minutes (ac-verify runs a suite), so its start is recorded the way an agent call's is:
        # the dashboard reads it as in flight until the outcome (#680). `call_start` stays an agent call's own.
        ctx.record.append("gate_start", node=node["name"], entry=entry, item=sc.item, pid=os.getpid(),
                          limit=node.get("deadline"), started_at=ctx.wall())
        inp = GateInput(ctx.artifacts.view(sc.item), {n: f.value for (n, it), f in ctx.facts.values.items() if it in (None, sc.item)},
                        str(ctx.workspace_path(None, sc.item)))
        r = run_check(node["check"], node["params"], inp)
        rel = ctx.artifacts.put(node["name"], sc.item, entry, {"ok": r.ok, "findings": r.findings})
        extra = {"evidence": {f"{node['check']}:{k}": v for k, v in r.evidence.items()}} if r.evidence else {}
        if r.counts:     # the share of the suite each counting command printed (#80): the record holds it, no verdict reads it
            extra["counts"] = r.counts
        return ctx.finish(node, entry, sc, "pass" if r.ok else "fail", [] if r.ok else [f"{node['check']}:{f}" for f in r.findings] or [f"{node['check']}:fail"], rel, elapsed=ctx.clock() - t0, **extra)
