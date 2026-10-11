"""Ticket #92: the graph lays itself out in layers, so it spreads as nodes are added and an edge stops crossing a card.

A node's column is the longest path from the root; its row follows the mean row of its parents (the barycenter heuristic). No
layout library: the page stays pure and Node-tested. A dispatch's children anchor on its agent entry, one column
over, instead of on the dispatch node two columns back, so the edge to a child never crosses the entry or its check gates.
"""
import re

from tests.dashboard_helpers import PAGE_SRC, ROOT, run_node
from tests.test_dashboard_failures import BOX, _run

FIX = ROOT / "tests" / "fixtures" / "run-router-waves"
CHROME = ROOT / "dashboard" / "page" / "chrome.js"
GRAPH = ROOT / "dashboard" / "page" / "graph.js"


def _layout(doc):
    return run_node([{"op": "layout", "doc": doc}])[0]


def _box(p):
    return (p["x"], p["y"], p["x"] + BOX["w"], p["y"] + BOX["h"])


def _crosses(line, box):
    (x1, y1, x2, y2) = line
    (bx1, by1, bx2, by2) = box
    if x1 == x2:
        return bx1 < x1 < bx2 and min(y1, y2) < by2 and max(y1, y2) > by1
    t_lo, t_hi = sorted(((bx1 - x1) / (x2 - x1), (bx2 - x1) / (x2 - x1)))
    t_lo, t_hi = max(t_lo, 0), min(t_hi, 1)
    if t_lo > t_hi:
        return False
    y_lo, y_hi = sorted((y1 + t_lo * (y2 - y1), y1 + t_hi * (y2 - y1)))
    return y_lo < by2 and y_hi > by1


def five():
    nodes = [
        {"name": "scout", "kind": "agent", "model": "sonnet", "effort": "low", "tools": [], "deadline": 60, "children": ["spec"]},
        {"name": "spec", "kind": "agent", "model": "sonnet", "effort": "low", "tools": [], "deadline": 60, "children": ["decompose"]},
        {"name": "decompose", "kind": "agent", "model": "sonnet", "effort": "low", "tools": [], "deadline": 60, "children": ["execute"]},
        {"name": "execute", "kind": "agent", "model": "sonnet", "effort": "low", "tools": [], "deadline": 60, "children": ["verify"]},
        {"name": "verify", "kind": "gate", "check": "scope", "params": {"allowed_generated": []}},
    ]
    return {"schema_version": 1, "name": "five", "run": {"deadline": 300, "max_parallel": 1, "spend_cap": 1}, "facts": [], "gates": [{"id": "scope", "params": ["allowed_generated"]}], "nodes": nodes, "root": "scout"}


# ---- layering: a column is the longest path from the root ----
def test_a_chain_lands_in_order_one_column_apart():
    pos = _layout(five())
    xs = [pos[n]["x"] for n in ("scout", "spec", "decompose", "execute", "verify")]
    assert xs == sorted(xs) and len(set(xs)) == 5   # a straight chain: every node its own column, in order


def test_two_nodes_reached_by_different_length_paths_sit_past_every_parent():
    d = five()
    d["nodes"].append({"name": "shortcut", "kind": "gate", "check": "scope", "params": {"allowed_generated": []}, "children": ["verify"]})
    d["nodes"][0]["children"] = ["spec", "shortcut"]   # scout -> shortcut -> verify, and scout -> spec -> decompose -> execute -> verify
    pos = _layout(d)
    assert pos["verify"]["x"] > pos["execute"]["x"] and pos["verify"]["x"] > pos["shortcut"]["x"]   # the long way in wins the column


def test_growing_the_graph_does_not_move_an_existing_node_left():
    d = five()
    before = _layout(d)
    d["nodes"][2]["children"] = ["execute", "new_step"]
    d["nodes"].append({"name": "new_step", "kind": "agent", "model": "sonnet", "effort": "low", "tools": [], "deadline": 60})
    after = _layout(d)
    for n in ("scout", "spec", "decompose", "execute", "verify"):
        assert after[n]["x"] >= before[n]["x"]


