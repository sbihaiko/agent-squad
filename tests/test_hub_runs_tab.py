"""The Runs tab lists Live then Ended today, as rows that open the run sheet, with no embedded frame (#1272).

The served page's own row and group code runs under node over a fixture view; the words are read back as the owner sees them.
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
HELPERS = (r"^const usd=[^\n]*", r"^const age=s=>[^\n]*", r"^const label=[^\n]*",
           r"^const short=[^\n]*", r"^const name=[^\n]*", r"^const esc=[^\n]*")
LIVE = {"id": "run-20261103-101010-aaaa", "label": "gui-1272", "state": "running", "badge": "running",
        "rank": 0, "running": True, "spend": 1.25, "age": 12}
ENDED = {"id": "run-20261103-080000-bbbb", "label": "gui-1268", "state": "needs-human",
         "badge": "ended · needs a decision", "rank": 5, "running": False, "spend": 3.4, "age": 3600}


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


def _draw(hub, scenarios):
    if NODE is None:
        pytest.skip("node is required")
    page = hub.PAGE
    helpers = "\n".join(re.search(p, page, re.M).group(0) for p in HELPERS)
    draw = page[page.index("function drawMain("):page.index("function refresh(")]
    prelude = 'const el={innerHTML:""};const $=()=>el;let TAB="board",runs=[],ENDED=[];'
    body = ("const out=[];for(const s of " + json.dumps(scenarios) + "){TAB=s.tab;runs=s.runs;ENDED=s.ended;"
            "el.innerHTML='';drawMain();out.push(el.innerHTML)}console.log(JSON.stringify(out));")
    src = "\n".join([prelude, helpers, draw, body])
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _visible(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html)).strip()


def test_the_runs_tab_lists_live_then_ended_today(hub):
    live_html, = _draw(hub, [{"tab": "runs", "runs": [LIVE], "ended": [ENDED, LIVE]}])
    assert _visible(live_html) == (
        "Live gui-1272 $1.25 · last write 12s ago "
        "Ended today ended · needs a decision gui-1268 $3.40 · last write 60m ago")


def test_a_live_row_carries_no_verdict_tag_and_an_ended_row_does(hub):
    html, = _draw(hub, [{"tab": "runs", "runs": [LIVE], "ended": [ENDED]}])
    live_part, ended_part = html.split("<h3>Ended today</h3>")
    assert 'class="b ' not in live_part
    assert 'class="b needs-human">ended · needs a decision</span>' in ended_part


def test_every_row_opens_the_sheet_and_no_frame_is_drawn(hub):
    html, = _draw(hub, [{"tab": "runs", "runs": [LIVE], "ended": [ENDED]}])
    assert f'data-rid="{LIVE["id"]}"' in html and f'data-rid="{ENDED["id"]}"' in html
    assert "iframe" not in html


def test_an_empty_pane_says_so_in_one_sentence(hub):
    html, = _draw(hub, [{"tab": "runs", "runs": [], "ended": []}])
    assert _visible(html) == "No run is live and none ended today."


def test_the_runs_tab_draws_nothing_while_another_tab_is_open(hub):
    html, = _draw(hub, [{"tab": "flow", "runs": [LIVE], "ended": [ENDED]}])
    assert html == ""


def test_the_run_history_is_the_bottom_of_the_runs_pane(hub):
    """#1277: the history left Settings > Loop and sits under Live and Ended today."""
    pane = hub.PAGE[hub.PAGE.index("id=tab-runs"):hub.PAGE.index("</section>", hub.PAGE.index("id=tab-runs"))]
    assert pane.index("id=main") < pane.index("id=list"), "the run history is not under Live and Ended today"


def test_the_groups_come_from_the_answer_and_no_caps_are_substituted(hub):
    """The hub builds each header and marks empty bands; the page's own rule and the served caps constant are gone (#1277)."""
    src = hub.PAGE
    assert 'fetch("/api/runs?groups=1")' in src, "the page does not ask for the answer that carries the groups"
    assert "GROUPS=Array.isArray(d)?[]:d.groups||[]" in src, "the page does not take the groups the answer carries"
    assert "GROUPS=d.runs||[]" not in src
    assert "function groupHeader(" not in src, "the page builds a group header again"
    assert "const CAPS=" not in src and "__CAPS__" not in src, "the caps are substituted into the page again"
