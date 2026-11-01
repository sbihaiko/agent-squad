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
    {"name": "Code Complete", "fact": "wave_branch", "actor": "tester", "terminal": False, "order": 1, "flow": 7, "color": "#b087f9"},
    {"name": "Testing", "fact": "dev_ci", "actor": "tester", "terminal": False, "order": 2, "flow": 8, "color": "#2dd4bf"},
    {"name": "Blocked", "fact": "blocked", "actor": "owner", "terminal": False, "order": 5, "flow": 4, "color": "#ff6b6b"},
    {"name": "Done", "fact": "issue_closed", "actor": "none", "terminal": True, "order": 7, "flow": 9, "color": "#3ecf8e"},
    {"name": "Open PRs", "fact": "pr_open", "actor": "reviewer", "terminal": False, "order": 8, "flow": 0, "color": "#5aa9ff"},
)

BY_FACT = {column["fact"]: column["name"] for column in COLUMNS}

# The shipped vocabulary (ADR-0141): a copy of the published board definition beside this file, pinned by hash, so an edit
# by hand is refused and a change in the published file is reported by `board_pin.py`, never adopted silently.
PIN_SHA256 = "c10560aba624f76713860ac9286149e9d38dc84df1c43213a4b61ff39cefd114"
PINNED_COPY = Path(__file__).resolve().parent / "board-definition.json"
# What each old column is called in the shipped vocabulary. Blocked has no column: it is the owner freeze over the column
# the work reached, so it maps to None and the reconciler writes the marker instead.
SHIPPED_NAME = {"In Progress": "Doing", "Open Specs": "Backlog", "Done": "Closed", "Blocked": None, "Merging": "Pushing", "Landing": "Pushing"}
FREEZE_LABEL = {"owner": "frozen:owner", "gated": "frozen:dependency"}
CODE_REVIEW = "Code Review"   # the child's code is done, and its review or a fix round holds it (#603)


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
    each missing one false: abandoned, closed, dev_ci, landing, wave_branch, run_live, spec, child_live (a spec whose
    sub-issue is live work), owner (an owner label), gated (another item gates it), and review_wait (the child's code is
    done and its review or a fix round holds it: Code Review, #603)."""
    freeze = FREEZE_LABEL["owner"] if facts.get("owner") else FREEZE_LABEL["gated"] if facts.get("gated") else None
    if facts.get("abandoned"): return "Abandoned", freeze
    if facts.get("closed"): return "Closed", freeze
    if facts.get("dev_ci"): return "Testing", freeze
    if facts.get("landing"): return "Pushing", freeze
    if facts.get("wave_branch"): return "Code Complete", freeze
    if facts.get("review_wait"): return CODE_REVIEW, freeze
    if facts.get("run_live") or facts.get("child_live"): return "Doing", freeze
    # A spec is a document, never work (ADR-0142): the child_live fact above moves the spec's card, and never a dispatch.
    if facts.get("spec") or freeze: return "Backlog", freeze
    return "Todo", None


# The hub's view of the shipped definition (#247). The definition names a GitHub colour; the page needs a hex that reads
# at AA on the hub's chip. An open work branch is a lane beside the axis, never a column: the lane is named here, so the
# hub holds no name of its own. A role names the column a reader looks up by meaning, never by a literal.
#
# The open-PR lane stands beside the axis, never a column, and it is for tracking only (#384): no writer moves a card
# because of it and no gate reads it, which is what `tracking` records. A work branch has no lane at all: it is reached
# through the kinds view, under its own `branch` kind (#440). A landed branch is read from its issue card (Done = in dev).
HEX = {"GRAY": "#9aa4b1", "BLUE": "#79b8ff", "YELLOW": "#e8d44d", "ORANGE": "#f5b041", "PURPLE": "#b087f9",
       "PINK": "#f78fb3", "GREEN": "#3ecf8e", "RED": "#ff6b6b"}
LANES = ({"key": "pr_open", "name": "Open PRs", "color": "#5aa9ff", "tracking": True},)
ROLE = {"issue_closed": "Closed", "abandoned": "Abandoned", "unscheduled": "Backlog", "issue_todo": "Todo", "dev_ci": "Testing"}
SPEC_LABEL = "spec"


# A column whose GitHub colour another column already wears takes its own hex here: the hub draws one colour per column.
COLUMN_HEX = {CODE_REVIEW: "#fdba74"}


def view_columns():
    """The shipped columns in the definition's order, each with the hex the hub draws it in."""
    return [{"name": s["name"], "terminal": s["terminal"], "color": COLUMN_HEX.get(s["name"], HEX[s["color"]])}
            for s in json.loads(_pinned_text())["statuses"]]


def past(have, want):
    """True when the card's column `have` is further along the shipped axis than `want` (ADR-0143): a write of `want`
    would move the card back. An old name is read as its shipped name; a name the axis does not hold is never past."""
    order = [s["name"] for s in json.loads(_pinned_text())["statuses"]]
    have = SHIPPED_NAME.get(have, have) if have else have
    return have in order and want in order and order.index(have) > order.index(want)


# The close reason GitHub's issue carrier answers for an issue it closed without delivering it (#921): `state_reason`
# answers `completed`, `not_planned`, `duplicate` or `reopened`. `not_planned` and `duplicate` both say the issue was
# never delivered, so both name Abandoned (#517). The read parses no delivery out of `reopened`: a reopened issue is
# open, so no closed card carries that reason.
NOT_PLANNED = ("not_planned", "duplicate")


def not_planned(reason):
    """True when the carrier's close reason says the issue was not delivered (#921). The carrier answers lowercase and
    the GraphQL enum spells it uppercase, so the comparison folds the case either way."""
    return str(reason or "").strip().lower() in NOT_PLANNED


def view_column(status, closed, labels=(), reason=None):
    """(column, freeze label or None) the hub draws one card in, from its Status (old or shipped name), state and labels.

    Blocked is no column: it is the owner freeze over the column the work reached, Backlog when the Status names none.
    A card with no Status is not scheduled, so it is Backlog. A closed card is Closed, unless its Status says Abandoned
    or its close reason is not planned, or duplicate: that card was not delivered, so it is Abandoned too (#921)."""
    labels = set(labels or ())
    owner = status == "Blocked" or FREEZE_LABEL["owner"] in labels
    freeze = FREEZE_LABEL["owner"] if owner else FREEZE_LABEL["gated"] if FREEZE_LABEL["gated"] in labels else None
    name = SHIPPED_NAME.get(status, status) if status else None
    if closed:
        return (ROLE["abandoned"] if name == ROLE["abandoned"] or not_planned(reason) else ROLE["issue_closed"]), None
    return (name or ROLE["unscheduled"]), freeze


PR_KIND = "pr"
BRANCH_KIND = "branch"   # a work branch: no lane of its own, reached through the kinds view (#440)


def kind_label(kind):
    """The plural a kind's groups are named with: the kind's own words, never a list the hub keeps (`test case` -> Test Cases).

    The pull request kind wears the lane's own wording; a work branch wears the branch kind's own word."""
    if kind == PR_KIND: return "Open PRs"
    if kind == BRANCH_KIND: return "Branches"
    return kind.title() + "s"


def kind_groups(kinds, rows, default_repo=""):
    """One open and one closed group for each kind, in that order, named by the table.

    The lane kinds carry one open group and no closed one: the pull request lane is open PRs for tracking, and a work
    branch has no lane at all, so the kinds view is where a branch is reached (#384, #440). Every other kind takes the open
    and closed words. A row is `{"kd": kind, "closed": bool, "r": repository, ...}`, and its key is kind, repository and
    number, so two repositories sharing a number are two items. A row with no repository takes `default_repo`. A row of a
    kind not named here is not grouped: the pane draws what the kinds and the table name."""
    words = {"open": "Open", "closed": "Closed"}
    open_only = (PR_KIND, BRANCH_KIND)
    names = {PR_KIND: {"open": LANES[0]["name"]}, BRANCH_KIND: {"open": kind_label(BRANCH_KIND)}}
    out = []
    for kind in (PR_KIND, BRANCH_KIND, *kinds):
        for state in ("open", "closed"):
            if kind in open_only and state == "closed": continue
            items = [{**{k: v for k, v in r.items() if k not in ("kd", "closed")}, "k": kind,
                      "key": f"{kind}:{r.get('r') or default_repo}#{r['n']}"}
                     for r in rows if r.get("kd") == kind and bool(r.get("closed")) == (state == "closed")]
            name = names.get(kind, {}).get(state) or f"{words[state]} {kind_label(kind)}"
            out.append({"kind": kind, "state": state, "name": name, "n": len(items), "items": items})
    return out


OUTCOME_LABEL = "outcome:"
OUTCOME_ROLE = {"merged": "Merged", "abandoned": "Abandoned"}   # the outcome a merged and a given-up item take; both are entries of the set


def shipped_outcomes():
    """The outcomes of the shipped definition, in its order: the one closed set a filter or a gap is judged against."""
    return [o["name"] for o in json.loads(_pinned_text())["outcomes"]]


def outcome_of(closed, merged=None, column=None, labels=()):
    """The outcome one closed item carries, None for an open one.

    An `outcome:<name>` label says it outright (a failed test, a reverted change). Otherwise a pull request is merged or not
    (`merged` is set only for a pull request), and an issue is abandoned when its column says so and merged into main when
    it is Closed. A name the set does not hold is returned as written, so the caller reports it as a gap."""
    if not closed: return None
    named = {o.lower(): o for o in shipped_outcomes()}
    for l in labels or ():
        if l.startswith(OUTCOME_LABEL):
            word = l[len(OUTCOME_LABEL):]
            return named.get(word.lower(), word)
    if merged is not None: return OUTCOME_ROLE["merged"] if merged else OUTCOME_ROLE["abandoned"]
    return OUTCOME_ROLE["abandoned"] if column == ROLE["abandoned"] else OUTCOME_ROLE["merged"]


def filter_groups(groups, kind=None, outcome=None):
    """The kind groups the reader's filters keep. A kind keeps that kind's groups; an outcome keeps the closed items with it.

    An outcome the set does not name raises: a filter is never made from a value the definition does not hold."""
    if outcome is not None and outcome not in shipped_outcomes():
        raise ValueError(f"{outcome!r} is not an outcome of the shipped definition")
    out = []
    for g in groups:
        if kind is not None and g["kind"] != kind: continue
        if outcome is not None:
            if g["state"] != "closed": continue
            items = [x for x in g["items"] if x.get("oc") == outcome]
            g = {**g, "items": items, "n": len(items)}
        out.append(g)
    return out


def outcome_gaps(groups):
    """The closed items whose outcome the set does not name, as `{"key", "oc"}`: reported, never turned into a filter."""
    known = set(shipped_outcomes())
    return [{"key": x["key"], "oc": x.get("oc")} for g in groups if g["state"] == "closed" for x in g["items"] if x.get("oc") not in known]


# Every terminal entry carries its condition in text on the issue (spec #132, #245). The tag is what a later pass reads to
# know the comment is there, so a settled card is backfilled once and never twice.
TERMINAL = ("Closed", "Abandoned")
TERMINAL_TAG = "<!-- squad-goal:terminal -->"


def terminal_comment(column, facts):
    """The comment a move into a terminal column writes, or None when the facts do not state the condition.

    None is the guard: the caller makes no terminal write it cannot explain. Closed facts: base, and branch with sha and test (the checks on the branch tip that is in the base), or pr with sha and test
    (the checks on the merge commit, "" when none ran), or closed_by when no merged pull request names the issue.
    Abandoned facts: who decided and why."""
    facts = facts or {}
    if column == "Closed" and facts.get("branch"):
        sha = str(facts.get("sha") or "")[:7]
        test = facts.get("test") or (f"no check ran on {sha}" if sha else "no tip to test")
        body = f"Closed: branch {facts['branch']} is in {facts.get('base') or 'the base'} and carries this issue (tip {sha or 'unknown'}). Test on the tip: {test}."
    elif column == "Closed" and facts.get("pr"):
        sha = str(facts.get("sha") or "")[:7]
        test = facts.get("test") or (f"no check ran on merge commit {sha}" if sha else "no merge commit to test")
        body = f"Closed: pull request #{facts['pr']} merged into {facts.get('base') or 'the base'} carries this issue (merge commit {sha or 'unknown'}). Test on the merge: {test}."
    elif column == "Closed" and not_planned(facts.get("reason")):
        return None   # it was not delivered: "completed" would be false, and the card belongs in Abandoned (#517)
    elif column == "Closed" and facts.get("closed_by"):
        body = f"Closed: @{facts['closed_by']} closed it as completed; no merged pull request into {facts.get('base') or 'the base'} names it."
    elif column == "Abandoned" and facts.get("who") and facts.get("why"):
        body = f"Abandoned: {facts['why']}, decided by @{facts['who']}."
    else:
        return None
    return f"{TERMINAL_TAG}\n{body}\n"

