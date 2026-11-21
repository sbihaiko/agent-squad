"""The Runs tab lists Live then Ended today, as rows that open the run sheet, with no embedded frame (#1272).

Live draws each live row once, in the full form (#1770): the childcc pass, the live runs and the hand hold, under one heading
that carries the occupancy the runs answer built. The age bands left the desktop page.

The served page's own row code runs under node over a fixture view; the words are read back as the owner sees them.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
NODE = shutil.which("node")
DECLS = ("usd", "age", "label", "labelTip", "sub", "short", "name", "esc", "iq")
LIVE = {"id": "run-20261103-101010-aaaa", "label": "gui-1272", "state": "running", "badge": "running",
        "rank": 0, "running": True, "spend": 1.25, "age": 12, "dur": 7, "port": 50580,
        "issues": ["1272"], "titles": {"1272": "The tab draws each run once"}}
ENDED = {"id": "run-20261103-080000-bbbb", "label": "gui-1268", "state": "needs-human",
         "badge": "ended · needs a decision", "rank": 5, "running": False, "spend": 3.4, "age": 3600}
HOLD = {"id": "d-1710", "kind": "hold", "label": "HAL9k · #1710", "state": "held", "badge": "held",
        "rank": 0, "running": True, "spend": 0, "age": 30, "dur": 0, "port": ""}
GROUPS = [{"key": "running", "label": "WORKERS", "text": "Runs 1/4 · Agents 1/12", "title": "$1.25", "empty": False},
          {"key": "1h", "label": "≤ 1 h", "text": "1 · $3.40", "title": "$3.40", "empty": False}]


def _cc(held):
    return {"id": "childcc", "kind": "childcc", "rank": 0, "state": "running", "badge": "childcc", "label": "childcc pass",
            "live": True, "age": 9, "lines": ["run-20261103-101010-aaaa c1 #1272 · in review"], "held": held}


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_runs_tab", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _decl(src, name):
    i = src.index(f"\nconst {name}=") + 1
    depth, k = 0, i + len(f"const {name}=")
    while k < len(src):
        c = src[k]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ";" and depth == 0:
            return src[i : k + 1]
        k += 1
    raise AssertionError(f"const {name} is not closed")


def _draw(hub, scenarios):
    if NODE is None:
        pytest.skip("node is required")
    page = hub.PAGE
    helpers = "\n".join(_decl(page, d) for d in DECLS)
    mark = page[page.index("function loadMark("):]
    helpers += "\n" + mark[: mark.index("\n")]
    draw = page[page.index("function drawMain("):page.index("function refresh(")]
    prelude = ('const el={innerHTML:""};const $=()=>el;'
               'let TAB="board",runs=[],allRuns=[],ENDED=[],GROUPS=[],slots=[],pins=new Set();')
    body = ("const out=[];for(const s of " + json.dumps(scenarios) + "){TAB=s.tab;allRuns=s.runs;"
            "runs=allRuns.filter(r=>r.kind!==\"childcc\");ENDED=s.ended;GROUPS=s.groups||[];"
            "el.innerHTML='';drawMain();out.push(el.innerHTML)}console.log(JSON.stringify(out));")
    src = "\n".join([prelude, helpers, draw, body])
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _visible(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html)).strip()


def test_the_runs_tab_lists_live_then_ended_today(hub):
    html, = _draw(hub, [{"tab": "runs", "runs": [LIVE], "ended": [ENDED, LIVE], "groups": GROUPS}])
    assert _visible(html) == (
        "Live Runs 1/4 · Agents 1/12 "
        "running gui-1272 The tab draws each run once $1.25 · 7m · last write 12s ago · 0-aaaa · :50580 ○ "
        "Ended today ended · needs a decision gui-1268 $3.40 · last write 60m ago")


def test_the_live_heading_carries_the_occupancy_and_its_title_from_the_answer(hub):
    html, = _draw(hub, [{"tab": "runs", "runs": [LIVE], "ended": [], "groups": GROUPS}])
    assert '<span class=sr title="$1.25">Runs 1/4 · Agents 1/12</span>' in html
    assert "1 · $3.40" not in html, "an age band's header is drawn"


def test_a_live_row_carries_its_state_and_an_ended_row_its_verdict(hub):
    html, = _draw(hub, [{"tab": "runs", "runs": [LIVE], "ended": [ENDED], "groups": GROUPS}])
    live_part, ended_part = html.split("<h3>Ended today</h3>")
    assert 'class="b running">running</span>' in live_part
    assert 'class="b needs-human">ended · needs a decision</span>' in ended_part


def test_each_live_row_is_drawn_once(hub):
    html, = _draw(hub, [{"tab": "runs", "runs": [_cc(LIVE["id"]), LIVE, HOLD], "ended": [LIVE], "groups": GROUPS}])
    assert html.count("gui-1272") == 1, "a live run is drawn twice"
    assert html.count("childcc pass") == 1 and html.count("HAL9k · #1710") == 1
    assert "WORKERS" not in html, "the old WORKERS group is drawn"


def test_the_childcc_pass_is_a_live_row_that_opens_the_run_it_holds(hub):
    held, idle, gone = _draw(hub, [
        {"tab": "runs", "runs": [_cc(LIVE["id"]), LIVE], "ended": [], "groups": GROUPS},
        {"tab": "runs", "runs": [_cc(None), LIVE], "ended": [], "groups": GROUPS},
        {"tab": "runs", "runs": [_cc("run-not-listed")], "ended": [], "groups": GROUPS}])
    cc = re.search(r'<div class="r cc[^>]*>', held).group(0)
    assert f'data-rid="{LIVE["id"]}"' in cc, "the childcc row does not open the run it holds"
    assert held.index("childcc pass") < held.index("gui-1272"), "the pass is not the first live row"
    for html in (idle, gone):
        assert "data-rid" not in re.search(r'<div class="r cc[^>]*>', html).group(0), "a pass that holds no listed run opens one"
    assert "run-20261103-101010-aaaa c1 #1272 · in review" in held, "the pass lost its line per child"


def test_a_hand_hold_is_a_live_row_that_opens_nothing(hub):
    html, = _draw(hub, [{"tab": "runs", "runs": [HOLD], "ended": [], "groups": GROUPS}])
    assert "HAL9k · #1710" in html and 'class="b held">held</span>' in html
    assert 'data-rid="d-1710"' not in html, "a hand hold opens a run sheet it has no run for"


def test_every_run_row_opens_the_sheet_and_no_frame_is_drawn(hub):
    html, = _draw(hub, [{"tab": "runs", "runs": [LIVE], "ended": [ENDED], "groups": GROUPS}])
    assert f'data-rid="{LIVE["id"]}"' in html and f'data-rid="{ENDED["id"]}"' in html
    assert "iframe" not in html


def test_an_empty_pane_says_so_in_one_sentence(hub):
    html, = _draw(hub, [{"tab": "runs", "runs": [], "ended": [], "groups": GROUPS}])
    assert _visible(html) == "No run is live and none ended today."


def test_the_runs_tab_draws_nothing_while_another_tab_is_open(hub):
    html, = _draw(hub, [{"tab": "flow", "runs": [LIVE], "ended": [ENDED], "groups": GROUPS}])
    assert html == ""


def test_the_runs_pane_is_one_list_with_no_age_band(hub):
    """#1770: the bottom list (WORKERS and the age bands) left the pane; Live carries its rows."""
    page = hub.PAGE
    pane = page[page.index("id=tab-runs"):page.index("</section>", page.index("id=tab-runs"))]
    assert "id=main" in pane and "id=list" not in pane, "the pane still has a second list"
    for gone in ("openGroups", "no runs in ", "class=\"gh ", "groupOf"):
        assert gone not in page, f"the desktop page still carries {gone!r}"


def test_the_groups_come_from_the_answer_and_no_caps_are_substituted(hub):
    """The hub builds the occupancy header; the page's own rule and the served caps constant are gone (#1277)."""
    src = hub.PAGE
    assert 'fetch("/api/runs?groups=1")' in src, "the page does not ask for the answer that carries the groups"
    assert "GROUPS=Array.isArray(d)?[]:d.groups||[]" in src, "the page does not take the groups the answer carries"
    assert "GROUPS=d.runs||[]" not in src
    assert "function groupHeader(" not in src, "the page builds a group header again"
    assert "const CAPS=" not in src and "__CAPS__" not in src, "the caps are substituted into the page again"


def test_the_runs_tab_has_one_card_rule_and_no_rule_for_the_removed_list():
    """#1698 gave the two lists one layout; #1770 left one list, so no rule names the removed one."""
    css = HUB_PY.read_text()
    assert "#tab-runs #list" not in css, "a rule still styles the removed list"
    assert "#main .r{" in css and "#main h3{" in css, "the list has no card rule or heading rule"
