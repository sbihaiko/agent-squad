"""A card never carries two labels that state opposite things (#1905).

The shipped definition lists the contradictory pairs. A startable label with a hold label states opposite things, and so
does `needs-info` with a startable label. A writer that would leave one of these pairs writes neither label, and names the
pair it refused. One repair pass corrects every existing contradictory card, and the hold wins there: the card keeps its
hold and loses the startable label.
"""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts"
DEFINITION = SCRIPTS / "board-labels.json"

HOLDS = ("frozen:owner", "frozen:dependency", "frozen:not-startable")


def _load():
    spec = importlib.util.spec_from_file_location("board_labels_under_test", SCRIPTS / "board_labels.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def labels():
    return _load()


def test_the_definition_lists_every_contradictory_pair(labels):
    listed = json.loads(DEFINITION.read_text())["labels"]

    assert listed["startable"] == ["ready-for-agent"]
    assert listed["holds"] == list(HOLDS)
    assert listed["waits"] == ["needs-info"]
    expected = [["ready-for-agent", hold] for hold in HOLDS] + [["ready-for-agent", "needs-info"]]
    assert [list(p) for p in listed["contradictory"]] == expected


def test_the_definition_names_the_pairs_the_rule_refuses(labels):
    assert {tuple(p) for p in labels.contradictory()} == {("ready-for-agent", h) for h in HOLDS} | {
        ("ready-for-agent", "needs-info")}


@pytest.mark.parametrize("hold", HOLDS)
def test_a_writer_refuses_a_startable_label_on_a_held_card(labels, hold):
    written = []

    added, refused = labels.write([hold], ["ready-for-agent"], lambda label: written.append(label))

    assert added == [] and written == []
    assert refused == ("ready-for-agent", hold)


@pytest.mark.parametrize("hold", HOLDS)
def test_a_writer_refuses_a_hold_on_a_startable_card(labels, hold):
    written = []

    added, refused = labels.write(["ready-for-agent"], [hold], lambda label: written.append(label))

    assert added == [] and written == []
    assert refused == ("ready-for-agent", hold)


def test_a_writer_refuses_needs_info_with_a_startable_label(labels):
    written = []

    added, refused = labels.write(["needs-info"], ["ready-for-agent"], lambda label: written.append(label))

    assert added == [] and written == []
    assert refused == ("ready-for-agent", "needs-info")


def test_a_writer_writes_a_label_that_leaves_no_contradictory_pair(labels):
    written = []

    added, refused = labels.write([], ["ready-for-human"], lambda label: written.append(label))

    assert added == ["ready-for-human"] and written == ["ready-for-human"]
    assert refused is None


def test_one_pass_repairs_every_contradictory_card_and_the_hold_wins(labels):
    cards = {7: ["ready-for-agent", "frozen:owner"], 8: ["ready-for-agent", "needs-info"],
             9: ["frozen:dependency"], 10: ["ready-for-agent", "ready-for-human", "frozen:not-startable"]}
    removed = []

    repaired = labels.repair_cards(cards, lambda number, label: removed.append((number, label)))

    assert repaired == {7: ["ready-for-agent"], 8: ["ready-for-agent"], 10: ["ready-for-agent"]}
    assert sorted(removed) == [(7, "ready-for-agent"), (8, "ready-for-agent"), (10, "ready-for-agent")]
