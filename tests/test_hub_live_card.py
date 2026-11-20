"""The Monitor's live-runs card is a bounded glance (#1276, #2507).

The card keeps its place under the Monitor and stops being a second full list: its summary states the whole live count and
the hand holds, and it draws at most four run rows, oldest first. The header carries two pills, the runs and the hand holds,
and never a cost. A run row is one short line -- the run icon, the issue id, the node it is at and its age -- and it keeps
its `data-id`, so the page's own row handler still opens the run sheet; the full label, the title, the model and the load
live in its tooltip. The served page's own code runs under node over a fixture view; the words are read back as the owner
sees them.
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
HELPERS = (r"^const age=s=>[^\n]*", r"^const iq=t=>[^\n]*", r"^function loadMark\(r\)\{[^\n]*",
           r"^const LIVE_ROWS=\d+;", r"^const LIVE_QUIET=\d+;")


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
    prelude = ('const esc=s=>String(s==null?"":s);const sub=r=>{const v=Object.values(r.titles||{});return v.length?v[0]:""};'
               'const name=r=>r.label||r.id;const labelTip=r=>"";const pins=new Set();let liveWork=null;')
    body = "console.log(JSON.stringify(liveCard(" + json.dumps(runs) + ")));"
    src = "\n".join([helpers, prelude, card, body])
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _run(age, tag, spend=0.0, title=""):
    r = {"id": f"run-20261103-1010{tag}0-aaaa", "label": f"gui-{tag}", "age": age, "rank": 0, "spend": spend}
    if title:
        r["issues"] = ["1"]
        r["titles"] = {"1": title}
    return r


LIVE = [_run(7200, "a", 1.5, "[SPEC 1311] Show four live runs on the Monitor card"), _run(60, "b", 0.25), _run(600, "c", 0.0),
        _run(1800, "d", 2.75), _run(5400, "e", 1.0)]


def _visible(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html)).strip()


def test_the_card_states_the_whole_count_and_never_the_spend(page):
    """#2507: the summary keeps the whole count even with the rows capped, and the cost leaves the card."""
    out = _card(page, LIVE)
    assert out["head"] == "5 live", "the summary states the whole count, even with the rows capped"
    assert "🏃 <b>5</b>" in out["html"], "the header carries the runs pill"
    assert "$" not in out["html"] and "oldest" not in out["html"], "the spend and the oldest age leave the card"


def test_the_card_draws_at_most_four_rows_oldest_first(page):
    out = _card(page, LIVE)
    ids = re.findall(r'data-id="([^"]+)"', out["html"])
    # a hand-worked answer, never the card's own sort rule: a=7200 s, e=5400 s, d=1800 s, c=600 s are the four oldest of LIVE
    assert ids == ["run-20261103-1010a0-aaaa", "run-20261103-1010e0-aaaa", "run-20261103-1010d0-aaaa", "run-20261103-1010c0-aaaa"], \
        "at most four rows, the oldest first"
    assert out["html"].count("data-id=") == 4


def test_the_count_is_not_the_row_count_when_rows_are_capped(page):
    out = _card(page, LIVE)
    assert out["head"].startswith("5 live"), "the count keeps stating the whole truth"
    assert out["html"].count("data-id=") == 4


def test_a_row_reads_the_icon_the_issue_the_node_and_the_age(page):
    """#2507: a run row is one short line -- the run icon, the issue, the node and the age; the bare title is its tooltip."""
    runs = [{"id": "run-20261103-101012-aaaa", "label": "implement #1282", "age": 12, "rank": 0, "state": "running",
             "stage": "implement", "issues": ["1282"], "titles": {"1282": "[SPEC 1311] Build the thing"}}]
    out = _card(page, runs)
    body = out["html"]
    assert "🏃" in body, "a row opens with the run icon"
    assert "<span class=lid>#1282</span>" in body, "the id is the issue the run delivers; `implement` is every card run's word"
    assert ">implement</span>" in body, "the run's current node reads as its chip"
    assert "Build the thing" in body, "the bare title rides the row's tooltip"
    assert "SPEC 1311" not in body, "the spec prefix is stripped from the ticket's title"
    assert ">12s<" in body, "a writing run says how long since its last write, at the row's right"
    assert "running" not in _visible(body), "the state word left the row"


