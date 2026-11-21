"""Truthful filters, the named scope and Search on the phone (SPEC 1596, rules 4, 5 and 6; #1600).

Every expected value below is written out from the fixture facts the hub serves: the board rows (`t`, `n`, `l`) and the
run rows (`live`, `running`). The chat's Waiting filter left the page with the chat (#2490). The page is booted under the stub DOM of
`tests/hub_mobile_stub.py`, so the labels, the predicates and the remainder line are read where the owner sees them.
The search rule is one pure module with no DOM and no client, run under node alone (ADR-0137).

The issue cites frames 8, 9, 13 and 16 and rules 4, 5 and 6 of a Notion spec the repository does not hold; this suite
builds to the issue's acceptance criteria and reads none of those frames or rules.
"""
import json
import pathlib
import re
import shutil

import pytest

from tests.hub_mobile_stub import hub, run_js, run_page  # noqa: F401  (hub is a fixture)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEARCH_JS = ROOT / "contrib" / "squad-goal" / "scripts" / "hub-mobile-search.js"
NODE = shutil.which("node")

TABS = ["now", "work", "specs", "runs"]
# The filter of each root, stated as the owner reads it: the last option says what it includes. Work and Specs offer none
# (SPEC 2498, #2501): the board's and the spec list's filters left the page, and only Runs keeps its segment.
FILTERS = {
    "now": [],
    "work": [],
    "specs": [],
    "runs": ["Live", "Ended", "Any run"],
}

CLICK = 'const tap = t => [...document.querySelectorAll("#cr-seg button")].find(b => b.textContent === t).onclick();'


# --- (1) the labels and the scope pill -----------------------------------------------------------------------------

@pytest.mark.node_bound
def test_every_filter_ends_with_an_option_that_says_what_it_includes(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, f"""
        const seen = {{}};
        for (const t of {TABS!r}) {{ show(t); seen[t] = [...document.querySelectorAll("#cr-seg button")].map(b => b.textContent); }}
        return seen;
    """)
    assert out == FILTERS, out


@pytest.mark.node_bound
def test_no_filter_option_is_labelled_all(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, f"""
        const seen = {{}};
        for (const t of {TABS!r}) {{ show(t); seen[t] = [...document.querySelectorAll("#cr-seg button")].map(b => b.textContent); }}
        return seen;
    """)
    for tab, options in out.items():
        assert "All" not in options, f"{tab} offers an option labelled All: {options}"
    # The lead-time period bar is a filter too: its last option names the whole series, never "All".
    lead = re.search(r'data-ltf="all" type="button">([^<]*)<', hub.MOBILE_PAGE)
    assert lead, "the lead-time period bar lost its whole-series option"
    assert lead.group(1) != "All", "the lead-time filter offers an option labelled All"


# --- (2) the truthful predicates and the remainder line ------------------------------------------------------------

LIVE_ROW = {"id": "run-20261104-101010-aaaa", "label": "a live process", "state": "running", "badge": "running",
            "rank": 0, "live": True, "age": 12, "spend": 1.25, "agents": 2, "kind": "code", "running": True}
STALE_ROW = {"id": "run-20261104-090000-bbbb", "label": "a stale run", "state": "running", "badge": "running",
             "rank": 1, "live": False, "age": 20, "spend": 0.5, "agents": 0, "kind": "code", "running": True}
# An ended run is what the Ended section reads: the Runs segment never hides it, so the Live remainder never counts it.
ENDED_ROW = {"id": "run-20261103-080000-cccc", "label": "an ended run", "state": "done", "badge": "done",
             "rank": 2, "live": False, "age": 300, "spend": 3.0, "agents": 0, "kind": "code", "running": False}


@pytest.mark.node_bound
def test_live_lists_only_runs_with_a_live_process(hub):  # noqa: F811
    """The stale row's `running` fact still says yes, its `live` fact says no: Live keeps the live process alone. The
    ended run stays under Ended today, so the remainder counts the live candidate pool alone: one, the stale row."""
    out = run_page(hub.MOBILE_PAGE, f"""
        show("runs"); await loadRuns();
        {CLICK}
        tap("Live");
        return {{ rows: [...$("runs-live").children].map(b => b.dataset.name),
                 ended: [...$("runs-ended").children].map(b => b.dataset.name),
                 rest: $("runs-rest").textContent, restHidden: $("runs-rest").hidden }};
    """, routes={"/api/runs": [{"body": json.dumps([LIVE_ROW, STALE_ROW])}],
                "/api/ended": [{"body": json.dumps([ENDED_ROW])}]})
    assert out["rows"] == ["a live process"], f"Live lists a run with no live process: {out}"
    assert out["ended"] == ["an ended run"], f"Live hides a run the Ended section still shows: {out}"
    assert out["rest"] == "1 hidden by Live" and out["restHidden"] is False, out


