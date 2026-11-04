"""One theme shape for the hub's Settings tab (SPEC 1117 #1278).

Every theme draws the same two blocks: a Now block of read-only tiles, each a key, a value and one of the hub's tones,
and a Settings block of editable rows. No input lives in a Now block and no read-only fact lives in a Settings block.
One moment function, one span function and one money function format every reading, and the three older writers are gone.
The Save bar never hides: a theme the page does not write reads "nothing to save here" with both buttons disabled. The
themes keep one order, and each theme's nav line equals its summary chip.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUB_PY = os.path.join(ROOT, "contrib", "squad-goal", "scripts", "hub.py")
THEMES = ["lop", "lim", "sub", "mdl", "slk", "acc"]


@pytest.fixture(scope="module")
def hub():
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4")):
        os.environ.setdefault(k, v)
    for k in ("SG_HUB_DIR", "SG_STATE", "SG_CHECKOUT", "SG_SHARED"):
        os.environ[k] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_settings_shape", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _view(page):
    i = page.index("<section id=tab-set")
    return page[i:page.index("</section>", i)]


def _node(body):
    if shutil.which("node") is None:
        pytest.skip("node is required")
    p = subprocess.run(["node", "-"], input=body, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout


def _slices(page):
    """The page's own sources a reader needs: `esc`, the loop constants, and the Now/Settings builders (#1278)."""
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    usd = re.search(r"^const usd=.*$", page, re.M).group(0)
    sett = page[page.index("// SETT{"):page.index("// }SETT")]
    loop = page[page.index("// }SETT"):page.index("// loop-switch:end")]
    stub = 'const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:""});'
    return "\n".join([stub, esc, usd, sett, loop])


def _run(page, expr):
    return json.loads(_node(_slices(page) + f"\nconsole.log(JSON.stringify({expr}))"))


LOOP_VIEW = {"state": "on", "who": "owner", "when": "2026-11-02T09:00:00-03:00", "tailnet": False,
             "access": {"loop_write": False, "terminals_send": False},
             "health": {"live_runs": 2, "live_gates": 0, "cycle_age": 45, "starting_left": None},
             "live": {"runs": [], "gates": []}, "log": []}


# ---------------------------------------------------------------- one format for every reading

def test_one_moment_one_span_one_money_function_and_no_old_writer(hub):
    """The page holds one writer per format: `moment`, `span`, `usd`. The three that answered the same question beside
    them are gone, so two panels can no longer read the same fact two ways."""
    page = hub.PAGE
    assert len(re.findall(r"^function moment\(", page, re.M)) == 1
    assert len(re.findall(r"^function span\(", page, re.M)) == 1
    assert len(re.findall(r"^const usd=", page, re.M)) == 1
    for gone in ("rstText", "loopWhen", "usdc"):
        assert not re.search(rf"(function|const) {gone}\b", page), f"{gone} still formats a reading"
    assert not re.search(r"^function (ago|rel)\(", page, re.M), "a third relative-time writer is back"
    assert not re.search(r"(function|const) money\b", page), "a second money writer is back"
    assert "Intl.NumberFormat" not in page, "a second currency formatter is back"


def test_a_moment_keeps_its_wall_clock_and_a_span_reads_in_days_hours_or_minutes(hub):
    out = _run(hub.PAGE, "[moment('2026-11-02T09:05:00-03:00'),moment(1730590000),span(388800),span(1200),"
                         "moment('not a date the hub knows'),moment(''),span(NaN),span(null),span(-5)]")
    assert out[0] == "2026-11-02 09:05" and re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d", out[1]), out
    assert out[2:4] == ["4d 12h", "20m"]
    assert out[4:6] == ["not a date the hub knows", "?"], "a value the hub hands is shown, never replaced by ?"
    assert out[6:] == ["?", "?", "?"], "a span the hub cannot read is '?', never a zero reading"


