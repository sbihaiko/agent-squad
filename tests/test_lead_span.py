"""The lead time chart starts and ends at a chosen column (SPEC 889 #905).

Each card's span is read from the transitions the board writer recorded (#897). A card with no transition into the
chosen start or end column gives no dot, and the window text counts it with its reason.
"""
import json

from tests.test_board_leadtime import lt  # noqa: F401  (fixture)
from tests.test_hub_board_flow_charts import _card, board, hub  # noqa: F401  (fixtures)

NOW = "2026-10-20T00:00:00+00:00"


def _row(n, opened, closed):
    return {"n": n, "o": opened, "c": closed, "column": "Closed"}


def _move(issue, to, at):
    return {"issue": issue, "item": f"I{issue}", "from": None, "to": to, "at": at, "repo": "x/y"}


def test_the_default_span_is_opened_to_closed(lt):  # noqa: F811
    rows = [_row(1, "2026-10-01T00:00:00+00:00", "2026-10-03T00:00:00+00:00")]
    assert lt.span(rows, []) == rows


def test_doing_to_closed_measures_from_the_doing_transition(lt):  # noqa: F811
    rows = [_row(1, "2026-10-01T00:00:00+00:00", "2026-10-03T00:00:00+00:00")]
    moves = [_move(1, "Doing", "2026-10-02T00:00:00Z")]
    out = lt.span(rows, moves, start="Doing")
    assert lt.lead_time(out)["series"][0]["hours"] == 24.0


def test_a_card_with_no_start_transition_is_excluded_and_counted(lt):  # noqa: F811
    rows = [_row(1, "2026-10-01T00:00:00+00:00", "2026-10-03T00:00:00+00:00"),
            _row(2, "2026-10-01T00:00:00+00:00", "2026-10-04T00:00:00+00:00")]
    moves = [_move(1, "Doing", "2026-10-02T00:00:00Z")]
    chart = lt.control_chart(lt.span(rows, moves, start="Doing"), timeframe=30, now=lt.datetime.fromisoformat(NOW))
    assert [d["n"] for d in chart["dots"]] == [1]
    assert chart["excluded"] == {"no_transition": 1}
    assert chart["text"] == "last 30 days, 1 excluded because no transition into the chosen columns"


def test_an_end_column_before_the_start_column_is_no_span(lt):  # noqa: F811
    rows = [_row(1, "2026-10-01T00:00:00+00:00", "2026-10-05T00:00:00+00:00")]
    moves = [_move(1, "Review", "2026-10-02T00:00:00Z"), _move(1, "Doing", "2026-10-03T00:00:00Z")]
    assert lt.lead_time(lt.span(rows, moves, start="Doing", end="Review"))["excluded"] == {"no_transition": 1}


def test_an_open_card_stays_counted_as_open(lt):  # noqa: F811
    rows = [{"n": 3, "o": "2026-10-01T00:00:00+00:00", "c": None, "column": "Doing"}]
    assert lt.lead_time(lt.span(rows, [], start="Doing"))["excluded"] == {"open": 1}


def test_choosing_doing_to_closed_changes_the_dots_to_that_span(hub, board):  # noqa: F811
    closed = hub.COLUMN["issue_closed"]
    hub.board_cache["items"] = [_card(1, st=closed, s="CLOSED", c="2026-10-05T00:00:00Z", o="2026-10-01T00:00:00Z"),
                                _card(2, st=closed, s="CLOSED", c="2026-10-06T00:00:00Z", o="2026-10-01T00:00:00Z")]
    (hub.STATE / "board-transitions.x_y.jsonl").write_text(json.dumps(_move(1, "Doing", "2026-10-04T00:00:00Z")) + "\n")
    try:
        full = hub.lead_zoom("tf=all&scale=jira")
        doing = hub.lead_zoom(f"tf=all&scale=jira&a=Doing&b={closed}")
    finally:
        (hub.STATE / "board-transitions.x_y.jsonl").unlink()
    assert [(d["n"], d["hours"]) for d in full["dots"]] == [(1, 96.0), (2, 120.0)]
    assert [(d["n"], d["hours"]) for d in doing["dots"]] == [(1, 24.0)]
    assert doing["excluded_total"] == 1 and "1 excluded because no transition into the chosen columns" in doing["text"]


def test_the_route_refuses_an_unknown_column(hub, board):  # noqa: F811
    assert "error" in hub.lead_zoom("tf=all&a=Nowhere")


def test_the_page_offers_start_and_end_selectors_and_sends_them(hub):  # noqa: F811
    assert "id=lta" in hub.PAGE and "id=ltb" in hub.PAGE
    assert "&a=" in hub.PAGE and "&b=" in hub.PAGE
    assert hub.flow_lead()["lead"]["columns"] == hub.ORDER


def _leave(issue, frm, to, at):
    return {**_move(issue, to, at), "from": frm}


def test_a_card_born_in_todo_enters_todo_at_its_creation(lt):  # noqa: F811
    """#1012: the record holds no move into the column a card was created in; its first move leaves that column."""
    rows = [_row(1, "2026-10-01T00:00:00+00:00", "2026-10-04T00:00:00+00:00")]
    moves = [_leave(1, "Todo", "Doing", "2026-10-02T00:00:00Z"), _leave(1, "Doing", "Closed", "2026-10-04T00:00:00Z")]
    out = lt.span(rows, moves, start="Todo", end="Closed")
    assert lt.lead_time(out)["series"][0]["hours"] == 72.0


def test_an_explicit_move_into_todo_wins_over_creation(lt):  # noqa: F811
    rows = [_row(2, "2026-10-01T00:00:00+00:00", "2026-10-04T00:00:00+00:00")]
    moves = [_leave(2, "Backlog", "Todo", "2026-10-03T00:00:00Z"), _leave(2, "Todo", "Closed", "2026-10-04T00:00:00Z")]
    out = lt.span(rows, moves, start="Todo", end="Closed")
    assert lt.lead_time(out)["series"][0]["hours"] == 24.0


def test_a_card_born_in_backlog_is_not_taken_as_born_in_todo(lt):  # noqa: F811
    rows = [_row(3, "2026-10-01T00:00:00+00:00", "2026-10-04T00:00:00+00:00")]
    moves = [_leave(3, "Backlog", "Doing", "2026-10-02T00:00:00Z")]
    assert lt.lead_time(lt.span(rows, moves, start="Todo"))["excluded"] == {"no_transition": 1}


def test_start_todo_on_the_hub_plots_a_card_born_in_todo(hub, board):  # noqa: F811
    closed = hub.COLUMN["issue_closed"]
    hub.board_cache["items"] = [_card(1, st=closed, s="CLOSED", c="2026-10-05T00:00:00Z", o="2026-10-01T00:00:00Z")]
    (hub.STATE / "board-transitions.x_y.jsonl").write_text(
        json.dumps(_leave(1, "Todo", "Doing", "2026-10-02T00:00:00Z")) + "\n")
    try:
        todo = hub.lead_zoom("tf=all&scale=jira&a=Todo")
    finally:
        (hub.STATE / "board-transitions.x_y.jsonl").unlink()
    assert [(d["n"], d["hours"]) for d in todo["dots"]] == [(1, 96.0)]
    assert todo["excluded_total"] == 0
