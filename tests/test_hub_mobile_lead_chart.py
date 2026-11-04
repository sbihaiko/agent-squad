"""The phone's lead-time chart (#1333, SPEC 1327): the mobile hub draws the payload the desktop page already reads.

The drawing is one client module, run under node alone with no DOM and no server. The page is composed from its files
the way the hub composes it: the chart screen fetches `/api/chart/lead`, the desktop page's own route, never a second
one. On a phone the legend and the reference values sit under the plot with no right gutter, a tap pins one mark's
tooltip, and zoom and reset are explicit buttons instead of a drag.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
PAGE = SCRIPTS / "hub-mobile.html"
HUB_PY = SCRIPTS / "hub.py"
CHART_JS = SCRIPTS / "hub-mobile-chart.js"
GESTURE_JS = SCRIPTS / "hub-mobile-gesture.js"
BOARD_FILTER_JS = SCRIPTS / "board-filter.js"
NODE = shutil.which("node")

DOT_ONE = {"n": 1, "title": "first card", "closed": "2026-10-01", "text": "2h", "hours": 2.0,
           "x": 0.2, "y": 0.3, "rolling": {"y": 0.3, "y_low": 0.2, "y_high": 0.4},
           "stack": [{"column": "Todo", "text": "1h"}, {"column": "Doing", "text": "1h"}],
           "approx": False, "unknown_first": False}
DOT_TWO = {"n": 2, "title": "second card", "closed": "2026-10-02", "text": "1h", "hours": 1.0,
           "x": 0.6, "y": 0.5, "rolling": {"y": 0.35, "y_low": 0.25, "y_high": 0.45},
           "stack": [{"column": "Todo", "text": "1h"}], "approx": False, "unknown_first": False}
DOT_THREE = {"n": 3, "title": "third card", "closed": "2026-10-03", "text": "4h", "hours": 4.0,
             "x": 0.62, "y": 0.52, "rolling": {"y": 0.4, "y_low": 0.3, "y_high": 0.5},
             "stack": [{"column": "Doing", "text": "4h"}], "approx": False, "unknown_first": False}
CHART = {
    "plot_frame": {"width": 720, "height": 240},
    "scale": "linear",
    "ticks": [{"text": "0h", "y": 0.0}, {"text": "6h", "y": 0.5}, {"text": "12h", "y": 1.0}],
    "xticks": [{"x": 0.0, "text": "Oct 1"}, {"x": 1.0, "text": "Oct 3"}],
    "dots": [DOT_ONE, DOT_TWO, DOT_THREE],
    "clusters": [
        {"x": 0.2, "y": 0.3, "r": 5, "count": 1, "cards": [1], "dots": [DOT_ONE]},
        {"x": 0.61, "y": 0.51, "r": 7, "count": 2, "cards": [2, 3], "dots": [DOT_TWO, DOT_THREE],
         "lead": {"text": "1h to 4h"}, "closed": {"text": "Oct 2 to Oct 3"}},
    ],
    "average": {"y": 0.4, "label": "Average", "text": "3h 10m"},
    "median": {"y": 0.35, "label": "Median", "text": "2h 5m"},
    "p85": {"y": 0.6, "label": "p85", "text": "5h 0m"},
    "range": {"start": "2026-10-01T00:00:00+00:00", "end": "2026-10-03T00:00:00+00:00"},
}


def page():
    return (PAGE.read_text().replace("/*GESTURE*/", GESTURE_JS.read_text())
            .replace("/*BOARD_FILTER*/", BOARD_FILTER_JS.read_text())
            .replace("/*CHART*/", CHART_JS.read_text()))


def lead_screen():
    m = re.search(r'<article data-screen="lead"[^>]*>(.*?)</article>', page(), re.S)
    assert m, "the More tab has no lead-time screen"
    return m.group(0)


def run_js(expr):
    script = f"{CHART_JS.read_text()}\nconsole.log(JSON.stringify({expr}));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_the_phone_reads_the_desktop_payload_with_no_second_route():
    """#1333: the screen fetches /api/chart/lead, the route the desktop page draws from; no mobile-only lead route."""
    html = page()
    src = re.search(r'data-src="([^"]+)"', lead_screen()).group(1)
    assert src == "/api/chart/lead"
    desktop = HUB_PY.read_text()
    assert 'self.path.startswith("/api/chart/lead")' in desktop, "the hub serves no lead chart route"
    assert "/api/chart/lead" in desktop, "the desktop page lost its lead route"
    routes = {re.sub(r"\?.*", "", p) for p in re.findall(r'"(/api/[^"]*)"', html)}
    assert {r for r in routes if "lead" in r} <= {"/api/chart/lead", "/api/leadtime"}, routes
    assert '"lead": {"measure"' in desktop, "the served payload no longer carries the lead object"
    assert "d.lead" in html, "the phone draws no answer from the served payload"


def test_the_legend_and_the_reference_values_sit_under_the_plot():
    """#1333: the plot comes first, the legend and the three reference values follow it, with no right gutter."""
    screen = lead_screen()
    assert screen.index('id="ltplot"') < screen.index('id="ltlegend"'), "the legend is not under the plot"
    assert "lttg" not in screen, "the phone screen keeps the desktop's gutter tag"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_the_drawn_legend_names_who_is_who_and_each_reference_value():
    out = run_js(f"mltChart({json.dumps(CHART)})")
    assert "<svg" in out["svg"] and "data-lt" in out["svg"]
    assert "Card" in out["legend"] and "Cluster" in out["legend"] and "Rolling mean" in out["legend"]
    for name, value in (("Average", "3h 10m"), ("Median", "2h 5m"), ("p85", "5h 0m")):
        assert name in out["legend"] and value in out["legend"], (name, value)
    assert "mltsolid" in out["legend"] and "mltdash" in out["legend"] and "mltdot" in out["legend"]
    assert "lttg" not in out["svg"], "the phone chart draws the desktop gutter"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_a_tap_pins_the_mark_s_tooltip():
    cluster = CHART["clusters"][1]
    pinned = run_js(f"mltPin({json.dumps(cluster)})")
    assert "2 issues" in pinned and "#2" in pinned and "#3" in pinned
    assert "1h to 4h" in pinned and "Oct 2 to Oct 3" in pinned
    assert "Doing 4h" in pinned, "the pinned tooltip drops the stage strip"
    lone = run_js(f"mltPin({json.dumps(DOT_ONE)})")
    assert "#1" in lone and "first card" in lone and "2h" in lone and "2026-10-01" in lone
    screen = lead_screen()
    assert 'id="ltpin"' in screen and "data-ltpin" in screen, "no dismissable pin on the phone"
    assert 'circle[data-lt-k' in page(), "a tapped mark names no cluster"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart drawing")
def test_zoom_and_reset_are_explicit_buttons():
    rng = json.dumps(CHART["range"])
    zoom_in = run_js(f"mltZoom({rng}, 'in')")
    zoom_out = run_js(f"mltZoom({rng}, 'out')")
    assert zoom_in == {"from": "2026-10-01T12:00:00.000Z", "to": "2026-10-02T12:00:00.000Z"}
    assert zoom_out == {"from": "2026-09-30T00:00:00.000Z", "to": "2026-10-04T00:00:00.000Z"}
    assert run_js(f"mltZoom({rng}, 'reset')") is None
    screen = lead_screen()
    for action in ("in", "out", "reset"):
        assert re.search(rf'data-ltzoom="{action}"', screen), action
    assert "/api/leadtime" in page(), "the zoom does not read the desktop's zoom route"
    assert "onpointerdown" not in page(), "the phone keeps drag-to-zoom instead of explicit buttons"
