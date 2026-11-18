"""The card limit of each board column: `columns.<column>.max_cards` in the project settings over the plugin defaults.

A limit is a whole number from 1 to 100, or `none` for no limit (`squad/project_settings.py` holds the parse). The
defaults follow the present machine limits, so the first release changes no behaviour: Todo none, Doing 6, Review 4,
Landing none, Gating none. MACHINE names the machine cap that also bounds a column, and `effective` answers the lower of
the two: a column limit never raises a machine cap.

The frontier, the review pass, the Settings page and the Kanban header read this one module, so the limit of a column
has one resolution.

Guard: `tests/test_board_limits.py`, `tests/test_hub_board_column_limits.py`.
"""
import argparse

from squad import defaults as D
from squad import project_settings as P

COLUMNS = P.COLUMN_CARDS
DEFAULTS = {"Todo": None, "Doing": 6, "Review": 4, "Landing": None, "Gating": None}
# The caps entry that also bounds a column: a review column never holds more reviews than the machine allows.
MACHINE = {"Review": "reviews", "Gating": "gates"}


def _applied(out, block):
    """`out` with each limit the block names: a limit the file leaves out keeps the value below it."""
    for name in COLUMNS:
        row = (block or {}).get(name)
        if isinstance(row, dict) and "max_cards" in row:
            out[name] = row["max_cards"]
    return out


def limits(target="."):
    """Every column's limit: the plugin defaults, then the project settings file under `target`. A file that cannot be
    read leaves the defaults: a read that fails never raises a limit and never stops a pass."""
    out = _applied(dict(DEFAULTS), (D.load() or {}).get("columns"))
    try:
        data, _ = P.load(target)
    except Exception:
        data = None
    return _applied(out, (data or {}).get("columns"))


def limit(name, target="."):
    """One column's limit, or None when it has none."""
    return limits(target).get(name)


def effective(column_limit, machine_cap):
    """The tighter of a column limit and the machine cap that bounds it. None on both is no bound."""
    values = [v for v in (column_limit, machine_cap) if v is not None]
    return min(values) if values else None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Print one board column's card limit.")
    ap.add_argument("column")
    ap.add_argument("--target", default=".")
    a = ap.parse_args(argv)
    value = limit(a.column, a.target)
    print("none" if value is None else value)


if __name__ == "__main__":
    main()
