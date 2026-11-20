"""The phone page carries no chat (#2490).

The phone page shows the board, the runs, the usage and the specs. The Chat root, its waiting rows, the conversation
screen and its composer left the page with the chat rule module, so the page has no chat tab, no session list, no
conversation screen and no chat composer. The page is the served phone page, composed the way the hub composes it.
"""
import pytest

from tests.test_hub_mobile_move import hub, run_page  # noqa: F401  (hub is the page fixture)


def page(hub_):
    return hub_.mobile_html().decode()


def test_the_phone_page_shows_no_chat_tab(hub):  # noqa: F811
    body = page(hub)
    assert 'data-tab="chat"' not in body, "the phone page still shows a Chat tab"
    assert "Chat" not in body, "the phone page still names the chat"


def test_the_phone_page_holds_no_chat_root_and_no_conversation(hub):  # noqa: F811
    body = page(hub)
    for id_ in ("sessions", "sessions-state", "sessions-msg", "chat-rest", "termview", "termview-turns", "termview-text"):
        assert f'id="{id_}"' not in body, f"the phone page still holds #{id_}"
    assert "chatrow" not in body, "the phone page still draws a chat row"
    assert "Waiting" not in body, "the phone page still offers the chat filter"


@pytest.mark.node_bound
def test_the_phone_page_reads_no_session_list_or_conversation_on_boot(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, "return Object.keys(__state.gets);")
    for path in ("/api/sessions", "/api/sessions/conversation", "/api/sessions/card"):
        assert path not in out, f"the phone page reads {path} on boot: {sorted(out)}"
