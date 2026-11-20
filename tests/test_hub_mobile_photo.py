"""The phone page carries no photo row and no attach sheet (#2490).

The composer's image input, its preview chip and the attach sheet left the page with the chat. The phone page holds no
image input and no attachment control, and its content-security line no longer admits a picked photo's blob. The page is
the served phone page, composed the way the hub composes it.
"""
from tests.test_hub_mobile_move import hub, run_page  # noqa: F401  (hub is the page fixture)


def page(hub_):
    return hub_.mobile_html().decode()


def test_the_phone_page_holds_no_photo_row_and_no_attach_sheet(hub):  # noqa: F811
    body = page(hub)
    for id_ in ("termview-photo", "termview-file", "termview-attach", "termview-attach-img", "termview-attach-name",
                "termview-attach-x", "attach-sheet", "attach-cancel"):
        assert f'id="{id_}"' not in body, f"the phone page still holds #{id_}"
    assert 'type="file"' not in body, "the phone page still holds an image input"
    assert "Accept" not in body or "accept=\"image" not in body, "the phone page still accepts an image"


def test_the_phone_page_has_no_photo_script(hub):  # noqa: F811
    body = page(hub)
    for name in ("photoStage(", "photoPaint(", "attachOpen(", "attachDraw(", "chatAttach("):
        assert name not in body, f"the phone page still carries {name}"
    assert "/api/sessions/attach" not in body, "the phone page still names the attach route"


def test_the_phone_page_content_security_admits_no_blob(hub):  # noqa: F811
    assert "blob:" not in hub.MOBILE_CSP, f"the phone page still admits a picked photo's blob: {hub.MOBILE_CSP}"
