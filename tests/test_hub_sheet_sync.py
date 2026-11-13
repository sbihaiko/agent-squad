"""The issue sheet's Sync control (#1669): one press draws the sheet and the row from one answer.

The owner opens a card, presses Sync in the sheet's action line, and both the sheet and the kanban row behind it redraw
from the same `/api/sync` answer, so the two never disagree about a column, a label or a holder. A press names the count
and the instant of the read in the sheet, and a failed read names the error there too.

The route is the one #1668 landed. This module holds the page seam: the served page's lines, run under node with a stub
DOM and a recording `fetch`, so one press is proven to issue exactly one read and to draw both surfaces from it.
"""
import importlib.util
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
NODE = shutil.which("node")

# The page's sync block, in the order it is served: the read, the facts one answer carries, the two surfaces that draw
# them, and the two controls that take that one read.
SYNC_LINES = (
    "const syncInstant=",
    "const syncOne=",
    "const syncFacts=",
    "const syncFactsHtml=",
    "const syncMove=",
    "const syncRow=",
    "const syncApply=",
    "const syncNote=",
    "const syncSheetNote=",
    "const syncMark=",
)


@pytest.fixture(scope="module")
def hub():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SG_REPO", "x/y")
        mp.setenv("SG_OWNER", "x")
        mp.setenv("SG_PROJECT", "4")
        mp.setenv("SG_CHECKOUT", tempfile.mkdtemp())
        mp.setenv("SG_HUB_DIR", tempfile.mkdtemp())
        mp.delenv("SG_PROFILE", raising=False)
        spec = importlib.util.spec_from_file_location("hub_sheet_sync", HUB_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod


def _line(hub, start):
    """The served page's line that starts with `start`."""
    for ln in hub.PAGE.splitlines():
        if ln.startswith(start):
            return ln
    raise AssertionError(f"the page serves no line starting {start!r}")


def _tail(hub, needle):
    """The served page's one line that carries `needle`."""
    found = [ln for ln in hub.PAGE.splitlines() if needle in ln]
    assert len(found) == 1, f"the page serves {len(found)} lines carrying {needle!r}"
    return found[0]


STUBS = r"""
const calls=[];
const esc=s=>String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
const age=()=>"0m";const lbc=()=>"";const bo={labels:[]};const CSS={escape:s=>String(s)};
function Elm(sel){this.sel=sel;this.dataset={};this.innerHTML="";this.textContent="";this.title="";this.disabled=false;
 this.kids={};this.parentElement=null;this.classList={contains:()=>true,add(){},remove(){}}}
Elm.prototype.querySelector=function(s){return this.kids[s]||null};
Elm.prototype.closest=function(){return null};
Elm.prototype.appendChild=function(x){x.parentElement=this;return x};
Elm.prototype.remove=function(){this.gone=true};
Elm.prototype.insertAdjacentHTML=function(p,h){this.innerHTML+=h};
const els={};
const $=s=>els[s]||(els[s]=new Elm(s));
const card=new Elm("card");
card.dataset={n:"5",r:"__REPO__",col:encodeURIComponent("Todo"),b:encodeURIComponent("[]"),
 ho:"",bl:encodeURIComponent("[]"),prf:encodeURIComponent("[]")};
for(const k of [".tt",".lbs",".blk",".hld"])card.kids[k]=new Elm(k);
globalThis.$=$;
globalThis.document={querySelector:s=>s.indexOf('.it[data-n="5"]')>=0?card:null,addEventListener(){},
 createElement:t=>new Elm(t),querySelectorAll:()=>[]};
let ANSWER={};
globalThis.fetch=(u,o)=>{calls.push(u);return Promise.resolve({json:async()=>ANSWER})};
let cur=5,irepo="__REPO__";
"""


def _run(hub, answer):
    """Run the page's sync block under node against one answer; return what the press drew."""
    script = STUBS + "\n".join(_line(hub, k) for k in SYNC_LINES) + "\n" + _line(hub, '$("#tksync").onclick=') + f"""
ANSWER = {json.dumps(answer)};
(async () => {{
  await $("#tksync").onclick();
  console.log(JSON.stringify({{
    calls,
    col: decodeURIComponent(card.dataset.col), ho: decodeURIComponent(card.dataset.ho),
    labels: card.kids[".lbs"].innerHTML, holder: card.kids[".hld"].textContent,
    facts: $("#tkfacts").innerHTML, note: $("#tksyncnote").textContent,
    rownote: $("#bsync").textContent, rowhtml: card.innerHTML, disabled: $("#tksync").disabled,
  }}));
}})().catch(e => {{ console.error(e); process.exit(1); }});
"""
    result = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


ANSWER = {"n": 5, "count": 1, "at": "2026-11-13T17:00:00Z", "err": None,
          "rows": [{"n": 5, "t": "a better title", "st": "Doing", "l": ["bug", "wave:2"], "ho": "held · run x",
                    "hc": "Squad · running", "hs": "running", "b": [9], "bl": [7], "prf": []}]}


# --- AC1: the issue sheet's action line carries the Sync control --------------------------------------------------

def test_the_issue_sheets_action_line_carries_the_sync_control(hub):
    sheet = _tail(hub, "<div id=tk ")
    footer = sheet[sheet.index("<footer>"):sheet.index("</footer>")]
    assert "id=tksync" in footer, "the sheet's action line carries the Sync control for the card it shows"


# --- AC2: one press draws the sheet and the row from one answer, never two reads ----------------------------------

def test_the_sheet_and_the_card_take_the_one_sync_read(hub):
    assert hub.PAGE.count('fetch("/api/sync"') == 1, "one route, one read, whatever the control"
    assert "syncOne(" in _tail(hub, 'closest("#bbody .syncb")'), "the card's control takes the page's one sync read"
    assert "syncOne(" in _tail(hub, '$("#tksync").onclick='), "the sheet's control takes that same read"


@pytest.mark.heavy   # one node start on the page's script
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_press_from_the_sheet_reads_once_and_draws_row_and_sheet_from_that_answer(hub):
    out = _run(hub, ANSWER)
    assert out["calls"] == ["/api/sync"], f"one press, one read: {out['calls']}"
    assert out["col"] == "Doing", "the row draws the column the answer carried"
    assert out["ho"] == "held · run x", "the row draws the holder the answer carried"
    assert "bug" in out["labels"], "the row draws the labels the answer carried"
    assert out["holder"] == "Squad · running", "the row's holder chip names the run"
    for fact in ("Doing", "bug", "wave:2", "held · run x", "#9", "#7"):
        assert fact in out["facts"], f"the sheet draws {fact!r} from the same answer"
    assert not out["disabled"], "the control is live again once the press answers"


# --- AC3: the press names the count and the instant in the sheet; a failed read names the error there ------------

@pytest.mark.heavy   # one node start on the page's script
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_one_answer_drives_the_row_and_the_sheet_alike(hub):
    out = _run(hub, ANSWER)
    assert out["col"] in out["facts"], "the sheet names the column the row draws"
    assert out["ho"] in out["facts"], "the sheet names the holder the row draws"
    for lab in ("bug", "wave:2"):
        assert lab in out["labels"] and lab in out["facts"], f"both surfaces draw the label {lab!r}"


@pytest.mark.heavy   # one node start on the page's script
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_blocked_by_link_in_the_sheet_resolves_under_the_cards_repo(hub):
    row = {"n": 5, "st": "Doing", "l": [], "b": [9], "t": "t", "r": "o/other"}
    out = _run(hub, {"n": 5, "rows": [row], "count": 1, "at": "2026-11-13T17:00:00Z", "err": None})
    assert 'data-r="o/other"' in out["facts"], "the sheet names the repo the card lives in, never the page's default"


@pytest.mark.heavy   # one node start on the page's script
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_press_names_the_count_and_the_read_instant_in_the_sheet(hub):
    out = _run(hub, ANSWER)
    assert "1 row refreshed at 2026-11-13 17:00:00 UTC" in out["note"], out["note"]


@pytest.mark.heavy   # one node start on the page's script
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_sheet_note_names_a_count_of_zero(hub):
    out = _run(hub, {"n": 5, "rows": [], "count": 0, "at": "2026-11-13T17:00:00Z", "err": None,
                     "note": "#5 is closed: it is no longer an open card"})
    assert out["note"] == "0 rows refreshed at 2026-11-13 17:00:00 UTC: #5 is closed: it is no longer an open card", out["note"]


@pytest.mark.heavy   # one node start on the page's script
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_sheet_note_counts_more_than_one_row(hub):
    row = {"n": 5, "st": "Doing", "l": ["bug"], "t": "a better title"}
    out = _run(hub, {"n": 5, "rows": [row, dict(row)], "count": 2, "at": "2026-11-13T17:00:00Z", "err": None})
    assert out["note"] == "2 rows refreshed at 2026-11-13 17:00:00 UTC", out["note"]


@pytest.mark.heavy   # one node start on the page's script
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_failed_read_names_the_error_in_the_sheet_and_keeps_the_row(hub):
    out = _run(hub, {"n": 5, "rows": [], "count": 0, "at": "2026-11-13T17:00:00Z", "err": "github timed out"})
    assert out["note"] == "sync failed: github timed out", out["note"]
    assert out["rownote"] == "sync failed: github timed out", "the row's own note names that same error"
    assert out["col"] == "Todo", "a failed read keeps the row the page held"
    assert out["facts"] == "", "a failed read draws no sheet fact"
    assert not out["disabled"], "the control is live again after a failed press"


# --- AC4: a card GitHub no longer carries keeps the row and is named in the sheet --------------------------------

@pytest.mark.heavy   # one node start on the page's script
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_missing_card_keeps_both_surfaces_and_is_named_on_both(hub):
    out = _run(hub, {"n": 5, "rows": [{"n": 5, "st": "Doing", "l": ["stale"], "t": "old"}], "count": 0,
                     "at": "2026-11-13T17:00:00Z", "err": None, "missing": True,
                     "note": "GitHub no longer carries #5"})
    assert out["note"] == "0 rows refreshed: GitHub no longer carries #5", out["note"]
    assert out["rownote"] == "0 rows refreshed: GitHub no longer carries #5", "the row's note names the miss too"
    assert "GitHub no longer carries #5" in out["rowhtml"], "the miss is named at the row's own location"
    assert out["col"] == "Todo", "a missing card keeps the column the row held"
    assert out["facts"] == "", "a missing card rewrites no sheet fact"
    assert "stale" not in out["labels"], "a missing card rewrites no row label"
