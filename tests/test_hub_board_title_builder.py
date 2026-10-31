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
    m = re.search(r"const GROUPS=\[(.*?)\];", hub.PAGE)
    assert m, "GROUPS missing"
    return re.findall(r'\["([^"]*)","([^"]*)"\]', m.group(1))


# --- the board pane's title ---


def test_board_header_docked_reads_tasks(hub):
    """The `<b>` of `#bhead` — what the docked header prints — reads TASKS, and it is the only title there."""
    head = _bhead(hub)
    bs = re.findall(r"<b>(.*?)</b>", head, re.S)
    assert len(bs) == 1, "the board header carries more than one bold element: the title is no longer one label"
    assert re.sub(r"<[^>]*>", "", bs[0]).strip() == "TASKS"


def test_board_header_collapsed_reads_the_same_tasks(hub):
    """The collapsed header draws that same `<b>`: `#board.off` hides it and a docked collapse reveals it again."""
    assert "display:none" in _css(hub, "#board.off #bhead b")
    assert "display:inline" in _css(hub, "body.dockb #board.off #bhead b")


def test_board_header_keeps_its_count_and_its_controls(hub):
    """The count span stays; the header keeps the swimlane selector and the filter bar, and no maximize or collapse (#899)."""
    head = _bhead(hub)
    assert '<span id=bt class=m></span>' in head
    assert '<select id=lmode' in head and '<details id=fw' in head and '<details id=fs' in head
    assert 'id=bz' not in head and 'id=bx' not in head


def test_no_issues_and_prs_title_is_left_on_the_page(hub):
    assert "ISSUES" not in hub.PAGE


# --- the run list's first group ---


def test_run_list_first_group_header_reads_workers(hub):
    """The label of the first group is WORKERS; the key it groups on stays `running`."""
    groups = _groups(hub)
    assert groups[0] == ("running", "WORKERS")


def test_other_run_groups_keep_their_wording(hub):
    assert _groups(hub)[1:] == [("1h", "≤ 1 h"), ("2h", "1–2 h"), ("4h", "2–4 h"), ("8h", "4–8 h"), ("24h", "8–24 h"), ("d", "24 h+ (days)")]


def test_the_running_group_key_is_unchanged(hub):
    """The rename is the label's text: a live run still groups under the key `running`."""
    assert hub.group_of({"state": "running", "age": 60, "live": True}) == "running"


def test_the_running_pill_keeps_its_occupancy_and_its_tooltip(hub):
    """The header beside WORKERS still prints occupancy and still carries the spend as its tooltip (#1508)."""
    rows = [{"state": "running", "live": True, "spend": s, "agents": 3} for s in (1.1, 2.2, 3.3)]
    h = hub.group_header("running", rows, {"runs": 10, "children": 20})
    assert h["text"] == "RUNS 3/10 · AGENTS 9/20"
    assert h["title"] == "$6.60"


def test_the_group_header_still_carries_the_tooltip(hub):
    assert 'title="${esc(h.title)}"' in hub.PAGE


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
        # the shipped columns (#247), then the one open-PR lane beside the axis (#440)
        "Backlog": "#9aa4b1", "Todo": "#79b8ff", "Doing": "#e8d44d", "Code Review": "#fdba74", "Code Complete": "#f5b041", "Pushing": "#b087f9",
        "Testing": "#f78fb3", "Closed": "#3ecf8e", "Abandoned": "#ff6b6b", "Open PRs": "#5aa9ff",
    }


def test_the_footer_is_unchanged(hub):
    assert "<span>load</span><b id=ldline>CPU ? · RAM ? · GPU ?</b>" in hub.PAGE
