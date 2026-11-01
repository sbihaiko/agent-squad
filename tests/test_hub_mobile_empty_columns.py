"""The mobile board carousel hides the columns that hold no visible card, and names each one in one place (#877).

The column rule is one pure function in `hub-mobile-gesture.js`, run under node with no DOM. The page is checked as text:
the toggle, the named list, the default that is on, and the save of the choice on the device.
"""
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
GESTURE_JS = SCRIPTS / "hub-mobile-gesture.js"
PAGE = SCRIPTS / "hub-mobile.html"
NODE = shutil.which("node")


def run_js(src, expr):
    script = f"{src}\nconsole.log(JSON.stringify({expr}));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


GROUPS = json.dumps([
    {"name": "Backlog", "n": 0, "items": []},
    {"name": "Todo", "n": 2, "items": [{"n": 1}, {"n": 2}]},
    {"name": "Testing", "n": 0, "items": []},
    {"name": "Done", "n": 1, "items": [{"n": 3}]},
])


@pytest.mark.node_bound
@pytest.mark.parametrize("hide", ["true", "false"])
def test_a_closed_status_is_never_a_carousel_column_and_is_named_as_a_total(hide):
    groups = [
        {"name": "Todo", "n": 1, "end": False, "items": [{"n": 1}]},
        {"name": "Closed", "n": 2, "end": True, "items": [{"n": 2}, {"n": 3}]},
        {"name": "Abandoned", "n": 1, "end": True, "items": [{"n": 4}]},
    ]
    out = run_js(GESTURE_JS.read_text(),
                 f"(() => {{ const r = carouselColumns({json.dumps(groups)}, [1, 2, 1], {hide}); "
                 "return { shown: r.shown.map(g => g.name), closed: r.closed }; })()")
    assert out["shown"] == ["Todo"], out
    assert out["closed"] == [{"name": "Closed", "count": 2}, {"name": "Abandoned", "count": 1}], out


@pytest.mark.node_bound
def test_the_filter_on_keeps_only_the_columns_that_hold_a_card():
    out = run_js(GESTURE_JS.read_text(), f"carouselColumns({GROUPS}, [0, 2, 0, 1], true)")
    assert [g["name"] for g in out["shown"]] == ["Todo", "Done"]


@pytest.mark.node_bound
def test_the_filter_on_names_each_hidden_empty_column_with_its_zero_count():
    out = run_js(GESTURE_JS.read_text(), f"carouselColumns({GROUPS}, [0, 2, 0, 1], true)")
    assert out["hidden"] == [{"name": "Backlog", "count": 0}, {"name": "Testing", "count": 0}]


@pytest.mark.node_bound
def test_the_filter_off_keeps_every_column_and_names_none():
    out = run_js(GESTURE_JS.read_text(), f"carouselColumns({GROUPS}, [0, 2, 0, 1], false)")
    assert [g["name"] for g in out["shown"]] == ["Backlog", "Todo", "Testing", "Done"]
    assert out["hidden"] == []


@pytest.mark.node_bound
def test_the_filter_counts_visible_cards_so_a_column_of_filtered_out_cards_is_empty():
    # Visible counts come from the page's label and spec filters: a column whose cards all fail them holds no card.
    out = run_js(GESTURE_JS.read_text(), f"carouselColumns({GROUPS}, [0, 0, 0, 1], true)")
    assert [g["name"] for g in out["shown"]] == ["Done"]
    assert {"name": "Todo", "count": 0} in out["hidden"]


def test_the_page_names_the_hidden_columns_in_one_place_with_a_toggle():
    html = PAGE.read_text()
    assert 'id="empty-cols"' in html
    assert 'id="hide-empty"' in html


def test_the_page_hides_empty_columns_by_default_and_saves_the_choice():
    html = PAGE.read_text()
    assert "bo.hideEmpty !== false" in html
    assert "hideEmpty" in html and "saveB()" in html
