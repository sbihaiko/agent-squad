"""Children of one dispatch group by status (done with one outcome, running with one harness); child cards clip, carry unique titles and share a bus."""
import json
import re

from tests.dashboard_helpers import PAGE_SRC, run_node
from tests.test_dashboard_failures import BOX, _run
from tests.test_dashboard_layout import _layout


def _key(dispatch, state, harness=""):
    return json.dumps(["group", dispatch, state, harness], separators=(",", ":"))


OUTCOMES = [None, "pass", "fail", "deadline", "needs-human"]


def _kids(n, dispatch="route"):
    doc, events = _run()
    one = run_node([{"op": "children", "events": events}])[0][0]
    return doc, [{**one, "dispatch": dispatch, "name": f"{dispatch}.router.{i}", "label": "issue #11", "index": i, "running_for": None, "outcome": OUTCOMES[i % 5]} for i in range(1, n + 1)]


def _mk(n, outcome, start=1, dispatch="route", **kw):
    _, base = _kids(1, dispatch)
    return [{**base[0], "name": f"{dispatch}.router.{start + i}", "index": start + i, "outcome": outcome, "running_for": None, "rejected": False, **kw} for i in range(n)]


def _split(kids, expanded=()):
    return run_node([{"op": "splitkids", "children": kids, "expanded": list(expanded)}])[0]


def test_done_children_with_one_outcome_form_one_group_each_so_21_passed_and_5_failed_make_two_cards():
    kids = _mk(21, "pass") + _mk(5, "fail", start=22)
    s = _split(kids)
    assert s["singles"] == [] and [g["name"] for g in s["groups"]] == ["route · 21 passed", "route · 5 failed"]
    assert [g["key"] for g in s["groups"]] == [_key("route", "passed"), _key("route", "failed")]
    assert [len(g["kids"]) for g in s["groups"]] == [21, 5]


def test_a_group_needs_three_children_two_stay_cards():
    s = _split(_mk(2, "fail") + _mk(3, "pass", start=3))
    assert [g["name"] for g in s["groups"]] == ["route · 3 passed"]
    assert [c["name"] for c in s["singles"]] == ["route.router.1", "route.router.2"]


def test_the_count_is_per_dispatch_not_across_dispatches():
    s = _split(_mk(2, "pass", dispatch="route") + _mk(2, "pass", dispatch="other"))
    assert s["groups"] == [] and len(s["singles"]) == 4


def test_running_children_group_by_harness_not_by_outcome_alone():
    a = _mk(3, None, running_for=4.0, model="m-a", effort="high", skill="s1", skillLabel="s1")
    b = _mk(3, None, start=4, running_for=2.0, model="m-b", effort="high", skill="s1", skillLabel="s1")
    s = _split(a + b)
    assert [g["name"] for g in s["groups"]] == ["route · 3 running", "route · 3 running"]
    assert s["groups"][0]["key"] != s["groups"][1]["key"] and "m-a" in s["groups"][0]["harness"] and "m-b" in s["groups"][1]["harness"]
    assert "high" in s["groups"][0]["harness"] and "s1" in s["groups"][0]["harness"]


def test_a_child_started_but_not_finished_counts_as_in_flight_so_a_replay_cut_still_shows_the_running_group():
    s = _split(_mk(4, "started", model="m-a", effort="low", skill="s1", skillLabel="s1"))
    assert [g["name"] for g in s["groups"]] == ["route · 4 running"]


def test_a_queued_child_and_a_rejected_request_stay_cards():
    kids = _mk(3, None) + _mk(3, "fail", start=4, rejected=True)
    s = _split(kids)
    assert s["groups"] == [] and len(s["singles"]) == 6


def test_the_group_carries_total_time_cost_and_tokens_and_a_dash_when_the_record_has_none():
    kids = _mk(3, "pass", elapsed=10, spend=0.5, tokens_in=1000, tokens_out=200)
    g = _split(kids)["groups"][0]
    assert g["time"] == 30 and g["cost"] == 1.5 and g["tokens_in"] == 3000 and g["tokens_out"] == 600
    assert "$1.50" in g["line"] and "3.0k" in g["line"]
    g = _split(_mk(3, "pass", elapsed=None, spend=None, tokens_in=None, tokens_out=None))["groups"][0]
    assert g["cost"] is None and g["time"] is None and "$0" not in g["line"]


def test_an_expanded_group_gives_its_children_back_as_cards_and_remembers_which_group_they_belong_to():
    kids = _mk(4, "pass") + _mk(3, "fail", start=5)
    s = _split(kids, [_key("route", "passed")])
    assert [g["key"] for g in s["groups"]] == [_key("route", "failed")]
    assert [c["name"] for c in s["singles"]] == [f"route.router.{i}" for i in range(1, 5)]
    assert s["groupOf"] == {f"route.router.{i}": _key("route", "passed") for i in range(1, 5)}


