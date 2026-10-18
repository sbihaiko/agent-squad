"""A spec's board column: a fold over the columns of its sub-issues, forward only (#550).

Doing once any child has reached Doing or gone past it (a delivered child counts) while another is still open. Closed when
every child is terminal and at least one is Closed. Abandoned when every child is terminal and none is Closed. Below Doing
the fold writes nothing (None): the spec keeps the column the owner gave it, Backlog or Todo, since only the owner moves a
card there (ADR-0142, ADR-0143). The fold takes column names the pass already holds, so it makes no call. A spec already
in a terminal column keeps it when the fold would move it back (a child reopened) and is tagged instead: the card never
moves backwards.
"""
DOING, CLOSED, ABANDONED = "Doing", "Closed", "Abandoned"
TAG = "reverted"   # the tag a card carries when its delivery was taken back (reconcile.py's TAG)
_CLOSED = {"Done", CLOSED}   # the shipped terminal column of a delivered issue, and the spec vocabulary's name for it
_REACHED_DOING = {"In Progress", "Code Complete", "Landing", "Merging", "Pushing", "Testing", DOING} | _CLOSED   # the child columns from which the work is under way or past it
_TERMINAL = _CLOSED | {ABANDONED}
_BACK = {None: 0, DOING: 1, CLOSED: 2, ABANDONED: 2}


def fold(children, current=""):
    """(column, reverted) for a spec whose sub-issues sit in `children` (column names, "" for no card); column None is no write."""
    kids = list(children)
    if kids and all(c in _TERMINAL for c in kids):
        column = CLOSED if any(c in _CLOSED for c in kids) else ABANDONED
    elif any(c in _REACHED_DOING for c in kids):
        column = DOING
    else:
        column = None   # no child reached Doing: the spec keeps the owner's column
    if current in (CLOSED, ABANDONED) and _BACK[column] < _BACK[current]:
        return current, True
    return column, False
