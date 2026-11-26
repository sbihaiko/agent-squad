"""Model chains are edited in a settings view with a diff before save (#913, SPEC 889).

The served page carries the Settings themes, a left theme list with a select for the phone, and one page-level Save.
The models panel is run in node over a fixture: its rows carry every control, the add list offers only the models that
are on and not in the chain, and the sidebar card carries no control that writes. The diff and the reset text are pure
functions of the page, run in node as they ship.
"""
import http.client
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
MOBILE = SCRIPTS / "hub-mobile.html"


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
    spec = importlib.util.spec_from_file_location("hub_settings_view", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, srv.server_port
    srv.shutdown()
    srv.server_close()


def _get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path)
    r = c.getresponse()
    return r.status, r.read().decode()


def _between(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i)]


# ---------------------------------------------------------------- the served page

def test_settings_has_a_theme_list_a_phone_select_and_one_page_save(hub):
    _, port = hub
    st, page = _get(port, "/")
    assert st == 200
    view = _between(page, "<section id=tab-set", "</section>\n</main>")
    nav = re.search(r'<nav class=snav aria-label="Settings themes">(.*?)</nav>', view, re.S)
    assert nav, "Settings has no left theme list"
    themes = re.findall(r"data-sp=(\w+)", nav.group(1))
    assert themes == ["lim", "sub", "mdl", "slk", "prs", "acc"], "Settings lists a theme that belongs to another screen (#2515)"
    sel = re.search(r"<select id=ssel[^>]*>(.*?)</select>", view, re.S)
    assert sel and re.findall(r"value=(\w+)", sel.group(1)) == themes, "the phone select does not list the same themes"
    assert view.count("id=ssave") == 1, "Settings needs exactly one page-level Save"
    for sp in ("lim", "acc"):
        panel = re.search(rf"<details id={sp} class=sp data-sp={sp}.*?</details>", view, re.S).group(0)
        assert "Save" not in panel, f"the {sp} theme carries a Save"


def test_settings_lists_no_loop_theme(hub):
    """#2515: the Loop theme left Settings. The page keeps one theme per remaining screen, in the nav and in the
    phone select, and the loop's change list lives in the Loop tab."""
    _, port = hub
    st, page = _get(port, "/")
    assert st == 200
    view = _between(page, "<section id=tab-set", "</section>\n</main>")
    nav = re.search(r'<nav class=snav aria-label="Settings themes">(.*?)</nav>', view, re.S)
    assert nav and "Loop" not in nav.group(1), "Settings still lists a Loop theme"
    assert "data-sp=lop" not in view and "<details id=lop " not in view, "Settings still carries the Loop panel"
    sel = re.search(r"<select id=ssel[^>]*>(.*?)</select>", view, re.S)
    assert sel and "value=lop" not in sel.group(1), "the phone select still lists the Loop theme"


def test_the_loop_change_list_is_reachable_from_the_loop_tab(hub):
    """#2515: the change list left Settings with its theme; the Loop tab still draws it."""
    _, port = hub
    _, page = _get(port, "/")
    loop_tab = _between(page, "<section id=tab-prof", "</section>")
    assert "id=lopaud" in loop_tab, "the loop change list left the page with the Loop theme"


def test_the_sidebar_carries_no_control_that_writes_a_chain_and_no_link_to_settings(hub):
    _, port = hub
    _, page = _get(port, "/")
    side = _between(page, "<aside id=side>", "</aside>")
    for w in ("data-ef", "data-hr", "data-mv", "data-rm", "data-add", "data-mo", "data-mt", "ssave", "msv"):
        assert w not in side, f"the sidebar carries {w}"
    assert "data-t=set" not in side and "#set" not in side


def test_the_mobile_page_carries_no_settings_entry(hub):
    """Re-anchored (#2499, SPEC 2498): Settings change only on the desktop, so the phone page the hub serves carries no
    More menu and no Settings link."""
    _, port = hub
    st, page = _get(port, "/mobile")
    assert st == 200
    assert 'id="more-menu"' not in page, "the phone page still draws the More menu"
    assert 'id="more-settings"' not in page, "the phone page still carries a Settings entry"
    assert './#set' not in page, "the phone page still links a Settings theme address"


# ---------------------------------------------------------------- the panel, run in node