def test_an_expanded_flag_for_a_group_that_shrank_below_three_does_nothing_and_comes_back_with_the_group():
    shrunk = _split(_mk(2, "pass"), [_key("route", "passed")])
    assert shrunk["groups"] == [] and shrunk["groupOf"] == {} and len(shrunk["singles"]) == 2
    again = _split(_mk(4, "pass"), [_key("route", "passed")])
    assert again["groups"] == [] and len(again["groupOf"]) == 4


def test_toggling_a_key_adds_then_removes_it_and_never_mutates_the_input():
    out = run_node([{"op": "toggle", "expanded": ["a"], "key": "b"}, {"op": "toggle", "expanded": ["a", "b"], "key": "a"}])
    assert out == [["a", "b"], ["b"]]


def test_two_presses_on_one_name_within_400_ms_are_a_double_press():
    rows = run_node([{"op": "double", "last": {"name": "x", "t": 1000}, "name": "x", "t": 1300},
                     {"op": "double", "last": {"name": "x", "t": 1000}, "name": "x", "t": 1500},
                     {"op": "double", "last": {"name": "y", "t": 1000}, "name": "x", "t": 1100},
                     {"op": "double", "last": None, "name": "x", "t": 1100}])
    assert rows == [True, False, False, False]


def test_every_outcome_has_its_own_glyph_so_color_is_never_the_only_cue():
    marks = run_node([{"op": "childmark", "c": {"outcome": o, "running_for": r}} for o, r in
                      [("pass", None), ("fail", None), ("deadline", None), ("needs-human", None), (None, 2.0), ("started", None), (None, None)]])
    glyphs = [m["glyph"] for m in marks]
    assert glyphs[:5] == ["✓", "✗", "◷", "!", "▶"]
    assert len(set(glyphs)) == len(glyphs) and all(g.strip() for g in glyphs)
    assert [m["state"] for m in marks][:5] == ["passed", "failed", "deadline", "needs-human", "running"]


def test_every_state_a_node_card_can_read_has_a_glyph_and_the_glyphs_differ():
    states = ["passed", "failed", "deadline", "needs-human", "running", "started", "not-run"]
    marks = run_node([{"op": "mark", "state": s} for s in states])
    glyphs = [m["glyph"] for m in marks]
    assert all(g and g.strip() for g in glyphs) and len(set(glyphs)) == len(glyphs)
    assert run_node([{"op": "mark", "state": "nonsense"}])[0] is None


def test_a_running_child_reads_running_even_with_an_earlier_outcome():
    assert run_node([{"op": "childmark", "c": {"outcome": "fail", "running_for": 1.0}}])[0]["state"] == "running"


def test_child_titles_carry_the_index_so_two_issue_11_cards_differ():
    _, events = _run()
    kids = run_node([{"op": "children", "events": events}])[0]
    titles = [k["title"] for k in kids]
    assert len(set(titles)) == len(titles)
    assert all(k["title"] == f'{k["index"]} · {k["label"]}' for k in kids)
    same = [k["index"] for k in kids if k["dispatch"] == kids[0]["dispatch"]]
    assert same == list(range(1, 1 + len(same)))


def test_the_roster_lists_one_row_per_child_of_the_dispatch_with_a_mark_and_the_figures():
    _, kids = _kids(14)
    kids[1].update(outcome="pass", model="claude-sonnet-5", elapsed=12, spend=0.5, turns=7)
    rows = run_node([{"op": "roster", "children": kids, "dispatch": "route"}])[0]
    assert len(rows) == 14 and [r["index"] for r in rows] == list(range(1, 15))
    r = rows[1]
    assert r["name"] == "route.router.2" and r["glyph"] == "✓" and r["state"] == "passed"
    assert r["model"] == "claude-sonnet-5" and r["time"] and r["cost"] and r["turns"] == "7"
    assert run_node([{"op": "roster", "children": kids, "dispatch": "nope"}])[0] == []


def _plan(kids, expanded=()):
    doc, _ = _kids(1)
    layout = _layout(doc)
    return layout, run_node([{"op": "plan", "children": kids, "expanded": list(expanded), "layout": layout, "box": BOX, "gap": 30, "doc": doc}])[0]


