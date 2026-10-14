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


# The hub's view of the shipped definition (#247). The definition names a GitHub colour; the page needs a hex that reads
# at AA on the hub's chip. A pull request is a lane beside the axis, never a column: the lanes are named here, so the hub
# holds no name of its own. A role names the column a reader looks up by meaning, never by a literal.
HEX = {"GRAY": "#9aa4b1", "BLUE": "#79b8ff", "YELLOW": "#e8d44d", "ORANGE": "#f5b041", "PURPLE": "#b087f9",
       "PINK": "#f78fb3", "GREEN": "#3ecf8e", "RED": "#ff6b6b"}
LANES = ({"key": "pr_open", "name": "Open PRs", "color": "#5aa9ff"},
         {"key": "pr_closed", "name": "Closed PRs", "color": "#8b9bb3"})
ROLE = {"issue_closed": "Closed", "abandoned": "Abandoned", "unscheduled": "Backlog", "merged": "Testing"}
SPEC_LABEL = "spec"


def view_columns():
    """The shipped columns in the definition's order, each with the hex the hub draws it in."""
    return [{"name": s["name"], "terminal": s["terminal"], "color": HEX[s["color"]]}
            for s in json.loads(_pinned_text())["statuses"]]


def view_column(status, closed, labels=()):
    """(column, freeze label or None) the hub draws one card in, from its Status (old or shipped name), state and labels.

    Blocked is no column: it is the owner freeze over the column the work reached, Backlog when the Status names none.
    A card with no Status is not scheduled, so it is Backlog. A closed card is Closed, unless its Status says Abandoned."""
    labels = set(labels or ())
    owner = status == "Blocked" or FREEZE_LABEL["owner"] in labels
    freeze = FREEZE_LABEL["owner"] if owner else FREEZE_LABEL["gated"] if FREEZE_LABEL["gated"] in labels else None
    name = SHIPPED_NAME.get(status, status) if status else None
    if closed: return (name if name == ROLE["abandoned"] else ROLE["issue_closed"]), None
    return (name or ROLE["unscheduled"]), freeze