def test_siblings_added_to_one_node_each_take_their_own_row_and_spread_out():
    d = five()
    d["nodes"][0]["children"] = ["spec", "b", "c"]
    d["nodes"] += [{"name": "b", "kind": "agent", "model": "sonnet", "effort": "low", "tools": [], "deadline": 60},
                   {"name": "c", "kind": "agent", "model": "sonnet", "effort": "low", "tools": [], "deadline": 60}]
    pos = _layout(d)
    rows = {pos["spec"]["y"], pos["b"]["y"], pos["c"]["y"]}
    assert len(rows) == 3   # three siblings, three distinct rows: none stacked on another


def test_an_unreachable_node_sits_past_everything_not_mixed_in():
    d = five()
    d["nodes"].append({"name": "orphan", "kind": "agent", "model": "sonnet", "effort": "low", "tools": [], "deadline": 60})
    pos = _layout(d)
    assert pos["orphan"]["x"] > max(p["x"] for n, p in pos.items() if n != "orphan")


# ---- the real dynamic run: a dispatch's children anchor on its entry, not on the dispatch itself ----
def test_a_dispatch_child_anchors_on_the_entry_one_column_over():
    doc, events = _run()
    layout = _layout(doc)
    kids = run_node([{"op": "children", "events": events}])[0]
    pos = run_node([{"op": "clayout", "children": kids, "layout": layout, "box": BOX, "gap": 30, "doc": doc}])[0]
    router = layout["router"]   # route's agent entry (docs/design/router.graph.json)
    for p in pos.values():
        assert p["x"] == router["x"] + BOX["w"] + 30   # one column past the entry, not two past the dispatch


def test_no_edge_from_route_to_a_child_crosses_the_entry_or_a_check_gate():
    doc, events = _run()
    layout = _layout(doc)
    kids = run_node([{"op": "children", "events": events}])[0]
    pos = run_node([{"op": "clayout", "children": kids, "layout": layout, "box": BOX, "gap": 30, "doc": doc}])[0]
    anchor = layout["router"]
    boxes = [_box(layout[n]) for n in ("router", "scope_gate", "security_gate")]
    for c in kids:
        if c["name"] not in pos:
            continue
        target = pos[c["name"]]
        line = (anchor["x"] + BOX["w"], anchor["y"] + BOX["h"] / 2, target["x"] - 3, target["y"] + BOX["h"] / 2)
        for other_name, b in zip(("router", "scope_gate", "security_gate"), boxes, strict=False):
            if other_name == "router":
                continue   # the line legitimately starts at the entry's own edge
            assert not _crosses(line, b), (c["name"], other_name)


# ---- #94: a wide fan-out wraps into multiple columns instead of one tall column ----
def test_a_wide_fan_out_wraps_into_multiple_columns():
    doc, events = _run()
    layout = _layout(doc)
    base = run_node([{"op": "children", "events": events}])[0]
    one = base[0]
    kids = [{**one, "name": f"route.router.{i}"} for i in range(1, 19)]   # 18 children, matching the real 24-issue run
    pos = run_node([{"op": "clayout", "children": kids, "layout": layout, "box": BOX, "gap": 30, "doc": doc}])[0]
    xs = sorted({p["x"] for p in pos.values()})
    assert len(xs) > 1   # more than one column
    for x in xs:
        rows = [p["y"] for p in pos.values() if p["x"] == x]
        assert len(rows) <= 7   # no column taller than the wrap budget


def test_a_fan_out_at_or_under_the_wrap_budget_stays_one_column():
    doc, events = _run()
    layout = _layout(doc)
    base = run_node([{"op": "children", "events": events}])[0]
    one = base[0]
    kids = [{**one, "name": f"route.router.{i}"} for i in range(1, 5)]   # matches the real fixture: no wrap expected
    pos = run_node([{"op": "clayout", "children": kids, "layout": layout, "box": BOX, "gap": 30, "doc": doc}])[0]
    assert len({p["x"] for p in pos.values()}) == 1


