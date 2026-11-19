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
COLUMN_STOP = {"Doing": "the dispatch pass (autofrontier) claims no card past this",
               "Todo": "the board write refuses a card that leaves Backlog for Todo past this",
               "Reviewing": "the branch review pass starts no review past this"}
NO_STOP = "no pass reads it yet"


def column_stop(name):
    """The one pass that stops at a column's card limit, or the no-reader line for a column that has none yet."""
    return COLUMN_STOP.get(name) or NO_STOP


def main(argv=None):
    ap = argparse.ArgumentParser(description="Print one board column's card limit.")
    ap.add_argument("column")
    ap.add_argument("--target", default=".")
    a = ap.parse_args(argv)
    value = limit(a.column, a.target)
    print("none" if value is None else value)


if __name__ == "__main__":
    main()
