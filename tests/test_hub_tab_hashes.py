"""Each hub tab has its own hash, the bar answers the arrow keys, and a card link opens the card (SPEC 1120, #1274).

The page's own tab code runs under node over a stubbed DOM; the snapshots read back what the owner sees.
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
TABS_ORDER = ["board", "runs", "flow", "prof", "set"]


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_tab_hashes", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _line(page, pattern):
    m = re.search(pattern, page, re.M)
    assert m, f"the page has no {pattern!r}"
    return m.group(0)


def _run(hub, tmp_path, name, hash_, stored, steps):
    """Load the page on `hash_` with `stored` localStorage, then run each step and snapshot the tab state after it.

    A step is one JS statement; `press(key)` sends a key to the tab bar and `focusTab(name)` puts focus on a tab.
    The first snapshot is the state after the load."""
    page = hub.PAGE
    dom = """
const localStorage={hub:__STORED__};const location={hash:__HASH__};
const writes=[];const history={replaceState(_s,_t,u){writes.push(u);location.hash=u}};
let TAB="board",pins=new Set(),q="",per=10,src="",FLOWK=null,focused=null;
const sTheme=null,setTheme=()=>{},drawMain=()=>{},flowOpen=()=>{},profPoll=()=>{};
const cl=()=>({s:new Set(),toggle(c,f){if(f===undefined)f=!this.s.has(c);f?this.s.add(c):this.s.delete(c)},contains(c){return this.s.has(c)}});
const els={};const $=s=>els[s]||(els[s]={hidden:s!=="#flowov"?false:true,classList:cl()});
const btns=__TABS__.map(t=>({dataset:{t},classList:cl(),attrs:{},tabIndex:0,setAttribute(k,v){this.attrs[k]=String(v)},focus(){focused=this}}));
const handlers={};const tabsEl={addEventListener(t,f){handlers[t]=f}};
const document={querySelectorAll(s){return s===".tabs button"||s===".tabs button[data-t]"?btns:[]},querySelector(s){return s===".tabs"?tabsEl:null}};
const ls=JSON.parse(localStorage.hub||"{}");
""".replace("__STORED__", json.dumps(stored)).replace("__HASH__", json.dumps(hash_)).replace("__TABS__", json.dumps(TABS_ORDER))
    extracted = [
        _line(page, r"^const TABS=[^\n]*"),
        _line(page, r"^const save=[^\n]*"),
        _line(page, r"^const HASH=[^\n]*"),
        _line(page, r"^function tabFromLocation\(.*$"),
        _line(page, r"^function tabNext\(.*$"),
        re.search(r"^function setTab\(.*?drawMain\(\)\}", page, re.M | re.S).group(0),
        _line(page, r"^function tabKey\(.*$"),
        _line(page, r'^document\.querySelector\("\.tabs"\)\.addEventListener\("keydown",tabKey\);'),
        _line(page, r"^setTab\(tabFromLocation\(\)[^\n]*"),
    ]
    helpers = """
const snaps=[];
function snap(){snaps.push({tab:TAB,hash:location.hash,writes:writes.slice(),
 selected:btns.filter(b=>b.attrs["aria-selected"]==="true").map(b=>b.dataset.t),
 tabindex:btns.filter(b=>b.tabIndex===0).map(b=>b.dataset.t),
 shown:Object.keys(TABS).filter(k=>$(TABS[k]).classList.contains("on")),
 focus:focused?focused.dataset.t:null})}
