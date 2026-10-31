"""The editor graph reads at a glance (#914, SPEC 889).

Each node kind has its own icon and color. Two edges never draw over each other. The model sits on its own line and is never cut.
The core decides the icon and the routes; the page only paints them.
"""
import itertools
import re

from squad import graph
from tests.dashboard_helpers import PAGE_SRC, ROOT, run_node

CSS = (ROOT / "dashboard" / "page.css").read_text()
GRAPH_JS = (ROOT / "dashboard" / "page" / "graph.js").read_text()
BOX = {"w": 200, "h": 60}


def _mark(kind):
    return run_node([{"op": "kindmark", "kind": kind}])[0]


def _routes(layout, pairs):
    return run_node([{"op": "edgeroutes", "layout": layout, "pairs": pairs, "box": BOX}])[0]


def test_every_node_kind_has_its_own_icon():
    marks = {k: _mark(k) for k in graph.KINDS}
    glyphs = [m["glyph"] for m in marks.values()]
    assert all(g for g in glyphs), "every kind needs a glyph"
    assert len(set(glyphs)) == len(graph.KINDS), f"two kinds share an icon: {marks}"
    assert all(m["cls"] == f"k-{k}" for k, m in marks.items())


def test_an_unknown_kind_gets_a_glyph_and_its_own_class_never_a_crash():
    got = _mark("nope")
    assert got["glyph"] and got["cls"] == "k-nope"


def test_every_node_kind_has_its_own_color_in_the_stylesheet():
    colors = {}
    for k in graph.KINDS:
        m = re.search(rf"\.card\.k-{k}\s+\.kc\s*\{{\s*fill:\s*(#[0-9a-fA-F]{{3,6}})", CSS)
        assert m, f"page.css has no chip color for kind {k}"
        colors[k] = m.group(1).lower()
    assert len(set(colors.values())) == len(graph.KINDS), f"two kinds share a color: {colors}"


def test_two_edges_never_draw_over_each_other():
    # A feeds three children; two of them feed one target; a sibling column sits beside them.
    layout = {"A": {"x": 20, "y": 20}, "B": {"x": 250, "y": 20}, "C": {"x": 250, "y": 96}, "D": {"x": 250, "y": 172},
              "F": {"x": 480, "y": 60}, "G": {"x": 20, "y": 200}}
    pairs = [{"from": "A", "to": "B"}, {"from": "A", "to": "C"}, {"from": "A", "to": "D"},
             {"from": "B", "to": "F"}, {"from": "C", "to": "F"}, {"from": "G", "to": "D"}]
    routes = _routes(layout, pairs)
    assert [(r["from"], r["to"]) for r in routes] == [(p["from"], p["to"]) for p in pairs]
    segs = {(r["from"], r["to"]): r["segs"] for r in routes}
    for k, s in segs.items():
        assert s and all(q["x1"] == q["x2"] or q["y1"] == q["y2"] for q in s), f"{k} is not orthogonal"
    def shared(p, q, axis):
        # two segments on one line that share a stretch of positive length
        a, b = sorted([p[axis + "1"], p[axis + "2"]]), sorted([q[axis + "1"], q[axis + "2"]])
        return max(a[0], b[0]) < min(a[1], b[1])

    for (ka, sa), (kb, sb) in itertools.combinations(segs.items(), 2):
        for p, q in itertools.product(sa, sb):
            if p["x1"] == p["x2"] and q["x1"] == q["x2"] and p["x1"] == q["x1"]:
                assert not shared(p, q, "y"), f"{ka} and {kb} run on one vertical line"
            if p["y1"] == p["y2"] and q["y1"] == q["y2"] and p["y1"] == q["y1"]:
                assert not shared(p, q, "x"), f"{ka} and {kb} run on one horizontal line"
    starts = [(r["segs"][0]["x1"], r["segs"][0]["y1"]) for r in routes]
    ends = [(r["segs"][-1]["x2"], r["segs"][-1]["y2"]) for r in routes]
    assert len(set(starts)) == len(starts), "two edges leave one port"
    assert len(set(ends)) == len(ends), "two edges enter one port"


def test_a_route_leaves_its_node_and_arrives_at_its_target():
    layout = {"A": {"x": 20, "y": 20}, "B": {"x": 250, "y": 96}}
    [r] = _routes(layout, [{"from": "A", "to": "B"}])
    assert r["segs"][0]["x1"] == 20 + BOX["w"]
    assert r["segs"][-1]["x2"] == 250 - 3 and r["segs"][-1]["arrow"] is True
    assert sum(1 for q in r["segs"] if q["arrow"]) == 1


def test_the_model_line_is_its_own_line_and_is_fitted_not_cut():
    assert 'class="kd model"' in GRAPH_JS, "the model needs its own text line with the model class"
    assert "clipText(n.kind" not in GRAPH_JS and "clipText(ml.text" not in GRAPH_JS, "the model text is never cut"
    assert "textLength" in GRAPH_JS and "getComputedTextLength" in GRAPH_JS, "a model wider than the card is fitted, not cut"
    assert ".card .kd.model" in CSS


def test_the_page_draws_the_routes_and_the_kind_mark():
    src = PAGE_SRC()
    assert "core.edgeRoutes(" in GRAPH_JS and "core.kindMark(" in GRAPH_JS
    assert "k-" in GRAPH_JS and 'class="kc"' in GRAPH_JS
    assert "core.edgeRoutes" in src
