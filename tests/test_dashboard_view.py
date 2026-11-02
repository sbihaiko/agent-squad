"""The view and the layout file: pan, zoom, fit, theme, and the layout that lives outside the document."""
import json
import re

from squad import graph
from tests.dashboard_helpers import ROOT, page_template, run_node
from tests.helpers import five, node


# --- layout lives outside the document ---
def test_layout_is_a_sibling_file_the_document_never_holds_and_the_hash_ignores():
    d = five()
    lay = run_node([{"op": "layout", "doc": d}])[0]
    assert set(lay) == {n["name"] for n in d["nodes"]} and all({"x", "y"} == set(v) for v in lay.values())
    edited = run_node([{"op": "edit", "doc": d, "name": "scout", "edited": node(d, "scout")}])[0]["doc"]
    assert "layout" not in edited
    assert run_node([{"op": "hash", "text": json.dumps(d)}])[0] == graph.canon_hash(d)  # no layout term enters the hash
    assert any("layout" in e["message"] for e in run_node([{"op": "validate", "doc": {**d, "layout": lay}}])[0])
    page = page_template()
    assert '".layout.json"' in page and "S.doc.layout" not in page


# --- the view: pan, zoom, and fit are pure functions of a view {k, tx, ty} ---
BOX = {"w": 200, "h": 44}


def _corners(layout, v):
    return [(x * v["k"] + v["tx"], y * v["k"] + v["ty"], (x + BOX["w"]) * v["k"] + v["tx"], (y + BOX["h"]) * v["k"] + v["ty"]) for x, y in ((p["x"], p["y"]) for p in layout.values())]


def test_fit_puts_every_node_inside_the_view_and_never_zooms_in_past_one():
    layout = run_node([{"op": "layout", "doc": five()}])[0]
    size = {"w": 900, "h": 600}
    big, small = run_node([{"op": "fit", "layout": layout, "box": BOX, "size": size, "pad": 24},
                           {"op": "fit", "layout": {"a": {"x": 0, "y": 0}}, "box": BOX, "size": size, "pad": 24}])
    assert 0.1 <= big["k"] < 1
    for x1, y1, x2, y2 in _corners(layout, big):
        assert 24 - 0.01 <= x1 and x2 <= 900 - 24 + 0.01 and 24 - 0.01 <= y1 and y2 <= 600 - 24 + 0.01
    assert small["k"] == 1 and run_node([{"op": "fit", "layout": {}, "box": BOX, "size": size, "pad": 24}])[0]["k"] == 1


def test_zoom_keeps_the_point_under_the_cursor_and_stays_between_the_limits():
    v = {"k": 1, "tx": 40, "ty": 30}
    a, b, lo, hi = run_node([{"op": "zoom", "view": v, "cx": 300, "cy": 200, "factor": 1.5}, {"op": "zoom", "view": v, "cx": 300, "cy": 200, "factor": 0.5},
                             {"op": "zoom", "view": v, "cx": 0, "cy": 0, "factor": 1e-6}, {"op": "zoom", "view": v, "cx": 0, "cy": 0, "factor": 1e6}])
    for r in (a, b):  # the graph point under (300, 200) before is under it after
        assert abs((300 - r["tx"]) / r["k"] - (300 - v["tx"]) / v["k"]) < 1e-9 and abs((200 - r["ty"]) / r["k"] - (200 - v["ty"]) / v["k"]) < 1e-9
    assert a["k"] == 1.5 and b["k"] == 0.5 and lo["k"] == 0.1 and hi["k"] == 3


def test_pan_moves_the_view_and_keeps_the_scale():
    assert run_node([{"op": "pan", "view": {"k": 2, "tx": 10, "ty": 5}, "dx": -7, "dy": 3}])[0] == {"k": 2, "tx": 3, "ty": 8}


def test_the_dashboard_has_a_dark_theme_a_side_panel_drawer_and_a_graph_area_that_fills_the_rest():
    page = page_template()
    # the reference palette is one module now (#1132): the page inlines it and draws the roles, never a copy of its own
    tokens = (ROOT / "contrib/squad-goal/scripts/web-tokens.css").read_text()
    assert "--bg:#0f1115" in tokens and "--panel:#161a21" in tokens and "--acc:#5aa9ff" in tokens
    assert tokens.strip() in page
    assert "background:var(--bg)" in page and "var(--panel)" in page and "var(--acc)" in page
    assert re.search(r"#left \{[^}]*position:fixed[^}]*top:37px[^}]*right:0[^}]*width:var\(--side-w,420px\)", page) and re.search(r"grid-template-columns:\s*1fr;", page)   # the panel is a drawer at the right edge, the graph keeps the width
    assert "agent-squad-run-dashboard" in page and "graph editor" not in page


def test_fit_floor_keeps_a_fit_that_would_shrink_below_the_floor_readable_at_the_anchor():
    wide = {f"n{i}": {"x": i * 230, "y": 0} for i in range(30)}
    size = {"w": 900, "h": 600}
    small, floored, fits, no_anchor = run_node([
        {"op": "fit", "layout": wide, "box": BOX, "size": size, "pad": 24},
        {"op": "fitFloor", "layout": wide, "box": BOX, "size": size, "pad": 24, "minK": 0.7, "anchor": {"x": 460, "y": 0}},
        {"op": "fitFloor", "layout": {"a": {"x": 0, "y": 0}}, "box": BOX, "size": size, "pad": 24, "minK": 0.7, "anchor": {"x": 0, "y": 0}},
        {"op": "fitFloor", "layout": wide, "box": BOX, "size": size, "pad": 24, "minK": 0.7, "anchor": None}])
    assert small["k"] < 0.7
    assert floored["k"] == 0.7 and floored["tx"] == 24 - 460 * 0.7 and floored["ty"] == 24
    assert fits["k"] == 1                      # a graph that fits above the floor is fitted as before
    assert no_anchor == small                  # no anchor, no floor
