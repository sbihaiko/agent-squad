"""Ticket #92: the graph lays itself out in layers, so it spreads as nodes are added and an edge stops crossing a card.

A node's column is the longest path from the root; its row follows the mean row of its parents (the barycenter heuristic). No
layout library: the page stays pure and Node-tested (ADR-0111). A dispatch's children anchor on its agent entry, one column
over, instead of on the dispatch node two columns back, so the edge to a child never crosses the entry or its check gates.
"""
from tests.dashboard_helpers import ROOT, run_node
from tests.test_dashboard_failures import BOX, _run

FIX = ROOT / "tests" / "fixtures" / "run-router-waves"


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
