"""The card limit of each board column: `columns.<column>.max_cards` in the project settings over the plugin defaults.

A limit is a whole number from 1 to 100, or `none` for no limit (`squad/project_settings.py` holds the parse). The
defaults follow the present machine limits, so the first release changes no behaviour: Backlog none, Todo none, Doing 6,
Reviewing 4, Batching none, Testing none. A column renamed by #2315 still answers under its old name (OLD), in a query and
in a settings file, so a caller or a file the rename has not reached keeps its limit.

This module answers a card limit only (#2430). A machine cap counts run slots, and a run carries one or more cards, so
the lower of the two numbers is a limit of neither: the pass that bounds a column reads the cap in its own unit.

The frontier, the review pass, the Settings page and the Kanban header read this one module, so the limit of a column
has one resolution.

Guard: `tests/test_board_limits.py`, `tests/test_hub_board_column_limits.py`.
"""
import argparse
import sys

from squad import defaults as D
from squad import project_settings as P

COLUMNS = P.COLUMN_CARDS
DEFAULTS = {"Backlog": None, "Todo": None, "Doing": 6, "Reviewing": 4, "Batching": None, "Testing": None}
OLD = P.OLD_COLUMN_CARDS   # the name each renamed column carried before #2315


def _applied(out, block):
    """`out` with each limit the block names: a limit the file leaves out keeps the value below it."""
    for name in COLUMNS:
        row = (block or {}).get(name)
        if row is None:   # a file the rename has not reached names the old column (#2315)
            row = (block or {}).get(OLD.get(name))
        if isinstance(row, dict) and "max_cards" in row:
            out[name] = row["max_cards"]
    return out


def limits(target="."):
    """Every column's limit: the plugin defaults, then the project settings file under `target`. A file that cannot be
    read leaves the defaults: a read that fails never raises a limit and never stops a pass."""
    out = _applied(dict(DEFAULTS), (D.load() or {}).get("columns"))
    try:
        data, _ = P.load(target)
    except (OSError, P.OptionError):    # an unreadable or unsound file leaves the defaults; it never raises a limit
        data = None
    return _applied(out, (data or {}).get("columns"))


def limit(name, target="."):
    """One column's limit, or None when it has none. An old column name answers its new column's limit (#2315)."""
    new = {old: new for new, old in OLD.items()}
    return limits(target).get(new.get(name, name))


# What a column's card limit stops, in the words of the pass that reads it (#2432). A column no pass reads yet says so,
# so every field the Settings screen draws states its effect.
COLUMN_STOP = {"Backlog": "the spec and ticket publish adds no card past this",
               "Doing": "the dispatch pass (autofrontier) claims no card past this",
               "Batching": "the dispatch pass (autofrontier) starts no run past this",
               "Reviewing": "the branch review pass starts no review past this"}
NO_STOP = "no pass reads it yet"


def column_stop(name):
    """The one pass that stops at a column's card limit, or the no-reader line for a column that has none yet."""
    return COLUMN_STOP.get(name) or NO_STOP


# The column a publish adds its card to: a spec and its tickets are born in Backlog (ADR-0142), and a bug is born in Todo
# (ADR-0148). A bug is startable by design, so no card limit ever holds it.
PUBLISH_COLUMN = {"spec": "Backlog", "ticket": "Backlog", "bug": "Todo"}


def publish_column(kind):
    """The column a publish of `kind` adds its card to."""
    return PUBLISH_COLUMN[kind]


def publish_refusal(kind, cards, target="."):
    """The refusal a publish reports when its column is at its limit, or None when the card may be added (#2436).

    A bug answers None whatever the limits: it is born in Todo and startable at once (ADR-0148). A column with no limit
    answers None. `cards` is what the column holds now, from the publisher's one board read.
    """
    if kind == "bug":
        return None
    col = publish_column(kind)
    lim = limit(col, target)
    if lim is None or cards < lim:
        return None
    return (f"column {col} full: {cards} of {lim} card(s) in {col}; the {kind} publish adds no card past this limit")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["refuse"]:   # the publisher's door (#2436): the refusal and exit 3, or exit 0 and no line
        ap = argparse.ArgumentParser(prog="board_limits refuse", description="Refuse a publish into a full column.")
        ap.add_argument("kind", choices=sorted(PUBLISH_COLUMN))
        ap.add_argument("cards", type=int)
        ap.add_argument("--target", default=".")
        a = ap.parse_args(argv[1:])
        why = publish_refusal(a.kind, a.cards, a.target)
        if why is None:
            return 0
        print(why)
        raise SystemExit(3)
    if argv[:1] == ["column"]:   # the column a publish adds its card to (#2436)
        ap = argparse.ArgumentParser(prog="board_limits column", description="Print the column a publish adds to.")
        ap.add_argument("kind", choices=sorted(PUBLISH_COLUMN))
        a = ap.parse_args(argv[1:])
        print(publish_column(a.kind))
        return 0
    ap = argparse.ArgumentParser(description="Print one board column's card limit.")
    ap.add_argument("column")
    ap.add_argument("--target", default=".")
    a = ap.parse_args(argv)
    value = limit(a.column, a.target)
    print("none" if value is None else value)
    return 0


if __name__ == "__main__":
    main()
