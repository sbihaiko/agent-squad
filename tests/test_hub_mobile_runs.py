"""The mobile Runs tab (#830): live runs first, then today's ended runs, a full-screen run view, an edge swipe back and a pull
down refresh. The gesture rules run under node alone, with no DOM and no server."""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
PAGE = SCRIPTS / "hub-mobile.html"
GESTURE_JS = SCRIPTS / "hub-mobile-gesture.js"
BOARD_FILTER_JS = SCRIPTS / "board-filter.js"
RUN_JS = SCRIPTS / "hub-mobile-run.js"
NODE = shutil.which("node")


def page():
    return (PAGE.read_text().replace("/*GESTURE*/", GESTURE_JS.read_text())
            .replace("/*RUN*/", RUN_JS.read_text())
            .replace("/*BOARD_FILTER*/", BOARD_FILTER_JS.read_text()))


def run_js(src, expr):
    script = f"{src}\nconsole.log(JSON.stringify({expr}));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def runs_panel():
    m = re.search(r'<section data-panel="runs"[^>]*>(.*?)</section>', page(), re.S)
    assert m, "no runs panel"
    return m.group(0)


@pytest.mark.skipif(NODE is None, reason="node is needed for the gesture rules")
@pytest.mark.parametrize("g, act", [
    ({"x0": 10, "dx": 80, "dy": 4, "atTop": False}, "back"),
    ({"x0": 24, "dx": 60, "dy": -10, "atTop": True}, "back"),
    ({"x0": 100, "dx": 80, "dy": 4, "atTop": False}, None),
    ({"x0": 10, "dx": 30, "dy": 2, "atTop": False}, None),
    ({"x0": 10, "dx": 40, "dy": 90, "atTop": False}, None),
    ({"x0": 100, "dx": 0, "dy": 90, "atTop": True}, "refresh"),
    ({"x0": 100, "dx": 0, "dy": 90, "atTop": False}, None),
    ({"x0": 100, "dx": 0, "dy": 30, "atTop": True}, None),
    ({"x0": 100, "dx": 80, "dy": 70, "atTop": True}, None),
])
def test_the_runs_gesture_rules_give_back_or_refresh_or_nothing(g, act):
    got = run_js(GESTURE_JS.read_text(), f"runsGesture({json.dumps(g)})")
    assert (got or {}).get("act") == act


def test_the_runs_tab_has_no_placeholder():
    assert "Placeholder" not in runs_panel()


def test_live_runs_are_listed_before_the_ended_runs():
    panel = runs_panel()
    live, ended = panel.find('id="runs-live"'), panel.find('id="runs-ended"')
    assert live > 0 and ended > live


def test_the_runs_tab_reads_the_run_list_for_live_runs_and_the_ended_route_for_today():
    script = page()
    assert '"/api/runs"' in script
    assert '"/api/ended"' in script


def test_a_tap_opens_the_run_full_screen_in_one_frame():
    html = page()
    assert 'id="runview"' in html and 'id="runframe"' in html
    assert re.search(r"#runview\{[^}]*position:fixed[^}]*inset:0", html)


def test_the_run_view_holds_an_edge_strip_that_takes_the_back_swipe():
    assert 'data-role="edge"' in page()


def test_the_runs_code_holds_no_run_state_rule():
    assert not re.search(r"\.state\s*[!=]==?", page())
