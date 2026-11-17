"""Hub chrome text (#1818): the board pane's title reads TASKS and the run list's first group reads WORKERS.

Two labels are renamed and nothing else moves. These pin the new text where it is drawn, and pin the
neighbours the rename must leave alone: the group key `running`, the running header's occupancy text and its
spend tooltip, the `hidden:` strip, the column names, the footer, and the other five run groups.
"""
import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_board_title_builder", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _css(hub, selector):
    m = re.search(r"(?:^|[\n}])\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", hub.PAGE)
    assert m, f"no CSS rule for {selector}"
    return m.group(1)


def _bhead(hub):
    """The docked board header's markup, from `<div id=bhead>` to the board's column area that follows it."""
    i = hub.PAGE.index("<div id=bhead>")
    return hub.PAGE[i : hub.PAGE.index("<div id=bcases>", i)]


def _groups(hub):
    """The ladder is the hub's own rule, read from the answer the page draws (#1277)."""
    return [(g["key"], g["label"]) for g in hub.run_groups([], None)]


# --- the board pane's title ---


def test_board_header_reads_no_title_and_the_tab_names_the_pane(hub):
    """Re-anchored to the owner-approved mock (#889): the board's toolbar carries no `TASKS` title; the `Kanban` tab names the pane (the owner renamed Board to Kanban and Flow charts to Charts)."""
    head = _bhead(hub)
    assert "<b>" not in head, "the board toolbar carries a bold title again"
    tabs = hub.PAGE[hub.PAGE.index("<div class=tabs"):hub.PAGE.index("</div>", hub.PAGE.index("<div class=tabs"))]
    assert [re.sub(r"<[^>]*>", "", t) for t in re.findall(r"<button[^>]*>.*?</button>", tabs)] == ["Kanban", "Runs", "Flow", "Loop", "Settings"]   # re-anchored (#1272): Runs is its own tab; #1028 moved the run history into Settings > Loop; #1743: the Loop tab


def test_board_has_no_collapsed_state(hub):
    """The docked board's collapse left with the tab layout (#889): no `#board.off` header rule remains."""
    assert "#board.off #bhead b" not in hub.PAGE


def test_board_header_keeps_its_count_and_its_controls(hub):
    """The count span stays; the header keeps the swimlane selector and the filter bar, and no maximize or collapse (#899)."""
    head = _bhead(hub)
    assert '<span id=bt class=m></span>' in head
    assert '<select id=lmode' in head and '<details id=fw' not in head and '<details id=fs' in head
    assert 'id=bz' not in head and 'id=bx' not in head


def test_no_issues_and_prs_title_is_left_on_the_page(hub):
    assert "ISSUES" not in hub.PAGE


# --- the run list's first group ---


def test_run_list_first_group_header_reads_workers(hub):
    """The label of the first group is WORKERS; the key it groups on stays `running`."""
    groups = _groups(hub)
    assert groups[0] == ("running", "WORKERS")


def test_the_answer_carries_the_live_group_alone(hub):
    """No page draws an age band any more (#1778): the answer names the Live group and no history band."""
    assert _groups(hub) == [("running", "WORKERS")]


def test_the_running_group_key_is_unchanged(hub):
    """The rename is the label's text: a live run still groups under the key `running`."""
    assert hub.group_of({"state": "running", "age": 60, "live": True}) == "running"


def test_the_running_pill_keeps_its_occupancy_and_its_tooltip(hub):
    """The header beside WORKERS still prints occupancy and still carries the spend as its tooltip (#1508)."""
    rows = [{"state": "running", "live": True, "spend": s, "agents": 3} for s in (1.1, 2.2, 3.3)]
    h = hub.group_header("running", rows, {"runs": 10, "children": 20})
    assert h["text"] == "RUNS 3/10 · AGENTS 9/20"
    assert h["title"] == "$6.60"


# --- the rest of the page's text ---


def test_the_hidden_strip_is_unchanged(hub):
    strip = _css(hub, ".hidbar")
    assert "min-width:120px;max-width:120px" in strip
    assert '<span class=lbl>${esc(g.name)}</span>' in hub.PAGE


def test_the_column_names_are_unchanged(hub):
    m = re.search(r"const COLORS=\{(.*?)\};", hub.PAGE)
    assert m, "COLORS missing"
    # The map is written from the column table, so its key order and quoting are not the guard; its names and colours are.
    assert json.loads("{" + m.group(1) + "}") == {
        # the shipped columns (#247), then the one open-PR lane beside the axis (#440); each wears a chart role
        "Backlog": "var(--chart-series-8)", "Todo": "var(--chart-series-1)", "Doing": "var(--chart-wait)",
        "Code Review": "var(--chart-series-5)", "Code Complete": "var(--chart-warn)", "Gating": "var(--chart-series-7)",
        "Closed": "var(--chart-ok)", "Abandoned": "var(--chart-err)", "Open PRs": "var(--chart-bar)",
    }


def test_the_machine_card_is_unchanged(hub):
    # re-anchored to #983: the Settings strip became the sidebar's Monitor card (owner renamed it)
    assert "<details id=inst class=sp open><summary>Monitor<span class=sr id=instsr></span></summary>" in hub.PAGE


def test_the_header_names_only_the_project_dropdown(hub):
    """The owner: no "Squad hub" title; the header holds only the project dropdown."""
    h1 = hub.PAGE[hub.PAGE.index("<header class=top><h1>"):hub.PAGE.index("</h1>", hub.PAGE.index("<header class=top><h1>"))]
    assert "Squad hub" not in h1 and "<button id=pj" in h1
