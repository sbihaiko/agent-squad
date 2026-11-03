"""#916: the run dashboard and the editor at tablet and phone widths.

`squad dashboard` serves the built page and `squad editor` opens that same page from `file://`, so one page source answers
for both. A phone lays the page out by its own `<meta name=viewport>`; without one it lays out at 980px. The layout
checks read the stylesheet's own rules at a given width -- every rule whose media query holds at that width, in source
order, as the cascade applies them -- and never a browser, so a rule that only wraps at one breakpoint fails here.
"""
import re

import pytest

from squad import runs
from tests.dashboard_helpers import OUT, PAGE_SRC
from tests.test_dashboard_layout import _page_css, _rules

VIEWPORT = re.compile(r"<meta\s+name=[\"']viewport[\"']\s+content=[\"']([^\"']+)[\"']", re.I)
WIDTH_QUERY = re.compile(r"\(\s*(max|min)-width\s*:\s*(\d+)px\s*\)")
# A user-preference query says nothing about the width, and the page is laid out for a reader with no preference set, so
# its rules do not apply here (#1140). Every other feature query still fails loudly: the page queries widths and this.
PREFERENCE_QUERY = re.compile(r"\(\s*prefers-[\w-]+\s*:\s*[\w-]+\s*\)")


def _applies(media, width):
    """Whether a rule's media list holds at `width`; the page only queries widths, so any other query fails loudly."""
    if not media:
        return True
    for part in media.split(" and "):
        if PREFERENCE_QUERY.fullmatch(part.strip()):
            return False
        m = WIDTH_QUERY.fullmatch(part.strip())
        assert m, f"the layout test models width queries only: {media!r}"
        bound = int(m.group(2))
        if not (width <= bound if m.group(1) == "max" else width >= bound):
            return False
    return True


def _decls_at(width, selector):
    """What the browser gets for `selector` at `width`: every rule of that selector whose media query holds, merged in source order."""
    out = {}
    for media, sel, decls in _rules(_page_css()):
        if _applies(media, width) and selector in [s.strip() for s in sel.split(",")]:
            out.update(decls)
    return out


def _resolve(value, tokens):
    for _ in range(5):
        value = re.sub(r"var\((--[\w-]+)(?:,[^)]*)?\)", lambda m: tokens.get(m.group(1), m.group(0)), value)
    return value


def _px(value):
    m = re.fullmatch(r"\s*(\d+)px\s*", value or "")
    return int(m.group(1)) if m else None


def test_the_dashboard_and_the_editor_open_one_page_that_declares_the_phone_viewport():
    """Both commands open the one built page, and that page tells a phone to lay out at the screen's own width."""
    assert runs.PAGE.resolve() == OUT.resolve()
    for name, html in (("page template", PAGE_SRC()), ("built page", OUT.read_text())):
        m = VIEWPORT.search(html)
        assert m, f"{name} declares no viewport meta, so a phone lays it out at 980px"
        content = {p.strip().replace(" ", "") for p in m.group(1).split(",")}
        assert "width=device-width" in content and "initial-scale=1" in content, (name, m.group(1))


def test_the_side_panel_docks_as_a_full_width_sheet_at_768_and_390():
    """The side-panel breakpoint: under 900px the drawer is a bottom sheet, so its width never pushes the page sideways."""
    assert "@media (max-width: 900px)" in OUT.read_text()
    for width in (768, 390):
        left = _decls_at(width, "#left")
        assert (left["top"], left["left"], left["right"], left["width"]) == ("auto", "0", "0", "auto"), (width, left)
        assert left["height"] == "var(--side-w,52vh)", (width, left)
        assert left["border-left"] == "0" and left["border-top"] == "1px solid var(--line)"


def test_no_horizontal_scroll_contract_at_768_and_390():
    """The top bar wraps to its own rows and the panel footer keeps only the transport, so no in-flow row is wider than the window."""
    for width in (768, 390):
        assert _decls_at(width, "body")["grid-template-rows"] == "auto 1fr", width   # the extra top-bar rows take height from the map
        top = _decls_at(width, "#topbar")
        assert top["flex-wrap"] == "wrap" and top["height"] == "auto" and top["min-width"] == "0", (width, top)
        assert _decls_at(width, "#left")["width"] == "auto", width
        tokens = _decls_at(width, ":root")
        legend = _px(_resolve(_decls_at(width, "#legend")["width"], tokens))
        assert legend is not None and legend <= width, (width, legend)   # the one fixed-width strip fits the phone
    footer = _decls_at(390, "#footer")
    assert footer["right"] == "12px" and footer["left"] == "12px"
    assert _decls_at(390, "#hint")["display"] == "none"          # the hint would run the row past 390
    assert _decls_at(390, "#replay")["flex-wrap"] == "wrap"
    assert _decls_at(768, "#hint").get("display") != "none"       # a tablet keeps the hint


def test_every_control_is_tall_enough_to_tap_at_768_and_390():
    """32px on a tablet and 44px on a phone, from the one token block, applies to the file-menu labels as well as the buttons."""
    for width, floor in ((768, 32), (390, 44)):
        tokens = _decls_at(width, ":root")
        assert _px(_resolve(_decls_at(width, "button")["min-height"], tokens)) == floor, width
        label = _decls_at(width, "label.btn")
        assert _px(_resolve(label["min-height"], tokens)) == floor, (width, label)
        assert label["display"] == "inline-flex", width   # min-height does nothing on an inline label


@pytest.mark.parametrize("width", (768, 390))
def test_the_built_page_carries_the_rules_the_layout_checks_read(width):
    """The served file is the built page: the viewport and both breakpoints the tests read are in the text a browser gets."""
    page = OUT.read_text()
    assert VIEWPORT.search(page)
    assert "@media (max-width: 900px)" in page and "@media (max-width: 600px)" in page
