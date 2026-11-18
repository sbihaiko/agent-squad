"""A spec's board column: a fold over the columns of its sub-issues, forward only (#550, #1131, #1906).

A spec is a document, never work (ADR-0142): while any of its tickets is in flight (Todo or beyond, before a terminal
column) its card reads Todo, and it never follows a ticket into Doing, Review or Code Complete. When no ticket is in
flight the children write no column: the spec's own issue state answers, `own` (Closed or Abandoned), and an open issue
answers None, so the card keeps the column the owner gave it. The children's terminal columns never give a spec a column;
the card follows the issue (ADR-0142 section 7, #1906). A card the fold itself lifted returns to the owner's column once no
ticket is in flight, because the lift is the loop's own write and never outlives its tickets: that return is the one
backward move ADR-0143 allows (#1034). The fold takes column names the pass already holds, so it makes no call. A spec
already in a terminal column keeps it when the fold would move it back (a child reopened, or its own issue still open) and
is tagged instead: the card never moves backwards. A parent that is not a spec still folds to Doing when a child reaches
it, and to Closed or Abandoned when every child is terminal: that roll-up is not a spec (#942).
"""
DOING, CLOSED, ABANDONED, TODO = "Doing", "Closed", "Abandoned", "Todo"
TAG = "reverted"   # the tag a card carries when its delivery was taken back (reconcile.py's TAG)
_CLOSED = {"Done", CLOSED}   # the shipped terminal column of a delivered issue, and the spec vocabulary's name for it
_REACHED_DOING = {"In Progress", "Review", "Code Complete", DOING} | _CLOSED   # the child columns from which the work is under way or past it
_IN_FLIGHT = {"Todo", "In Progress", "Review", "Code Complete", DOING}   # an open ticket the spec's lane reports: Todo or beyond
WORK = _REACHED_DOING - _CLOSED   # the work columns: a spec card in one of them is not work, and the fold returns it to the owner's column
_TERMINAL = _CLOSED | {ABANDONED}
_BACK = {None: 0, "Backlog": 0, TODO: 1, DOING: 2, CLOSED: 3, ABANDONED: 3}


def fold(children, current="", owner="", spec=False, todo=TODO, own=None):
    """(column, reverted) for a parent whose sub-issues sit in `children` (column names, "" for no card); column None is
    no write. `current` is the card's own column, and `owner` the column it returns to when the loop's lift is undone.
    `spec` marks a spec: its card reads `todo` while any ticket is in flight and never enters a work column (#1131).
    `own` is the column the spec's own issue state earns, Closed or Abandoned, or None while the issue is open: no
    sub-issue in flight, and the spec's own state answers, never the children's terminal columns (#1906)."""
    kids = list(children)
    if spec:
        column = todo if any(c in _IN_FLIGHT for c in kids) else own   # a spec reads Todo while a ticket is in flight, never the work column
    elif kids and all(c in _TERMINAL for c in kids):
        column = CLOSED if any(c in _CLOSED for c in kids) else ABANDONED
    elif any(c in _REACHED_DOING for c in kids):
        column = DOING
    else:
        column = None   # the card is below Doing: the spec keeps the owner's column
    if column is None and current in WORK and owner and owner not in _TERMINAL and _BACK.get(owner, 0) < _BACK[DOING]:
        column = owner   # no ticket is in flight: the loop undoes its own lift
    if current in (CLOSED, ABANDONED) and _BACK.get(column, 0) < _BACK[current]:
        return current, True
    return column, False
