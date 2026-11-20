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
from html.parser import HTMLParser

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUB_PY = os.path.join(ROOT, "contrib", "squad-goal", "scripts", "hub.py")
THEMES = ["lim", "sub", "mdl", "slk", "acc"]   # the Board theme left for Spend & limits (#2432); the Loop theme left for the Loop tab (#2515)


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


# A DOM read of a rendered block: a test asks for the element by its class or its role, never by the order of the
# classes or the order of the attributes, so a cosmetic markup change does not fail a test and a changed element does.

class _Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children, self.parts = [], []

    @property
    def classes(self):
        return set((self.attrs.get("class") or "").split())

    @property
    def text(self):
        return "".join(self.parts) + "".join(c.text for c in self.children)


class _Dom(HTMLParser):
    VOID = {"input", "br", "img", "meta", "link", "hr", "col", "area", "base", "embed", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", [], None)
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].parts.append(data)


def _parse(html):
    dom = _Dom()
    dom.feed(html)
    dom.close()
    return dom.root


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _find_all(node, pred):
    return [n for n in _walk(node) if pred(n)]


def _by_class(root, cls):
    return _find_all(root, lambda n: cls in n.classes)


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


def test_the_loop_tab_draws_now_tiles_and_no_input(hub):
    """The Loop tab keeps the facts the Loop theme showed, and it reads: the switch stays in the Monitor (#2515)."""
    out = _run(hub.PAGE, f"loopLogHtml({json.dumps(LOOP_VIEW)})")
    assert re.findall(r"<span class=tk>(.*?)</span>", out) == ["Live", "Health", "Last change"], out
    for ctl in ("<input", "<select", "<textarea", "<button"):
        assert ctl not in out, f"the Loop tab's Now block carries {ctl}"
    assert "Monitor" in out and "Access" in out, out


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


def test_the_access_chip_is_written_by_the_client_alone(hub):
    """The served page seeds neither the chip nor the nav line: both start as a dash and the one client writer sets them
    from the access answer, so a cached loopback page and a tailnet answer never flicker between two sources (#1285)."""
    page = hub.served_page("me", True)
    assert re.search(r'<button data-sp=acc>Access<small id=accnav>&#8212;</small>', page)
    assert re.search(r'<summary>Access<span class=sr id=accsr>&#8212;</span>', page)
    assert "__REACH__" not in page
    draw = page[page.index("const accDraw="):page.index("async function accPoll(")]
    assert "#accsr" in draw and "#accnav" in draw and "accV.tailnet" in draw


def test_the_access_theme_states_its_facts_and_names_who_sets_each_value(hub):
    now, settings = _blocks(_run(hub.PAGE, "accHtml({tailnet:true,reach:true,access:{loop_write:false,terminals_send:false},file:'/p/.env'})"))
    assert "allowed" in now and "refused" in now and "<input" not in now and "<select" not in now, now
    assert 'title="SQUAD_TAILNET_LOOP_WRITE (tailnet.loop_write)"' in now, "the setting's name sits in the hover, not the value"
    assert "Read from <code>/p/.env</code>." in now, now
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

def _sl_theme(hub, people, connection="connected", chip_only=False, **extra):
    """The Communication theme as the page draws it, with the people a reader would see. The nav line is read before
    the render too, so a value left in the harness can never pass for the renderer's own write. With `chip_only`, the
    chip helper is narrowed to the summary alone, so the nav line can only be the render's own write."""
    view = dict(hub.SLACK_DEFAULT, connection=connection, people=people, **extra)
    override = ('\nfunction setChip(theme,text){const sr=$("#"+theme+"sr");if(sr)sr.textContent=text}'
                if chip_only else "")
    body = (_comm_slices(hub.PAGE) + override + f"\nconst nav0=$('#slknav').textContent;"
            f"slS={json.dumps(view)};slDraft=slClone(slS);slRender();"
            "console.log(JSON.stringify({body:STORE['#sbody'].innerHTML,nav0:nav0,"
            "chip:STORE['#slksr'].textContent,nav:STORE['#slknav'].textContent}))")
    return json.loads(_node(body))


