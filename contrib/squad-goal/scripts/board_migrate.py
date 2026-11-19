#!/usr/bin/env python3
"""The migration planner: today's cards move onto the shipped vocabulary, then the old Status options go (ADR-0143).

usage: board_migrate.py --plan  <items.json>   a dry run: every card move and every option removal; nothing is written
       board_migrate.py --check <items.json>   exit 3, with the counts, while a card carries an old option

A moved option has a shipped column that takes its cards. A retired option (Pushing) has none: its cards are never moved,
and a card still on one refuses the removal by name, so no card loses the value it carries. A renamed option (RENAMES)
is neither: board_define.py --rename renames it in place with its own id, so its cards keep their column (#2154, #2315).

The items are the list the board cache holds: n (issue), st (Status option), s (state), l (labels), cr (the close reason), and optionally facts
(what board_columns.column_for reads, to place a Blocked card where its work reached). The target of every move comes from
board_columns, never from a table here. This script has no write mode: the applier lives with the board definition (ADR-0141).
"""
import json
import sys

from board_columns import ALIAS_COLUMNS, V1_NAMES, FREEZE_LABEL, SHIPPED_NAME, SPEC_LABEL, canonical, column_for, view_column

MOVED_OPTIONS = tuple(SHIPPED_NAME)   # an old option with a shipped column to take its cards
RETIRED_OPTIONS = ("Pushing",)   # the definition no longer names it, and no column takes its cards; Testing is a column again (#2315)
OLD_OPTIONS = MOVED_OPTIONS + RETIRED_OPTIONS
REFUSED = 3
# The options the shipped definition renames in place (#2154, #2315): each old name, version 1 or version 2, and the
# canonical one. The map is read from the version 1 table and the one alias map, never written a second time (#2190).
RENAMES = {**{old: canonical(new) for old, new in V1_NAMES.items()},
           **{old: entry["name"] for entry in ALIAS_COLUMNS for old in entry["old"]}}


def clashes(options):
    """The canonical names a rename would give two options: the field would carry one name twice, so the rename refuses."""
    names = [RENAMES.get(o["name"], o["name"]) for o in options]
    return sorted({n for n in names if names.count(n) > 1})


def renamed(options):
    """(every option of the field with its own id under its canonical name, True when one name changed).

    One call carries every option, so an option sent with its id keeps that id and every card keeps its column. A board
    that already carries the canonical names comes back unchanged, so a second call changes nothing."""
    out, changed = [], False
    for o in options:
        name = RENAMES.get(o["name"], o["name"])
        changed = changed or name != o["name"]
        out.append({**o, "name": name})
    return out, changed


def _target(item):
    """(column, labels to add) for one card on an old option."""
    labels = list(item.get("l") or [])
    column, _ = view_column(item.get("st"), str(item.get("s")).lower() == "closed", labels, item.get("cr"))
    add = []
    if item.get("st") == "Blocked":
        if item.get("facts") and column != "Closed": column = column_for(item["facts"])[0]   # the column the work reached
        add = [FREEZE_LABEL["owner"]]
    elif item.get("st") == "Open Specs":
        add = [SPEC_LABEL]
    return column, [a for a in add if a not in labels]


def carried(items):
    """The count of cards on each old option: {} when none carries one."""
    counts = {o: sum(1 for i in items if i.get("st") == o) for o in OLD_OPTIONS}
    return {o: n for o, n in counts.items() if n}


def plan(items):
    """The dry-run plan: every move of a card on an old option, then each option's removal with the cards still on it
    once the moves are made. A card on a shipped column is never moved."""
    moves = []
    for item in items:
        if item.get("st") in MOVED_OPTIONS:
            column, add = _target(item)
            moves.append({"card": item["n"], "old": item["st"], "new": column, "add_labels": add})
    moved = {m["card"] for m in moves}
    left = carried([i for i in items if i["n"] not in moved])
    return {"moves": moves, "removals": [{"option": o, "remaining": left.get(o, 0)} for o in OLD_OPTIONS]}


def refusal(counts):
    """The refusal text, with the counts per option (ADR-0141)."""
    return "refused: a card still carries an old option; move it first. " + ", ".join(f"{o}: {n}" for o, n in counts.items())


def render(p):
    lines = ["dry run: nothing is written"]
    lines += [f"MOVE #{m['card']} {m['old']} -> {m['new']}" + "".join(f" +{a}" for a in m["add_labels"]) for m in p["moves"]]
    for r in p["removals"]:
        lines.append(f"REMOVE option {r['option']}" if not r["remaining"] else f"REFUSE option {r['option']}: {r['remaining']} card(s) left")
    return "\n".join(lines)


def main(argv):
    if len(argv) != 2 or argv[0] not in ("--plan", "--check"): sys.exit(__doc__)
    items = json.load(open(argv[1]))
    if argv[0] == "--plan":
        print(render(plan(items)))
        return 0
    counts = carried(items)
    if counts:
        print(refusal(counts), file=sys.stderr)
        return REFUSED
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
