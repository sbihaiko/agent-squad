"""The board vocabulary, independent of the hub and GitHub clients.

One entry per column: the fact that sets it, the actor it waits on, whether it is terminal, and the board's own
order, flow position and colour. The tuple's own order is the colour map's order.
"""
import functools
import hashlib
import json
import sys
from pathlib import Path

COLUMNS = (
    {"name": "No status", "fact": "missing_status", "actor": "owner", "terminal": False, "order": 6, "flow": 3, "color": "#f5b041"},
    {"name": "Todo", "fact": "unclaimed", "actor": "dispatch", "terminal": False, "order": 3, "flow": 5, "color": "#c9d1d9"},
    {"name": "Open Specs", "fact": "spec_open", "actor": "owner", "terminal": False, "order": 4, "flow": 2, "color": "#f78fb3"},
    {"name": "In Progress", "fact": "claimed", "actor": "agent", "terminal": False, "order": 0, "flow": 6, "color": "#e8d44d"},
    {"name": "Code Complete", "fact": "wave_branch", "actor": "tester", "terminal": False, "order": 1, "flow": 7, "color": "#b087f9"},
    {"name": "Blocked", "fact": "blocked", "actor": "owner", "terminal": False, "order": 5, "flow": 4, "color": "#ff6b6b"},
    {"name": "Done", "fact": "issue_closed", "actor": "none", "terminal": True, "order": 7, "flow": 9, "color": "#3ecf8e"},
    {"name": "Open PRs", "fact": "pr_open", "actor": "reviewer", "terminal": False, "order": 8, "flow": 0, "color": "#5aa9ff"},
)

BY_FACT = {column["fact"]: column["name"] for column in COLUMNS}

# The shipped vocabulary (ADR-0141): a copy of the published board definition beside this file, pinned by hash, so an edit
# by hand is refused and a change in the published file is reported by `board_pin.py`, never adopted silently.
PIN_SHA256 = "d4a5577dcf5f6e0d47011c395d6e5557aa2fd3c30eb57e50558fc7c7d0eb68f0"
PINNED_COPY = Path(__file__).resolve().parent / "board-definition.json"
# What each old column is called in the shipped vocabulary. Blocked has no column: it is the owner freeze over the column
# the work reached, so it maps to None and the reconciler writes the marker instead.
SHIPPED_NAME = {"In Progress": "Doing", "Open Specs": "Backlog", "Done": "Closed", "Blocked": None, "Merging": "Code Complete", "Landing": "Code Complete"}
FREEZE_LABEL = {"owner": "frozen:owner", "gated": "frozen:dependency"}
CODE_REVIEW = "Code Review"   # the child's code is done, and its review or a fix round holds it (#603)


def _pinned_text():
    text = PINNED_COPY.read_text()
    if hashlib.sha256(text.encode()).hexdigest() != PIN_SHA256:
        raise ValueError(f"the pinned copy {PINNED_COPY.name} does not match its hash; restore it from the published definition")
    return text


CURRENT_DEFINITION_VERSION = 1   # the version this reader knows; a version it does not know is unknown, never guessed


class UnknownDefinition(Exception):
    """The definition states a version or a fact expression this reader does not know (#1901)."""


# The closed grammar (#1901): an expression is a fact name, `all`, `any` or `not`, and nothing else. A fact name must
# name one of the board's facts, so an expression the loop cannot evaluate is unknown, never read as false.
KNOWN_FACTS = frozenset({"abandoned", "closed", "in_dev", "gating", "wave_branch", "review_wait", "run_live",
                         "child_live", "spec", "owner", "gated"})


def parse_expression(value, column=None):
    """One expression of the grammar, parsed: a fact name, `("not", x)`, `("all", (x, ...))` or `("any", (x, ...))`.

    A value outside the grammar raises UnknownDefinition, named with the column it sits in. Nothing else is accepted: an
    `all` or `any` takes a non-empty list, `not` takes one expression, and a dict holds exactly one operator."""
    if isinstance(value, str):
        if value not in KNOWN_FACTS:
            raise UnknownDefinition(f"column {column!r}: the fact {value!r} is not one of the board's facts")
        return value
    if isinstance(value, dict) and len(value) == 1:
        op, arg = next(iter(value.items()))
        if op == "not":
            return ("not", parse_expression(arg, column))
        if op in ("all", "any") and isinstance(arg, list) and arg:
            return (op, tuple(parse_expression(x, column) for x in arg))
    raise UnknownDefinition(f"column {column!r}: {value!r} is outside the grammar (a fact name, all, any, not)")


def _evaluate(expression, facts):
    """True when one parsed expression holds for the facts; a fact the dict lacks is false."""
    if isinstance(expression, str):
        return bool(facts.get(expression))
    op, arg = expression
    if op == "not":
        return not _evaluate(arg, facts)
    if op == "all":
        return all(_evaluate(x, facts) for x in arg)
    return any(_evaluate(x, facts) for x in arg)