def test_the_phone_page_offers_no_chat_filter(hub):  # noqa: F811
    """The Waiting filter and the session list left the page with the chat (#2490)."""
    body = hub.MOBILE_PAGE
    assert "Waiting" not in body and "Every chat" not in body, "the phone page still offers the chat filter"
    assert 'id="sessions"' not in body and 'id="chat-rest"' not in body, "the phone page still holds a session list"


# --- (3) Search on Work and Specs ----------------------------------------------------------------------------------

WORK_BOARD = {"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None,
              "groups": [{"name": "Backlog", "n": 2, "items": [
                              {"n": 7, "t": "Fix the phone", "l": ["area:phone"]},
                              {"n": 8, "t": "Draw a chart", "l": ["area:chart"]}]},
                          {"name": "Todo", "n": 1, "items": [
                              {"n": 42, "t": "Land the shell", "l": ["area:phone"]}]}]}

WORK_DRIVER = """
    await loadBoard(); show("work");
    $("work-search").onclick();
    const opened = { bar: !$("searchbar").hidden, control: $("control-row").hidden };
    const query = q => { $("search-input").value = q; $("search-input").oninput(); return [...$("work-results").children].map(b => b.dataset.n); };
    const byLabel = query("phone");
    const replaced = { cards: $("cards").hidden, results: $("work-results").hidden };
    const byNumber = query("42");
    const byTitle = query("chart");
    $("search-input").onkeydown({ key: "Escape" });
    const cleared = { bar: $("searchbar").hidden, cards: $("cards").hidden, results: $("work-results").hidden, value: $("search-input").value };
    return { opened, byLabel, replaced, byNumber, byTitle, cleared };
"""


@pytest.mark.node_bound
def test_work_search_matches_title_number_and_label_and_replaces_the_list(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, WORK_DRIVER, board=[WORK_BOARD])
    assert out["opened"] == {"bar": True, "control": True}, f"the title row's Search does not open the field: {out}"
    assert out["byLabel"] == ["7", "42"], f"a label match is missing or out of order: {out}"
    assert out["replaced"] == {"cards": True, "results": False}, f"the results do not replace the list: {out}"
    assert out["byNumber"] == ["42"], f"a number match is missing: {out}"
    assert out["byTitle"] == ["8"], f"a title match is missing: {out}"
    assert out["cleared"] == {"bar": True, "cards": False, "results": True, "value": ""}, f"Esc does not clear it: {out}"


