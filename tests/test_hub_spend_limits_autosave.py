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
    # a field the page has not drawn carries no value; a test that types one sets `value` itself
    # the stub carries the page's read-only switch and its focus reader, which the write path reads as it loads them
    stub = ('const STORE=new Proxy({},{get:(o,s)=>o[s]||(o[s]={innerHTML:"",textContent:"",className:"",value:undefined})});'
            'const document={activeElement:null};let RO=false;const RO_WHY="";'
            'const $=s=>STORE[s];const setBar=()=>{};function setChip(){}')
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
STORE["#limc"].value="2000";   // the reader typed 2000 into the drawn field
limWrite("cap",async b=>{n++;seen=b;return {diff:["Spend & limits · token cap: 1000 -> 2000"],state:{...limS,cap:2000}}}).then(()=>{
  console.log(JSON.stringify({n,seen,ok:limOK,val:limVal,state:limS.cap}))});""")
    assert got["n"] == 1, got
    assert got["seen"]["confirm"] is True, got["seen"]
    assert got["seen"]["cap"] == "2000", got["seen"]   # the typed value, never the saved one and never a blank
    assert (got["ok"], got["val"], got["state"]) == ("cap", "2000", 2000), got


def test_a_pause_in_typing_posts_the_field_once(hub):
    mod, _, _ = hub
    got = _node(mod, """
let n=0;limPauseMs=20;
const send=async()=>{n++;return {diff:[],state:{guard:"on",cap:2000,factor:2}}};
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
limQueue("cap",send);limQueue("cap",send);limQueue("cap",send);
setTimeout(()=>console.log(JSON.stringify({n,ok:limOK})),120);""")
    assert got == {"n": 1, "ok": "cap"}, got


def test_two_fields_edited_inside_one_pause_each_post_once(hub):
    """Each field has its own timer: a pause in one field never cancels another field's pending save."""
    mod, _, _ = hub
    got = _node(mod, """
let sent=[];limPauseMs=20;
const send=async b=>{sent.push(b);return {diff:[],state:{guard:"on",cap:+b.cap,factor:+b.factor}}};
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
STORE["#limc"].value="2000";STORE["#limf"].value="3";
limQueue("cap",send);limQueue("factor",send);
setTimeout(()=>{const last=sent[sent.length-1]||{};
  console.log(JSON.stringify({n:sent.length,order:sent.map(b=>b.factor==="3"?"factor":"cap"),cap:last.cap,factor:last.factor}))},150);""")
    assert got == {"n": 2, "order": ["cap", "factor"], "cap": "2000", "factor": "3"}, got


def test_a_write_inside_another_field_s_write_carries_the_saved_value(hub):
    """The writes run one after the other: the second body carries the value the first one saved, never the stale one."""
    mod, _, _ = hub
    got = _node(mod, """
let sent=[];limPauseMs=10;
const send=async b=>{sent.push(b);await new Promise(r=>setTimeout(r,30));
  return {diff:[],state:{guard:"on",cap:+b.cap,factor:+b.factor}}};
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
STORE["#limc"].value="2000";STORE["#limf"].value="3";
limQueue("cap",send);limQueue("factor",send);
setTimeout(()=>console.log(JSON.stringify({n:sent.length,cap:sent[1]&&sent[1].cap})),220);""")
    assert got == {"n": 2, "cap": "2000"}, got


def test_one_field_write_merges_its_edit_onto_the_saved_state(hub):
    """The body is the whole theme, because the route writes one file whole; only the edited field leaves the saved state."""
    mod, _, _ = hub
    got = _node(mod, """
let seen=null;limS={guard:"off",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};
limRender();
STORE["#limg"].checked=true;   // only the guard is edited; the other rows are drawn but untouched
STORE["#limc"].value="1000";STORE["#limf"].value="2";
limWrite("guard",async b=>{seen=b;return {diff:["token guard: on"],state:{guard:"on",cap:1000,factor:2}}}).then(()=>{
  console.log(JSON.stringify(seen))});""")
    assert got == {"guard": "on", "cap": "1000", "factor": "2", "confirm": True}, got


