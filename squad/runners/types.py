"""The two small values every runner passes: a node's result and the scope it runs in."""
from dataclasses import dataclass, field


@dataclass
class Res:
    outcome: str
    reasons: list = field(default_factory=list)
    word: str | None = None     # the word the node routed to, when the engine overrode the node's `next` (the dispatch stop word)


@dataclass
class Scope:
    item: int | None
    deadline_at: float
