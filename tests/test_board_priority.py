"""The priority mark a hub card wears (#1981, ADR-0137).

The rule answers one icon, one tone, one label and one inheritance mark per card. A card with no value wears the normal
icon. An inherited value and a pending inheritance each wear their own mark.
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


board_priority = _load("board_priority_under_test", SCRIPTS / "board_priority.py")


@pytest.mark.parametrize("level, icon, tone", [
    ("urgent", "double-chevron-up", "red"),
    ("high", "arrow-up", "red"),
    ("normal", "bar", "yellow"),
    ("low", "arrow-down", "blue"),
])
def test_each_level_has_its_icon_and_tone(level, icon, tone):
    mark = board_priority.mark(level)
    assert (mark["icon"], mark["tone"]) == (icon, tone)


@pytest.mark.parametrize("value", [None, ""])
def test_card_with_no_value_shows_the_normal_icon(value):
    mark = board_priority.mark(value)
    assert (mark["level"], mark["icon"], mark["tone"]) == ("normal", "bar", "yellow")


@pytest.mark.parametrize("level", ["urgent", "high", "normal", "low"])
def test_label_names_the_level(level):
    assert board_priority.mark(level)["label"] == f"Priority {level}"


def test_inherited_value_shows_the_inherited_mark():
    assert board_priority.mark("high", inherit="inherited")["inherit"] == "inherited"


def test_pending_inheritance_shows_the_pending_mark():
    assert board_priority.mark("high", inherit="pending")["inherit"] == "pending"


def test_own_value_shows_no_inheritance_mark():
    assert board_priority.mark("low")["inherit"] is None


def test_card_mark_reads_the_card_value():
    assert board_priority.card_mark({"n": 1981, "p": "urgent"})["level"] == "urgent"


def test_card_without_a_value_is_marked_normal():
    assert board_priority.card_mark({"n": 1981})["level"] == "normal"
