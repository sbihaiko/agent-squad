"""The phone page carries no question card (#2490).

The question card was the chat surface's control for answering a coder's question. It left the phone page with the chat
and its rule module, so the page holds no card renderer, no card style and no question control. The page is the served
phone page, composed the way the hub composes it.
"""
from tests.test_hub_mobile_move import hub  # noqa: F401  (hub is the page fixture)


def page(hub_):
    return hub_.mobile_html().decode()


def test_the_phone_page_carries_no_question_card(hub):  # noqa: F811
    body = page(hub)
    assert "// <question-card-view>" not in body, "the phone page still carries the question card renderer"
    assert "drawQuestionCard(" not in body, "the phone page still draws a question card"
    assert ".qcard{" not in body, "the phone page still styles a question card"
    assert "cardStart(" not in body and "cardSubmit(" not in body, "the phone page still carries the card rule module"