def test_a_group_card_sits_right_of_the_entry_and_reports_a_corner_for_fit():
    layout, plan = _plan(_mk(13, "pass"))
    p = plan["groupPos"][_key("route", "passed")]
    assert p["x"] == layout["router"]["x"] + BOX["w"] + 30 and p["w"] == BOX["w"] and p["h"] > BOX["h"]
    assert plan["groupPts"][_key("route", "passed")] == {"x": p["x"] + p["w"] - BOX["w"], "y": p["y"] + p["h"] - BOX["h"]}
    assert plan["childPos"] == {}


def test_two_groups_of_one_dispatch_stack_without_overlap_and_singles_take_the_columns_before_them():
    kids = _mk(12, "pass") + _mk(4, "fail", start=13) + _mk(2, "deadline", start=17)
    _, plan = _plan(kids)
    a, b = plan["groupPos"][_key("route", "passed")], plan["groupPos"][_key("route", "failed")]
    assert a["x"] == b["x"] and b["y"] >= a["y"] + a["h"]
    xs = {p["x"] for p in plan["childPos"].values()}
    assert len(plan["childPos"]) == 2 and a["x"] > max(xs)


def test_squares_per_group_row_fit_the_card_width():
    _, plan = _plan(_mk(26, "pass"))
    sq = plan["groupSq"][_key("route", "passed")]
    pos = plan["groupPos"][_key("route", "passed")]
    assert len(sq) == 26 and max(s["x"] for s in sq) + 20 <= pos["w"] and max(s["y"] for s in sq) + 20 <= pos["h"]


def test_expanding_recomputes_the_layout_with_cards_and_no_group():
    _, plan = _plan(_mk(6, "pass"), [_key("route", "passed")])
    assert plan["groups"] == [] and len(plan["childPos"]) == 6 and plan["groupPos"] == {}
    assert set(plan["groupOf"].values()) == {_key("route", "passed")}


def test_one_bus_serves_each_child_column_instead_of_a_fan_from_the_entry():
    anchor = {"x": 0, "y": 100}
    pos = [{"x": 260 + 230 * c, "y": 20 + 90 * r} for c in range(2) for r in range(7)]
    segs = run_node([{"op": "bus", "anchor": anchor, "pos": pos, "box": BOX}])[0]
    stubs = [s for s in segs if s["arrow"]]
    assert len(stubs) == 14   # one arrow into each child
    from_anchor = [s for s in segs if s["x1"] == anchor["x"] + BOX["w"]]
    assert len(from_anchor) == 1   # a single line leaves the entry
    assert all(s["x1"] == s["x2"] or s["y1"] == s["y2"] for s in segs)   # rails and stubs are axis-aligned


def test_the_child_card_clips_its_text_and_keeps_the_full_text_in_the_title():
    src = PAGE_SRC()
    assert "<clipPath" in src and 'clip-path="url(#card-clip)"' in src
    assert "<title>${esc(c.title)}" in src.replace("\n", "")


def test_the_panel_leaves_out_no_error_for_a_node_the_record_shows_failed():
    src = PAGE_SRC()
    assert "ns.cause !== null" in src and "no error on this node" in src


def test_the_load_toast_names_neither_the_folder_nor_the_reread_for_a_live_source():
    src = PAGE_SRC()
    assert "Rereading the record every" not in src.split("function followNote")[0].split("Opened run")[-1][:200]


def test_fit_reserves_the_footer_row_the_replay_bar_now_lives_in():
    src = PAGE_SRC()
    assert "m.bottom - f.getBoundingClientRect().top" in src   # the row the bar rides in: one measure, the map's edge to the row
    assert "m.bottom - f.top" in src                           # the side legend clears the same row by the one shared gap


def test_a_square_hover_names_the_child_and_its_cost():
    assert "${esc(c.title)} | ${esc(c.name)} | ${esc(m.label)} | ${esc(core.fmt.usd(c.spend))}" in PAGE_SRC()


def test_the_old_count_threshold_is_gone():
    assert "CHILD_GROUP_OVER" not in PAGE_SRC()


# --- pan the selection into view (owner decision C) ---

def _reveal(view, rect, size=None, **kw):
    insets = {"left": 376, "right": 16, "top": 16, "bottom": 16, **kw}   # the panel's 360 px plus a 16 px margin
    return run_node([{"op": "reveal", "view": view, "rect": rect, "size": size or {"w": 1200, "h": 800}, "insets": insets}])[0]


def test_a_node_under_the_panel_pans_right_to_clear_it_and_the_zoom_stays():
    v = {"k": 0.8, "tx": 20, "ty": 40}
    out = _reveal(v, {"x": 100, "y": 100, "w": 200, "h": 60})
    assert out["k"] == 0.8 and out["ty"] == 40
    assert out["tx"] + 100 * 0.8 == 376    # left edge sits at panel width + margin


