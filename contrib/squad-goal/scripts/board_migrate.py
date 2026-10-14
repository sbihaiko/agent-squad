#!/usr/bin/env python3
"""The migration planner: today's cards move onto the shipped vocabulary, then the old Status options go (ADR-0143).

usage: board_migrate.py --plan  <items.json>   a dry run: every card move and every option removal; nothing is written
       board_migrate.py --check <items.json>   exit 3, with the counts, while a card carries an old option

The items are the list the board cache holds: n (issue), st (Status option), s (state), l (labels), and optionally facts
(what board_columns.column_for reads, to place a Blocked card where its work reached). The target of every move comes from
board_columns, never from a table here. This script has no write mode: the applier lives with the board definition (ADR-0141).
"""
import json
import sys

from board_columns import FREEZE_LABEL, SHIPPED_NAME, SPEC_LABEL, column_for, view_column

OLD_OPTIONS = tuple(SHIPPED_NAME)
REFUSED = 3


def _target(item):
    """(column, labels to add) for one card on an old option."""
    labels = list(item.get("l") or [])
    column, _ = view_column(item.get("st"), str(item.get("s")).lower() == "closed", labels)
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
        if item.get("st") in OLD_OPTIONS:
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
