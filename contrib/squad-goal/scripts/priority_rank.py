"""The queue order of eligible cards (#1977): priority descending, then creation time ascending, then repository and number.

A card with no Priority ranks as normal. A Priority that names none of the four options holds its card out of the order.
A Priority field that cannot be read holds every card it touches.
"""
from datetime import datetime, timezone

LEVELS = {"urgent": 3, "high": 2, "normal": 1, "low": 0}


def _instant(created):
    when = datetime.fromisoformat(created.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def order(cards):
    ranked, held = [], []
    for card in cards:
        priority = card.get("priority")
        if priority in (None, ""):
            ranked.append((LEVELS["normal"], card))
        elif isinstance(priority, str) and priority in LEVELS:
            ranked.append((LEVELS[priority], card))
        else:
            held.append(card)
    ranked.sort(key=lambda pair: (-pair[0], _instant(pair[1]["created"]), pair[1]["repo"], int(pair[1]["number"])))
    return [card for _, card in ranked], held


def queue(cards, read):
    """Attach each card's Priority from `read()` ({str(number): name}), then order. A read that raises holds every card."""
    try: priorities = read()
    except RuntimeError: return [], list(cards)
    return order([{**card, "priority": priorities.get(str(card["number"]))} for card in cards])