def test_the_communication_legend_states_each_role_once_and_no_person_row_repeats_it(hub):
    """A role's behavior is a labelled legend above the People table, once for the theme, each role a term and its
    behavior a definition. Two people of one role do not double it, and no person row carries the behavior text: the
    row holds the name, the role select and Remove (#1284)."""
    roles = json.loads(_node(_comm_slices(hub.PAGE) + "\nconsole.log(JSON.stringify(SL_ROLE))"))
    assert sorted(roles) == ["Colleague", "Manager"], "the theme names an unexpected role"
    out = _sl_theme(hub, [{"name": "Ana", "role": "Manager"}, {"name": "Bo", "role": "Manager"}])
    body = out["body"]
    for role, text in roles.items():
        assert body.count(text) == 1, f"{role}'s behavior appears {body.count(text)} times, not once"
    settings = _blocks(body)[1]
    root = _parse(settings)
    nodes = list(_walk(root))
    legend = next((n for n in nodes if n.attrs.get("role") == "group" and n.attrs.get("aria-label") == "Roles"), None)
    assert legend is not None, "the settings block states no role legend"
    people = next((n for n in nodes if n.tag == "h4" and n.text.strip() == "People"), None)
    assert people is not None, "the settings block draws no People table heading"
    assert legend.parent is people.parent, "the legend is not in the settings block beside the People table"
    assert nodes.index(legend) < nodes.index(people), "the legend sits below the People table"
    assert [n.text.strip() for n in _find_all(legend, lambda n: n.tag == "dt")] == list(roles), \
        "the legend does not list each role once"
    assert [n.text.strip() for n in _find_all(legend, lambda n: n.tag == "dd")] == [roles[k] for k in roles]
    people_html = body[body.index("<h4>People</h4>"):body.index("<h4>Templates</h4>")]
    for text in roles.values():
        assert text not in people_html, "a person row repeats a role's behavior"
    assert people_html.count('data-sl="pp:') == 2, "the person rows lost their role select"


def _tiles(now):
    """Every Now tile as its tone, key and value, in the order the block draws them. A tile is found by its own class
    and its inner spans by theirs, so class order or span order cannot move a tile out of the reading."""
    tiles = []
    for tile in _by_class(_parse(now), "tile"):
        tone = next(iter(tile.classes - {"tile"}), "neutral")
        key = next((n.text.strip() for n in _by_class(tile, "tk")), None)
        val = next((n.text.strip() for n in _by_class(tile, "tv")), None)
        tiles.append((tone, key, val))
    return tiles


def _row_labels(settings_html):
    """The label each settings row states, read from the row element itself."""
    return [lab.text.strip() for row in _by_class(_parse(settings_html), "setrow")
            for lab in row.children if lab.tag == "span" and "sl" in lab.classes]


def test_the_connection_fact_is_a_now_tile_with_a_tone_and_not_an_editable_row(hub):
    """The connection is a Now tile carrying the hub's tone, and no settings row states it: the row labels the theme
    builds are read from each row element, so an unrelated mention cannot hide or fake the row (#1284)."""
    out = _sl_theme(hub, [])
    now, settings = _blocks(out["body"])
    assert ("ok", "Connection", "connected") in _tiles(now), now
    labels = _row_labels(settings)
    assert labels, "the settings block carries no row"
    assert not any("connect" in lab.lower() for lab in labels), labels
    warn = _sl_theme(hub, [], connection="not connected")
    assert ("warn", "Connection", "not connected") in _tiles(_blocks(warn["body"])[0]), warn["body"]


def _inbox_switches(settings_html):
    """Each switch under the Owner inbox heading as its data-sl key, aria-label, visible label and disabled state, read
    from its own input element so an attribute reorder cannot hide it."""
    root = _parse(settings_html)
    nodes = list(_walk(root))
    at = next(i for i, n in enumerate(nodes) if n.tag == "h4" and n.text.strip() == "Owner inbox")
    switches = []
    for label in nodes[at:]:
        if label.tag != "label" or "swt" not in label.classes:
            continue
        inp = next((n for n in _walk(label) if n.tag == "input"), None)
        if inp is None or inp.attrs.get("data-sl") not in ("dm", "audio"):
            continue
        switches.append((inp.attrs["data-sl"], inp.attrs.get("aria-label", ""), label.text.strip(),
                         "disabled" in inp.attrs))
    return switches


def test_the_owner_inbox_switches_carry_the_owner_inbox_name(hub):
    """The switches that give the owner an inbox sit under the Owner inbox heading, and each switch's visible label
    and aria-label names that inbox, so a rename of the labels cannot pass on the heading alone. The audio switch
    keeps its dependency on the self-DM switch (#1284)."""
    settings = _blocks(_sl_theme(hub, [])["body"])[1]
    heads = [n.text.strip() for n in _find_all(_parse(settings), lambda n: n.tag == "h4")]
    assert "Owner inbox" in heads and "Optional" not in heads, heads
    switches = _inbox_switches(settings)
    assert [s[0] for s in switches] == ["dm", "audio"], switches
    for key, aria, text, _ in switches:
        assert aria.startswith("Owner inbox"), f"{key}: aria-label {aria!r} does not name the inbox"
        assert text.startswith("Owner inbox"), f"{key}: visible label {text!r} does not name the inbox"
    assert switches[1][3] is True, "the audio switch is live while its self-DM switch is off"
    on = _blocks(_sl_theme(hub, [], dm=True)["body"])[1]
    audio = _inbox_switches(on)[1]
    assert audio[3] is False, "the audio switch stays off while its self-DM switch is on"


