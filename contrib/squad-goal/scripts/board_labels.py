"""The loop's label definition and the contradictory pairs one writer refuses (#1905).

Two labels on one card state opposite things when one says the card is work an agent may take and the other says it is
held or waiting. The labels section of the shipped board definition lists each startable label, each hold, each waiting
label and every contradictory pair, so one file answers what a card may carry (ADR-0141).

The section is read under the definition's pin: a copy that differs from the pinned hash is refused, and the loop keeps
no second definition of the board's vocabulary.

A writer that would leave one of these pairs writes neither label, and names the pair it refused. A repair pass corrects
every existing contradictory card, and the hold wins there: the card keeps the label that is not startable and loses the
startable one, because a hold is the owner's statement about the card.

This module reads no host and calls no client: the caller passes the card's labels and the write seam, so the rule is
proven without a carrier.
"""
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from board_columns import PIN_SHA256

DEFINITION = pathlib.Path(__file__).resolve().parent / "board-definition.json"


def definition(path=None):
    """The definition's label section: the startable labels, the holds, the waits and the contradictory pairs. A copy
    that differs from the pin is refused, named with the file it names."""
    target = pathlib.Path(path or DEFINITION)
    text = target.read_text()
    if hashlib.sha256(text.encode()).hexdigest() != PIN_SHA256:
        raise ValueError(f"the definition {target.name} does not match the pinned hash; restore it from the published definition")
    labels = json.loads(text).get("labels")
    if not isinstance(labels, dict):
        raise ValueError(f"the definition {target.name} lists no label section")
    return labels


def contradictory(path=None):
    """The contradictory pairs, in the definition's order: each pair of two label names."""
    return tuple(tuple(pair) for pair in definition(path)["contradictory"])


def refusal(labels, path=None):
    """The first contradictory pair both of whose labels `labels` holds, or None. The pair is the refusal's name."""
    held = set(labels or ())
    for pair in contradictory(path):
        if held.issuperset(pair):
            return pair
    return None


def write(current, adds, write_label, path=None):
    """Add each label of `adds` to a card that holds `current`, through `write_label(name)`.

    A write that would leave a contradictory pair writes neither label, and names the pair it refused: the answer is
    `((), pair)`. Otherwise every label is written, in the order given, and the answer is `(added, None)`."""
    wanted = list(dict.fromkeys(adds))
    pair = refusal(list(current) + wanted, path)
    if pair is not None:
        return [], pair
    for name in wanted:
        write_label(name)
    return wanted, None


def repair(labels, path=None):
    """The one repair of a card's labels: `(kept, removed)`. The hold (or the wait) wins, so every startable label of a
    contradictory pair goes and the other label stays."""
    held = list(dict.fromkeys(labels or ()))
    if refusal(held, path) is None:
        return held, []
    startable = set(definition(path)["startable"])
    removed = [name for name in held if name in startable]
    return [name for name in held if name not in startable], removed


def repair_cards(cards, remove_label, path=None):
    """One pass over `{number: labels}`: every contradictory card loses its startable label, through
    `remove_label(number, name)`. Answers `{number: removed labels}` for the cards this pass corrected."""
    corrected = {}
    for number, labels in cards.items():
        _, removed = repair(labels, path)
        for name in removed:
            remove_label(number, name)
        if removed:
            corrected[number] = removed
    return corrected
