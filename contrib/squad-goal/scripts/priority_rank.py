"""The queue order of eligible cards (#1977): priority descending, then creation time ascending, then repository and number.

A card ranks at the highest Priority among itself and the open cards it blocks, transitively; the stored Priority is never changed.
A card with no Priority ranks as normal. A Priority that names none of the four options holds its card out of the order.
A Priority field that cannot be read holds every card it touches.
"""
from datetime import datetime, timezone

LEVELS = {"urgent": 3, "high": 2, "normal": 1, "low": 0}


def _instant(created):
    when = datetime.fromisoformat(created.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _key(card):
    return (card["repo"], int(card["number"]))


def _stored(card):
    priority = card.get("priority")
    if priority in (None, ""):
        return LEVELS["normal"]
    if isinstance(priority, str) and priority in LEVELS:
        return LEVELS[priority]
    return None


def _effective(cards):
    """Each card's level: its stored level, raised to the highest effective level of every open card it blocks, transitively."""
    stored = {_key(card): _stored(card) for card in cards}
    blocks = {}
    for card in cards:
        for edge in card.get("blocked_by_edges") or []:
            blocker = (edge["repo"], int(edge["number"]))
            if blocker in stored: blocks.setdefault(blocker, []).append(_key(card))
    memo, visiting = {}, set()

    def level(key):
        if key in memo: return memo[key]
        if key in visiting: return stored[key]
        visiting.add(key)
        found = [stored[key]] + [level(blocked) for blocked in blocks.get(key, [])]
        visiting.discard(key)
        memo[key] = max((x for x in found if x is not None), default=None)
        return memo[key]

    return {key: level(key) for key in stored}


def order(cards):
    effective = _effective(cards)
    ranked, held = [], []
    for card in cards:
        if _stored(card) is None:
            held.append(card)
        else:
            ranked.append((effective[_key(card)], card))
    ranked.sort(key=lambda pair: (-pair[0], _instant(pair[1]["created"]), pair[1]["repo"], int(pair[1]["number"])))
    return [card for _, card in ranked], held


def queue(cards, read):
    """Attach each card's Priority from `read()` ({str(number): name}), then order. A read that raises holds every card."""
    try: priorities = read()
    except RuntimeError: return [], list(cards)
    return order([{**card, "priority": priorities.get(str(card["number"]))} for card in cards])
