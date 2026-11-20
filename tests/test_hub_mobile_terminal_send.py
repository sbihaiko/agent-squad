"""The phone page carries no session send (#2490).

The composer, its per-session draft and its send left the page with the chat, so the phone page posts no message to a
session. The served page is booted under node over the stub DOM of `tests/test_hub_mobile_move.py`; the test reads the
requests the page posts, never a substring of its text.
"""
import pytest

from tests.test_hub_mobile_move import hub, run_page  # noqa: F401  (hub is the page fixture)


def test_the_phone_page_holds_no_composer(hub):  # noqa: F811
    body = hub.mobile_html().decode()
    for name in ("termview-text", "termview-send", "termview-sent", "termview-draft"):
        assert f'id="{name}"' not in body, f"the phone page still holds #{name}"
    assert "/api/sessions/send" not in body, "the phone page still names the send route"


@pytest.mark.node_bound
def test_the_phone_page_posts_no_session_message_on_boot(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        await loadBoard(); await __tick(20);
        return { posts: __state.posts.map(p => p.path) };
    """)
    assert "/api/sessions/send" not in out["posts"], out
    assert "/api/sessions/card/answer" not in out["posts"], out