def test_the_communication_chip_states_the_connection_and_the_people_count_in_the_nav_line(hub):
    """The theme's chip is a status: it reads the connection and how many people the agent answers, and the nav's small
    line carries the same text from the one render (#1284). The nav line is empty before the render, so its value can
    only be what the render wrote."""
    cases = [([{"name": "Ana", "role": "Manager"}, {"name": "Bo", "role": "Colleague"}], "connected",
              "connected · 2 people"),
             ([{"name": "Ana", "role": "Manager"}], "connected", "connected · 1 person"),
             ([], "not connected", "not connected · 0 people")]
    for people, connection, want in cases:
        out = _sl_theme(hub, people, connection=connection)
        assert out["nav0"] == "", "the harness pre-filled the nav line"
        assert out["chip"] == want, out["chip"]
        assert out["nav"] == out["chip"], "the nav line disagrees with the summary chip"


def test_the_render_writes_the_nav_line_from_the_one_status_text(hub):
    """The nav line is the render's own write, not a side effect of the chip helper: with the helper narrowed to the
    summary, a changed connection or people count still reaches the nav line from the one text (#1284)."""
    for people, connection, want in (
            ([{"name": "Ana", "role": "Manager"}], "connected", "connected · 1 person"),
            ([], "not connected", "not connected · 0 people")):
        out = _sl_theme(hub, people, connection=connection, chip_only=True)
        assert out["nav0"] == "", "the harness pre-filled the nav line"
        assert out["chip"] == want, out["chip"]
        assert out["nav"] == want, "the render did not write the nav line from the status text"


# ---------------------------------------------------------------- the Save bar and the dialog

def _set_bar(hub):
    """The Save bar's own source, run in node: what it labels the bar and which buttons it disables for one theme."""
    page = hub.PAGE
    src = page[page.index("const STH="):page.index("function setTheme(")]
    # the four names the bar's own slice calls from outside it: the DOM accessor, the stored theme, and two dirty tests
    stub = ('const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:"",disabled:false,hidden:null,'
            'classList:{toggle(){}}});const ls={};const mdlChanged=()=>false;const slDirty=()=>false;'
            'let RO=false;const RO_WHY="Read-only over the tailnet";')
    body = ('const out={};for(const t of ["lim","sub","mdl","slk","acc"]){sTheme=t;RO=false;setBar();'
            'out[t]={hidden:$("#sbar").hidden,disabled:$("#ssave").disabled,text:$("#schg").textContent,'
            'save:$("#ssave").hidden,disc:$("#sdisc").hidden}}'
            'RO=true;sTheme="mdl";setBar();out.ro={hidden:$("#sbar").hidden,disabled:$("#ssave").disabled,'
            'text:$("#schg").textContent};console.log(JSON.stringify(out));')
    return json.loads(_node("\n".join([stub, src, body])))


def test_the_save_bar_never_hides_and_a_read_only_theme_says_so(hub):
    out = _set_bar(hub)
    for theme in THEMES:
        assert out[theme]["hidden"] is False, f"{theme}: the Save bar hid"
        assert out[theme]["disabled"] is True, f"{theme}: Save was armed with nothing to write"
    for theme in ("acc",):
        assert out[theme]["text"] == "nothing to save here", f"{theme}: {out[theme]['text']!r}"
    # re-anchored (#1281): Subscriptions stages its edits like every other writable theme, so its bar reads as no change.
    assert out["sub"]["text"] == "Subscriptions: no change", out["sub"]["text"]
    assert out["mdl"]["text"] == "Models & chains: no change"
    # re-anchored (#2433): Spend & limits writes each field on its own, so its bar keeps no button and says so.
    assert out["lim"]["text"] == "Spend & limits: each field saves as you set it", out["lim"]["text"]
    assert out["lim"]["save"] is True and out["lim"]["disc"] is True, out["lim"]
    for theme in ("sub", "mdl", "slk", "acc"):
        assert out[theme]["save"] is False and out[theme]["disc"] is False, (theme, out[theme])
    assert out["ro"]["hidden"] is False and out["ro"]["disabled"] is True
    assert out["ro"]["text"] == "Read-only over the tailnet"
    assert 'const SET_SAVE=["mdl","slk","sub"]' in hub.PAGE


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
    """Re-anchored (#2433): Spend & limits writes each field on its own, so the one dialog no longer carries its group;
    the theme still names the profile file it writes, and the other two themes name theirs in the dialog."""
    page = hub.PAGE
    assert "(mdl&&mdl.file)" in page and "(slS&&slS.file)" in page
    assert "(limS&&limS.file)" not in page, "the one dialog still carries a Spend & limits group"
    # the theme's Now footer names the profile it writes: the read's `file` on hover, its name in view
    lim = page[page.index("function limRender("):page.index("async function limLoad()")]
    assert 'Profile: <code title="${esc(s.file||"none in force")}">' in lim, \
        "the limits footer no longer names the file the read returned"
    assert '(s.profile||"none in force").split("/").pop()' in lim, \
        "the theme no longer names the profile file it writes"
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
           ["Spend & limits", "Subscriptions", "Models & chains", "Communication", "Access"]


