"""The hub reads each repository's card transitions; the board writer is the one that records them (#897).

A board pass that sees a different card state appends a flow sample, never a transition: a transition is a proved move,
and nothing is inferred by diffing two reads of an eventually consistent board (ADR-0144).
"""
import json

import pytest

from tests.test_hub_board_flow_history import _card, _load_hub, _pass, board, hub  # noqa: F401  (fixtures and the pass helper)


@pytest.fixture(autouse=True)
def _no_records(request):
    for path in request.getfixturevalue("hub").STATE.glob("board-transitions.*"):
        path.unlink()


def _line(issue, frm, to, repo):
    return json.dumps({"issue": issue, "item": f"I{issue}", "from": frm, "to": to, "at": "2026-10-31T10:00:00Z", "repo": repo})


def _record(state, repo_tag, lines):
    (state / f"board-transitions.{repo_tag}.jsonl").write_text("\n".join(lines) + "\n")


def test_the_hub_reads_only_the_records_of_its_repository(hub):  # noqa: F811
    _record(hub.STATE, "x_y", [_line(1, "Todo", "Doing", "x/y"), _line(1, "Doing", "Todo", "x/y")])
    _record(hub.STATE, "a_b", [_line(9, "Todo", "Doing", "a/b")])
    assert [(t["issue"], t["from"], t["to"]) for t in hub.card_transitions()] == [(1, "Todo", "Doing"), (1, "Doing", "Todo")]
    assert [t["issue"] for t in hub.card_transitions("a/b")] == [9]


def test_a_repository_with_no_record_reads_as_empty(hub):  # noqa: F811
    assert hub.card_transitions("nobody/none") == []


def test_the_record_survives_a_restart_of_the_hub(hub):  # noqa: F811
    _record(hub.STATE, "x_y", [_line(1, "Todo", "Doing", "x/y")])
    restarted = _load_hub("restart")
    assert restarted.card_transitions() == hub.card_transitions() and len(hub.card_transitions()) == 1


def test_a_board_read_that_differs_from_the_last_read_records_no_transition(hub, board, monkeypatch):  # noqa: F811
    _pass(hub, monkeypatch, [_card(1, st="Todo")])
    before = hub.card_transitions()
    _pass(hub, monkeypatch, [_card(1, st="Doing")])
    assert hub.card_transitions() == before == []
    assert not list(hub.STATE.glob("board-transitions.*"))
