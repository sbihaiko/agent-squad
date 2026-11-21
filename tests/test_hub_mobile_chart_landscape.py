"""The phone's charts in landscape (SPEC 2498, #2504): a chart screen draws the desktop payload at the box the page
measured, in landscape only, with no tab bar, and its portrait phone shows one rotate hint.

The rules live in the chart module (`hub-mobile-chart.js`), run under node alone with no DOM (ADR-0137): the frame comes
from the measured box on every draw, so a rotation that measures again draws at the new width. The page carries only the
wiring: the route, the measure, the redraw and the hint. The browser proof of the same behaviour is
`tests/test_hub_mobile_chart_browser.py`; this file proves the rules and the page's shape without a host.
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
NODE = shutil.which("node")

# One cumulative-flow view in the shape `/api/chart/cfd` serves: three days, two bands, the hub's own stack.
CFD = {
    "start": "2026-11-18", "zone": "UTC", "days": ["2026-11-18", "2026-11-19", "2026-11-20"],
    "ymax": 4, "yticks": [0, 2, 4],
    "xticks": [{"i": 0, "day": "2026-11-18"}, {"i": 1, "day": "2026-11-19"}, {"i": 2, "day": "2026-11-20"}],
    "bands": [
        {"name": "Todo", "lower": [0, 0, 0], "upper": [2, 2, 1], "count": [2, 2, 1], "held": [0, 0, 0],
         "held_upper": [0, 0, 0]},
        {"name": "Closed", "lower": [0, 0, 1], "upper": [0, 1, 3], "count": [0, 1, 2], "held": [0, 0, 0],
         "held_upper": [0, 0, 1]},
    ],
    "reads": [{"day": d, "counts": [{"name": "Todo", "count": 1}, {"name": "Closed", "count": 1}],
               "wip": 2, "throughput": 1} for d in ("2026-11-18", "2026-11-19", "2026-11-20")],
    "last": {"day": "2026-11-20", "wip": 2, "throughput": 1},
}
COLORS = {"Todo": "var(--chart-series-2)", "Closed": "var(--chart-series-7)"}


def run_js(expr):
    script = f"{CHART_JS.read_text()}\nconsole.log(JSON.stringify({expr}));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def page():
    return PAGE.read_text()


def chart_screen(name):
    m = re.search(rf'<article[^>]*data-screen="{name}"[^>]*>(.*?)</article>', page(), re.S)
    assert m, f"the phone has no {name} chart screen"
    return m.group(0)


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart rules")
def test_a_wider_than_tall_box_is_landscape():
    assert run_js("mcLandscape(844, 390)") is True
    assert run_js("mcLandscape(390, 844)") is False, "a portrait phone read as landscape"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart rules")
def test_the_drawing_frame_is_the_box_the_page_measured():
    """A rotation measures the box again: the same payload at a new box draws at the new width, never at a fixed frame."""
    assert run_js("mcChartBox({width: 844, height: 300})") == {"width": 844, "height": 300}
    assert run_js("mcChartBox({width: 390, height: 240})") == {"width": 390, "height": 240}
    assert run_js("mcChartBox({width: 0, height: 0}).width") > 0, "a box with no layout leaves the chart unmeasurable"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart rules")
def test_the_cumulative_flow_draws_at_the_measured_width_and_fills_the_viewport():
    svg = run_js(f"mcCfd({json.dumps(CFD)}, {{width: 844, height: 300}}, {json.dumps(COLORS)})")["svg"]
    w, h = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg).groups()
    assert float(w) == 844 and float(h) == 300, f"the chart drew at {w}x{h}, not the measured box"
    assert svg.count("<polygon") >= len(CFD["bands"]), "a band of the payload is not drawn"
    for name, paint in COLORS.items():
        assert f'fill="{paint}"' in svg, f"the band {name} lost the color the page gives it"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart rules")
def test_the_cumulative_flow_legend_names_the_bands_the_payload_serves():
    legend = run_js(f"mcCfd({json.dumps(CFD)}, {{width: 844, height: 300}}, {json.dumps(COLORS)})")["legend"]
    for band in CFD["bands"]:
        assert band["name"] in legend, band["name"]


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart rules")
def test_a_tap_names_the_day_and_its_counts():
    """A tap on a point shows its value: the day's counts, WIP and throughput, as the hub's reads carry them."""
    text = run_js(f"mcCfdPin({json.dumps(CFD)}, 1)")
    assert text.startswith("2026-11-19:"), text
    assert "Todo 1" in text and "Closed 1" in text, text
    assert "WIP 2" in text and "throughput 1" in text, text


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart rules")
def test_a_chart_drawn_in_portrait_keeps_the_hint_and_draws_no_plot():
    """The rotate hint is the whole portrait screen: nothing is drawn there."""
    script = CHART_JS.read_text()
    assert "mcLandscape" not in script or "mcChartBox" in script
    html = page()
    start = html.find("@media (orientation: portrait)")
    assert start != -1, "the page carries no portrait media query"
    end = html.find("@media", start + 1)   # the block runs to the next media query
    block = re.sub(r"\s+", "", html[start: len(html) if end == -1 else end])
    assert ".chart-body{display:none}" in block, "the portrait phone still draws the plot"
    assert ".chart-hint{display:flex}" in block, "the portrait phone shows no rotate hint"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart rules")
def test_a_chart_screen_carries_no_tab_bar():
    html = page()
    assert re.search(r"body\.chart nav\{display:none\}", html), "the tab bar still draws on a chart screen"
    assert "chart-screen" in html or "chartBodyOf" in html, "the page never marks a chart screen"


def test_each_chart_screen_carries_the_rotate_hint():
    for name, title in (("lead", "Lead Time"), ("cfd", "Cumulative Flow"), ("profiler", "Loop")):
        screen = chart_screen(name)
        assert "chart-body" in screen, f"the {name} screen has no chart body to hide in portrait"
        assert "Rotate to view" in screen, f"the {name} screen carries no rotate hint"
        assert f"{title} draws in landscape." in screen, f"the {name} hint does not name its chart"


def test_the_route_table_opens_the_three_charts_by_their_plain_address():
    html = page()
    for name, title in (("cfd", "Cumulative Flow"), ("lead", "Lead Time"), ("profiler", "Loop")):
        assert re.search(rf'{name}:\s*\{{\s*seg:[^}}]*title:\s*"{re.escape(title)}"', html), \
            f"the route table names no {name} chart"
        assert re.search(rf'{name}:\s*"{name}"', html), f"the plain address of {name} is not in the table"


def test_the_cumulative_flow_reads_the_desktop_route():
    screen = chart_screen("cfd")
    assert 'data-src="/api/chart/cfd"' in screen, "the cumulative flow reads no payload"
    assert 'self.path.startswith("/api/chart/cfd")' in HUB_PY.read_text(), "the hub serves no cumulative-flow route"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart rules")
def test_the_desktop_flow_chart_loses_its_fixed_box():
    """The desktop chart's width is the pane's, whatever the window gives it: no floor of a fixed 600 px box."""
    line = next(line for line in HUB_PY.read_text().splitlines() if line.startswith("const ltPaneW="))
    script = "const ltEl=()=>({clientWidth:380});\n" + line + "\nconsole.log(JSON.stringify(ltPaneW()));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == 360, f"the desktop chart holds a fixed box: {p.stdout}"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart rules")
def test_the_lead_chart_draws_at_the_measured_width():
    script = CHART_JS.read_text()
    start = script.index("const mltChart")
    body = script[start: script.index("\nconst ", start + 1)]   # the whole function, through its closing brace
    assert "mcChartBox" in body, "the lead chart still draws at the payload's fixed frame"
