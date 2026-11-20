"""The phone page on a notched screen (#1008): safe-area insets, per-tab scroll, and targets that never zoom.

The full-screen run header clears the top inset, and the run's action row clears the bottom inset. A sheet sizes to the
dynamic viewport. A tab keeps its own page scroll. The search field is at least 16 px, so iOS never zooms on focus;
"Desktop page" is a 44 px target; the board says it is loading before the first read answers.

The styles are read from the page's own stylesheet, comments stripped; the behavior runs the served page under node over
the stub DOM of the move tests.
"""
import pathlib
import re

import pytest

from tests.test_hub_mobile_move import hub, run_page  # noqa: F401  (hub is a fixture)

PAGE = pathlib.Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts/hub-mobile.html"


def _decls(selector):
    html = PAGE.read_text()
    css = re.sub(r"/\*.*?\*/", "", html[:html.index("</style>")], flags=re.S)
    out = [m.group(2) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)
           if selector in [s.strip() for s in m.group(1).split(",")]]
    return ";".join(out).replace(" ", "")


@pytest.mark.parametrize("selector", ["#runview-head"])
def test_a_full_screen_header_clears_the_top_inset(selector):
    assert "env(safe-area-inset-top)" in _decls(selector), f"{selector} does not clear the top safe area"


def test_the_run_action_row_clears_the_bottom_inset():
    assert "env(safe-area-inset-bottom)" in _decls("#runview-actions")


@pytest.mark.parametrize("selector", ["#sheet", "#psheet"])
def test_a_sheet_sizes_to_the_dynamic_viewport(selector):
    assert re.search(r"max-height:\d+dvh", _decls(selector)), f"{selector} does not use dvh"


def test_the_search_field_never_triggers_the_ios_focus_zoom():
    declarations = _decls("#searchbar input")
    assert "font-size:var(--fs-field)" in declarations, "the search field does not use the field token"
    assert "--fs-field:16px" in (PAGE.parent / "web-tokens.css").read_text(), "the field token is under 16 px"


def test_the_desktop_link_is_a_44_px_target():
    m = re.search(r"min-height:(\d+)px", _decls("#to-desktop"))
    assert m and int(m.group(1)) >= 44, '"Desktop page" has no 44 px hit area'


@pytest.mark.node_bound
def test_each_tab_keeps_its_own_page_scroll(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
      show("monitor"); window.scrollTo(0, 300);
      show("runs"); const runsAt = window.scrollY; window.scrollTo(0, 120);
      show("monitor"); const monitorBack = window.scrollY;
      show("runs"); const runsBack = window.scrollY;
      return { runsAt, monitorBack, runsBack };
    """)
    assert out == {"runsAt": 0, "monitorBack": 300, "runsBack": 120}, out


@pytest.mark.node_bound
def test_the_board_says_it_is_loading_before_the_first_answer(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
      fetch = () => new Promise(() => {});
      show("board");
      return { cards: $("cards").textContent };
    """)
    assert out["cards"] == "Loading the board.", out
