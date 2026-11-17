"""The priority mark a hub card wears (#1981, ADR-0137).

A card with no value, or a value that names none of the four levels, wears the normal mark. An inherited value and a
pending inheritance each wear their own inheritance mark; a card's own value wears none.
"""

LEVELS = {
    "urgent": ("double-chevron-up", "red"),
    "high": ("arrow-up", "red"),
    "normal": ("bar", "yellow"),
    "low": ("arrow-down", "blue"),
}
INHERITS = ("inherited", "pending")


def mark(value, inherit=None):
    level = value if value in LEVELS else "normal"
    icon, tone = LEVELS[level]
    return {"level": level, "icon": icon, "tone": tone, "label": f"Priority {level}",
            "inherit": inherit if inherit in INHERITS else None}


def card_mark(item):
    return mark(item.get("p"))