def test_a_refused_value_in_one_field_is_never_reposted_by_another(hub):
    """A cap the hub refused stays in its own field; a later guard save never carries it, and the cap is not written."""
    mod, _, _ = hub
    got = _node(mod, """
let sent=[];
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};
limRender();
STORE["#limc"].value="1.5";
const send=async b=>{sent.push(b);return b.cap==="1.5"?{err:"token cap must be a positive whole number"}:{diff:[],state:{...limS,guard:"off"}}};
limWrite("cap",send).then(async()=>{
  STORE["#limg"].checked=false;
  await limWrite("guard",send);
  console.log(JSON.stringify({sent,cap:limS.cap,bad:limBad}));});""")
    assert got["sent"], got            # the refused cap was tried once
    assert [b.get("cap") for b in got["sent"]].count("1.5") == 1, got["sent"]   # and never again by the guard save
    assert got["sent"][-1]["cap"] == "1000", got["sent"]   # the guard save carries the saved cap, not the refused text
    assert got["cap"] == 1000, got


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


def test_a_busy_field_still_updates_the_tokens_state_chip(hub):
    mod, _, _ = hub
    got = _node(mod, """
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};
limRender();
document.activeElement={id:"limc"};
limS={...limS,paused:true};
limRender();
console.log(JSON.stringify({text:STORE["#limtok"].textContent,cls:STORE["#limtok"].className}));""")
    assert got["text"] == "paused", got
    assert got["cls"] == "chip err", got


def test_the_group_shows_one_line_that_names_the_saved_value_and_its_age(hub):
    mod, _, _ = hub
    got = _node(mod, """
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};
STORE["#limc"].value="2000";
limWrite("cap",async()=>({diff:["x"],state:{guard:"on",cap:2000,factor:2}})).then(()=>{
  limAt=Date.now()-4000;limRender();
  const h=STORE["#lbody"].innerHTML,set=h.slice(h.indexOf("data-blk=settings"),h.indexOf("data-blk=kanban"));
  const notes=[...set.matchAll(/<p class=note>(.*?)<\\/p>/g)],last=notes.length?notes[notes.length-1][1]:"";
  console.log(JSON.stringify({n:(set.match(/Saved · /g)||[]).length,line:last.replace(/<[^>]*>/g,"").replace(/\\s+/g," ").trim()}))});""")
    assert got["n"] == 1, got
    assert got["line"] == "✓ Saved · daily token cap 2000 · 4s ago", got


def test_the_age_line_grows_with_the_clock_and_no_new_write(hub):
    """The line reads the clock as it ticks: the age moves on with no second write and no re-render."""
    mod, _, _ = hub
    got = _node(mod, """
let n=0;
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};
STORE["#limc"].value="2000";
limWrite("cap",async()=>{n++;return {diff:["x"],state:{guard:"on",cap:2000,factor:2}}}).then(()=>{
  limAt=Date.now()-4000;limRender();
  limTick(Date.now()+3000);
  console.log(JSON.stringify({n,age:STORE["#limage"].textContent}))});""")
    assert got["n"] == 1, got
    assert got["age"] == "7s ago", got


def test_the_now_footer_names_the_profile_file_the_theme_writes(hub):
    """The Now block keeps the one line naming the file: the theme's own file on hover, its name in view (#2434)."""
    mod, _, _ = hub
    got = _node(mod, """
limS={guard:"on",cap:1000,factor:2,profile:"/p/squad.sh",file:"/p/squad.sh",paused:false,last_change:null};
limRender();
const h=STORE["#lbody"].innerHTML;
console.log(JSON.stringify({foot:(h.match(/Profile: <code[^>]*>[^<]*<\\/code>/)||[""])[0]}))""")
    assert got["foot"] == 'Profile: <code title="/p/squad.sh">squad.sh</code>', got


