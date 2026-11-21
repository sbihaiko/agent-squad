"""The phone page carries no Terminals screen and no terminal control (#2490).

The Terminals tab, its session list and its conversation screen left the page. The server's terminal routes stay for the
desktop page, and the server rules are in test_hub_terminal_routes.py. The page is read as the hub serves it.
"""
from tests.test_hub_mobile_move import hub  # noqa: F401  (hub is the page fixture)


def page(hub_):
    return hub_.mobile_html().decode()


def test_the_phone_page_has_no_terminals_panel(hub):  # noqa: F811
    body = page(hub)
    assert 'data-panel="terminals"' not in body, "the phone page still holds a Terminals panel"
    assert "Terminals" not in body, "the phone page still names Terminals"


def test_the_phone_page_holds_no_terminal_control(hub):  # noqa: F811
    body = page(hub)
    for name in ("termview", "terminals/write", "terminals/start", "terminals/attach"):
        assert name not in body, f"the phone page still carries {name}"


def test_the_phone_page_opens_no_conversation(hub):  # noqa: F811
    body = page(hub)
    assert "openConversation(" not in body, "the phone page still opens a conversation"
    assert "/api/sessions/conversation" not in body, "the phone page still names the conversation route"
