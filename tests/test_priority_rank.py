"""The queue order of eligible cards: priority descending, then creation time ascending, then repository and number (#1977).

A card with no Priority ranks as normal. A Priority that names none of the four options holds its card out of the order.
"""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts" / "priority_rank.py"
SPEC = importlib.util.spec_from_file_location("priority_rank", SCRIPT)
priority_rank = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(priority_rank)

REPO = "sbihaiko/agent-squad"
EARLY = "2026-11-01T00:00:00Z"
LATE = "2026-11-02T00:00:00Z"


def card(number, priority=None, created=EARLY, repo=REPO):
    return {"number": number, "repo": repo, "created": created, "priority": priority}


def numbers(cards):
    return [c["number"] for c in priority_rank.order(cards)[0]]


def test_urgent_ranks_first_and_low_last():
    cards = [card(1, "normal"), card(2, "low"), card(3, "urgent"), card(4, "high")]

    assert numbers(cards) == [3, 4, 1, 2]


def test_an_urgent_task_wins_the_one_free_slot_over_a_high_bug():
    bug = card(228, "high", created=EARLY)
    task = card(300, "urgent", created=LATE)

    assert numbers([bug, task])[0] == 300


def test_equal_priority_orders_by_creation_then_repository_then_number():
    cards = [
        card(9, "high", created=LATE),
        card(5, "high", created=EARLY, repo="sbihaiko/zeta"),
        card(7, "high", created=EARLY, repo="sbihaiko/alpha"),
        card(2, "high", created=EARLY, repo="sbihaiko/alpha"),
    ]

    assert numbers(cards) == [2, 7, 5, 9]


def test_a_card_with_no_priority_ranks_as_normal():
    cards = [card(1, None, created=LATE), card(2, "high"), card(3, "low"), card(4, "normal", created=EARLY)]

    assert numbers(cards) == [2, 4, 1, 3]


def test_an_empty_priority_ranks_as_normal():
    assert numbers([card(1, "low"), card(2, "")]) == [2, 1]


def test_a_priority_outside_the_four_options_holds_its_card():
    ranked, held = priority_rank.order([card(7, "critical"), card(8, "high")])

    assert [c["number"] for c in ranked] == [8]
    assert [c["number"] for c in held] == [7]


def test_the_order_does_not_depend_on_the_input_order():
    cards = [card(4, "low"), card(1, "urgent"), card(3, "normal", created=LATE), card(2, "normal")]

    assert numbers(cards) == numbers(list(reversed(cards))) == [1, 2, 3, 4]


def test_the_input_list_is_not_reordered():
    cards = [card(2, "low"), card(1, "urgent")]
    before = list(cards)

    priority_rank.order(cards)

    assert cards == before
