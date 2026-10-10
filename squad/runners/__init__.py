"""Node runners: one per node kind, behind one registry.

A runner is `async run(ctx, node, entry, sc) -> Res`. `ctx` exposes only what a runner uses. The engine
keeps the entry count, fast-forward, dry frontier, and `when` rules in `Engine.exec_node`, then looks the
runner up here by `node["kind"]`.
"""
from typing import Protocol

from squad.runners.action import ActionRunner
from squad.runners.agent import AgentRunner
from squad.runners.ctx import RunCtx
from squad.runners.dispatch import DispatchRunner
from squad.runners.each import EachRunner
from squad.runners.gate import GateRunner
from squad.runners.loop import LoopRunner
from squad.runners.wait import WaitRunner


class NodeRunner(Protocol):
    async def run(self, ctx, node, entry, sc): ...


def default_registry():
    """A fresh registry per engine, so a test can wrap one runner without touching another engine."""
    return {
        "gate": GateRunner(), "wait": WaitRunner(), "action": ActionRunner(),
        "agent": AgentRunner(), "loop": LoopRunner(),
        "each": EachRunner(), "dispatch": DispatchRunner(),
    }


__all__ = ["NodeRunner", "RunCtx", "default_registry"]
