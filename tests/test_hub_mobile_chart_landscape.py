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
from html.parser import HTMLParser

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
PAGE = SCRIPTS / "hub-mobile.html"
HUB_PY = SCRIPTS / "hub.py"
CHART_JS = SCRIPTS / "hub-mobile-chart.js"
PROFILER_JS = SCRIPTS / "hub-profiler.js"
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

# One lead view in the shape `/api/chart/lead` serves: the frame, its ticks and three reference values.
LEAD = {
    "plot_frame": {"width": 672, "height": 212},
    "ticks": [{"y": 0, "text": "1h"}, {"y": 1, "text": "4h"}],
    "xticks": [{"x": 0, "text": "Nov 18"}, {"x": 1, "text": "Nov 20"}],
    "dots": [], "clusters": [],
    "average": {"y": 0.4, "text": "4h", "label": "Average"},
    "median": {"y": 0.3, "text": "1h 36m", "label": "Median"},
    "p85": {"y": 0.7, "text": "7h 21m", "label": "p85"},
    "mark": {"outline": 1.5},
}


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


class _Parents(HTMLParser):
    """Each id of the served markup and the id of its nearest named ancestor, read from the page's own elements."""

    VOID = {"meta", "link", "br", "hr", "img", "input", "source", "area", "base", "col", "embed", "param", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parent = {}

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if d.get("id"):
            self.parent[d["id"]] = next((i for _, i in reversed(self.stack) if i), None)
        if tag not in self.VOID:
            self.stack.append((tag, d.get("id")))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop()[0] == tag:
                break


def parents(markup):
    parser = _Parents()
    parser.feed(markup)
    return parser.parent


def stylesheet(html):
    return "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))


def declarations(body):
    """One rule body as its declaration map, keyed by property, never by a whitespace-stripped token."""
    decls = {}
    for part in body.split(";"):
        name, sep, value = part.partition(":")
        if sep:
            decls[name.strip()] = value.strip()
    return decls


def parse_rules(css):
    """Each rule of one style sheet: its selector text (inner whitespace collapsed to one space, so a descendant
    combinator stays a space) and its declaration map. A selector list gives one entry per selector, and a later rule
    of the same selector overrides the earlier one, as the cascade does. An at-rule's body is parsed as rules in turn
    and keyed by its whole prelude, so a media query is read apart from the top-level rules and from the other query."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules, at = {}, {}
    i = 0
    while i < len(css):
        brace = css.find("{", i)
        if brace == -1:
            break
        prelude = " ".join(css[i:brace].split())
        depth, j = 1, brace + 1
        while j < len(css) and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        body = css[brace + 1: j - 1]
        if prelude.startswith("@"):
            at[prelude] = parse_rules(body)
        else:
            for selector in prelude.split(","):
                selector = " ".join(selector.split())
                if selector:
                    rules.setdefault(selector, {}).update(declarations(body))
        i = j
    return {"rules": rules, "at": at}


def test_the_landscape_lead_shares_one_row_and_gives_the_plot_the_rest_of_the_height():
    """#2663: in landscape the period buttons and the zoom control stand in one row over the plot, the plot takes the
    remaining height of the chart area, and the portrait phone keeps the layout it has."""
    html = page()
    screen = chart_screen("lead")
    where = parents(screen)
    assert where.get("ltbars") == "ltrow" and where.get("ltzoom") == "ltrow", \
        f"the period buttons and the zoom control share no row: {where.get('ltbars')}, {where.get('ltzoom')}"
    assert where.get("ltrow") == "lead-content", "the control row does not sit in the lead screen's own body"
    assert screen.index('id="ltrow"') < screen.index('id="ltplot"'), "the control row does not stand over the plot"

    sheet = parse_rules(stylesheet(html))
    land = sheet["at"].get("@media (orientation: landscape)")
    assert land, "the page carries no landscape media query"
    rules = land["rules"]
    row = rules.get(".chart-screen #ltrow")
    assert row, f"the landscape phone has no rule for the control row: {sorted(rules)}"
    assert row.get("display") == "flex", f"the landscape control row is not a flex row: {row}"
    assert row.get("flex-direction") == "row" and row.get("flex-wrap") == "nowrap", row
    assert row.get("flex") == "none", f"the control row takes height from the plot: {row}"
    for part in ("#ltbars", "#ltzoom"):
        rule = rules.get(f".chart-screen #ltrow {part}")
        assert rule and rule.get("margin") == "0", f"the row's {part} keeps its own margin: {rule}"

    # The body is a flex column in landscape, so the plot grows into the height the row leaves.
    body = sheet["rules"].get(".chart-body")
    assert body and body.get("display") == "flex" and body.get("flex-direction") == "column", body
    plot = sheet["rules"].get(".chart-screen .chart-plot")
    assert plot and plot.get("flex") == "1" and plot.get("min-height") == "0", \
        f"the plot no longer takes the remaining height of the chart area: {plot}"

    # The empty status line keeps no height in landscape, and its margin stays out of the portrait layout.
    msg = rules.get(".chart-screen .chart-body .msg")
    assert msg and msg.get("margin") == "0", f"the landscape status line keeps its margin: {msg}"
    assert ".chart-screen .chart-body .msg" not in sheet["rules"], \
        "the status-line margin left the landscape query for every screen"

    port = sheet["at"].get("@media (orientation: portrait)")
    assert port, "the page carries no portrait media query"
    prules = port["rules"]
    assert prules.get("body.chart .chart-body", {}).get("display") == "none", \
        "the portrait phone still draws the plot"
    assert prules.get("body.chart .chart-hint", {}).get("display") == "flex", \
        "the portrait phone lost its rotate hint"
    assert not any("ltrow" in selector for selector in prules), \
        "the portrait phone picked up the landscape control row"


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


def test_a_chart_measures_again_after_the_legend_takes_its_row():
    """The legend takes its row from the plot's flex box: the draw measures again, else the drawing is letterboxed."""
    html = page()
    for fn in ("leadDraw", "cfdDraw"):
        m = re.search(rf"function {fn}\(\) \{{(.*?)\n\}}", html, re.S)
        assert m, f"the page has no {fn}"
        body = m.group(1)
        measures = [i for i in range(len(body)) if body.startswith("chartSized(plot)", i)]
        assert len(measures) == 2, f"{fn} measures the plot {len(measures)} time(s), not once before and once after the legend"
        paint_at = body.find("paint(box)")
        assert measures[0] < paint_at, f"{fn} paints before it measures the plot"
        assert paint_at < measures[1], f"{fn} measures the second time before it paints the legend"
        assert "again.height !== box.height" in body, f"{fn} draws again even when the legend left the box unchanged"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart rules")
def test_the_cumulative_flow_legend_names_the_bands_the_payload_serves():
    legend = run_js(f"mcCfd({json.dumps(CFD)}, {{width: 844, height: 300}}, {json.dumps(COLORS)})")["legend"]
    for band in CFD["bands"]:
        assert band["name"] in legend, band["name"]


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart rules")
def test_the_end_labels_turn_inward_so_the_box_cuts_no_date():
    """The first date starts at the axis and the last ends there: a centred end label overflows the measured box."""
    svg = run_js(f"mcCfd({json.dumps(CFD)}, {{width: 844, height: 300}})")["svg"]
    assert re.search(r"text-anchor=start fill=[^>]*>2026-11-18</text>", svg), \
        "the first day is centred on the axis, so the box cuts its left half"
    assert re.search(r"text-anchor=end fill=[^>]*>2026-11-20</text>", svg), \
        "the last day is centred on the axis, so the box cuts its right half"
    lead = run_js(f"mltChart({json.dumps(LEAD)}, {{width: 820, height: 200}})")["svg"]
    assert re.search(r'text-anchor=start fill="var\(--chart-axis\)">Nov 18</text>', lead), \
        "the first lead date is centred on the axis, so the box cuts its left half"
    assert re.search(r'text-anchor=end fill="var\(--chart-axis\)">Nov 20</text>', lead), \
        "the last lead date is centred on the axis, so the box cuts its right half"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart rules")
def test_a_tap_names_the_day_and_its_counts():
    """A tap on a point shows its value: the day's counts, WIP and throughput, as the hub's reads carry them."""
    text = run_js(f"mcCfdPin({json.dumps(CFD)}, 1)")
    assert text.startswith("2026-11-19:"), text
    assert "Todo 1" in text and "Closed 1" in text, text
    assert "WIP 2" in text and "throughput 1" in text, text


def test_a_chart_drawn_in_portrait_keeps_the_hint_and_draws_no_plot():
    """The rotate hint is the whole portrait screen: the rule reads the measured orientation and the plot is hidden."""
    assert "mcLandscape(window.innerWidth" in PROFILER_JS.read_text(), \
        "the Loop chart never asks the measured orientation, so the narrow rule cannot switch its drawing"
    html = page()
    start = html.find("@media (orientation: portrait)")
    assert start != -1, "the page carries no portrait media query"
    end = html.find("@media", start + 1)   # the block runs to the next media query
    block = re.sub(r"\s+", "", html[start: len(html) if end == -1 else end])
    assert ".chart-body{display:none}" in block, "the portrait phone still draws the plot"
    assert ".chart-hint{display:flex}" in block, "the portrait phone shows no rotate hint"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart rules")
def test_every_chart_screen_marks_the_page_as_it_enters_and_leaves():
    """The chart mark follows the layer stack, and the Loop's loader marks it again when its first payload arrives."""
    html = page()
    for name in ("lead", "cfd", "profiler"):
        entry = re.search(rf"\b{name}: \{{ show\(\)[^\n]*\n[^\n]*\}}", html)
        assert entry, f"the {name} layer is not in the stack table"
        assert entry.group(0).count("markChart()") == 2, f"the {name} screen marks neither its show nor its hide"
    assert "markChart(false)" in PROFILER_JS.read_text(), "the Loop loader never marks the screen when its payload arrives"


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


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart rules")
def test_a_reading_past_the_plot_clamps_to_its_edge():
    """A log-scale tick or a reference line above 1 plots at the plot's top, never outside the viewBox."""
    assert run_js("mltY(1.6, 260, 240)") == 20, "a reading above 1 leaves the plot"
    assert run_js("mltY(-0.2, 260, 240)") == 260, "a reading below 0 leaves the plot"
    high = {**LEAD, "average": {"y": 1.6, "text": "1 day", "label": "Average"}}
    part = run_js(f"mltChart({json.dumps(high)}, {{width: 844, height: 300}})")["svg"]
    ys = [float(v) for v in re.findall(r'(?<![A-Za-z-])y(?:1|2)?="?(-?\d+(?:\.\d+)?)', part)]
    assert ys and min(ys) >= 0 and max(ys) <= 300, f"the chart draws outside its box: {min(ys)}..{max(ys)}"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart rules")
def test_a_reference_value_stands_in_the_legend_alone():
    """The legend is the one place for a reference value, so a chart screen never draws it twice."""
    part = run_js(f"mltChart({json.dumps(LEAD)}, {{width: 844, height: 300}})")
    assert "mltref" not in part["svg"], "the reference value is drawn in the plot and in the legend"
    for key in ("average", "median", "p85"):
        assert LEAD[key]["text"] in part["legend"], f"the legend lost {key}"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart rules")
def test_the_two_paths_keep_one_margin_rule():
    """A measured box fills its viewport; with no size the payload's frame keeps the old desktop geometry."""
    box = run_js(f"mltChart({json.dumps(LEAD)}, {{width: 844, height: 300}})")["svg"]
    assert 'viewBox="0 0 844 300"' in box, "the measured box is not the drawing's frame"
    frame = run_js(f"mltChart({json.dumps(LEAD)})")["svg"]
    assert 'viewBox="0 0 720 308"' in frame, f"the desktop geometry changed: {frame[:60]}"


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart rules")
def test_a_hidden_state_is_not_named_in_the_day_reading():
    """A tap names the states the drawing keeps; WIP and throughput stay the hub's whole-board figures."""
    text = run_js(f"mcCfdPin({json.dumps(CFD)}, 1, {json.dumps(['Closed'])})")
    assert "Todo 1" in text, text
    assert "Closed" not in text, f"a hidden state is still named: {text}"
    assert "WIP 2 (whole board)" in text and "throughput 1 (whole board)" in text, text


@pytest.mark.skipif(NODE is None, reason="node is needed for the chart rules")
def test_a_band_takes_the_payloads_color_with_no_page_map():
    """The phone names no board column: each band's paint is the role the hub sends, not the palette position."""
    cfd = json.loads(json.dumps(CFD))
    cfd["bands"][0]["color"] = "var(--chart-series-2)"   # the first band's palette role would be series-1
    svg = run_js(f"mcCfd({json.dumps(cfd)}, {{width: 844, height: 300}}, null)")["svg"]
    assert 'fill="var(--chart-series-2)"' in svg, "the band fell back to the palette role, not the payload's color"