def test_a_node_already_visible_right_of_the_panel_does_not_move_the_view():
    v = {"k": 1, "tx": 0, "ty": 0}
    assert _reveal(v, {"x": 500, "y": 100, "w": 200, "h": 60}) == v


def test_a_node_past_the_right_edge_pans_left_but_never_under_the_panel():
    v = {"k": 1, "tx": 0, "ty": 0}
    out = _reveal(v, {"x": 1300, "y": 100, "w": 200, "h": 60})
    assert out["tx"] + 1300 + 200 == 1200 - 16 and out["tx"] + 1300 >= 376
    wide = _reveal(v, {"x": 1000, "y": 100, "w": 900, "h": 60})
    assert wide["tx"] + 1000 == 376


def test_a_node_below_the_map_pans_up_into_view():
    out = _reveal({"k": 1, "tx": 0, "ty": 0}, {"x": 500, "y": 900, "w": 200, "h": 60})
    assert out["ty"] + 900 + 60 == 800 - 16


def test_with_the_panel_closed_the_page_does_not_ask_for_a_pan():
    src = PAGE_SRC()
    assert "function revealSelected" in src and "if (!sideOpen()" in src.split("function revealSelected")[1][:300]


# --- state visuals (owner decision B) ---

def _rule(css, selector):
    return next(body for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css) if selector in [x.strip() for x in sel.split(",")])


def test_a_running_card_has_a_thick_teal_outline_and_nothing_animates_without_the_reduced_motion_query():
    css = PAGE_SRC().split("<style>")[1].split("</style>")[0]
    body = _rule(css, ".card.running rect")
    assert "var(--run)" in body and float(re.search(r"stroke-width:([\d.]+)", body).group(1)) >= 3
    outside = re.sub(r"@media \(prefers-reduced-motion: [\w-]+\)\s*\{.*?\}\s*\}", "", css, flags=re.S)
    assert "animation" not in outside and "@keyframes" not in outside


def test_a_failure_reason_is_at_least_12_px_and_light_on_the_dark_red_card():
    css = PAGE_SRC().split("<style>")[1].split("</style>")[0]
    body = _rule(css, ".card .bd-c")
    size_var = re.search(r"font-size:var\((--[\w-]+)\)", body).group(1)
    assert int(re.search(rf"{size_var}:(\d+)px", css).group(1)) >= 13
    role = re.search(r"fill:var\((--[\w-]+)\)", body).group(1)
    fg = re.search(rf"{role}:#([0-9a-f]{{6}})", css).group(1)
    assert min(int(fg[i:i + 2], 16) for i in (0, 2, 4)) >= 0xa0   # light on every channel


def test_no_verdict_yet_never_shares_the_amber_of_a_deadline_card():
    css = PAGE_SRC().split("<style>")[1].split("</style>")[0]
    assert "--none" in _rule(css, ".state-none") and "--warn" not in _rule(css, ".state-none")
    root = re.search(r":root\s*\{([^}]*)\}", css).group(1)
    none, warn = (re.search(rf"--{n}:(#[0-9a-f]+)", root).group(1) for n in ("none", "warn"))
    assert none != warn


def test_the_node_and_child_card_markup_draws_a_glyph_text_on_every_card():
    src = PAGE_SRC()
    assert src.count('<text class="gl" x="${BOX.w - GLYPH.right}"') >= 3   # node card, child card, group card


def test_the_bus_is_one_connected_tree_when_a_later_column_hangs_off_a_short_first_column():
    anchor = {"x": 0, "y": 20}
    pos = [{"x": 260, "y": 20}, {"x": 490, "y": 20}]   # one card per column: the first rail has no span of its own
    segs = run_node([{"op": "bus", "anchor": anchor, "pos": pos, "box": BOX}])[0]
    pts = {}
    for s in segs:
        pts.setdefault((s["x1"], s["y1"]), set()).add((s["x2"], s["y2"]))
        pts.setdefault((s["x2"], s["y2"]), set()).add((s["x1"], s["y1"]))
    seen, todo = set(), [(anchor["x"] + BOX["w"], anchor["y"] + BOX["h"] / 2)]
    while todo:
        p = todo.pop()
        if p not in seen:
            seen.add(p)
            todo += list(pts.get(p, ()))
    ends = {(s["x2"], s["y2"]) for s in segs if s["arrow"]}
    assert ends <= seen   # every child stub reaches the entry: no floating gutter line
    assert any(s["x1"] == s["x2"] and s["y1"] != s["y2"] and 20 + BOX["h"] / 2 in (s["y1"], s["y2"]) for s in segs)