@functools.lru_cache(maxsize=8)
def _definition_from(text):
    """The columns one definition text names, each with its parsed `when`; UnknownDefinition when it is unknown.

    A file with no `definition_version` reads as the current version, with today's behaviour: only a version this reader
    does not know is unknown. An expression outside the grammar is unknown too, named with its column."""
    data = json.loads(text)
    version = data.get("definition_version", CURRENT_DEFINITION_VERSION)
    if version != CURRENT_DEFINITION_VERSION:
        raise UnknownDefinition(f"the definition version {version!r} is not version {CURRENT_DEFINITION_VERSION}, the one this reader knows")
    statuses = data.get("statuses") if isinstance(data, dict) else None
    if not isinstance(statuses, list) or not statuses:
        raise UnknownDefinition("the definition names no statuses")
    return tuple({"name": s.get("name"), "fact": s.get("fact"), "description": s.get("description", ""),
                  "actor": s.get("actor"), "terminal": bool(s.get("terminal")),
                  "when": parse_expression(s.get("fact"), s.get("name"))}
                 for s in statuses)


@functools.lru_cache(maxsize=1)
def _shipped_text():
    """The pinned copy, read and hashed once: a served board asks this rule for every card, and the copy never changes while
    a process runs. A hand edit is still refused, because the hash is checked before the read is cached."""
    return _pinned_text()


def shipped_definition(text=None):
    """The columns of the definition, each with its parsed `when` expression; the pinned copy when no text is given."""
    return _definition_from(text if text is not None else _shipped_text())


def shipped_columns(text=None):
    """The columns of the shipped definition, in its order: name, fact expression, description, actor, terminal."""
    return [{"name": s["name"], "fact": s["fact"], "description": s["description"], "actor": s["actor"], "terminal": s["terminal"]}
            for s in shipped_definition(text)]


_NOTED = set()   # the unknowns already named on stderr: a reader that meets the same one every card says it once


def _note_unknown(unknown):
    """One stderr line names an unknown definition: a reader never fails silently (#1901)."""
    if unknown in _NOTED: return
    _NOTED.add(unknown)
    print(f"board: {unknown}; the card keeps its last written column", file=sys.stderr)


def resolve_column(facts, written=None, text=None):
    """(column, freeze label or None, unknown name or None) for one card's facts: the one rule both carriers read (#1901).

    The column whose expression is true, and the freeze the facts name. Exactly one column answers; when none or more than
    one does, the reader guesses nothing. A definition version or an expression this reader does not know returns `written`,
    the card's last written column, and names the unknown. Facts are booleans, each missing one false."""
    facts = facts or {}
    freeze = FREEZE_LABEL["owner"] if facts.get("owner") else FREEZE_LABEL["gated"] if facts.get("gated") else None
    try:
        columns = shipped_definition(text)
    except UnknownDefinition as e:
        _note_unknown(str(e))
        return written, freeze, str(e)
    matched = [c["name"] for c in columns if _evaluate(c["when"], facts)]
    if len(matched) != 1:
        unknown = f"the definition names {len(matched)} columns ({', '.join(matched) or 'none'}) for one card; one column answers"
        _note_unknown(unknown)
        return written, freeze, unknown
    return matched[0], freeze, None


def pin_drift(published_text):
    """One line per difference between the published definition and the pinned copy: [] when they are the same bytes."""
    if hashlib.sha256(published_text.encode()).hexdigest() == PIN_SHA256: return []
    return [f"drift: the published definition differs from the pinned copy {PINNED_COPY.name}; review it, then replace the copy and its hash"]


def column_for(facts, written=None, text=None):
    """(column, freeze label or None) the shipped table names for one open or closed issue's facts.

    The column whose expression in the definition holds; a freeze never moves it (the definition's own rule). Facts are
    booleans, each missing one false: abandoned, closed, in_dev (the wave is in dev), gating (a gate is live for the wave,
    or its last attempt ended without a pass and none was made since: #1257), wave_branch, run_live, spec, child_live
    (a spec whose sub-issue is in flight), owner (an owner label), gated (another item gates it), and review_wait (the
    child's code is done and its review or a fix round holds it: Code Review, #603). The push, the approval and the dev CI
    are reported facts, never a column: a card in the wave holds Code Complete until its wave is in dev, and then it is
    Closed. `written` is the card's last written column, kept when the definition is unknown (#1901)."""
    return resolve_column(facts, written, text)[:2]


# The hub's view of the shipped definition (#247). The definition names a GitHub colour; the page needs a hex that reads
# at AA on the hub's chip. An open work branch is a lane beside the axis, never a column: the lane is named here, so the
# hub holds no name of its own. A role names the column a reader looks up by meaning, never by a literal.
#
# The open-PR lane stands beside the axis, never a column, and it is for tracking only (#384): no writer moves a card
# because of it and no gate reads it, which is what `tracking` records. A work branch has no lane at all: it is reached
# through the kinds view, under its own `branch` kind (#440). A landed branch is read from its issue card (Done = in dev).
HEX = {"GRAY": "var(--s8)", "BLUE": "var(--s1)", "YELLOW": "var(--wait)", "ORANGE": "var(--warn)",
       "PURPLE": "var(--none)", "PINK": "var(--s7)", "GREEN": "var(--ok)", "RED": "var(--err)"}