def test_the_money_function_reads_cents_under_a_hundred_and_whole_dollars_over(hub):
    out = _run(hub.PAGE, "[usd(27.24),usd(99.99),usd(100),usd(281.6),usd(null)]")
    assert out == ["$27.24", "$99.99", "$100", "$282", "—"]


# ---------------------------------------------------------------- Now is read-only, Settings is editable

def _blocks(html):
    """The two blocks of one theme, split apart: what a reader sees and what a reader changes."""
    i = html.index("data-blk=now")
    j = html.index("data-blk=settings")
    return html[i:j], html[j:]


def test_the_loop_theme_draws_now_tiles_and_no_input(hub):
    now, settings = _blocks(_run(hub.PAGE, f"loopLogHtml({json.dumps(LOOP_VIEW)})"))
    assert re.findall(r"<span class=tk>(.*?)</span>", now) == ["Live", "Health", "Last change"], now
    for ctl in ("<input", "<select", "<textarea", "<button"):
        assert ctl not in now, f"the Loop Now block carries {ctl}"
    assert "Settings" in settings and "Monitor" in settings, settings


def test_the_loop_health_reads_the_last_cycles_age_and_says_none_when_it_has_none(hub):
    """The Now tiles' `cycle_age` is a fact of the cycle log: seconds since the last cycle's own line. A log with no cycle
    is None, never zero, so the tile says "no cycle yet" (#1280, SPEC 1117)."""
    log = hub.STATE / "cycle.log"
    log.unlink(missing_ok=True)
    assert hub.loop_health({}, now=1_700_000_000.0)["cycle_age"] is None
    now = time.time()
    log.write_text(time.strftime("%H:%M:%S", time.localtime(now - 90)) + " CYCLE 30s\n")
    health = hub.loop_health({"runs": ["a", "b"], "gates": []}, now=now)
    assert health["live_runs"] == 2 and health["live_gates"] == 0
    assert 85 <= health["cycle_age"] <= 95, health["cycle_age"]
    log.unlink()


def test_the_access_theme_states_its_facts_and_names_who_sets_each_value(hub):
    now, settings = _blocks(_run(hub.PAGE, "accHtml({tailnet:true,access:{loop_write:false,terminals_send:false}})"))
    assert "tailnet" in now and "refused" in now and "<input" not in now and "<select" not in now, now
    assert "Settings" in settings and "project file" in settings, settings


def test_every_tone_a_tile_takes_is_one_the_hub_names(hub):
    out = _run(hub.PAGE, '[setTile("k","v","ok"),setTile("k","v","warn"),setTile("k","v","err"),'
                         'setTile("k","v","neutral"),setTile("k","v","bogus")]')
    assert all(re.search(r'<div class="tile (ok|warn|err|neutral)"', t) for t in out), out
    assert 'class="tile neutral"' in out[4], "a tone the hub does not name fell through"


def test_a_settings_row_carries_its_label_and_its_control(hub):
    out = _run(hub.PAGE, 'setSettings("x",[setRow("token cap","<input id=c>")])')
    assert 'data-blk=settings' in out and "<span class=sl>token cap</span>" in out and "<input id=c>" in out


def _comm_slices(page):
    """The Communication theme's own renderer beside the one Now/Settings shape it draws through."""
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    sett = page[page.index("// SETT{"):page.index("// }SETT")]
    sl = page[page.index("let slS=null"):page.index("async function slLoad()")]
    stub = 'const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:""});'
    return "\n".join([stub, esc, sett, sl])


