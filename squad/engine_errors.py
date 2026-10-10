"""The engine's control-flow exceptions, apart from the engine so runners can raise them."""


class Halt(Exception):
    """The run ends here without an exit event for the root: a wait holds, or `--stop-after` fired."""

    def __init__(self, kind, node):
        super().__init__(f"{kind} at {node}")
        self.kind, self.node = kind, node


class Frontier(Exception):
    """A dry walk reached nodes that still need to run. `positions` is a list of (node, item)."""

    def __init__(self, positions):
        super().__init__(", ".join(f"{n}#{i}" if i is not None else n for n, i in positions))
        self.positions = list(positions)


class ResumeRefused(Exception):
    """A resume that the record or the graph forbids. No event is written."""


class Defect(Exception):
    """The engine or its inputs are wrong. The run ends failed."""