@pytest.mark.node_bound
def test_work_search_clears_on_back(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        await loadBoard(); show("work");
        $("work-search").onclick();
        $("search-input").value = "phone"; $("search-input").oninput();
        const before = !$("work-results").hidden;
        $("search-back").onclick();
        return { before, bar: $("searchbar").hidden, cards: $("cards").hidden, value: $("search-input").value };
    """, board=[WORK_BOARD])
    assert out == {"before": True, "bar": True, "cards": False, "value": ""}, out


# A card with a live process carries `ho`: the Work segment's own fact. Search reads the same pool the segment reads.
SEGMENTED_BOARD = {"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None,
                   "groups": [{"name": "Todo", "n": 2, "items": [
                                   {"n": 7, "t": "Fix the phone", "l": ["area:phone"], "ho": True},
                                   {"n": 9, "t": "Rewrite the phone", "l": ["area:phone"]}]}]}


@pytest.mark.node_bound
def test_work_search_names_every_card_the_board_holds(hub):  # noqa: F811
    """Work has no segment (SPEC 2498, #2501): a search names every card the board holds that matches, and the columns
    step aside while the results stand."""
    out = run_page(hub.MOBILE_PAGE, """
        await loadBoard(); show("work");
        $("work-search").onclick();
        $("search-input").value = "phone"; $("search-input").oninput();
        return { rows: [...$("work-results").children].map(b => b.dataset.n), cards: $("cards").hidden };
    """, board=[SEGMENTED_BOARD])
    assert out["rows"] == ["7", "9"], f"the search leaves a card out: {out}"
    assert out["cards"] is True, f"the columns still stand behind the results: {out}"


@pytest.mark.node_bound
def test_leaving_work_with_search_open_brings_the_control_row_back(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        await loadBoard(); show("work");
        $("work-search").onclick();
        const open = $("control-row").hidden;
        show("specs");
        return { open, after: $("control-row").hidden, bar: $("searchbar").hidden };
    """)
    assert out == {"open": True, "after": True, "bar": True}, out   # Specs draws no control row either


SPEC_BOARD = {"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None, "done": "Closed",
              "groups": [{"name": "Todo", "n": 2, "items": [
                              {"n": 1540, "t": "Phone hub redesign", "sp": True, "l": ["area:phone"], "r": "agent-squad"},
                              {"n": 1500, "t": "Old spec", "sp": True, "l": ["area:legacy"], "r": "agent-squad"}]},
                          {"name": "Closed", "end": True, "n": 0, "items": []}]}


@pytest.mark.node_bound
def test_specs_search_matches_its_own_list_and_replaces_it(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        await loadBoard(); show("specs");
        $("specs-search").onclick();
        const query = q => { $("search-input").value = q; $("search-input").oninput(); return [...$("specs-list").children].map(r => r.children[0].textContent); };
        const all = [...$("specs-list").children].map(r => r.children[0].textContent);
        const byTitle = query("phone");
        const byLabel = query("legacy");
        const byNumber = query("1540");
        $("search-back").onclick();
        return { all, byTitle, byLabel, byNumber, restored: [...$("specs-list").children].map(r => r.children[0].textContent) };
    """, board=[SPEC_BOARD])
    assert out["all"] == ["agent-squad · SPEC 1540 Phone hub redesign", "agent-squad · SPEC 1500 Old spec"], out
    assert out["byTitle"] == ["agent-squad · SPEC 1540 Phone hub redesign"], f"a title match is missing: {out}"
    assert out["byLabel"] == ["agent-squad · SPEC 1500 Old spec"], f"a label match is missing: {out}"
    assert out["byNumber"] == ["agent-squad · SPEC 1540 Phone hub redesign"], f"a number match is missing: {out}"
    assert out["restored"] == out["all"], f"Back does not restore the list: {out}"


# --- the search rule is one pure module, and its facts are asserted directly ----------------------------------------

def test_the_search_rule_holds_no_dom_and_no_client():
    assert SEARCH_JS.is_file(), "the search rule module is missing"
    text = SEARCH_JS.read_text()
    for banned in ("document", "fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "import "):
        assert banned not in text, f"the search rule module holds {banned!r}"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_search_rule_matches_title_number_and_label():
    src = SEARCH_JS.read_text()
    rows = [{"n": 7, "title": "Fix the phone", "labels": ["area:phone"]},
            {"n": 8, "title": "Draw a chart", "labels": ["area:chart"]},
            {"n": 42, "title": "Land the shell", "labels": ["area:phone"]}]
    got = run_js(src, f"""searchRows({json.dumps(rows)}, "phone").map(r => r.n)""")
    assert got == [7, 42], got
    assert run_js(src, f"""searchRows({json.dumps(rows)}, "42").map(r => r.n)""") == [42]
    assert run_js(src, f"""searchRows({json.dumps(rows)}, "chart").map(r => r.n)""") == [8]
    assert run_js(src, f"""searchRows({json.dumps(rows)}, "").map(r => r.n)""") == [7, 8, 42]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_search_rule_never_matches_a_number_inside_another_number():
    """A number is compared exactly: the query 42 is card 42, never card 142, and #42 is the same card."""
    src = SEARCH_JS.read_text()
    rows = [{"n": 142, "title": "An older shell", "labels": []},
            {"n": 42, "title": "Land the shell", "labels": []}]
    assert run_js(src, f"""searchRows({json.dumps(rows)}, "42").map(r => r.n)""") == [42]
    assert run_js(src, f"""searchRows({json.dumps(rows)}, "#42").map(r => r.n)""") == [42]
    assert run_js(src, f"""searchRows({json.dumps(rows)}, "#142").map(r => r.n)""") == [142]


SPECS_JS = ROOT / "contrib" / "squad-goal" / "scripts" / "hub-mobile-specs.js"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_spec_progress_keeps_each_spec_labels():
    """The spec row carries its labels for the search rule, and a spec with none carries an empty list."""
    board = {"done": "Closed", "groups": [
        {"name": "Todo", "items": [{"n": 1540, "t": "Phone hub redesign", "sp": True, "l": ["area:phone"]},
                                   {"n": 1500, "t": "Old spec", "sp": True}]},
        {"name": "Closed", "end": True, "items": []}]}
    got = run_js(SPECS_JS.read_text(), f"specProgress({json.dumps(board)}).map(s => [s.n, s.labels])")
    assert got == [[1540, ["area:phone"]], [1500, []]], got


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_remainder_line_counts_what_the_filter_hides():
    src = SEARCH_JS.read_text()
    assert run_js(src, 'restLine(5, 3, "Live")') == "2 hidden by Live"
    assert run_js(src, 'restLine(1, 0, "Waiting")') == "1 hidden by Waiting"
    assert run_js(src, 'restLine(3, 3, "Any card")') == ""
