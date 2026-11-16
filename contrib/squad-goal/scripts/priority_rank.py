"""The queue order of eligible cards (#1977): priority descending, then creation time ascending, then repository and number.

A card with no Priority ranks as normal. A Priority that names none of the four options holds its card out of the order.
"""
LEVELS = {"urgent": 3, "high": 2, "normal": 1, "low": 0}


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
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["created"], pair[1]["repo"], pair[1]["number"]))
    return [card for _, card in ranked], held
