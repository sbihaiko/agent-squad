"""The wait runner: halt for the owner, then pass, fail, or apply the default at the deadline (ADR-0107)."""
from squad.engine_errors import Halt


class WaitRunner:
    async def run(self, ctx, node, entry, sc):
        name, key = node["name"], (node["name"], sc.item)
        w = ctx.waits.get(key)
        if w is None:
            fields = dict(node=name, owner=node["owner"], deadline_at=ctx.wall() + node["deadline"], default=node["default"])
            if sc.item is not None:
                fields["item"] = sc.item
            ctx.waits[key] = ctx.record.append("wait", **fields)
            raise Halt("wait", name)
        if ctx.answer in ("pass", "fail"):
            outcome, reasons = ctx.answer, ["answered"]
        elif ctx.wall() >= w["deadline_at"]:
            outcome, reasons = w["default"], ["default_applied"]
            ctx.default_applied.append(name)
        else:
            raise Halt("wait", name)
        return ctx.finish(node, entry, sc, outcome, reasons)
