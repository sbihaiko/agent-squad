"""The Monitor's live-runs card is a bounded glance that opens the Runs tab (#1276).

The card keeps its place under the Monitor and stops being a second full list: it states the whole live count and the
oldest live run's age, draws at most four rows, and carries one control that switches the page to the Runs tab. A row
keeps its `data-id`, so the page's own row handler still opens the run sheet. The served page's own code runs under node
over a fixture view; the words are read back as the owner sees them.
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
HELPERS = (r"^const age=s=>[^\n]*", r"^const LIVE_ROWS=\d+;")


@pytest.fixture(scope="module")
def page():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_live_card", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PAGE


def _pieces(page):
    helpers = [re.search(p, page, re.M).group(0) for p in HELPERS]
    card = page[page.index("function liveCard("):page.index("function drawList(")]
    return "\n".join(helpers), card


def _card(page, runs):
    if NODE is None:
        pytest.skip("node is required")
    helpers, card = _pieces(page)
    prelude = 'const row=r=>`<div class="r" data-id="${r.id}"><b>${r.label}</b></div>`;'
    body = "console.log(JSON.stringify(liveCard(" + json.dumps(runs) + ",row)));"
    src = "\n".join([helpers, prelude, card, body])
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _run(age, tag):
    return {"id": f"run-20261103-1010{tag}0-aaaa", "label": f"gui-{tag}", "age": age, "rank": 0}


LIVE = [_run(7200, "a"), _run(60, "b"), _run(600, "c"), _run(1800, "d"), _run(5400, "e")]


def _visible(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html)).strip()


def test_the_card_states_the_whole_count_and_the_oldest_age(page):
    out = _card(page, LIVE)
    assert out["head"] == "5 live · oldest 2h", "the card states the whole truth, even with the rows capped"


def test_the_card_draws_at_most_four_rows_oldest_first(page):
    out = _card(page, LIVE)
    ids = re.findall(r'data-id="([^"]+)"', out["html"])
    # a hand-worked answer, never the card's own sort rule: a=7200 s, e=5400 s, d=1800 s, c=600 s are the four oldest of LIVE
    assert ids == ["run-20261103-1010a0-aaaa", "run-20261103-1010e0-aaaa", "run-20261103-1010d0-aaaa", "run-20261103-1010c0-aaaa"], \
        "at most four rows, the oldest first"
    assert out["html"].count('class="r"') == 4


def test_the_count_is_not_the_row_count_when_rows_are_capped(page):
    out = _card(page, LIVE)
    assert out["head"].startswith("5 live"), "the count keeps stating the whole truth"
    assert out["html"].count('class="r"') == 4


def test_the_card_carries_one_control_that_switches_to_the_runs_tab(page):
    out = _card(page, LIVE)
    assert out["html"].count("id=lrmore") == 1, "the card carries one control"
    assert 'data-t=runs' in out["html"], "the control names the Runs tab"
    assert re.search(r"\bruns\b", _visible(out["html"]), re.I), "the control says what it opens"


def test_the_control_routes_through_the_page_tab_handler(page):
    if NODE is None:
        pytest.skip("node is required")
    handler = page[page.index("function tabClick("):page.index("\n", page.index("function tabClick("))]
    src = "\n".join(["let hit=null;function setTab(t){hit=t}", handler,
                     'tabClick({target:{closest:s=>s==="button[data-t]"?{dataset:{t:"runs"}}:null}});',
                     "console.log(JSON.stringify(hit));"])
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == "runs"
    assert re.search(r'\$\("#lrun"\)\.addEventListener\("click",tabClick\)', page), "the card's control is not wired"


def test_the_card_says_none_and_draws_no_control_when_nothing_is_live(page):
    out = _card(page, [])
    assert out["head"] == "none"
    assert "no live runs" in _visible(out["html"])
    assert "id=lrmore" not in out["html"]


def test_a_card_row_opens_the_run_sheet(page):
    if NODE is None:
        pytest.skip("node is required")
    out = _card(page, LIVE)
    ids = re.findall(r'data-id="([^"]+)"', out["html"])
    assert ids, "the card drew no row to click"
    handlers = "\n".join(line for line in page.splitlines() if line.strip().startswith('$("#lrun").addEventListener('))
    src = "\n".join([
        "const L={};",
        'const $=s=>({addEventListener:(k,f)=>{(L[s+" "+k]=L[s+" "+k]||[]).push(f)},set innerHTML(v){},set textContent(v){}});',
        "function tabClick(){}",
        "let hl=null;const paintHL=()=>{};let opened=[];function openRun(id){opened.push(id)}",
        handlers,   # the page's own listeners on the card, bound here against a stub DOM
        "const row={dataset:{id:" + json.dumps(ids[0]) + "}};",
        'const ev={key:"Enter",target:{closest:s=>s===".r[data-id]"?row:null},preventDefault(){}};',
        '(L["#lrun click"]||[]).forEach(f=>f(ev));(L["#lrun keydown"]||[]).forEach(f=>f(ev));',
        'const miss={key:"Enter",target:{closest:()=>null},preventDefault(){}};',
        '(L["#lrun click"]||[]).forEach(f=>f(miss));(L["#lrun keydown"]||[]).forEach(f=>f(miss));',
        "console.log(JSON.stringify({opened,hl}));",
    ])
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    got = json.loads(p.stdout)
    assert got["opened"] == [ids[0], ids[0]], "a card row opens the run sheet, by click and by the keyboard"
    assert got["hl"] == {"run": ids[0]}, "the opened run is highlighted, as the list's row does"