LANES = ({"key": "pr_open", "name": "Open PRs", "color": "var(--acc)", "tracking": True},)
ROLE = {"issue_closed": "Closed", "abandoned": "Abandoned", "unscheduled": "Backlog", "issue_todo": "Todo",
        "gating": "Gating"}
DOING = "Doing"   # the column a claimed card wears: the lane bar counts it as the amber share (owner, 2026-11-02)
SPEC_LABEL = "spec"


def claimed_names():
    """The names a claimed card may wear, as one `|`-joined line: the definition's own name first, then the shipped name (#1342)."""
    name = BY_FACT["claimed"]
    return "|".join(dict.fromkeys((name, SHIPPED_NAME.get(name, name))))


# A column whose GitHub colour another column already wears takes its own hex here: the hub draws one colour per column.
COLUMN_HEX = {CODE_REVIEW: "var(--s5)"}


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


UNCLAIM = (DOING, "Todo")   # the one backward move: the loop's dispatch undoes its own claim (ADR-0143)
HOLD = ("Backlog", "Todo")  # the owner's move that starts a card: no automated writer makes it, whatever the reason (ADR-0142, #1909)
REOPENED = "reopened"         # the reason an issue open again gives: its Closed card returns to Todo and no other column (#1420)
NO_LANDING = "no-landing"     # the reason a run that landed nothing gives: the column the loop set goes back to Todo (#1059)


def refused_for_automated(have, want, reason=None):
    """True when an automated writer may not move a card from `have` to `want`; a card with no column takes its first one.
    A run that lands nothing undoes the column the loop itself set: that unclaim reaches Todo and no other column (#1059)."""
    if not have: return False
    if (have, want) == HOLD: return True
    if (have, want) == UNCLAIM: return False
    if reason == NO_LANDING and want == "Todo": return False
    if reason == REOPENED and have == "Closed" and want == "Todo": return False   # the reopen is the door: the issue state is the owner's intent (ADR-0143 rule 2, #1420)
    return past(have, want)


# The close reason GitHub's issue carrier answers for an issue it closed without delivering it (#921): `state_reason`
# answers `completed`, `not_planned`, `duplicate` or `reopened`. `not_planned` and `duplicate` both say the issue was
# never delivered, so both name Abandoned (#517). The read parses no delivery out of `reopened`: a reopened issue is
# open, so no closed card carries that reason.
NOT_PLANNED = ("not_planned", "duplicate")


def not_planned(reason):
    """True when the carrier's close reason says the issue was not delivered (#921). The carrier answers lowercase and
    the GraphQL enum spells it uppercase, so the comparison folds the case either way."""
    return str(reason or "").strip().lower() in NOT_PLANNED


def terminal_column(state, reason=None):
    """The terminal column one issue's carrier facts earn, or None while it is open: Abandoned when the close reason says
    the issue was not delivered (spec #132), Closed for a completed close. The janitor's terminal rule and the reconciler
    both read this one function, so the two passes never disagree about where a closed issue's card belongs (#1932)."""
    if str(state or "").strip().lower() != "closed": return None
    return ROLE["abandoned"] if not_planned(reason) else ROLE["issue_closed"]


STATUS_FACT = {"Backlog": "spec", "Todo": "child_live", "Doing": "run_live", "Code Review": "review_wait",
               "Code Complete": "wave_branch", "Gating": "gating", "Closed": "closed", "Abandoned": "abandoned"}


def view_column(status, closed, labels=(), reason=None, text=None):
    """(column, freeze label or None) the hub draws one card in, from its Status (old or shipped name), state and labels.

    The written Status names the facts, and the column is the one the definition's expression answers (#1901): the hub read
    and the GitHub writer reach a column by the same rule, so no reader invents one. Blocked is no column: it is the owner
    freeze over the column the work reached, Backlog when the Status names none. A card with no Status is not scheduled, so
    it is Backlog. A closed card is Closed, unless its Status says Abandoned or its close reason is not planned, or
    duplicate: that card was not delivered, so it is Abandoned too (#921). A Status the definition does not ship, and an
    unknown definition, keep the written column, named: the reader never guesses one."""
    labels = set(labels or ())
    owner = status == "Blocked" or FREEZE_LABEL["owner"] in labels
    freeze = FREEZE_LABEL["owner"] if owner else FREEZE_LABEL["gated"] if FREEZE_LABEL["gated"] in labels else None
    name = SHIPPED_NAME.get(status, status) if status else None
    if closed:
        facts = {"abandoned": True} if (name == ROLE["abandoned"] or not_planned(reason)) else {"closed": True}
        column, _, _ = resolve_column(facts, written=name or ROLE["issue_closed"], text=text)
        return (column or ROLE["issue_closed"]), None
    if name in STATUS_FACT:
        facts = {STATUS_FACT[name]: True}
        if owner: facts["owner"] = True
        if FREEZE_LABEL["gated"] in labels: facts["gated"] = True
        column, _, _ = resolve_column(facts, written=name, text=text)
        return (column or name), freeze
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

