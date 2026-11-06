"""The phone overlay control stays out of the Undo band and out of an open sheet's path (#1447).

The rules are read from the page's own declarations: the overlay button's size and its offset variable, the Undo bar's
offset, height and padding, and the sheet's height. Safe-area insets cancel on both sides, so they are left out.
"""
import pathlib
import re

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts"
PAGE = (SCRIPTS / "hub-mobile.html").read_text()
PAGE_CSS = PAGE[:PAGE.index("</style>")]
OVERLAY = (SCRIPTS / "hub-overlay.js").read_text()


def _decls(text, selector):
    m = re.search(r"(?:^|[\s>};])" + re.escape(selector) + r"\{([^{}]*)\}", text)
    assert m, f"{selector} has no rule"
    return m.group(1)


def _px(decl, prop):
    m = re.search(r"(?<![-\w])" + prop + r":[^;]*?(\d+)px", decl)
    assert m, f"{prop} has no px value in {decl!r}"
    return int(m.group(1))


def _overlay_bottom(selector):
    m = re.search(r"--sq-ov-bottom:\s*calc\((\d+)px", _decls(PAGE_CSS, selector))
    assert m, f"{selector} does not set a px offset for the overlay control"
    return int(m.group(1))


def test_the_overlay_control_never_intersects_the_undo_bar():
    undo = _decls(PAGE_CSS, "#undo")
    undo_bottom = _px(undo, "bottom")
    undo_top = undo_bottom + _px(_decls(PAGE_CSS, "#undo button"), "min-height") + 2 * _px(undo, "padding") + 2 * _px(undo, "border")

    overlay_height = _px(_decls(OVERLAY, "#sq-ov-btn"), "height")
    overlay_bottom = _overlay_bottom(":root:has(#undo:not([hidden]))")

    assert not (overlay_bottom < undo_top and overlay_bottom + overlay_height > undo_bottom), (
        f"overlay control {overlay_bottom}px to {overlay_bottom + overlay_height}px overlaps the Undo bar "
        f"{undo_bottom}px to {undo_top}px"
    )


def test_the_overlay_control_sits_above_an_open_sheet():
    assert "height:60dvh" in _decls(PAGE_CSS, "#sheet"), "the sheet no longer takes 60dvh"
    rule = _decls(PAGE_CSS, ":root:has(#sheet:not([hidden]))")
    m = re.search(r"--sq-ov-bottom:\s*calc\(\s*60dvh\s*\+\s*(\d+)px\s*\)", rule)
    assert m and int(m.group(1)) > 0, "the overlay control does not clear the top of an open sheet"
    assert PAGE_CSS.index(":root:has(#sheet:not([hidden]))") > PAGE_CSS.index(":root:has(#undo:not([hidden]))"), (
        "the sheet rule must follow the Undo rule so the sheet wins when both show"
    )
