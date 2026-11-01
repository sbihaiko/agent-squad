"""The phone's Throughput screen: one spec's burndown, read from the hub's burndown route, behind the More tab (#1049).

The page is read from its files and composed as the mobile test suite composes it. The drawing runs under node, as the
desktop burndown test runs its draw. The screen is read-only: the page's one write is the owner's move on the Board tab.
"""
import json
import re
import shutil
import subprocess

from tests.test_hub_mobile_more import HUB_GET_ROUTES, block, open_tag, page

NODE = shutil.which("node")
TODAY_PAYLOAD = {
    "specs": [{"n": 1045, "t": "card 1045"}],
    "spec": 1045,
    "burn": {
        "points": [
            {"day": "2026-10-20", "remaining": 2, "closed": 0, "added": 2, "closed_tickets": [], "added_tickets": [2, 3]},
            {"day": "2026-10-21", "remaining": 1, "closed": 1, "added": 0, "closed_tickets": [2], "added_tickets": []},
        ],
        "gaps": ["2026-10-19"],
        "days": [f"2026-10-{d:02d}" for d in range(19, 29)],
        "start": "2026-10-19",
        "ymax": 2,
        "remaining": 1,
        "as_of": "2026-10-21",
        "closed_week": 1,
        "added_week": 2,
        "guide": {"source": "trailing", "from": {"day": "2026-10-21", "remaining": 1}, "to": {"day": "2026-10-28", "remaining": 0}},
        "reason": None,
    },
}


def region():
    m = re.search(r"// <burndown>(.*?)// </burndown>", page(), re.S)
    assert m, "the page carries no burndown region"
    return m.group(1)


def run(expr, payload=TODAY_PAYLOAD):
    assert NODE is not None, "node is a test dependency of the burndown screen"
    script = f"const escHTML = t => String(t);\nconst PAYLOAD = {json.dumps(payload)};\n{region()}\nconsole.log({expr});"
    result = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_the_screen_sits_behind_the_more_tab_and_the_nav_keeps_five_tabs():
    html = page()
    assert 'data-more="throughput"' in block(html, "section", "data-panel", "more")
    assert "hidden" in open_tag(html, "throughput")
    assert 'data-tab="throughput"' not in html
    assert len(re.findall(r'<button data-tab="\w+">', html)) == 5


def test_the_selector_stacks_above_the_svg():
    out = run("burnSelect(PAYLOAD) + burnChart(PAYLOAD)")
    assert out.index("<select") < out.index("<svg"), "the spec selector sits above the chart"
    css = page()
    assert re.search(r"#burn select\{[^}]*display:block[^}]*width:100%", css), "the selector takes its own row"


def test_the_svg_fits_380_px_without_sideways_scroll():
    out = run("burnChart(PAYLOAD)")
    svg = re.search(r"<svg[^>]*>", out).group(0)
    assert not re.search(r"\swidth=", svg), "the svg has no fixed pixel width"
    view = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    assert view and int(view[1]) <= 340, "the drawing is no wider than the 380 px phone less its margins"
    assert re.search(r"#burn svg\{[^}]*width:100%[^}]*height:auto", page()), "the svg scales to the screen width (as #cchart)"


def test_a_gap_is_named_and_never_drawn_as_a_zero():
    out = run("burnChart(PAYLOAD)")
    assert "2026-10-19: no whole read this day, so no point" in out, "the gap day is named"
    assert 'data-day="2026-10-19"' not in out, "a gap has no point"
    assert "remaining 0" not in out, "no day reads as a zero remaining"


def test_the_guide_is_a_dashed_line():
    out = run("burnChart(PAYLOAD)")
    guide = re.search(r"<line[^>]*stroke-dasharray=[^>]*>", out)
    assert guide, "the guide is drawn"
    assert 'stroke-dasharray="6 4"' in guide.group(0)


def test_a_point_is_a_tap_target_with_a_keyboard_focus():
    out = run("burnChart(PAYLOAD)")
    points = re.findall(r'<g data-day="([\d-]+)" tabindex="0" role="button"', out)
    assert points == ["2026-10-20", "2026-10-21"]


def test_a_tap_names_the_date_the_remaining_count_and_the_tickets_that_closed():
    reading = run('burnReading(PAYLOAD, "2026-10-21")')
    assert "2026-10-21" in reading
    assert "remaining 1" in reading
    assert "closed that day #2" in reading


def test_the_tap_and_the_keyboard_both_open_the_reading():
    body = region()
    assert "host.onclick" in body and "host.onkeydown" in body, "a click and a key both open the reading"
    assert 'e.key === "Enter"' in body and 'e.key === " "' in body


def test_the_screen_reads_the_hub_burndown_route():
    html = page()
    assert re.search(r'data-src="/api/chart/burndown"', html)
    assert '"/api/chart/burndown?spec="' in html or '"/api/chart/burndown"' in html
    assert "/api/chart/burndown" in HUB_GET_ROUTES


def test_the_screen_writes_nothing_and_the_region_posts_nothing():
    body = region()
    for token in ("POST", "method", "fetch(", "/api/promote", "/api/loop", "/api/sessions"):
        assert token not in body, token
    more = block(page(), "section", "data-panel", "more")
    assert not re.search(r"\bPOST\b", more)
