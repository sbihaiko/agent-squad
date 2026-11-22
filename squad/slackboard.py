"""The board rules of one `approve_and_move` request (SPEC 2682, stories 26 and 67; #2706).

A role holder approves the review of a card and moves it between columns after Todo. Three rules bound the action, and
this module is their one home:

- a card moves only forward along the board axis, so a move back is refused (ADR-0143);
- a card moves only between columns after Todo, because Todo is the owner's go signal and starting work stays with the
  owner (ADR-0142);
- the action never touches a spec and never approves a breakdown into tickets, because a spec is a document and the
  owner alone decides when it becomes tickets (ADR-0142).

The module reads no clock and calls no network. The caller passes the board, and the board answers the card of one
number and takes the approval and the move (`card`, `approve`, `move`). A refusal names one rule and writes nothing.

Guard: `tests/test_approve_and_move.py`.
"""
from typing import NamedTuple

TODO = "Todo"
SPEC_LABEL = "spec"
# The forward axis of the board definition (ADR-0141, ADR-0143). Abandoned is outside it: the action never abandons a card.
FORWARD = ("Backlog", TODO, "Doing", "Reviewing", "Batching", "Testing", "Closed")


class Card(NamedTuple):
    """One card as the board answers it: the issue number, its column and its labels."""
    number: str
    column: str
    labels: tuple = ()

    @property
    def is_spec(self):
        """True when the card carries the `spec` label (ADR-0142): a spec is a document, never work."""
        return SPEC_LABEL in {str(label) for label in self.labels or ()}


def _rank(column):
    """The place of a column on the forward axis, or None for a column outside it."""
    try:
        return FORWARD.index(str(column))
    except ValueError:
        return None


def refusal(number, card, to):
    """The one rule a move of this card breaks, or None when the move follows every rule. The rules run in one order:
    the card exists, the card is not a spec, the target is a column the action moves to, the target is after Todo, the
    card sits after Todo, and the move is forward."""
    if card is None:
        return f"the board has no card {number}"
    if card.is_spec:
        return f"the card {card.number} is a spec, and the action never touches a spec"
    target = _rank(to)
    if target is None:
        return f"the action never moves a card to the column {to!r}"
    if target <= _rank(TODO):
        return f"the action never moves a card to {to!r}: starting work stays with the owner"
    source = _rank(card.column)
    if source is None or source <= _rank(TODO):
        return f"the card {number} is in {card.column!r}, and the action moves a card only between columns after Todo"
    if target <= source:
        return f"the card {number} is in {card.column!r}, and a card only moves forward"
    return None


def approve_and_move(board, number, to):
    """One `(text, None)` for a request that follows every rule, or `(None, reason)` for the one rule it breaks. The
    approval and the move reach the board only after every rule passes, so a refusal writes nothing."""
    number, to = str(number), str(to)
    card = board.card(number)
    reason = refusal(number, card, to)
    if reason is not None:
        return None, reason
    board.approve(number)
    board.move(number, to)
    return f"approved #{number}, moved from {card.column} to {to}", None