# ---- #1509: the side panel is a drawer docked to the right edge, so it never covers the diagram ----
# The stylesheet the browser gets, read back as rules, so a test can ask what the page computes for a selector instead of
# grepping a string that a reordered rule would keep passing.
MEDIA = re.compile(r"@media[^{]*\{")


def _page_css():
    src = PAGE_SRC()
    return src.split("<style>", 1)[1].split("</style>", 1)[0]


def _decls(text):
    return {p.strip().lower(): v.strip() for p, _, v in (part.partition(":") for part in text.split(";")) if p.strip()}


def _rules(css):
    """Every style rule as (media, selector, declarations), in source order; one level of @media is unwrapped."""
    css, out, pos = re.sub(r"/\*.*?\*/", "", css, flags=re.S), [], 0
    def add(media, text):
        out.extend((media, sel.strip(), _decls(body)) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text))
    while pos < len(css):
        m = MEDIA.search(css, pos)
        if not m:
            add("", css[pos:]); break
        add("", css[pos:m.start()])
        depth, i = 1, m.end()
        while depth and i < len(css):
            depth += (css[i] == "{") - (css[i] == "}"); i += 1
        add(re.search(r"@media\s*([^{]*)", css[m.start():m.end()]).group(1).strip(), css[m.end():i - 1])
        pos = i
    return out


def _computed(selector, media=""):
    """What the page computes for `selector`: every rule of that exact selector merged in source order, under a viewport whose media query contains `media`."""
    out = {}
    for m, sel, decls in _rules(_page_css()):
        if (media in m if media else not m) and selector in [s.strip() for s in sel.split(",")]:
            out.update(decls)
    return out


def test_the_open_panel_is_a_drawer_at_the_right_edge_under_the_top_bar():
    """The acceptance shape at 1440x900: fixed, top 37, right 0, bottom 0, 420 wide, no transform, no corner."""
    assert _computed("body")["--side-w"] == "420px"
    assert _computed("#left") == {
        "position": "fixed", "top": "37px", "right": "0", "bottom": "0", "left": "auto",
        "width": "var(--side-w,420px)", "max-height": "none", "transform": "none", "border-radius": "0",
        "border": "0", "border-left": "1px solid var(--line)", "box-shadow": "-8px 0 32px #0008",
        "z-index": "7", "padding": "8px 16px 16px", "overflow": "auto", "background": "var(--panel)",
    }
    assert _computed("body.noside #left")["display"] == "none"   # collapsed is still no panel at all


def test_nothing_dims_the_page_behind_the_panel():
    """The scrim is gone: getComputedStyle(body, "::after").content is "none" with no rule giving body a generated box."""
    assert [sel for sel, decls in [(s, d) for _, s, d in _rules(_page_css()) if s.startswith("body") and "::" in s] if decls.get("content", "none") != "none"] == []


def test_a_click_on_the_map_no_longer_closes_the_panel():
    src = CHROME.read_text()
    assert "e.target === document.body" not in src and 'document.addEventListener("click"' not in src
    assert 'e.key === "Escape" && sideOpen()' in src   # Escape still closes it


def test_the_panel_keeps_its_place_in_the_layers_of_the_page():
    assert {s: _computed(s)["z-index"] for s in ("#help", "#menu", "#left", "#status", "#legend")} == {"#help": "20", "#menu": "10", "#left": "7", "#status": "5", "#legend": "4"}
    assert "z-index" not in _computed("#replay")   # the bar is kept clear by where it sits, not by a layer


def test_the_fit_takes_the_open_drawers_width_off_the_map():
    src = GRAPH.read_text()
    assert 'const s = mapSize(), side = sideOpen() ? $("left").offsetWidth : 0' in src
    assert "tx: v.tx + side" not in src   # the drawer sits at the right: the fitted view starts at the map's left edge, unshifted