def test_the_age_ticker_runs_only_while_a_saved_value_shows_in_a_visible_theme(hub):
    """A page with nothing saved arms no ticker, and a cleared or hidden theme stops the one it had (#2433)."""
    mod, _, _ = hub
    got = _node(mod, """
let out={};limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};
limRender();out.before=!!limAgeTmr;
limWrite("cap",async()=>({diff:[],state:{...limS,cap:2000}})).then(()=>{
  out.on=!!limAgeTmr;
  limOK="";limRender();out.cleared=!!limAgeTmr;
  limOK="cap";STORE["#lim"]={open:false};limTickStart();out.hidden=!!limAgeTmr;
  console.log(JSON.stringify(out))})""")
    assert got == {"before": False, "on": True, "cleared": False, "hidden": False}, got


def test_the_mark_of_the_field_that_lost_its_save_leaves_the_row(hub):
    """One check mark shows at a time: the field that lost limOK loses its mark, never a hidden leftover (#2433)."""
    mod, _, _ = hub
    got = _node(mod, """
let gone=[];
STORE["#limmk-cap"]={id:"limmk-cap",hidden:false,remove(){gone.push("cap")}};
limOK="factor";limPatch();
console.log(JSON.stringify({gone,status:/Saved · pause factor/.test(STORE["#limstat"].innerHTML)}))""")
    assert got == {"gone": ["cap"], "status": True}, got


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
    mod, prof, state = hub
    ledger = state / "spend-limits-ledger.jsonl"
    assert mod.limits_set({"guard": "on", "cap": "2000", "factor": "2", "confirm": True})["diff"]   # one valid change lands
    before, lines = prof.joinpath("t.sh").read_text(), ledger.read_text().splitlines()
    out = mod.limits_set({"guard": "on", "cap": "1.5", "factor": "2", "confirm": True})
    assert "err" in out, out
    assert prof.joinpath("t.sh").read_text() == before
    assert ledger.read_text().splitlines() == lines   # a refused payload records no second change


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


def test_a_kanban_column_the_last_write_posted_never_posts_again(hub):
    """The whole payload posts once per changed column: a timer save and then a blur send one request."""
    mod, _, _ = hub
    got = _node(mod, """
let n=0;
kanS={file:"/p",columns:[{name:"Doing",max_cards:6,count:5,stops:""}]};kanDraft=kanClone(kanS.columns);
const send=async()=>{n++;return {diff:["Kanban · Doing: 6 -> 5"],state:{file:"/p",columns:[{name:"Doing",max_cards:5,count:5,stops:""}]}}};
kanWrite("Doing",send).then(()=>kanWrite("Doing",send)).then(()=>{
  console.log(JSON.stringify({n,line:kanHtml().replace(/<[^>]*>/g,"")}))});""")
    assert got["n"] == 1, got
    assert "Kanban · Doing: 6 -&gt; 5" in got["line"], got["line"]   # the confirm ADR-0137 asks for shows on the line


def test_two_kanban_columns_edited_inside_one_pause_each_post_once(hub):
    mod, _, _ = hub
    got = _node(mod, """
let sent=[];limPauseMs=20;
kanS={file:"/p",columns:[{name:"Doing",max_cards:6,count:5,stops:""},{name:"Reviewing",max_cards:4,count:2,stops:""}]};
kanDraft=kanClone(kanS.columns);
const send=async b=>{sent.push(b);return {diff:[],state:{file:"/p",columns:[{name:"Doing",max_cards:+b.columns.Doing,count:5,stops:""},{name:"Reviewing",max_cards:+b.columns.Reviewing,count:2,stops:""}]}}};
kanDraft[0].max_cards=5;limQueue("kan:Doing",send);
kanDraft[1].max_cards=3;limQueue("kan:Reviewing",send);
setTimeout(()=>{const last=sent[sent.length-1]||{columns:{}};
  console.log(JSON.stringify({n:sent.length,doing:last.columns.Doing,reviewing:last.columns.Reviewing}))},150);""")
    assert got == {"n": 2, "doing": "5", "reviewing": "3"}, got


# --- the bar: Spend & limits keeps no button, every other theme keeps both

