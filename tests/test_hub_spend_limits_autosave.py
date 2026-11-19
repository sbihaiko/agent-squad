"""Spend & limits writes each field on its own (#2433).

The theme keeps no Save and no Discard button: a valid edit posts once, on the field's blur or after a short pause in
typing, and the hub writes the profile atomically and records the change (ADR-0030, ADR-0137). The field shows a check
mark after the write, its group shows one line naming the saved value and its age, and a value the hub refuses marks the
field and writes nothing. Every other theme keeps the page-level Save and Discard.

No browser runs: the seams under test are the page's own render and write functions, run in node, its served markup, and
the hub's `limits_set` route behind them.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"

PROFILE = ': "${SG_REPO:=x/y}"\n: "${SG_TOKEN_BURN_CAP:=1000}"\n: "${SG_TOKEN_PAUSE_FACTOR:=2}"\n'


@pytest.fixture
def hub(monkeypatch, tmp_path):
    prof, state, co = tmp_path / "profiles", tmp_path / "state", tmp_path / "co"
    for d in (prof, state, co):
        d.mkdir()
    (prof / "t.sh").write_text(PROFILE)
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_PROFILE", "t"),
                 ("SG_PROFILES_DIR", str(prof)), ("HOME", str(tmp_path)), ("SG_CHECKOUT", str(co)),
                 ("SG_HUB_DIR", str(tmp_path)), ("SG_STATE", str(state))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_TOKEN_GUARD", "SG_TOKEN_BURN_CAP", "SG_TOKEN_PAUSE_FACTOR"):
        monkeypatch.delenv(k, raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", SCRIPTS / "ghread.py")
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_spend_limits_autosave", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, prof, state


def _node(mod, body):
    """The theme's own sources, run in node as they ship: its render, its write and the Kanban sub-group's rules."""
    if shutil.which("node") is None:
        pytest.skip("node is required")
    page = mod.PAGE
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    sett = page[page.index("// SETT{"):page.index("// }SETT") + len("// }SETT")]
    lim = page[page.index("let limS=null"):page.index("async function limLoad()")]
    for fn in ("function limRender(", "function limWrite(", "function kanHtml("):
        assert fn in lim, f"{fn!r} left the // lim slice: fix the harness, not the renderer"
    stub = ('const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:"",className:"",value:""});'
            'const setBar=()=>{};function setChip(){}')
    p = subprocess.run(["node", "-"], input="\n".join([stub, esc, sett, lim, body]),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


# --- a valid edit writes once, with no button

def test_a_valid_edit_posts_once_with_the_confirm_the_route_writes_on(hub):
    mod, _, _ = hub
    got = _node(mod, """
let n=0,seen=null;
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};
limRender();
limWrite("cap",async b=>{n++;seen=b;return {diff:["Spend & limits · token cap: 1000 -> 2000"],state:{...limS,cap:2000}}}).then(()=>{
  console.log(JSON.stringify({n,seen,ok:limOK,val:limVal,state:limS.cap,pend:limPend}))});""")
    assert got["n"] == 1, got
    assert got["seen"]["confirm"] is True, got["seen"]
    assert str(got["seen"]["cap"]) in ("", "1000"), got["seen"]   # the DOM stub carries no typed value
    assert (got["ok"], got["val"], got["state"], got["pend"]) == ("cap", "2000", 2000, None), got


def test_a_pause_in_typing_posts_the_field_once(hub):
    mod, _, _ = hub
    got = _node(mod, """
let n=0;limPauseMs=20;
const send=async()=>{n++;return {diff:[],state:{guard:"on",cap:2000,factor:2}}};
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
limQueue("cap",send);limQueue("cap",send);limQueue("cap",send);
setTimeout(()=>console.log(JSON.stringify({n,ok:limOK})),120);""")
    assert got == {"n": 1, "ok": "cap"}, got


def test_every_write_carries_the_whole_theme_so_one_file_lands_whole(hub):
    mod, _, _ = hub
    got = _node(mod, """
