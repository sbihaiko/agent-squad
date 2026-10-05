"""The each runner: run the children once per item, in parallel or in order, then join the item outcomes."""
import anyio

from squad import verdict as V
from squad.engine_errors import Frontier, Halt
from squad.runners.types import Res, Scope
from squad.runners.walk import walk_children


async def run_item(ctx, node, sc):
    child, word, res = await walk_children(ctx, node, sc)
    if word == "continue":
        return Res("pass", [])
    if word.startswith("exit:"):
        return Res(word[5:], res.reasons)
    return Res("fail", ["repeat_outside_loop"])


class EachRunner:
    async def run(self, ctx, node, entry, sc):
        n = ctx.facts.read(node["items"], sc.item)
        if n is None:
            return ctx.finish(node, entry, sc, "fail", [f"items_unavailable:{node['items']}"])
        items = list(range(1, n + 1)) if isinstance(n, int) else list(range(1, len(n) + 1))
        join_rule, completed, reasons, halts, frontier = node["join"], [], [], [], []
        maxp = min(node.get("max_parallel", ctx.run_cfg["max_parallel"]), ctx.run_cfg["max_parallel"])
        each_at = min(sc.deadline_at, ctx.clock() + node["deadline"] * ctx.scale)
        cancel = {}

        async def one(i):
            try:
                res = await run_item(ctx, node, Scope(i, each_at))
            except Halt as h:
                halts.append(h)
                return
            except Frontier as f:
                frontier.extend(f.positions)
                return
            completed.append(res.outcome)
            if res.outcome != "pass":
                reasons.extend(r for r in res.reasons if r not in reasons)
                if join_rule == "first_failure" and "scope" in cancel:
                    cancel["scope"].cancel()

        if node["mode"] == "parallel" and maxp > 1:
            limiter = anyio.CapacityLimiter(maxp)

            async def guarded(i):
                async with limiter:
                    await one(i)
            async with anyio.create_task_group() as tg:
                cancel["scope"] = tg.cancel_scope
                for i in items:
                    tg.start_soon(guarded, i)
        else:
            with anyio.CancelScope() as scope:
                cancel["scope"] = scope
                for i in items:
                    await one(i)
                    if join_rule == "first_failure" and completed and completed[-1] != "pass":
                        break
        if frontier:
            raise Frontier(frontier)
        if halts:
            raise halts[0]
        return ctx.finish(node, entry, sc, V.join(completed, join_rule), reasons)
