"""The board vocabulary, independent of the hub and GitHub clients.

One entry per column: the fact that sets it, the actor it waits on, whether it is terminal, and the board's own
order, flow position and colour. The tuple's own order is the colour map's order.
"""
import hashlib
import json
from pathlib import Path

COLUMNS = (
    {"name": "No status", "fact": "missing_status", "actor": "owner", "terminal": False, "order": 6, "flow": 3, "color": "#f5b041"},
    {"name": "Todo", "fact": "unclaimed", "actor": "dispatch", "terminal": False, "order": 3, "flow": 5, "color": "#c9d1d9"},
    {"name": "Open Specs", "fact": "spec_open", "actor": "owner", "terminal": False, "order": 4, "flow": 2, "color": "#f78fb3"},
    {"name": "In Progress", "fact": "claimed", "actor": "agent", "terminal": False, "order": 0, "flow": 6, "color": "#e8d44d"},
    {"name": "Code Complete", "fact": "open_pr", "actor": "tester", "terminal": False, "order": 1, "flow": 7, "color": "#b087f9"},
    {"name": "Testing", "fact": "merged", "actor": "tester", "terminal": False, "order": 2, "flow": 8, "color": "#2dd4bf"},
    {"name": "Blocked", "fact": "blocked", "actor": "owner", "terminal": False, "order": 5, "flow": 4, "color": "#ff6b6b"},
    {"name": "Done", "fact": "issue_closed", "actor": "none", "terminal": True, "order": 7, "flow": 9, "color": "#3ecf8e"},
    {"name": "Open PRs", "fact": "pr_open", "actor": "reviewer", "terminal": False, "order": 8, "flow": 0, "color": "#5aa9ff"},
    {"name": "Closed PRs", "fact": "pr_closed", "actor": "none", "terminal": True, "order": 9, "flow": 1, "color": "#8b9bb3"},
)

BY_FACT = {column["fact"]: column["name"] for column in COLUMNS}

# The shipped vocabulary (ADR-0141): a copy of the published board definition beside this file, pinned by hash, so an edit
# by hand is refused and a change in the published file is reported by `board_pin.py`, never adopted silently.
PIN_SHA256 = "868f8c99fdf44a50e6fef618a01d88ba1369456917b5691192c6a1892d41f69e"
PINNED_COPY = Path(__file__).resolve().parent / "board-definition.json"
# What each old column is called in the shipped vocabulary. Blocked has no column: it is the owner freeze over the column
# the work reached, so it maps to None and the reconciler writes the marker instead.
SHIPPED_NAME = {"In Progress": "Doing", "Open Specs": "Backlog", "Done": "Closed", "Blocked": None}
FREEZE_LABEL = {"owner": "frozen:owner", "gated": "frozen:dependency"}


def _pinned_text():
    text = PINNED_COPY.read_text()
    if hashlib.sha256(text.encode()).hexdigest() != PIN_SHA256:
        raise ValueError(f"the pinned copy {PINNED_COPY.name} does not match its hash; restore it from the published definition")
    return text


def shipped_columns():
    """The columns of the shipped definition, in its order: name, fact, actor, terminal."""
    return [{"name": s["name"], "fact": s["fact"], "actor": s["actor"], "terminal": s["terminal"]}
            for s in json.loads(_pinned_text())["statuses"]]


def pin_drift(published_text):
    """One line per difference between the published definition and the pinned copy: [] when they are the same bytes."""
    if hashlib.sha256(published_text.encode()).hexdigest() == PIN_SHA256: return []
    return [f"drift: the published definition differs from the pinned copy {PINNED_COPY.name}; review it, then replace the copy and its hash"]


def column_for(facts):
    """(column, freeze label or None) the shipped table names for one open or closed issue's facts.

    The furthest the work reached sets the column; a freeze never moves it (the definition's own rule). Facts are booleans,
    each missing one false: abandoned, closed, merged (a PR naming the issue merged into the base), pr_open, pr_green (that
    PR is mergeable and unheld), run_live, spec, owner (an owner label), gated (another item gates it)."""
    freeze = FREEZE_LABEL["owner"] if facts.get("owner") else FREEZE_LABEL["gated"] if facts.get("gated") else None
    if facts.get("abandoned"): return "Abandoned", freeze
    if facts.get("closed"): return "Closed", freeze
    if facts.get("merged"): return "Testing", freeze
    if facts.get("pr_open"): return ("Merging" if facts.get("pr_green") else "Code Complete"), freeze
    if facts.get("run_live"): return "Doing", freeze
    if facts.get("spec") or freeze: return "Backlog", freeze
    return "Todo", None