def test_a_reveal_keeps_a_selection_between_the_drawer_and_the_map_edge():
    reveal = GRAPH.read_text().split("function revealSelected()")[1].split("function edgeLines")[0]
    assert '{ left: REVEAL_INSET, right: $("left").offsetWidth + REVEAL_INSET, top: REVEAL_INSET,' in reveal


def test_the_panel_has_a_grip_a_pointer_and_a_keyboard_can_drag():
    m = re.search(r'<div id="side-grip"[^>]*>', PAGE_SRC().split("<script>")[0])
    assert m, "the page needs a real grip element, not only a pseudo-element"
    for attr in ('tabindex="0"', 'role="separator"', 'aria-orientation="vertical"', 'aria-valuemin="320"', "title="):
        assert attr in m.group(0), attr
    assert _computed("#side-grip")["cursor"] == "col-resize"
    hit = _computed("#left::before")
    assert hit["content"] == '""' and hit["cursor"] == "col-resize" and hit["width"] == "8px"
    src = CHROME.read_text()
    assert '$("side-grip").addEventListener("pointerdown"' in src and "SIDE_HIT = 8" in src
    assert '$("side-grip").addEventListener("dblclick"' in src
    for key in ("ArrowLeft", "ArrowRight", "Home", "End"):
        assert f'"{key}"' in src, key


def test_the_drag_stops_at_the_drawers_minimum_and_at_the_room_the_diagram_keeps():
    body, src = _computed("body"), CHROME.read_text()
    assert body["--side-min"] == "320px" and body["--side-max"] == "calc(100vw - 520px)"
    assert '"--side-min"' in src and '"--side-max"' in src   # the travel is measured from the stylesheet's own tokens, never copied into the page
    assert "SIDE_STEP = 24" in src and "SIDE_RESET = 420" in src


def test_the_width_is_remembered_across_a_reload_like_the_panels_other_state():
    src = CHROME.read_text()
    assert "UI.sideW" in src and "saveUI()" in src and "setSideWidth(UI.sideW" in src


def test_the_legend_steps_out_of_the_drawers_way_and_comes_back_when_the_panel_closes():
    assert _computed("body:not(.noside) #legend")["visibility"] == "hidden"
    assert 'e.key === "[" && !e.metaKey && !e.ctrlKey' in CHROME.read_text() and "setSide(!sideOpen())" in CHROME.read_text()


def test_the_replay_bar_and_the_zoom_buttons_keep_to_the_free_side_of_the_drawer():
    assert _computed("body:not(.noside) #replay")["left"] == "calc((100vw - var(--side-w,0px)) / 2)"
    assert _computed("body:not(.noside) #view")["right"] == "calc(var(--side-w) + 12px)"


def test_a_window_under_1024px_defaults_to_a_360px_drawer_with_504px_of_travel_at_1024():
    assert _computed("body", media="max-width: 1024px")["--side-w"] == "360px"


def test_a_window_under_900px_docks_the_panel_as_a_bottom_sheet_with_a_sideways_grip():
    body = _computed("body", media="max-width: 900px")
    assert body["--side-w"] == "52vh" and body["--side-min"] == "30vh" and body["--side-max"] == "75vh"
    left = _computed("#left", media="max-width: 900px")
    assert left["top"] == "auto" and left["left"] == "0" and left["right"] == "0" and left["border-top"] == "1px solid var(--line)"
    assert left["height"] == "var(--side-w,52vh)"
    assert _computed("body:not(.noside) #mapbox", media="max-width: 900px")["padding-bottom"] == "52vh"
    assert _computed("#side-grip", media="max-width: 900px")["cursor"] == "row-resize"
    assert _computed("#left::before", media="max-width: 900px")["cursor"] == "row-resize"


def test_the_replay_bar_stays_above_the_bottom_sheet_rather_than_under_it():
    assert _computed("body:not(.noside) #replay", media="max-width: 900px")["bottom"] == "calc(var(--side-w,52vh) + 12px)"