def test_each_nav_line_equals_its_theme_summary_chip_and_one_writer_sets_both(hub):
    view = _view(hub.PAGE)
    nav = re.search(r'<nav class=snav aria-label="Settings themes">(.*?)</nav>', view, re.S).group(1)
    for t in THEMES:
        small = re.search(rf"<small id={t}nav>(.*?)</small>", nav).group(1)
        summ = re.search(rf"<summary>[^<]*<span class=sr id={t}sr>(.*?)</span>", view).group(1)
        assert small == summ, f"{t}: the nav line {small!r} disagrees with its chip {summ!r}"
    out = _run(hub.PAGE, '(setChip("acc","loopback"),[STORE["#accsr"].textContent,STORE["#accnav"].textContent])')
    assert out == ["loopback", "loopback"], "one chip update did not reach both places"


def test_a_loop_poll_draws_the_monitor_switch_and_the_loop_tab_change_list(hub):
    """After `/api/loop` answers, the Monitor's switch and the Loop tab's blocks both draw from the real page renderers,
    never a stub (#2515). The tab emits the classes `#lopaud` styles — a setblk, a now grid, a tile, and the aud
    disclosure — so a rendered Loop tab never shows unstyled markup while this test stays green."""
    view = json.dumps(LOOP_VIEW)
    monitor = _run(hub.PAGE, f'(loopErr="",loopSetHtml({view}))')
    assert "role=switch" in monitor, "the Monitor draws no switch from the loop answer"
    tab = _run(hub.PAGE, f"loopLogHtml({view})")
    assert "class=setblk" in tab and "<div class=now>" in tab, tab
    assert '<div class="tile ' in tab, "the Loop tab's tiles carry no `#lopaud .tile` class"
    assert "<details class=aud>" in tab, "the change list is not the `#lopaud details.aud` disclosure"
    assert "<h3 class=blkh>Now</h3>" in tab, "the Now grid carries no `#lopaud .blkh` heading"


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
const DETAILS=["lim","sub","mdl","slk","acc"].map(sp=>{const e=new El(sp);e.dataset.sp=sp;return e});
const NAV=DETAILS.map(e=>new El(e.dataset.sp));
const ELS={};DETAILS.forEach(e=>ELS[e.id]=e);
const $=s=>{const id=String(s).replace(/^#/,"");return ELS[id]||(ELS[id]=new El(id));};
const document={querySelectorAll:s=>s==="#tab-set details.sp"?DETAILS:s===".snav button"?NAV:[]};
const RO=false,RO_WHY="";
let mdln=0,limn=0,slkn=0;
function mdlLoad(){mdln++;}function limLoad(){limn++;}function slLoad(){slkn++;}
const mdlChanged=()=>false,slDirty=()=>false,kanDirty=()=>false,slotDirty=()=>false;
const limTickStart=()=>{},limTickStop=()=>{},kanTickStart=()=>{},kanTickStop=()=>{};   // the theme's age tickers, stubbed: this harness proves the loads, not the clock
let mdl=null,limS=null,slS=null,slDraft=null,slotS=null,slotDraft=null;
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
    # lim: 1 in every case, because Spend & limits answers on boot for its chip and its Kanban sub-group (#2432).
    cases = {
        "mdl": ({"sth": "mdl"}, {"mdl": 1, "lim": 1, "slk": 0}),
        "lim": ({"sth": "lim"}, {"mdl": 1, "lim": 1, "slk": 0}),
        "slk": ({"sth": "slk"}, {"mdl": 1, "lim": 1, "slk": 1}),
        "a removed theme": ({"sth": "lop"}, {"mdl": 1, "lim": 1, "slk": 0}),
    }
    for name, (stored, want) in cases.items():
        assert _boot_loads(hub, stored) == want, f"{name}: {stored}"
