"""The dashboard's single-card mode (ticket #1782): the panel can hand ONE of its cards to a second view, so the pip
over the hub's maximized board shows the panel's own card instead of a copy that drifts. These pin the card's data
(the run's children grouped by dispatch and state, unfinished first), the names the page may ask for, and the clear
message a card the panel does not have is answered with — never an empty frame."""
from pathlib import Path

from tests.dashboard_helpers import PAGE_SRC, run_node
from tests.test_dashboard_groups import _mk


def _card(children, name="groups"):
    return run_node([{"op": "card", "name": name, "children": children}])[0]


def test_the_grouped_card_puts_the_unfinished_children_first():
    kids = _mk(3, "pass") + _mk(1, None, start=4, running_for=4.0, model="m-a") + _mk(2, None, start=5)
    card = _card(kids)
    assert card["ok"] is True
    assert [g["state"] for g in card["groups"]] == ["running", "queued", "passed"]   # a child in flight or waiting outranks a finished one


def test_each_group_names_its_dispatch_its_state_and_its_count():
    card = _card(_mk(21, "pass") + _mk(5, "fail", start=22))
    # among the finished groups the failed one comes first: a card that lists what went well before what did not hides the work left
    assert [g["name"] for g in card["groups"]] == ["route · 5 failed", "route · 21 passed"]
    assert [g["count"] for g in card["groups"]] == [5, 21]
    assert card["groups"][0]["dispatch"] == "route" and card["groups"][0]["state"] == "failed"
    assert card["groups"][1]["label"] == "passed"


def test_the_same_state_of_two_dispatches_makes_two_groups():
    card = _card(_mk(2, "pass") + _mk(2, "pass", start=3, dispatch="other"))
    assert [g["name"] for g in card["groups"]] == ["other · 2 passed", "route · 2 passed"]
    assert [g["count"] for g in card["groups"]] == [2, 2]


def test_the_card_carries_the_children_a_view_needs_to_read_each_group():
    card = _card(_mk(3, "fail", label="issue #11"))
    g = card["groups"][0]
    assert [k["name"] for k in g["kids"]] == ["route.router.1", "route.router.2", "route.router.3"]
    assert g["kids"][0]["label"] == "issue #11"


def test_a_card_the_panel_does_not_have_is_a_message_never_an_empty_frame():
    card = _card(_mk(2, "pass"), name="totals")
    assert card["ok"] is False
    assert "totals" in card["message"] and "groups" in card["message"]   # the message names what the panel does have


def test_the_run_that_has_no_children_yet_says_so():
    card = _card([])
    assert card["ok"] is True and card["groups"] == []
    assert "no children" in card["empty"]


def test_the_page_publishes_the_card_names_it_can_be_asked_for():
    names = run_node([{"op": "cardnames"}])[0]
    assert names == ["groups"]        # the panel has one card it can name; the parameter takes that one alone


def test_the_page_reads_the_card_parameter_of_its_url_and_renders_that_one_card():
    page = PAGE_SRC()
    assert 'URLSearchParams(location.search).get("card")' in page    # a documented parameter, not a DOM trick
    assert "renderCard()" in page                                    # the page renders the single card itself


def test_the_parameter_is_documented_where_the_dashboard_is():
    readme = Path(__file__).resolve().parent.parent / "docs" / "design" / "README.md"
    text = readme.read_text()
    assert "card=" in text and "Single-card mode" in text   # the mode is documented, not folklore
