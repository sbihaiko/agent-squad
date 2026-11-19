"""Subscriptions saves through stage, diff and confirm (SPEC 1117 #1281).

A plan, amount, currency or quota change stages into a draft; the dirty flag lists the Subscriptions theme; the one Save
bar asks the hub for a diff first and writes only on confirm, so a mistake is discarded before it is written. The preview
answers one line per changed plan and writes nothing. The theme names the plans file, draws each plan's quota with the
sidebar usage card's own bar and text from the same answer, holds no dead write, and its summary reads "n at limit",
"all under limit" or "no plan chosen".
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
HUB_SRC = HUB_PY.read_text()
NODE = shutil.which("node")


@pytest.fixture
def hub(monkeypatch, tmp_path):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path)), ("SG_STATE", str(tmp_path / "st"))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", SCRIPTS / "ghread.py")
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_plans_save", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    plans = tmp_path / "plans.json"
    plans.write_text(json.dumps({k: None for k in mod.SUBS}, indent=2) + "\n")
    monkeypatch.setattr(mod, "PLANS", plans)
    monkeypatch.setattr(mod, "quota", {})
    monkeypatch.setattr(mod, "all_runs", lambda: [])
    monkeypatch.setattr(mod, "rate", lambda cur: 1.0)
    return mod, plans


# ---------------------------------------------------------------- the plans write: preview, confirm, no write

def test_a_preview_returns_one_line_per_changed_plan_and_writes_nothing(hub):
    mod, plans = hub
    before = plans.read_text()
    out = mod.plans_set({"claude": {"plan": "max20"}, "grok": {"plan": "heavy"}, "confirm": False})
    assert out["diff"] == ["Subscriptions · Claude Max 20x: no plan -> Max 20x · US$200",
                           "Subscriptions · SuperGrok Heavy: no plan -> SuperGrok Heavy · US$300"], out
    assert plans.read_text() == before, "a preview wrote the plans file"


def test_a_confirmed_save_writes_the_file(hub):
    mod, plans = hub
    out = mod.plans_set({"claude": {"plan": "max20"}, "confirm": True})
    assert json.loads(plans.read_text())["claude"] == {"plan": "max20", "amount": 200, "currency": "USD"}
    assert out["diff"] == ["Subscriptions · Claude Max 20x: no plan -> Max 20x · US$200"]


def test_a_confirmed_save_with_no_change_writes_nothing(hub):
    mod, plans = hub
    before = plans.read_text()
    out = mod.plans_set({"confirm": True})
    assert out["diff"] == [] and plans.read_text() == before


def test_a_quota_change_previews_then_writes_the_same_plan(hub):
    mod, plans = hub
    mod.plans_set({"claude": {"plan": "max20"}, "confirm": True})
    before = plans.read_text()
    out = mod.plans_set({"claude": {"quota": 80, "qwin": "weekly"}, "confirm": False})
    assert out["diff"] == ["Subscriptions · Claude Max 20x: Max 20x · US$200 -> Max 20x · US$200 · quota 80%"], out
    assert plans.read_text() == before, "the quota preview wrote the plans file"
    mod.plans_set({"claude": {"quota": 80, "qwin": "weekly"}, "confirm": True})
    assert json.loads(plans.read_text())["claude"]["quota"] == 80


def test_an_invalid_plan_is_refused_with_no_write(hub):
    mod, plans = hub
    before = plans.read_text()
    out = mod.plans_set({"claude": {"plan": "nope"}, "confirm": True})
    assert out["err"] and plans.read_text() == before


def test_the_preview_names_a_changed_plan_and_never_raises_on_an_odd_row(hub):
    """plans_diff calls sub_name; a plan cleared, a row with no `plan` key and no row at all all read as the provider,
    so a preview never throws inside the write path (#1281 review)."""
    mod, _ = hub
    assert mod.plans_diff({"claude": {"plan": "max20"}}, {"claude": {"plan": "max5"}}) == \
        ["Subscriptions · Claude Max 5x: Max 20x · US$200 -> Max 5x · US$100"]
    assert mod.plans_diff({"claude": {"plan": "max5"}}, {"claude": None}) == \
        ["Subscriptions · Claude: Max 5x · US$100 -> no plan"]
    assert mod.plans_diff({"claude": {"quota": 40}}, {"claude": {"plan": "max5"}})   # a malformed row never raises
    assert mod.sub_name("claude", {"quota": 40}, None) == "Claude"
    assert mod.sub_name("claude", None, None) == "Claude"


def test_the_plans_route_still_writes_without_confirm_and_previews_only_on_false(hub):
    """The /api/plans wire contract keeps its write-on-POST (#1281 review): a body with no confirm writes, as it did
    before this change, so an existing caller (a script, curl) never turns into a silent preview. Save's diff step is
    explicit: `confirm: false` answers the diff and writes nothing, and only a boolean confirm is accepted, so the
    string "true" or the number 1 is refused instead of read as either a save or a preview."""
    mod, plans = hub
    before = plans.read_text()
    out = mod.plans_set({"claude": {"plan": "max20"}, "confirm": False})
    assert out["diff"] and "plans" not in out and plans.read_text() == before
    for bad in ("true", 1, "yes"):
        out = mod.plans_set({"claude": {"plan": "max20"}, "confirm": bad})
        assert out["err"] and plans.read_text() == before, (bad, out)
    mod.plans_set({"claude": {"plan": "max5"}})
    assert json.loads(plans.read_text())["claude"]["plan"] == "max5", "a body with no confirm no longer writes"
    out = mod.plans_set({"claude": {"plan": "max20"}, "confirm": True})
    assert out["diff"] and json.loads(plans.read_text())["claude"]["plan"] == "max20"


# ---------------------------------------------------------------- the page: stage, diff, confirm

def _page_slices(page, stub=None):
    """The subscriptions panel's own source: its state, its renderer and its save path."""
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    usd = re.search(r"^const usd=.*$", page, re.M).group(0)
    iq = re.search(r"^const iq=.*$", page, re.M).group(0)
    sett = page[page.index("// SETT{"):page.index("// }SETT")]
    gauges = page[page.index("function subsGauges(d)"):page.index("function instGauges(d)")]
    state = page[page.index("const STH="):page.index("async function pollStats(")]   # carries fpct, which the card's row reads
    stub = stub or ('const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:"",disabled:false,hidden:null,'
                    'value:"",onclick:null,addEventListener(){},showModal(){},close(){},classList:{toggle(){}}});'
                    'function setBar(){};function save(){};const ls={};const RO=false,RO_WHY="";'
                    'let limS=null,slDraft=null,slS=null;const limVals=()=>({}),mdlChanged=()=>false,slDirty=()=>false,brdDirty=()=>false,'
                    'mdlLoad=()=>{};'
                    'globalThis.document={addEventListener(){},querySelectorAll:()=>[],activeElement:null};')
    return "\n".join([stub, esc, usd, iq, sett, gauges, state])


# A shell that records the listeners the panel registers and every outgoing write, so the change handler is run, not read.
_LIVE = ('const STORE={},HANDLERS={},SENT=[];'
         'const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:"",disabled:false,hidden:null,value:"",onclick:null,'
         'addEventListener(t,f){(HANDLERS[s+":"+t]=HANDLERS[s+":"+t]||[]).push(f)},'
         'querySelector(){return null},querySelectorAll(){return[]},closest(){return null},showModal(){},close(){},'
         'classList:{toggle(){}}});'
         'function setBar(){};function save(){};const ls={};const RO=false,RO_WHY="";'
         'let limS=null,slDraft=null,slS=null;const limVals=()=>({}),mdlChanged=()=>false,slDirty=()=>false,brdDirty=()=>false,'
         'mdlLoad=()=>{};'
         'globalThis.document={addEventListener(){},querySelectorAll:()=>[],activeElement:null};'
         'globalThis.fetch=(u)=>{SENT.push(u);return Promise.resolve({json:async()=>({})})};'
         'globalThis.XMLHttpRequest=function(){this.open=(m,u)=>SENT.push(u);this.send=()=>SENT.push("sent");'
         'this.setRequestHeader=()=>{}};'
         'globalThis.navigator={sendBeacon:u=>{SENT.push(u);return true}};')


def _node(mod, body, stub=None):
    if NODE is None:
        pytest.skip("node is required")
    p = subprocess.run(["node", "-"], input=_page_slices(mod.PAGE, stub) + "\n" + body, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _payload(pct=None, saved=True, api=28.0):
    return {"frac": 0.34, "plans": 255, "api": api, "total": 283, "file": "/p/plans.json",
            "presets": {"claude": {"c1": {"name": "Max 20x", "usd": 200}},
                        "grok": {"g1": {"name": "SuperGrok", "usd": 30}}},
            "subs": {
                "claude": {"name": "Claude Max 20x", "saved": 10 if saved else None, "usd": 200, "rate": 1,
                           "noRate": False, "eq": 250, "plan": {"plan": "max20", "amount": 200, "currency": "USD"},
                           "quota": {"windows": [{"label": "weekly", "pct": 77, "resets": 2}]}},
                "grok": {"name": "Grok", "saved": None, "usd": None, "rate": 1, "noRate": False, "eq": 0,
                         "plan": None, "quota": {"unavailable": True, "err": "cli probe failed"}}},
            "gauges": [
                {"key": "grok", "label": "Grok", "pct": None, "resets": None, "why": "no reading"},
                {"key": "claude", "label": "Claude Max 20x", "pct": pct, "resets": 1792807174.0, "window": "weekly"}]}


def test_a_plan_change_stages_a_draft_and_the_page_posts_nothing_on_change(hub):
    """The change handler is run, not read: with a fetch/XHR/sendBeacon recorder it stages the draft, arms the Save,
    and sends nothing. A post under any other call name would still be caught (#1281 review)."""
    mod = hub[0]
    page = mod.PAGE
    assert page.count('fetch("/api/plans"') == 1, "a second path posts the plans route"
    i = page.index('fetch("/api/plans"')
    assert page.index("async function subPost(") < i < page.index("async function pollSubs("), "the only post is not the confirm"
    got = _node(mod, """
subS={claude:null,grok:null};subSrv=JSON.stringify(subS);subDraft=JSON.parse(subSrv);
subData={frac:0.3,plans:0,api:0,total:0,file:"/p/plans.json",gauges:[],
 presets:{claude:{},grok:{}},
 subs:{claude:{name:"Claude",plan:null,quota:null},grok:{name:"Grok",plan:null,quota:null}}};
const before=JSON.stringify(subDraft);
const h=HANDLERS["#subs:change"][0];
const row={querySelector:s=>s==="[data-f=plan]"?{value:"max20"}:{value:""}};
h({target:{dataset:{plan:"claude",f:"plan"},closest:()=>row}});
console.log(JSON.stringify({sent:SENT,staged:subDraft.claude,dirty:setDirty(),changed:JSON.stringify(subDraft)!==before}));
""", stub=_LIVE)
    assert got["sent"] == [], got["sent"]
    assert got["staged"] == {"plan": "max20"} and got["changed"], got
    assert "sub" in got["dirty"], got["dirty"]


def test_the_dirty_flag_lists_the_subscriptions_theme_while_a_change_is_staged(hub):
    mod = hub[0]
    assert '["sub",subDirty()]' in mod.PAGE, "the dirty flag does not list the subscriptions theme"
    got = _node(mod, "subS={claude:null};subSrv=JSON.stringify(subS);subDraft=JSON.parse(subSrv);"
                     'const a=subDirty();subDraft.claude={plan:"max20"};'
                     "console.log(JSON.stringify([a,subDirty()]));")
    assert got == [False, True], got


def test_the_theme_names_the_plans_file_and_draws_each_quota_with_the_sidebar_card(hub):
    mod = hub[0]
    page = mod.PAGE
    seg = page[page.index("const STH="):page.index("async function pollSubs(")]
    assert "subsGauges({subs:[" in seg, "the theme draws its own bar instead of the sidebar card's"
    assert '"gauges": subs_gauges()' in HUB_SRC and '"file": str(PLANS)' in HUB_SRC, \
        "the subs answer carries neither the sidebar's gauges nor the plans file"
    got = _node(mod, f"subData={json.dumps(_payload(77))};subS={{}};subSrv='{{}}';subDraft={{}};subRender();"
                     'console.log(JSON.stringify({now:$("#subnow").innerHTML,subs:$("#subs").innerHTML,chip:$("#subsr").textContent}));')
    assert "Plans file" in got["now"] and "/p/plans.json" in got["now"], got["now"]
    assert "<span class=sn>Claude Max 20x</span>" in got["subs"], got["subs"][:400]
    assert "<b class=sp2>77%</b>" in got["subs"] and 'class="srow' in got["subs"], got["subs"][:400]
    assert got["chip"] == "all under limit", got["chip"]


def test_the_hand_quota_row_shows_whenever_a_plan_is_saved(hub):
    """The hand-entered quota row follows the saved plan object, never the live CLI window (#1281 review): with a plan
    saved the owner can correct or clear a stored quota even when the probe reports one, and a row with no plan keeps
    no quota field."""
    mod = hub[0]

    def rendered(windowed):
        p = _payload(77)
        p["subs"]["claude"]["quota"] = {"windows": [{"label": "weekly", "pct": 77, "resets": 2}]} if windowed else None
        return _node(mod, f"subData={json.dumps(p)};subS={{}};subSrv='{{}}';"
                          'subDraft={claude:{plan:"max20",amount:200,currency:"USD"}};subRender();'
                          'console.log(JSON.stringify($("#subs").innerHTML));')

    assert "data-f=quota" in rendered(True), "a plan with a live CLI reading lost its hand quota row"
    assert "data-f=quota" in rendered(False), "a saved plan with no CLI reading lost its hand quota row"
    alone = _node(mod, f"subData={json.dumps(_payload(77))};subS={{}};subSrv='{{}}';subDraft={{}};subRender();"
                       'const h=$("#subs").innerHTML.split("<div class=sub>");'
                       'console.log(JSON.stringify(h.find(x=>x.includes("Grok"))||""));')
    assert "data-f=quota" not in alone, "a row with no plan still offers a hand quota field"


def test_the_summary_reads_at_limit_all_under_limit_or_no_plan_chosen(hub):
    mod = hub[0]
    for payload, want in ((_payload(100), "1 at limit"), (_payload(77), "all under limit"),
                          (_payload(77, saved=False, api=0.0), "no plan chosen")):
        got = _node(mod, f"subData={json.dumps(payload)};subS={{}};subSrv='{{}}';subDraft={{}};subRender();"
                         'console.log(JSON.stringify($("#subsr").textContent));')
        assert got == want, (got, want)


def test_the_page_holds_no_dead_write_in_the_subscriptions_panel(hub):
    """Every element the panel writes must exist on the page, whatever its name, and the exact call site #1281 deleted
    stays deleted (#1281 review): a re-add of the cash-element write fails on the literal even when its id exists."""
    page = hub[0].PAGE
    seg = page[page.index("const STH="):page.index("async function pollStats(")]
    targets = set(re.findall(r'\$\("#([A-Za-z0-9_]+)"\)', seg))
    ids = set(re.findall(r"id=([A-Za-z0-9_-]+)", page))
    assert targets <= ids, f"the panel writes elements it does not have: {sorted(targets - ids)}"
    for dead in ("cashEl", '"#cash"'):
        assert dead not in page, f"the dead write to the cash element is back: {dead}"
