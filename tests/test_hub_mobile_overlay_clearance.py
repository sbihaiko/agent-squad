"""The phone page carries no overlay control (#2490).

The overlay hosted a session composer and left the phone page with the chat, so the page sets no offset for it and names
no overlay button. The overlay still serves the hub's desktop pages (#898), where its rules live.
"""
import pathlib

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts"
PAGE = (SCRIPTS / "hub-mobile.html").read_text()
PAGE_CSS = PAGE[:PAGE.index("</style>")]


def test_the_page_sets_no_overlay_offset():
    assert "--sq-ov-bottom" not in PAGE_CSS, "the phone page still offsets an overlay control"


def test_the_page_carries_no_overlay_button():
    assert "sq-ov-btn" not in PAGE, "the phone page still carries an overlay button"
