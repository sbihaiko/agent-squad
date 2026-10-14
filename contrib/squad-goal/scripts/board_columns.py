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


# Every terminal entry carries its condition in text on the issue (spec #132, #245). The tag is what a later pass reads to
# know the comment is there, so a settled card is backfilled once and never twice.
TERMINAL = ("Closed", "Abandoned")
TERMINAL_TAG = "<!-- squad-goal:terminal -->"


def terminal_comment(column, facts):
    """The comment a move into a terminal column writes, or None when the facts do not state the condition.

    None is the guard: the caller makes no terminal write it cannot explain. Closed facts: base, and pr with sha and test
    (the checks on the merge commit, "" when none ran), or closed_by when no merged pull request names the issue.
    Abandoned facts: who decided and why."""
    facts = facts or {}
    if column == "Closed" and facts.get("pr"):
        sha = str(facts.get("sha") or "")[:7]
        test = facts.get("test") or (f"no check ran on merge commit {sha}" if sha else "no merge commit to test")
        body = f"Closed: pull request #{facts['pr']} merged into {facts.get('base') or 'the base'} carries this issue (merge commit {sha or 'unknown'}). Test on the merge: {test}."
    elif column == "Closed" and facts.get("closed_by"):
        body = f"Closed: @{facts['closed_by']} closed it as completed; no merged pull request into {facts.get('base') or 'the base'} names it."
    elif column == "Abandoned" and facts.get("who") and facts.get("why"):
        body = f"Abandoned: {facts['why']}, decided by @{facts['who']}."
    else:
        return None
    return f"{TERMINAL_TAG}\n{body}\n"


# A pull request's state, from the loop's own review line for its current head (revci.sh prints `#<pr> <sha9> <mergeable_state>
# CI=<conclusion:count,...> <verdict>`), so the column costs no read of its own (#244).
PR_RED_BADGE = "pr:red"   # a Merging card whose PR turned red keeps its column and carries this label: a marker, never a move back
PR_HOLD_LABELS = {"hold", "needs-human"}   # the labels autopilot.sh holds a PR on
RED_CHECKS = ("failure", "timed_out", "cancelled", "action_required", "startup_failure")


def pr_green(line, labels):
    """True when the review line says the PR is green, mergeable and unheld: GitHub calls it clean, every check finished
    and none red, the review approved, and no hold label. A missing line is no review given, so not green."""
    f = (line or "").split()
    if len(f) < 4 or not f[3].startswith("CI="): return False
    checks = f[3][3:]
    if f[2] != "clean" or checks in ("", "none") or any(c in checks for c in RED_CHECKS): return False
    return "APPROVE" in " ".join(f[4:]) and not (set(labels) & PR_HOLD_LABELS)


def pr_column(green, have):
    """(column, badge or None) for an issue an open PR names: green -> Merging; not green -> Code Complete, except a card
    already in Merging, which keeps its column and carries the badge (a card never moves back)."""
    if green: return "Merging", None
    if have == "Merging": return "Merging", PR_RED_BADGE
    return "Code Complete", None