def test_the_communication_settings_keep_their_rows_and_their_tables_apart(hub):
    """Each value is one label/control row; the channel, people and template tables follow as siblings, never folded
    into one row (#1278)."""
    view = dict(hub.SLACK_DEFAULT,
                channels=[{"name": "#dev", "lang": "en-US", "watch": True, "thread": True, "mention": False,
                           "events": {k: True for k in hub.SLACK_EVENTS}}],
                people=[{"name": "Ana", "role": "Manager"}])
    body = (_comm_slices(hub.PAGE) + f"\nslS={json.dumps(view)};slDraft=slClone(slS);slRender();"
            "console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    settings = _blocks(json.loads(_node(body)))[1]
    assert settings.count("class=setrow") == 6, settings
    assert settings.count("<h4>") == 4, settings
    for head in ("Channels", "People", "Templates", "Owner inbox"):
        assert f"<h4>{head}</h4>" in settings, head
    assert "#dev" in settings and "Ana" in settings


# ---------------------------------------------------------------- Communication states each role once (#1284)

def _sl_theme(hub, people, connection="connected"):
    """The Communication theme as the page draws it, with the people a reader would see."""
    view = dict(hub.SLACK_DEFAULT, connection=connection, people=people)
    body = (_comm_slices(hub.PAGE) + f"\nslS={json.dumps(view)};slDraft=slClone(slS);slRender();"
            "console.log(JSON.stringify({body:STORE['#sbody'].innerHTML,"
            "chip:STORE['#slksr'].textContent,nav:STORE['#slknav'].textContent}))")
    return json.loads(_node(body))


def test_the_communication_legend_states_each_role_once_and_no_person_row_repeats_it(hub):
    """A role's behavior is a legend above the People table, once for the theme. Two people of one role do not double it,
    and no person row carries the behavior text: the row holds the name, the role select and Remove (#1284)."""
    roles = json.loads(_node(_comm_slices(hub.PAGE) + "\nconsole.log(JSON.stringify(SL_ROLE))"))
    assert sorted(roles) == ["Colleague", "Manager"], "the theme names an unexpected role"
    out = _sl_theme(hub, [{"name": "Ana", "role": "Manager"}, {"name": "Bo", "role": "Manager"}])
    body = out["body"]
    for role, text in roles.items():
        assert body.count(text) == 1, f"{role}'s behavior appears {body.count(text)} times, not once"
    start = body.index("</p>", body.index("<h4>People</h4>"))   # after the legend paragraph
    people = body[start:body.index("<h4>Templates</h4>")]
    for text in roles.values():
        assert text not in people, "a person row repeats a role's behavior"
    assert people.count('data-sl="pp:') == 2, "the person rows lost their role select"


def test_the_connection_fact_is_a_now_tile_with_a_tone_and_not_an_editable_row(hub):
    out = _sl_theme(hub, [])
    now, settings = _blocks(out["body"])
    assert re.search(r'<div class="tile ok"><span class=tk>Connection</span><span class=tv>connected</span>', now), now
    assert "connection" not in settings.lower(), "the connection fact is still a settings row"
    warn = _sl_theme(hub, [], connection="not connected")
    assert re.search(r'<div class="tile warn"><span class=tk>Connection</span>', _blocks(warn["body"])[0])


def test_the_owner_inbox_switches_carry_the_owner_inbox_name(hub):
    settings = _blocks(_sl_theme(hub, [])["body"])[1]
    assert "<h4>Owner inbox</h4>" in settings
    assert "Optional" not in settings, "the old Optional heading stayed"


def test_the_communication_chip_states_the_connection_and_the_people_count_in_the_nav_line(hub):
    """The theme's chip is a status: it reads the connection and how many people the agent answers, and the nav's small
    line carries the same text (#1284)."""
    cases = [([{"name": "Ana", "role": "Manager"}, {"name": "Bo", "role": "Colleague"}], "connected",
              "connected · 2 people"),
             ([{"name": "Ana", "role": "Manager"}], "connected", "connected · 1 person"),
             ([], "not connected", "not connected · 0 people")]
    for people, connection, want in cases:
        out = _sl_theme(hub, people, connection=connection)
        assert out["chip"] == want, out["chip"]
        assert out["nav"] == out["chip"], "the nav line disagrees with the summary chip"


# ---------------------------------------------------------------- the Save bar and the dialog

def _set_bar(hub):
    """The Save bar's own source, run in node: what it labels the bar and which buttons it disables for one theme."""
    page = hub.PAGE
    src = page[page.index("const STH="):page.index("function setTheme(")]
    stub = ('const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:"",disabled:false,hidden:null,'
            'classList:{toggle(){}}});const ls={};const mdlChanged=()=>false;const slDirty=()=>false;'
            'const limVals=()=>({guard:"",cap:"",factor:""});let limS=null,slDraft=null,slS=null;'
            'let RO=false;const RO_WHY="Read-only over the tailnet";')
    body = ('const out={};for(const t of ["lop","lim","sub","mdl","slk","acc"]){sTheme=t;RO=false;setBar();'
            'out[t]={hidden:$("#sbar").hidden,disabled:$("#ssave").disabled,text:$("#schg").textContent}}'
            'RO=true;sTheme="mdl";setBar();out.ro={hidden:$("#sbar").hidden,disabled:$("#ssave").disabled,'
            'text:$("#schg").textContent};console.log(JSON.stringify(out));')
    return json.loads(_node("\n".join([stub, src, body])))


def test_the_save_bar_never_hides_and_a_read_only_theme_says_so(hub):
    out = _set_bar(hub)
    for theme in THEMES:
        assert out[theme]["hidden"] is False, f"{theme}: the Save bar hid"
        assert out[theme]["disabled"] is True, f"{theme}: Save was armed with nothing to write"
    for theme in ("lop", "acc"):
        assert out[theme]["text"] == "nothing to save here", f"{theme}: {out[theme]['text']!r}"
    # re-anchored (#1281): Subscriptions stages its edits like every other writable theme, so its bar reads as no change.
    assert out["sub"]["text"] == "Subscriptions: no change", out["sub"]["text"]
    assert out["lim"]["text"] == "Spend & limits: no change" and out["mdl"]["text"] == "Models & chains: no change"
    assert out["ro"]["hidden"] is False and out["ro"]["disabled"] is True
    assert out["ro"]["text"] == "Read-only over the tailnet"
    assert 'const SET_SAVE=["lim","mdl","slk","sub"]' in hub.PAGE


def test_every_input_in_settings_takes_the_body_size_and_the_page_control_height(hub):
    page = hub.PAGE
    assert re.search(r"#tab-set input,#tab-set select\{font-size:var\(--fs-body\);height:var\(--ctl-h\)\}", page)
    assert re.search(r"#tab-set textarea\{font-size:var\(--fs-body\);min-height:var\(--ctl-h\)\}", page)
    assert "#tab-set .tile{" in page and "#tab-set .setrow{" in page


def test_the_save_dialog_groups_its_lines_by_the_file_it_names(hub):
    out = _run(hub.PAGE, 'diffByFile([{file:"/p/models.json",lines:["a","b"]},{file:"/p/empty.json",lines:[]},'
                         '{file:"/p/slack.json",lines:["c"]}])')
    assert out == "/p/models.json\n  a\n  b\n/p/slack.json\n  c", out


def test_the_hub_names_each_path_the_dialog_shows(hub, monkeypatch):
    page = hub.PAGE
    assert "(mdl&&mdl.file)" in page and "(limS&&limS.file)" in page and "(slS&&slS.file)" in page
    assert hub.models_get()["file"].endswith("models.json")
    limit_file = hub.pathlib.Path("/p/squad.sh")
    monkeypatch.setattr(hub, "profile_file", lambda: limit_file)
    view = hub.limits_read()
    assert view["file"] == str(limit_file), "the Limits group names no real path"
    assert view["profile"] == str(limit_file)


# ---------------------------------------------------------------- one order, one chip per theme

def test_the_themes_keep_one_order_in_the_nav_the_phone_select_and_the_panels(hub):
    view = _view(hub.PAGE)
    nav = re.search(r'<nav class=snav aria-label="Settings themes">(.*?)</nav>', view, re.S).group(1)
    assert re.findall(r"data-sp=(\w+)", nav) == THEMES, "the nav order changed"
    sel = re.search(r"<select id=ssel[^>]*>(.*?)</select>", view, re.S).group(1)
    assert re.findall(r"value=(\w+)", sel) == THEMES, "the phone select does not follow the nav order"
    assert re.findall(r"<details id=(\w+) class=sp", view) == THEMES, "the panels do not follow the nav order"
    assert [t.replace("&amp;", "&") for t in re.findall(r"<button data-sp=\w+>(.*?)<small", nav)] == \
           ["Loop", "Spend & limits", "Subscriptions", "Models & chains", "Communication", "Access"]


def test_each_nav_line_equals_its_theme_summary_chip_and_one_writer_sets_both(hub):
    view = _view(hub.PAGE)
    nav = re.search(r'<nav class=snav aria-label="Settings themes">(.*?)</nav>', view, re.S).group(1)
    for t in THEMES:
        small = re.search(rf"<small id={t}nav>(.*?)</small>", nav).group(1)
        summ = re.search(rf"<summary>[^<]*<span class=sr id={t}sr>(.*?)</span>", view).group(1)
        assert small == summ, f"{t}: the nav line {small!r} disagrees with its chip {summ!r}"
    out = _run(hub.PAGE, '(setChip("lop","on"),[STORE["#lopsr"].textContent,STORE["#lopnav"].textContent])')
    assert out == ["on", "on"], "one chip update did not reach both places"


def test_a_loop_poll_writes_one_chip_text_to_the_nav_line_and_the_summary(hub):
    """After `/api/loop` answers, the Loop chip carries the live count; the nav line and the summary must still agree,
    because the reader compares them at a glance (#1278)."""
    page = hub.PAGE
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    sett = page[page.index("// SETT{"):page.index("// }SETT")]
    loop = page[page.index("// }SETT"):page.index("// loop-switch:end")]
    draw = page[page.index("function loopDraw("):page.index("async function loopPoll(")]
    stub = ('const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:"",dataset:{}});'
            'let loopV=null,loopErr="";')
    body = "\n".join([stub, esc, sett, loop, draw,
                      f"loopV={json.dumps(LOOP_VIEW)};loopDraw();"
                      "console.log(JSON.stringify([STORE['#lopsr'].textContent,STORE['#lopnav'].textContent]))"])
    assert json.loads(_node(body)) == ["on · 2 runs", "on · 2 runs"]


# ---------------------------------------------------------------- a page that may not write

def test_the_read_only_flag_follows_the_hubs_write_rule(hub):
    """The server lets an allowed login write the settings routes whatever its Host (do_POST), so an allowed page is
    writeable. The page never applies a stricter rule than the hub; only a request the hub refuses to write is read-only."""
    allowed = hub.served_page("me", True)
    assert 'const RO=("false"==="true")' in allowed, "an allowed page was drawn read-only"
    assert "Read-only over the tailnet" not in allowed
    refused = hub.served_page("me", False)
    assert 'const RO=("true"==="true")' in refused
    assert "Read-only over the tailnet" in refused
    assert re.search(r"<p class=ro id=ro hidden></p>", refused)


def test_the_read_only_pass_leaves_the_theme_navigation_live(hub):
    """Browsing the six themes is reading, not writing: a read-only page still opens every panel. Only real controls are
    disabled, and the 'mobile view' link goes with them."""
    page = hub.PAGE
    src = page[page.index("function roAll("):page.index("function roWatch(")]
    body = """
function el(tag,inside){return {tagName:tag.toUpperCase(),disabled:false,style:{},setAttribute(){},
  closest:s=>inside.includes(s)?{}:null}}
const nav=el("button",[".snav"]), sel=el("select",["#ssel"]), input=el("input",[]), link=el("a",[]);
roAll({querySelectorAll:()=>[nav,sel,input,link]});
console.log(JSON.stringify({nav:nav.disabled,sel:sel.disabled,input:input.disabled,link:link.disabled}));"""
    out = json.loads(_node(src + body))
    assert out == {"nav": False, "sel": False, "input": True, "link": True}, out


def test_the_loop_switch_follows_the_hubs_write_rule_not_the_page_flag(hub):
    """The grant is the hub's to decide: `loopSw` disables the switch on `write`, never on the page-wide flag, and the
    read-only pass never touches the Monitor's switch (#1278)."""
    out = _run(hub.PAGE, '[loopSw({state:"on",write:true,reason:""}),loopSw({state:"on",write:false,reason:"read-only"})]')
    assert " disabled" not in out[0], "a granted tailnet write was disabled"
    assert " disabled" in out[1], "a refused write stayed live"
    assert 'roWatch($("#iloop"))' not in hub.PAGE, "the read-only pass overrode the loop grant"


def test_a_loopback_page_is_not_read_only(hub):
    page = hub.served_page("me", True)
    assert 'const RO=("false"==="true")' in page
    assert "Read-only over the tailnet" not in page


# ---------------------------------------------------------------- one boot load per panel

def _boot_loads(hub, stored):
    """The page's own accordion boot, run in node: each panel opened at boot loads its data once and no panel loads twice."""
    page = hub.PAGE
    sth = page[page.index("const STH="):page.index("function setTheme(")]
    theme = page[page.index("function setTheme("):page.index('$(".snav")')]
    acc = page[page.index("const ACC="):page.index("if(RO){")]
    boot = page[page.index("setTheme(sTheme);"):page.index("const PCOL=")]
    harness = """
const Q=[];
class El{
 constructor(id){this.id=id;this._o=false;this.dataset={};this.classList={toggle(){}};this.value="";this.disabled=false;}
 get open(){return this._o;}
 set open(v){v=!!v;if(v===this._o)return;this._o=v;if(this.bound)Q.push(this);}
 addEventListener(t,f){(this.L=this.L||{});(this.L[t]=this.L[t]||[]).push(f);this.bound=true;}
 fire(t){(this.L&&this.L[t]||[]).forEach(f=>f({target:this}));}
}
const DETAILS=["lop","lim","sub","mdl","slk","acc"].map(sp=>{const e=new El(sp);e.dataset.sp=sp;return e});
const NAV=DETAILS.map(e=>new El(e.dataset.sp));
const ELS={};DETAILS.forEach(e=>ELS[e.id]=e);
const $=s=>{const id=String(s).replace(/^#/,"");return ELS[id]||(ELS[id]=new El(id));};
const document={querySelectorAll:s=>s==="#tab-set details.sp"?DETAILS:s===".snav button"?NAV:[]};
const RO=false,RO_WHY="";
let mdln=0,limn=0,slkn=0;
function mdlLoad(){mdln++;}function limLoad(){limn++;}function slLoad(){slkn++;}
const mdlChanged=()=>false,slDirty=()=>false;
let mdl=null,limS=null,slS=null,slDraft=null;
const ls=__STORED__,save=()=>{};
"""
    tail = """
function drain(){let n=0;while(Q.length){if(++n>100)throw new Error("toggle storm");Q.shift().fire("toggle");}}
drain();console.log(JSON.stringify({mdl:mdln,lim:limn,slk:slkn}));"""
    src = "\n".join([harness.replace("__STORED__", json.dumps(stored)), sth, theme, acc, boot, tail])
    return json.loads(_node(src))


def test_an_opened_panel_loads_its_data_once_at_boot(hub):
    """A panel opened at boot loads through its own toggle, once. The models panel answers on boot while it is closed (its
    chip needs the count), and skips that boot load when the toggle already fetched it (#1278)."""
    cases = {
        "mdl": ({"sth": "mdl"}, {"mdl": 1, "lim": 0, "slk": 0}),
        "lim": ({"sth": "lim"}, {"mdl": 1, "lim": 1, "slk": 0}),
        "slk": ({"sth": "slk"}, {"mdl": 1, "lim": 0, "slk": 1}),
        "closed": ({"sth": "lop"}, {"mdl": 1, "lim": 0, "slk": 0}),
    }
    for name, (stored, want) in cases.items():
        assert _boot_loads(hub, stored) == want, f"{name}: {stored}"