let seen=null;limS={guard:"off",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};
$("#limg").checked=true;$("#limc").value="3000";$("#limf").value="4";
limRender();
limWrite("guard",async b=>{seen=b;return {diff:["token guard: on"],state:{guard:"on",cap:3000,factor:4}}}).then(()=>{
  console.log(JSON.stringify(seen))});""")
    assert got == {"guard": "on", "cap": "3000", "factor": "4", "confirm": True}, got


# --- the field shows a check mark, the group one line with the value and its age

def test_the_saved_field_shows_a_check_mark(hub):
    mod, _, _ = hub
    got = _node(mod, """
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};
limWrite("cap",async()=>({diff:["x"],state:{guard:"on",cap:2000,factor:2}})).then(()=>{
  const h=STORE["#lbody"].innerHTML;
  console.log(JSON.stringify({mark:limMark("cap"),other:limMark("factor"),
    capRow:(h.match(/<div class=setrow[^>]*>(?:(?!<div class=setrow).)*?aria-label="Daily token cap".*?<\\/div>/)||[""])[0]}))});""")
    assert got["mark"].startswith("<span class=lok"), got
    assert got["other"] == "", got
    assert "✓" in got["capRow"], got["capRow"]


def test_the_group_shows_one_line_that_names_the_saved_value_and_its_age(hub):
    mod, _, _ = hub
    got = _node(mod, """
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};
limWrite("cap",async()=>({diff:["x"],state:{guard:"on",cap:2000,factor:2}})).then(()=>{
  limAt=Date.now()-4000;limRender();
  const h=STORE["#lbody"].innerHTML,set=h.slice(h.indexOf("data-blk=settings"),h.indexOf("data-blk=kanban"));
  console.log(JSON.stringify({n:(set.match(/Saved · /g)||[]).length,line:(set.match(/Saved · [^<]*/)||[""])[0]}))});""")
    assert got["n"] == 1, got
    assert got["line"].strip() == "Saved · daily token cap 2000 · 4s ago", got


# --- an invalid value marks the field and writes nothing

def test_a_refused_value_marks_the_field_and_keeps_the_saved_state(hub):
    mod, _, _ = hub
    got = _node(mod, """
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};
$("#limc").value="1.5";$("#limg").checked=true;$("#limf").value="2";
limRender();
limWrite("cap",async()=>({err:"token cap must be a positive whole number"})).then(()=>{
  const h=STORE["#lbody"].innerHTML;
  console.log(JSON.stringify({state:limS.cap,bad:limBad,ok:limOK,
    field:(h.match(/<input id=limc[^>]*>/)||[""])[0],line:limLine()}))});""")
    assert got["state"] == 1000, "the refused value replaced the saved state"
    assert got["bad"] == "cap" and got["ok"] == "", got
    assert "bad" in got["field"].split(">")[0] and 'aria-invalid=true' in got["field"], got["field"]
    assert "positive whole number" in got["line"], got


def test_a_refused_value_writes_nothing_through_the_route(hub):
    mod, _, _ = hub
    before = (hub[1] / "t.sh").read_text()
    out = mod.limits_set({"guard": "on", "cap": "1.5", "factor": "2", "confirm": True})
    assert "err" in out, out
    assert (hub[1] / "t.sh").read_text() == before
    assert not (hub[2] / "spend-limits-ledger.jsonl").exists()


def test_one_field_saved_alone_writes_once_and_records_one_change(hub):
    """ADR-0030/ADR-0137: the one write is atomic and lands one ledger line, as the Save wrote it."""
    mod, prof, state = hub
    save = mod.limits_set
    out = save({"guard": "on", "cap": "2000", "factor": "2", "confirm": True})
    assert out["diff"] == ["Spend & limits · token cap: 1000 -> 2000"], out
    lines = (state / "spend-limits-ledger.jsonl").read_text().splitlines()
    assert len(lines) == 1, lines
    assert json.loads(lines[0])["text"] == "Spend & limits · token cap: 1000 -> 2000"
    assert ': "${SG_TOKEN_BURN_CAP:=2000}"' in prof.joinpath("t.sh").read_text()


# --- the Kanban sub-group saves on its own too

def test_a_kanban_field_saves_on_its_own_and_names_the_value(hub):
    mod, _, _ = hub
    got = _node(mod, """
let n=0;
kanS={file:"/p",columns:[{name:"Doing",max_cards:6,count:5,stops:""}]};kanDraft=kanClone(kanS.columns);
kanWrite("Doing",async b=>{n++;return {diff:["Kanban · Doing: 6 -> 5"],state:{file:"/p",columns:[{name:"Doing",max_cards:5,count:5,stops:""}]}}}).then(()=>{
  const h=kanHtml();console.log(JSON.stringify({n,ok:kanOK,val:kanVal,line:(h.match(/Saved · [^<]*/)||[""])[0]}))});""")
    assert got["n"] == 1 and got["ok"] == "Doing" and got["val"] == "5", got
    assert got["line"].startswith("Saved · Doing") and "5" in got["line"], got


# --- the bar: Spend & limits keeps no button, every other theme keeps both

def _set_bar(mod):
    page = mod.PAGE
    src = page[page.index("const STH="):page.index("function setTheme(")]
    stub = ('const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:"",disabled:false,hidden:false,'
            'classList:{toggle(){}}});const ls={};const mdlChanged=()=>false;const slDirty=()=>false;'
            'const kanDirty=()=>false;'
            'const limVals=()=>({guard:"",cap:"",factor:""});let limS=null,slDraft=null,slS=null;'
            'let RO=false;const RO_WHY="";')
    body = ('const out={};for(const t of ["lop","lim","sub","mdl","slk","acc"]){sTheme=t;setBar();'
            'out[t]={save:$("#ssave").hidden,disc:$("#sdisc").hidden,text:$("#schg").textContent}}'
            'console.log(JSON.stringify(out));')
    return _node_src(mod, "\n".join([stub, src, body]))


def _node_src(mod, body):
    if shutil.which("node") is None:
        pytest.skip("node is required")
    p = subprocess.run(["node", "-"], input=body, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_the_spend_and_limits_theme_shows_no_save_and_no_discard_button(hub):
    out = _set_bar(hub[0])
    assert out["lim"]["save"] is True and out["lim"]["disc"] is True, out["lim"]
    assert out["lim"]["text"] == "Spend & limits: each field saves as you set it", out["lim"]


def test_every_other_theme_keeps_its_save_and_discard_buttons(hub):
    out = _set_bar(hub[0])
    for theme in ("lop", "sub", "mdl", "slk", "acc"):
        assert out[theme]["save"] is False and out[theme]["disc"] is False, (theme, out[theme])


def test_the_page_saves_a_field_on_blur_and_after_a_pause_in_typing(hub):
    page = hub[0].PAGE
    assert re.search(r'\$\("#lbody"\)\.addEventListener\("change",.*?limWrite\(', page, re.S), \
        "the theme has no blur save"
    assert re.search(r'\$\("#lbody"\)\.addEventListener\("input",.*?limQueue\(', page, re.S), \
        "the theme has no pause save"
    assert "const SET_SAVE=[\"mdl\",\"slk\",\"sub\"]" in page, "the page Save still writes Spend & limits"