FIXTURE = {
    "coding_models": ["a", "b"], "review_models": ["c"], "vision_models": [], "router_models": ["a"],
    "efforts": {}, "hours": {"coding_models": {"b": "22-07"}}, "off": {}, "model_off": {"b": True},
    "models": [{"id": "a", "state": "on"}, {"id": "b", "state": "off (model)"}, {"id": "c", "state": "at limit"},
               {"id": "d", "state": "on"}],
    "suggest": {k: [{"id": x, "text": x.upper()} for x in "abcd"] for k in
                ("coding_models", "review_models", "vision_models", "router_models")},
    "boxes": {}, "outside": {}, "chain": {}, "version": 1,
    "file": "/p/models.json", "counts": {"on": 2, "at_limit": 1},   # the shape models_get() hands the panel (#1278)
}


def _node(hub, body):
    if shutil.which("node") is None:
        pytest.skip("node is required")
    mod, _ = hub
    page = mod.PAGE
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    setv = re.search(r"^// SETV\{.*?^// \}SETV$", page, re.M | re.S)
    assert setv, "the page carries no SETV block"
    sett = _between(page, "// SETT{", "// }SETT")   # the Now/Settings builders the panel draws through (#1278)
    panel = _between(page, "let mdl=null", "async function mdlLoad()")
    stub = 'const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:""});'
    out = subprocess.run(["node", "-"], input="\n".join([stub, esc, setv.group(0), sett, panel, body]),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _render(hub):
    return _node(hub, f"mdl={json.dumps(FIXTURE)};mdlSrv=JSON.stringify(mdl);mdlRender();"
                      'console.log(JSON.stringify({b:$("#mbody").innerHTML}))')


def test_each_chain_row_removes_takes_hours_sets_effort_and_moves(hub):
    b = _render(hub)["b"]
    for ctl in ('data-ef="coding_models|a"', 'data-hr="coding_models|b"', 'data-mv="coding_models|0|1"',
                'data-rm="coding_models|1"'):
        assert ctl in b, ctl
    assert 'value="22-07"' in b
    assert all(f'<option value="{v}">' in b for v in ("22-07", "07-22", "20-08", "18-06"))


def test_add_offers_only_models_that_are_on_and_not_in_the_chain(hub):
    b = _render(hub)["b"]
    add = re.search(r'<select data-add="coding_models"[^>]*>(.*?)</select>', b, re.S)
    assert add, "the coding chain has no add list"
    assert "+ add a model…" in add.group(1)
    assert re.findall(r'<option value="(\w+)"', add.group(1)) == ["c", "d"]   # a, b in the chain; b is off too


def test_an_off_model_row_reads_off_model_struck_and_still_removes(hub):
    b = _render(hub)["b"]
    row = re.search(r'<div class="mr[^"]*"[^>]*data-row="coding_models\|b".*?data-rm="coding_models\|1"([^>]*)>', b, re.S)
    assert row, "the off row is missing"
    assert ' off' in row.group(0).split(">")[0] and "off (model)" in row.group(0)
    assert "disabled" not in row.group(1)


def test_the_models_table_is_compact_and_a_chip_shows_only_at_limit_or_off(hub):
    b = _render(hub)["b"]
    tab = re.search(r"<table class=mtab>(.*?)</table>", b, re.S)
    assert tab, "the models are not one table"
    t = tab.group(1)
    assert "Hours" not in t and re.findall(r"<th>(.*?)</th>", t) == ["Model", "Subscription", "State", "On"]
    chips = re.findall(r'<span class="stc[^"]*">(.*?)</span>', t)
    assert chips == ["off", "at limit"]


def test_the_chain_rows_carry_no_switch_and_no_position_words(hub):
    b = _render(hub)["b"]
    chains = b[b.index("</table>"):]
    assert "data-mo" not in b and "data-sw" not in chains and "data-mt" not in chains
    assert "primary" not in b and "fallback" not in b


def test_the_models_theme_draws_read_only_tiles_and_editable_rows(hub):
    """#1278: the Now block is tiles only (a key, a value, one tone), and every control sits in the Settings block."""
    b = _render(hub)["b"]
    now = b[b.index("data-blk=now"):b.index("data-blk=settings")]
    settings = b[b.index("data-blk=settings"):]
    assert re.findall(r"<span class=tk>(.*?)</span>", now) == ["Models on", "At limit", "Last coding", "Repository chains"]
    for ctl in ("<input", "<select", "<textarea", "<button"):
        assert ctl not in now, f"the Models Now block carries {ctl}"
    assert 'class="tile ok"' in now and 'class="tile err"' in now, "the tiles lost their tones"
    for ctl in ('data-ef="coding_models|a"', 'data-hr="coding_models|b"', 'data-add="coding_models"'):
        assert ctl in settings, ctl


def test_the_models_chip_counts_what_the_table_counts(hub):
    b = _render(hub)["b"]
    assert "data-blk=now" in b
    got = _node(hub, f"mdl={json.dumps(FIXTURE)};mdlSrv=JSON.stringify(mdl);mdlRender();"
                     'console.log(JSON.stringify([$("#mdlsr").textContent,$("#mdlnav").textContent]))')
    assert got == ["2 on · 1 at limit", "2 on · 1 at limit"], got


def test_the_theme_explains_its_rules_once_above_the_chains_and_the_chip_counts_models(hub):
    """Re-anchored (#1283): the rules are stated once, in the one disclosure above the Chains heading, so neither
    the lead paragraph nor the paragraph under the Models table remains. The summary and the nav line are the models
    answer's counts, never a coding model or another run fact, and the chain rows keep their controls and chips."""
    b = _render(hub)["b"]
    assert b.count('class="note lead"') == 0, "the theme still carries a lead paragraph"
    assert "On or off, for every chain" not in b, "the Models table keeps its paragraph"
    assert b.count("<summary>How it works</summary>") == 1, "the theme carries more than one explanation"
    assert b.index("<summary>How it works</summary>") < b.index("<h3>Chains</h3>"), "the disclosure sits below Chains"
    assert "id=msv" not in b, "the panel keeps its own Save: the Save is page-level"
    # The chain rows keep their controls, whatever the fixture names its models.
    chains = b[b.index("<h3>Chains</h3>"):]
    rows = re.findall(r'<div class="mr[^"]*"[^>]*data-row="[^"]+">(.*?)(?=<div class="mr|<div class=madd)', chains, re.S)
    assert rows, "the chains carry no rows"
    for row in rows:
        for ctl in ("data-ef=", "data-hr=", "data-mv=", "data-rm="):
            assert ctl in row, f"a chain row lost {ctl}"
    assert len(re.findall(r"<div class=madd><select data-add=", chains)) == 4, "a chain lost its add list"
    chips = re.findall(r'<span class="stc[^"]*">(.*?)</span>', chains)
    assert "at limit" in chips and any(c.startswith("off") for c in chips), "the chain rows lost their state chips"
    # The summary and the nav line are built from the models answer's counts, even when it names another model.
    got = _node(hub, f"const f={json.dumps(FIXTURE)};f.counts={{on:3,at_limit:0}};f.last_coding='z';"
                     "mdl=f;mdlSrv=JSON.stringify(mdl);mdlRender();"
                     'console.log(JSON.stringify([$("#mdlsr").textContent,$("#mdlnav").textContent]))')
    assert got == ["3 on · 0 at limit", "3 on · 0 at limit"], got
    assert not any("z" in t or "Last coding" in t for t in got), f"the summary reports a run fact: {got}"


def test_the_diff_shows_before_and_after_per_chain_and_a_model_switch(hub):
    lines = _node(hub, f"""const old={json.dumps(FIXTURE)};const cur=JSON.parse(JSON.stringify(old));
cur.model_off={{b:false}};cur.coding_models=["b","a"];cur.efforts={{coding_models:{{a:"high"}}}};
console.log(JSON.stringify(mdlDiffLines(old,cur,MLAB)))""")
    assert lines == [
        "- Models & chains · model b: off", "+ Models & chains · model b: on",
        "- Models & chains · Coding: a · always → b · 22-07",
        "+ Models & chains · Coding: b · 22-07 → a · high · always",
    ]


def test_an_unchanged_panel_has_no_diff(hub):
    assert _node(hub, f"const o={json.dumps(FIXTURE)};console.log(JSON.stringify(mdlDiffLines(o,o,MLAB)))") == []


def test_a_span_reads_as_days_hours_or_minutes(hub):
    """Re-anchored (#1278): the one span function replaced rstText, and it answers every duration the page draws."""
    got = _node(hub, "console.log(JSON.stringify([span(4.5*86400),span(6*3600),span(20*60),span(59),span(0)]))")
    assert got == ["4d 12h", "6h 0m", "20m", "59s", "0s"]


def test_a_diff_line_names_its_theme_once(hub):
    got = _node(hub, 'console.log(JSON.stringify([themeLine("Spend & limits","token guard: on"),'
                     'themeLine("Spend & limits","Spend & limits · token cap: 1 -> 2")]))')
    assert got == ["Spend & limits · token guard: on", "Spend & limits · token cap: 1 -> 2"]


def test_access_holds_a_this_device_section_with_the_mobile_link_and_command(hub):
    """#1285: the link is not a rule, so it leaves the rule list for a "This device" heading that holds both the mobile
    view link and the mobile start command. The rule container `accb` holds neither."""
    mod, _ = hub
    acc = _between(mod.PAGE, "<details id=acc", "</details>")
    rules = re.search(r"<div id=accb>(.*?)</div>", acc, re.S)
    assert rules, "the Access theme holds its rule container"
    assert "mobile-link" not in rules.group(1), rules.group(1)
    assert "/agent-squad:start-mobile" not in rules.group(1), rules.group(1)
    sec = re.search(r"<div class=sdev><h4>This device</h4>(.*?)</div>", acc, re.S)
    assert sec, "the Access theme holds a This device section"
    assert 'id=mobile-link' in sec.group(1), sec.group(1)
    assert "/agent-squad:start-mobile" in sec.group(1), sec.group(1)
    assert re.search(r"<h4>This device</h4>.*<p class=note>.*<code>/agent-squad:start-mobile</code>", acc, re.S)


# ---------------------------------------------------------------- review of #913: the phone header, a clean bar, a marked row

def test_the_header_wraps_on_the_phone_so_the_page_never_scrolls_sideways(hub):
    """At 390 px the header row held the four tabs and the pill on one line, 462 px wide (criterion 12)."""
    _, port = hub
    _, page = _get(port, "/")
    phone = "".join(re.findall(r"@media \(max-width:600px\)\{(.*?)\}\s*(?:\n|$)", page, re.M))
    assert re.search(r"header\.top\{[^}]*flex-wrap:wrap", phone), "the header does not wrap on the phone"
    assert re.search(r"\.tabs\{[^}]*overflow-x:auto", phone), "the tabs can push the page wide on the phone"


def _dirty_after(hub, steps):
    return _node(hub, f"mdl={json.dumps(FIXTURE)};mdlSrv=JSON.stringify(mdl);{steps};"
                      "console.log(JSON.stringify({c:mdlChanged(),d:mdlDiffLines(JSON.parse(mdlSrv),mdlOut(),MLAB)}))")


def test_a_switch_off_and_on_again_leaves_nothing_to_save(hub):
    got = _dirty_after(hub, 'mdlSetOffM("a",true);mdlSetOffM("a",false)')
    assert got == {"c": False, "d": []}


def test_a_row_removed_and_put_back_leaves_nothing_to_save(hub):
    got = _dirty_after(hub, 'mdlRmToggle("coding_models","a");mdlRmToggle("coding_models","a")')
    assert got == {"c": False, "d": []}
    got = _dirty_after(hub, 'mdlSetEff("vision_models","z","");mdlSetHrs("vision_models","z","")')
    assert got == {"c": False, "d": []}


def test_a_removed_row_stays_marked_until_save_and_the_diff_drops_it(hub):
    got = _node(hub, f"mdl={json.dumps(FIXTURE)};mdlSrv=JSON.stringify(mdl);mdlRmToggle(\"coding_models\",\"b\");mdlRender();"
                     "const o=mdlOut();console.log(JSON.stringify({b:$(\"#mbody\").innerHTML,chain:mdl.coding_models,"
                     "out:o.coding_models,hrs:(o.hours.coding_models||{}).b||null,c:mdlChanged()}))")
    assert got["chain"] == ["a", "b"], "the row left the chain before Save"
    assert got["out"] == ["a"] and got["hrs"] is None and got["c"] is True
    row = re.search(r'<div class="mr[^"]*"[^>]*data-row="coding_models\|b"', got["b"])
    assert row and " rmk" in row.group(0), "the removed row is not marked"
    assert 'aria-label="undo remove b"' in got["b"]


def test_the_last_unmarked_row_cannot_be_marked_and_a_marked_row_can_be_kept(hub):
    got = _node(hub, f"mdl={json.dumps(FIXTURE)};mdlSrv=JSON.stringify(mdl);mdlRmToggle(\"coding_models\",\"a\");mdlRender();"
                     "console.log(JSON.stringify($(\"#mbody\").innerHTML))")
    btn = dict(re.findall(r'<button class=x data-rm="coding_models\|(\d)"([^>]*)>', got))
    assert "disabled" not in btn["0"], "a marked row cannot be kept"
    assert "disabled" in btn["1"], "the last unmarked row can be marked"
