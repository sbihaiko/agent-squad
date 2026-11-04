"""The lead time chart starts and ends at a chosen column (SPEC 889 #905, #1024).

A card's span is read from its column intervals in the history store: from its first entry into the chosen start column
to its last entry into the chosen end column. A card with no entry into the start column gives no dot, and the window
text counts it with its reason.
"""
import importlib.util
from pathlib import Path

from tests.test_hub_board_flow_charts import _card, _life, _seed, board, hub  # noqa: F401  (fixtures)

HISTORY = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts" / "board_history.py"
_spec = importlib.util.spec_from_file_location("board_history_span", HISTORY)
_hist = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_hist)


def _span(events, start=None, end=None):
    return _hist.lead_span(_hist.intervals(events), start=start, end=end)


def test_doing_to_closed_measures_from_the_doing_transition():
    events = _life("2026-10-01T00:00:00Z", ("2026-10-02T00:00:00Z", "Doing"), ("2026-10-03T00:00:00Z", "Closed"))
    assert _span(events, start="Doing")["flowing"] == 24.0


def test_a_card_that_never_enters_the_start_column_has_no_span():
    events = _life("2026-10-01T00:00:00Z", ("2026-10-04T00:00:00Z", "Closed"))
    assert _span(events, start="Doing") is None


def test_an_end_column_before_the_start_column_is_no_span():
    events = _life("2026-10-01T00:00:00Z", ("2026-10-02T00:00:00Z", "Review"), ("2026-10-03T00:00:00Z", "Doing"))
    assert _span(events, start="Doing", end="Review") is None


def test_a_card_born_in_todo_enters_todo_at_its_creation():
    events = _life("2026-10-01T00:00:00Z", ("2026-10-02T00:00:00Z", "Doing"), ("2026-10-04T00:00:00Z", "Closed"))
    assert _span(events, start="Todo")["flowing"] == 72.0


def test_a_card_born_in_backlog_is_not_taken_as_born_in_todo():
    events = [{"t": "2026-10-01T00:00:00Z", "type": "added"}, {"t": "2026-10-01T00:00:00Z", "type": "status", "status": "Backlog"},
              {"t": "2026-10-02T00:00:00Z", "type": "status", "status": "Doing"},
              {"t": "2026-10-04T00:00:00Z", "type": "status", "status": "Closed"}, {"t": "2026-10-04T00:00:00Z", "type": "closed"}]
    assert _span(events, start="Todo") is None


def test_the_default_span_is_opened_to_closed():
    events = _life("2026-10-01T00:00:00Z", ("2026-10-03T00:00:00Z", "Closed"))
    assert _span(events)["flowing"] == 48.0


def test_choosing_doing_to_closed_changes_the_dots_to_that_span(hub, board):  # noqa: F811
    _seed(hub, {
        1: (_card(1, st="Closed", s="CLOSED", c="2026-10-05T00:00:00Z"),
            _life("2026-10-01T00:00:00Z", ("2026-10-04T00:00:00Z", "Doing"), ("2026-10-05T00:00:00Z", "Closed"))),
        2: (_card(2, st="Closed", s="CLOSED", c="2026-10-06T00:00:00Z"),
            _life("2026-10-01T00:00:00Z", ("2026-10-06T00:00:00Z", "Closed"))),
    })
    full = hub.lead_zoom("tf=all")
    doing = hub.lead_zoom(f"tf=all&a=Doing&b={hub.COLUMN['issue_closed']}")
    assert [(d["n"], d["hours"]) for d in full["dots"]] == [(1, 96.0), (2, 120.0)]
    assert [(d["n"], d["hours"]) for d in doing["dots"]] == [(1, 24.0)]
    assert doing["excluded_total"] == 1
    assert doing["text"] == "all closed cards, Doing -> closed, 1 issue plotted; Timeframe selects by end transition"


def test_a_card_with_no_start_transition_is_excluded_and_counted(hub, board):  # noqa: F811
    _seed(hub, {
        1: (_card(1, st="Closed", s="CLOSED", c="2026-10-03T00:00:00Z"),
            _life("2026-10-01T00:00:00Z", ("2026-10-02T00:00:00Z", "Doing"), ("2026-10-03T00:00:00Z", "Closed"))),
        2: (_card(2, st="Closed", s="CLOSED", c="2026-10-04T00:00:00Z"),
            _life("2026-10-01T00:00:00Z", ("2026-10-04T00:00:00Z", "Closed"))),
    })
    chart = hub.lead_zoom("tf=all&a=Doing")
    assert [d["n"] for d in chart["dots"]] == [1]
    assert chart["excluded"] == {"no_start": 1}, "re-anchored (#1248): a card that never entered the start is named so"


def test_start_todo_on_the_hub_plots_a_card_born_in_todo(hub, board):  # noqa: F811
    _seed(hub, {1: (_card(1, st="Closed", s="CLOSED", c="2026-10-05T00:00:00Z"),
                    _life("2026-10-01T00:00:00Z", ("2026-10-02T00:00:00Z", "Doing"), ("2026-10-05T00:00:00Z", "Closed")))})
    todo = hub.lead_zoom("tf=all&a=Todo")
    assert [(d["n"], d["hours"]) for d in todo["dots"]] == [(1, 96.0)]
    assert todo["excluded_total"] == 0


def test_the_route_refuses_an_unknown_column(hub, board):  # noqa: F811
    assert "error" in hub.lead_zoom("tf=all&a=Nowhere")


def test_the_page_offers_start_and_end_selectors_and_sends_them(hub):  # noqa: F811
    assert "id=lta" in hub.PAGE and "id=ltb" in hub.PAGE
    assert "&a=" in hub.PAGE and "&b=" in hub.PAGE
    assert hub.flow_lead()["lead"]["columns"] == hub.ORDER