def _set_bar(mod):
    page = mod.PAGE
    src = page[page.index("const STH="):page.index("function setTheme(")]
    # the four names the bar's own slice calls from outside it: the DOM accessor, the stored theme, and two dirty tests
    stub = ('const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:"",disabled:false,hidden:false,'
            'classList:{toggle(){}}});const ls={};const mdlChanged=()=>false;const slDirty=()=>false;'
            'let RO=false;const RO_WHY="";')
    body = ('const out={};for(const t of Object.keys(STH)){sTheme=t;setBar();'
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
    for theme in ("sub", "mdl", "acc"):
        assert out[theme]["save"] is False and out[theme]["disc"] is False, (theme, out[theme])
    # re-anchored (#2908): Slack saves each edit by itself, so its bar keeps no Save and no Discard, as Spend & limits.
    assert out["slk"]["save"] is True and out["slk"]["disc"] is True, out["slk"]


def test_a_refused_kanban_value_is_never_resent_by_a_later_pause(hub):
    """The refused value stays in its field, marked; a save in another field redraws the row and never resends it."""
    mod, _, _ = hub
    got = _node(mod, """
let sent=[];limPauseMs=20;
kanS={file:"/p",columns:[{name:"Doing",max_cards:9,count:1,stops:""}]};kanDraft=kanClone(kanS.columns);
limS={guard:"on",cap:1000,factor:2,profile:"/p/t.sh",paused:false,last_change:null};limRender();
const send=async b=>{sent.push(b.columns?b.columns.Doing:b.cap);
  return (b.columns&&b.columns.Doing==="250")?{err:"card limit must be an integer from 1 to 100"}:{diff:[],state:{...limS,cap:+b.cap}}};
(async()=>{
  kanDraft[0].max_cards=250;await kanWrite("Doing",send);
  STORE["#limc"].value="2000";await limWrite("cap",send);
  limQueue("kan:Doing",send);
  await new Promise(r=>setTimeout(r,120));
  const h=kanHtml();
  console.log(JSON.stringify({sent,bad:kanBad,mark:/class=bad/.test(h),draft:kanDraft[0].max_cards}))})();""")
    assert got["sent"] == ["250", "2000"], got      # the refused body went once; the next request is the cap save
    assert got["bad"] == "Doing" and got["mark"] is True, got   # and the field keeps its mark
    assert got["draft"] == 250, got                 # with the refused value still in the draft


def test_the_kanban_saved_age_grows_with_the_clock(hub):
    """A Kanban save's age moves each second, like the limits line, with no new write (#2433)."""
    mod, _, _ = hub
    got = _node(mod, """
kanS={file:"/p",columns:[{name:"Doing",max_cards:6,count:5,stops:""}]};kanDraft=kanClone(kanS.columns);
kanWrite("Doing",async()=>({diff:[],state:{file:"/p",columns:[{name:"Doing",max_cards:5,count:5,stops:""}]}})).then(()=>{
  kanAt=Date.now()-4000;
  const drawn=(kanHtml().match(/<span id=kanage>([^<]*)<\\/span>/)||[])[1];kanTick(Date.now()+3000);
  console.log(JSON.stringify({drawn,age:STORE["#kanage"].textContent}))})""")
    assert got == {"drawn": "4s ago", "age": "7s ago"}, got


def test_the_page_never_saves_a_number_field_the_browser_reports_empty(hub):
    """A phone reports a partly typed number as the empty string; the blur keeps it for the pause timer (#2433)."""
    page = hub[0].PAGE
    handler = re.search(r'\$\("#lbody"\)\.addEventListener\("change".*?limWrite\(key\)', page, re.S).group(0)
    guard = 'LIM_FIELD[e.target.id]==="factor"&&e.target.value===""&&+((limS||{}).factor||0)!==0)return'
    assert guard in handler, "the blur saves the empty string a phone reports for a partly typed number"
    assert handler.index(guard) < handler.index("limWrite(key)"), "the guard sits after the write"


def test_the_page_saves_a_field_on_blur_and_after_a_pause_in_typing(hub):
    page = hub[0].PAGE
    assert re.search(r'\$\("#lbody"\)\.addEventListener\("change",.*?limWrite\(', page, re.S), \
        "the theme has no blur save"
    assert re.search(r'\$\("#lbody"\)\.addEventListener\("input",.*?limQueue\(', page, re.S), \
        "the theme has no pause save"
    assert "const SET_SAVE=[\"mdl\",\"slk\",\"sub\"]" in page, "the page Save still writes Spend & limits"