def test_the_stage_reads_in_a_person_s_words_and_yields_to_the_load_mark(page):
    runs = [{"id": "run-a", "label": "implement #1282", "age": 9, "rank": 0, "state": "running",
             "stage": "route.router.2", "issues": ["1282"], "titles": {"1282": "Build the thing"}},
            {"id": "run-b", "label": "implement #1283", "age": 9, "rank": 0, "state": "running",
             "stage": "review_gate", "issues": ["1283"], "titles": {"1283": "Another thing"}},
            {"id": "run-c", "label": "implement #1284", "age": 9, "rank": 0, "state": "running",
             "stage": "review_gate", "cpu": 23, "issues": ["1284"], "titles": {"1284": "A third thing"}}]
    body = _card(page, runs)["html"]
    rows = body.split('data-id="')[1:]
    a, b, c = (r.split("</div>")[0] for r in rows)
    assert "router 2" in _visible(a), "the graph's own node name is not what a person reads"
    assert "review" in _visible(b), "a gate node drops its `_gate` suffix"
    # One diagnostic shares the line: a run that owns machine CPU shows `cpu N%` instead of its stage, never both cramped.
    assert "cpu 23%" in _visible(c) and "review" not in _visible(c)


def test_a_quiet_run_reads_quiet_with_an_amber_rule(page):
    out = _card(page, [{"id": "run-q", "label": "gui-q", "age": 900, "rank": 0, "state": "running"}])
    assert "quiet 15m" in _visible(out["html"])
    assert 'class="lr quiet"' in out["html"], "a quiet run marks its row's left edge"


def test_an_unreadable_record_reads_no_record_and_never_a_fresh_age(page):
    # The card keeps only the runs that are live now (rank 0), and an unreadable record keeps its figures from the last read:
    # it must say so, or a run that cannot be read looks exactly like one that is writing (#1816's own rule, applied here).
    out = _card(page, [{"id": "run-u", "label": "gui-u", "age": 0, "rank": 0, "state": "unreadable"}])
    body = _visible(out["html"])
    assert "no record" in body
    assert "0s ago" not in body, "an unreadable record never reads as a fresh write"
    assert 'class="lr wait"' in out["html"]


def test_the_card_carries_no_control_to_the_runs_tab(page):
    """#2507: the card is one glance; the Runs tab has its own control in the tab bar."""
    out = _card(page, LIVE)
    assert "id=lrmore" not in out["html"], "the card carries no control of its own"
    assert "All runs" not in _visible(out["html"]), "the card names no second list"


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
    assert "no live work" in _visible(out["html"])
    assert "id=lrmore" not in out["html"]


def test_a_hand_hold_is_counted_apart_from_a_live_run(page):
    """#2168: a hold is work by hand, never a run. One run and one hold read `1 live · 1 held`, never `2 live`, and the
    hold's age never becomes the oldest run's age."""
    hold = {"id": "hold-2149", "label": "Manual · #2149", "kind": "hold", "age": 9999, "rank": 0, "spend": 0.0,
            "issues": ["2149"], "titles": {"2149": "An unproven wave tip holds its issue past the age cap"}}
    out = _card(page, [_run(60, "b", 0.25), hold])
    assert out["head"] == "1 live · 1 held", f"the hold inflated the run count: {out['head']}"
    assert [h for h in re.findall(r'data-id="([^"]+)"', out["html"])] == ["run-20261103-1010b0-aaaa"], \
        "the hold is drawn as its own line, never as a run row"


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
        'const ev={key:"Enter",target:{closest:s=>s===".lr[data-id]"?row:null},preventDefault(){}};',
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