function press(k){handlers.keydown({key:k,target:focused,preventDefault(){},stopPropagation(){}})}
function focusTab(t){focused=btns.find(b=>b.dataset.t===t)}
"""
    body = ["snap();"]
    for step in steps:
        body.append(step)
        body.append("snap();")
    body.append("console.log(JSON.stringify(snaps));")
    src = "\n".join([dom, *extracted, helpers, *body])
    path = tmp_path / name
    path.write_text(src)
    result = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _only(snap, tab):
    return snap["tab"] == tab and snap["selected"] == [tab] and snap["shown"] == [tab] and snap["tabindex"] == [tab]


needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
@pytest.mark.parametrize("hash_,tab", [("#kanban", "board"), ("#runs", "runs"), ("#flow", "flow"), ("#loop", "prof"), ("#settings", "set")])
def test_loading_a_tab_hash_opens_that_tab(hub, tmp_path, hash_, tab):
    snaps = _run(hub, tmp_path, "load.js", hash_, "{}", [])
    assert _only(snaps[0], tab), snaps[0]
    assert snaps[0]["hash"] == hash_ and snaps[0]["writes"] == []


@needs_node
def test_the_old_settings_hash_still_opens_settings(hub, tmp_path):
    snaps = _run(hub, tmp_path, "alias.js", "#set", "{}", [])
    assert _only(snaps[0], "set"), snaps[0]


@needs_node
def test_no_hash_opens_the_tab_saved_last_or_kanban(hub, tmp_path):
    assert _only(_run(hub, tmp_path, "none.js", "", "{}", [])[0], "board")
    assert _only(_run(hub, tmp_path, "saved.js", "", json.dumps({"tab": "runs"}), [])[0], "runs")


@needs_node
def test_switching_a_tab_writes_its_hash(hub, tmp_path):
    steps = ['setTab("runs")', 'setTab("flow")', 'setTab("prof")', 'setTab("set")', 'setTab("board")']
    snaps = _run(hub, tmp_path, "switch.js", "#kanban", "{}", steps)
    assert [s["hash"] for s in snaps[1:]] == ["#runs", "#flow", "#loop", "#settings", "#kanban"]
    assert snaps[-1]["writes"] == ["#runs", "#flow", "#loop", "#settings", "#kanban"]
    for snap, tab in zip(snaps[1:], ["runs", "flow", "prof", "set", "board"], strict=True):
        assert _only(snap, tab), snap


@needs_node
def test_a_card_link_opens_the_card_on_kanban_and_keeps_its_form(hub, tmp_path):
    snaps = _run(hub, tmp_path, "card.js", "#issue=1171", json.dumps({"tab": "runs"}), [])
    assert _only(snaps[0], "board"), snaps[0]
    assert snaps[0]["hash"] == "#issue=1171" and snaps[0]["writes"] == []


def test_the_card_link_is_resolved_before_the_tab_is_chosen(hub):
    page = hub.PAGE
    assert page.index("const hm=location.hash") < page.index("setTab(tabFromLocation()")


@needs_node
def test_arrow_home_and_end_keys_move_one_current_tab_and_its_pane(hub, tmp_path):
    steps = ['focusTab("board")', 'press("ArrowRight")', 'press("ArrowRight")', 'press("ArrowRight")',
             'press("ArrowRight")', 'press("ArrowRight")', 'press("ArrowLeft")', 'press("Home")', 'press("End")', 'press("ArrowLeft")']
    snaps = _run(hub, tmp_path, "keys.js", "#kanban", "{}", steps)[2:]
    expected = ["runs", "flow", "prof", "set", "board", "set", "board", "set", "prof"]
    hashes = {"board": "#kanban", "runs": "#runs", "flow": "#flow", "prof": "#loop", "set": "#settings"}
    assert len(snaps) == len(expected)
    for snap, tab in zip(snaps, expected, strict=True):
        assert _only(snap, tab), snap
        assert snap["focus"] == tab and snap["hash"] == hashes[tab], snap


@needs_node
def test_a_key_that_is_not_a_tab_key_moves_nothing(hub, tmp_path):
    snaps = _run(hub, tmp_path, "other.js", "#runs", "{}", ['focusTab("runs")', 'press("a")'])
    assert _only(snaps[-1], "runs") and snaps[-1]["writes"] == []


def test_the_bar_is_a_tab_list_of_five_tabs(hub):
    top = hub.PAGE[hub.PAGE.index("<div class=tabs role=tablist>"):]
    top = top[:top.index("</div>")]
    assert re.findall(r"<button data-t=(\w+) role=tab>", top) == TABS_ORDER
