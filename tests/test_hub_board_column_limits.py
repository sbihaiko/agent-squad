"""The card limit of each board column on the hub (#2432).

Spend & limits holds a Kanban sub-group: one row for each active column, its limit, the cards in the column now and the
pass that stops at the limit. The Settings theme list carries no Board theme. The Kanban page shows `n / max` in the
header of each column that has a limit. The sub-group's pure rules and the header's one function run in node as they ship.
The group is the four-column table of the spec #2427 mockup (#2512); its renders at 1440 px and at 1000 px, and what each
one shows, are in docs/evidence/2512: kanban-1440.png, kanban-1000.png and README.md.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"

ACTIVE = ["Backlog", "Todo", "Doing", "Reviewing", "Batching", "Testing"]


@pytest.fixture
def hub(monkeypatch, tmp_path):
    co = tmp_path / "co"
    (co / "docs" / "squad").mkdir(parents=True)
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(co)), ("SG_HUB_DIR", str(tmp_path)), ("SG_STATE", str(tmp_path / "st"))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SG_SQUAD"):
        monkeypatch.delenv(k, raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", SCRIPTS / "ghread.py")
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_board_column_limits", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hub_board_column_limits"] = mod
    spec.loader.exec_module(mod)
    return mod, co


def _node(mod, body):
    if shutil.which("node") is None:
        pytest.skip("node is required")
    page = mod.PAGE
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    sett = page[page.index("// SETT{"):page.index("// }SETT") + len("// }SETT")]
    setv = page[page.index("// SETV{"):page.index("// }SETV") + len("// }SETV")]
    kan = re.search(r"^// KAN\{.*?^// \}KAN$", page, re.M | re.S)
    assert kan, "the page carries no KAN block"
    stub = 'const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:""});function setBar(){}function setChip(){}'
    out = subprocess.run(["node", "-"], input="\n".join([stub, esc, setv, sett, kan.group(0), body]),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# --- the settings route

def test_the_route_answers_every_active_column_its_limit_and_its_count(hub):
    mod, _ = hub
    got = mod.columns_read()
    assert [c["name"] for c in got["columns"]] == ACTIVE
    assert {c["name"]: c["max_cards"] for c in got["columns"]} == {"Backlog": None, "Todo": None, "Doing": 6, "Reviewing": 4, "Batching": None, "Testing": None}
    assert all(c["count"] is None for c in got["columns"]), "a column counts cards before any board read"
    assert got["file"].endswith("docs/squad/defaults.yaml")
    assert got["file_label"] == "docs/squad/defaults.yaml", "the page has no repository path for the footer"


def test_each_column_count_comes_from_the_loop_board_cache(hub):
    mod, _ = hub
    mod.board_cache["t"] = 1.0
    mod.board_cache["items"] = [
        {"n": 1, "st": "Doing", "s": "open", "l": []},
        {"n": 2, "st": "Doing", "s": "open", "l": []},
        {"n": 3, "st": "Todo", "s": "open", "l": []},
        {"n": 4, "st": None, "s": "open", "l": []},
        {"n": 5, "st": "Testing", "s": "open", "l": []},
        {"n": 6, "st": "Closed", "s": "closed", "l": []},
    ]
    try:
        got = {c["name"]: c["count"] for c in mod.columns_read()["columns"]}
    finally:
        mod.board_cache["items"] = []
        mod.board_cache["t"] = 0
    assert got == {"Backlog": 1, "Todo": 1, "Doing": 2, "Reviewing": 0, "Batching": 0, "Testing": 1}, got


def test_a_row_states_what_its_limit_stops_or_that_no_pass_reads_it(hub):
    mod, _ = hub
    stops = {c["name"]: c["stops"] for c in mod.columns_read()["columns"]}
    assert "publish" in stops["Backlog"], stops["Backlog"]
    assert "autofrontier" in stops["Doing"], stops["Doing"]
    assert "into Todo" in stops["Todo"], stops["Todo"]
    assert "autofrontier" in stops["Batching"], stops["Batching"]
    assert "branch review" in stops["Reviewing"], stops["Reviewing"]
    assert "wave" in stops["Testing"], stops["Testing"]
    for name in [n for n in stops if n not in ("Backlog", "Todo", "Doing", "Batching", "Reviewing", "Testing")]:
        assert "no pass reads it yet" in stops[name], (name, stops[name])


def test_a_save_asks_for_the_diff_before_it_writes(hub):
    mod, co = hub
    out = mod.columns_set({"columns": {"Doing": "2"}, "confirm": False})
    assert out["diff"] == ["Kanban · Doing: 6 -> 2"]
    assert not (co / "docs" / "squad" / "defaults.yaml").exists()


def test_a_confirmed_save_writes_the_project_settings_file(hub):
    mod, co = hub
    out = mod.columns_set({"columns": {"Doing": "2", "Reviewing": "none", "Backlog": "100"}, "confirm": True})
    assert out["diff"] == ["Kanban · Doing: 6 -> 2", "Kanban · Reviewing: 4 -> none", "Kanban · Backlog: none -> 100"]
    data = yaml.safe_load((co / "docs" / "squad" / "defaults.yaml").read_text())
    assert data["columns"]["Doing"]["max_cards"] == 2
    assert data["columns"]["Reviewing"]["max_cards"] is None
    assert data["columns"]["Backlog"]["max_cards"] == 100
    assert {c["name"]: c["max_cards"] for c in mod.columns_read()["columns"]}["Doing"] == 2


def test_a_limit_that_breaks_its_rule_is_refused_and_writes_nothing(hub):
    mod, co = hub
    out = mod.columns_set({"columns": {"Doing": "0"}, "confirm": True})
    assert "err" in out
    assert not (co / "docs" / "squad" / "defaults.yaml").exists()


def test_a_limit_over_a_hundred_is_refused(hub):
    mod, co = hub
    out = mod.columns_set({"columns": {"Testing": "101"}, "confirm": True})
    assert "err" in out
    assert not (co / "docs" / "squad" / "defaults.yaml").exists()


def test_a_write_that_fails_on_a_later_column_leaves_no_partial_file(hub, monkeypatch):
    # The save stages every column and writes the file once. A failure while writing must leave the file as it was, never
    # with the first column changed and the rest missing.
    mod, co = hub
    _, ps = mod._board_limit_parts()
    real = ps._write

    def boom(path, data):
        if isinstance((data.get("columns") or {}).get("Reviewing"), dict):
            raise OSError("disk full")
        return real(path, data)

    monkeypatch.setattr(ps, "_write", boom)
    out = mod.columns_set({"columns": {"Doing": "2", "Reviewing": "none"}, "confirm": True})
    assert "err" in out
    assert not (co / "docs" / "squad" / "defaults.yaml").exists(), "a failed save left a partial settings file"


# --- the Settings page

def test_the_settings_theme_list_has_no_board_theme(hub):
    mod, _ = hub
    page = mod.PAGE
    nav = re.search(r'<nav class=snav aria-label="Settings themes">(.*?)</nav>', page, re.S).group(1)
    assert "data-sp=brd" not in nav, "the theme list still has a Board theme"
    assert "Board" not in nav, "the theme list still names Board"
    sel = re.search(r"<select id=ssel[^>]*>(.*?)</select>", page, re.S).group(1)
    assert "value=brd" not in sel, "the phone select still has a Board theme"
    assert not re.search(r'<details id=brd class=sp', page), "the Board panel is still on the page"


def test_the_spend_and_limits_theme_loads_with_the_page(hub):
    mod, _ = hub
    page = mod.PAGE
    assert re.search(r'^if\(!\$\("#lim"\)\.open\)limLoad\(\);', page, re.M), "the Spend & limits data does not load with the page"
    assert "setChip(\"lim\"" in page, "the Spend & limits render does not write its chip"


def test_the_canban_sub_group_draws_one_row_per_active_column(hub):
    mod, _ = hub
    got = _node(mod, "kanS={file:'/p',columns:[{name:'Backlog',max_cards:null,count:2,stops:'no pass reads it yet'},"
                      "{name:'Doing',max_cards:6,count:3,stops:'the dispatch pass (autofrontier) claims no card past this'}]};"
                      "console.log(JSON.stringify({h:kanHtml()}))")
    h = got["h"]
    rows = re.findall(r'<tr[^>]*data-kanrow="([^"]+)"', h)
    assert rows == ["Backlog", "Doing"], rows
    assert 'data-kan="Backlog"' in h and 'data-kan="Doing"' in h
    assert "value=\"6\"" in h
    assert "autofrontier" in h, "the Doing row does not state what its limit stops"
    assert 'data-kan="Closed"' not in h and 'data-kan="Abandoned"' not in h, "a finished column carries a field"


def test_the_kanban_group_is_the_table_the_spec_shows(hub):
    # Issue #2512: the group is the mockup's four-column table, not a list of rows that wraps at 1000 px.
    mod, _ = hub
    got = _node(mod, "kanS={file:'/home/me/co/docs/squad/defaults.yaml',file_label:'docs/squad/defaults.yaml',columns:["
                      "{name:'Backlog',max_cards:null,count:31,stops:'a new spec or ticket stays out of Backlog'},"
                      "{name:'Doing',max_cards:6,count:6,stops:'dispatch (autofrontier, autobug) · a hand hold counts'},"
                      "{name:'Reviewing',max_cards:4,count:3,stops:'a new branch review waits'}]};"
                      "console.log(JSON.stringify({h:kanHtml()}))")
    h = got["h"]
    thead = re.search(r"<thead>.*?</thead>", h, re.S)
    assert thead, "the group draws no column headers"
    assert re.findall(r"<th scope=col[^>]*>(.*?)</th>", thead.group(0)) == ["Column", "Cards now", "Card limit", "What the limit stops"]
    doing = re.search(r'<tr[^>]*data-kanrow="Doing".*?</tr>', h, re.S).group(0)
    assert "kbar" in doing and "6 of 6" in doing, doing   # a limited column shows its bar and N of M
    backlog = re.search(r'<tr[^>]*data-kanrow="Backlog".*?</tr>', h, re.S).group(0)
    assert "kbar" not in backlog, "an unlimited column draws a bar"
    assert "3 of 4" in h, "a partly used limit shows N of M"
    head = re.sub(r"<[^>]*>", " ", re.search(r"<h3[^>]*>.*?</h3>", h, re.S).group(0))
    assert "Kanban" in head and "· cards · this board" in head, head
    assert "docs/squad/defaults.yaml" in h, "the footer does not name the project file by its repository path"
    assert "/home/me/co" not in h, "the footer shows a home path"


def test_the_visual_check_renders_ride_beside_the_test():
    # The issue's acceptance criteria hold the two ego-browser renders and their README beside this test, so the proof
    # of the layout travels with the change that makes it.
    for rel in ("docs/evidence/2512/kanban-1440.png", "docs/evidence/2512/kanban-1000.png",
                "docs/evidence/2512/README.md"):
        path = ROOT / rel
        assert path.is_file() and path.stat().st_size > 0, rel


def test_the_footer_derives_the_repository_path_from_the_file(hub):
    # An older columns payload carries no file_label: the footer names the file by the repository-relative suffix of
    # its own path, never a hardcoded one and never the home path.
    mod, _ = hub
    got = _node(mod, "kanS={file:'/home/me/co/docs/squad/project.yaml',columns:[{name:'Doing',max_cards:6,count:1,stops:''}]};"
                      "console.log(JSON.stringify({h:kanHtml()}))")
    h = got["h"]
    assert "docs/squad/project.yaml" in h, "the footer does not derive the repository path from the file"
    assert "/home/me/co" not in h, "the footer shows a home path"
    assert "docs/squad/defaults.yaml" not in h, "the footer names a hardcoded file the payload does not carry"


def test_the_kanban_table_keeps_its_own_layout_and_the_settings_rows_stay_two_column(hub):
    # Issue #2512 at 1000 px: the table aligns its own columns. The Settings rows keep the two-column layout main
    # shipped down to the 600 px breakpoint, so the fix adds no rule that restacks every label and control.
    mod, _ = hub
    page = mod.PAGE
    assert "max-width:1100px){#tab-set .setrow" not in page, "a rule restacks every Settings row at 1100 px"
    assert re.search(r"#tab-set \.ktab tbody th\{[^}]*white-space:nowrap", page), "a table label wraps into its field"


def test_an_empty_field_reads_unlimited_and_saves_as_none(hub):
    mod, _ = hub
    got = _node(mod, "kanS={file:'/p',columns:[{name:'Todo',max_cards:null,count:0,stops:'no pass reads it yet'}]};"
                      "kanDraft=kanClone(kanS.columns);console.log(JSON.stringify({h:kanHtml(),limits:kanLimits()}))")
    assert 'value=""' in got["h"] and "unlimited" in got["h"], got["h"]
    assert got["limits"] == {"Todo": "none"}, got["limits"]


def test_the_sub_group_chip_names_a_column_at_its_limit(hub):
    mod, _ = hub
    got = _node(mod, "kanS={file:'/p',columns:[{name:'Doing',max_cards:6,count:6,stops:''},{name:'Todo',max_cards:null,count:1,stops:''}]};"
                      "const full=kanChip();kanS={file:'/p',columns:[{name:'Doing',max_cards:6,count:2,stops:''}]};"
                      "console.log(JSON.stringify([full,kanChip()]))")
    assert got == ["Doing at its limit", "under its limit"], got


# --- the Kanban header

def test_the_column_header_shows_n_over_max_only_where_a_limit_exists(hub):
    mod, _ = hub
    got = _node(mod, "console.log(JSON.stringify([colCount(3,6),colCount(3,null),colCount(0,4)]))")
    assert got == ["3 / 6", "3", "0 / 4"]


def test_the_kanban_draws_the_header_through_col_count(hub):
    mod, _ = hub
    page = mod.PAGE
    assert "colCount(ccount(c),COLS[c])" in page, "the lane grid column header does not carry the limit"
    assert "colCount(g.n,COLS[g.name])" in page, "the column header does not carry the limit"
    assert "const COLS=b.limits||{}" in page, "the page does not read the limits the board payload carries"
    assert mod.board_column_limits() == {"Backlog": None, "Todo": None, "Doing": 6, "Reviewing": 4, "Batching": None, "Testing": None}


def test_a_settings_page_with_no_board_read_shows_a_dash_in_every_cards_now_cell(hub):
    # Issue #2526: before the first board read a count is unknown, so the cell shows a dash and the status line names the read.
    mod, _ = hub
    got = mod.columns_read()
    assert got["read"] is False
    h = _node(mod, "kanS=" + json.dumps(got) + ";console.log(JSON.stringify({h:kanHtml()}))")["h"]
    cells = re.findall(r'<tr[^>]*data-kanrow="([^"]+)"><th[^>]*>.*?</th><td class=kn>(.*?)</td>', h, re.S)
    assert [name for name, _ in cells] == ACTIVE, cells
    assert all(cell == "<b class=knum>—</b>" for _, cell in cells), cells
    assert "board items not read yet" in h, "the status line does not name the missing board read"


def test_the_kanban_counts_appear_after_a_board_read_without_a_reload(hub):
    # Issue #2541: the page loads before the board read, so the Kanban group reads "cards not read yet" until a board read.
    mod, _ = hub
    before = {"read": False, "file": "/p", "columns": [{"name": "Doing", "max_cards": 6, "count": None, "stops": ""}]}
    after = {"read": True, "file": "/p", "columns": [{"name": "Doing", "max_cards": 6, "count": 3, "stops": ""}]}
    got = _node(mod, "kanS=" + json.dumps(before) + ";kanDraft=kanClone(kanS.columns);"
                "globalThis.fetch=async()=>({json:async()=>(" + json.dumps(after) + ")});"
                "(async()=>{await kanLoad();console.log(JSON.stringify({chip:kanChip(),h:kanHtml()}))})()")
    assert "cards not read yet" not in got["chip"], got["chip"]
    assert "cards not read yet" not in got["h"], got["h"]
    assert re.search(r'data-kanrow="Doing".*?<b class=knum>3 of 6</b>', got["h"], re.S), got["h"]
    assert "pollBoard" in mod.PAGE and "kanLoad()" in mod.PAGE[mod.PAGE.index("async function pollBoard"):mod.PAGE.index("async function pollBoard") + 4000], \
        "the board read does not refresh the Kanban counts"


def test_a_kanban_refresh_keeps_an_unsaved_card_limit_edit(hub):
    # Issue #2541: a refresh replaces the saved state but keeps the limit the owner typed and did not save.
    mod, _ = hub
    saved = {"read": True, "file": "/p", "columns": [{"name": "Doing", "max_cards": 6, "count": 2, "stops": ""}]}
    newer = {"read": True, "file": "/p", "columns": [{"name": "Doing", "max_cards": 6, "count": 5, "stops": ""}]}
    got = _node(mod, "kanS=" + json.dumps(saved) + ";kanDraft=kanClone(kanS.columns);kanDraft[0].max_cards=9;"
                "globalThis.fetch=async()=>({json:async()=>(" + json.dumps(newer) + ")});"
                "(async()=>{await kanLoad();console.log(JSON.stringify({limit:kanDraft[0].max_cards,count:kanS.columns[0].count}))})()")
    assert got == {"limit": 9, "count": 5}, got


# --- the rendered table at 1000 px and 1440 px (issue #2526): a real layout, measured in ego-browser

_RENDER_JS = """
const task = await taskSpace("kanban limits render " + Date.now());
const page = task.page("p1");
await page.cdp("Emulation.setDeviceMetricsOverride", { width: __WIDTH__, height: 1100, deviceScaleFactor: 1, mobile: false });
await page.goto("http://127.0.0.1:__PORT__/");
await page.waitForSelector("nav button, [role=tab]", { timeout: 15000 });
await page.evaluate(() => {
  const btn = [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "Settings");
  if (btn) btn.click();
  const lim = document.querySelector("#lim");
  if (lim) lim.open = true;
  const sp = document.querySelector("[data-sp=lim]");
  if (sp) sp.click();
});
await page.waitForSelector(".ktab tbody tr", { timeout: 15000 });
const rows = await page.evaluate(() => [...document.querySelectorAll(".ktab tbody tr")].map((tr) => {
  const th = tr.querySelector("th");
  const range = document.createRange();
  range.selectNodeContents(th);
  const label = range.getBoundingClientRect();
  const bar = tr.querySelector(".kbar");
  const stops = tr.querySelector(".ks");
  const input = tr.querySelector("input");
  const lh = parseFloat(getComputedStyle(stops).lineHeight) || parseFloat(getComputedStyle(stops).fontSize) * 1.2;
  const text = document.createRange();
  text.selectNodeContents(stops);
  return {
    row: tr.dataset.kanrow,
    gapToBar: bar ? bar.getBoundingClientRect().left - label.right : null,
    stopsLines: Math.round(text.getBoundingClientRect().height / lh),
    inputAlign: getComputedStyle(input).textAlign,
  };
}));
await task.finish({ keep: [] });
console.log(JSON.stringify(rows));
"""


def _render_rows(mod, width):
    if shutil.which("ego-browser") is None:
        pytest.skip("ego-browser is required")
    mod.board_cache["t"] = 1.0
    mod.board_cache["items"] = [{"n": i, "st": st, "s": "open", "l": []} for i, st in
                                enumerate(["Doing"] * 6 + ["Reviewing"] * 3 + ["Testing"], start=1)]
    try:
        srv = _serve(mod)
        try:
            script = _RENDER_JS.replace("__WIDTH__", str(width)).replace("__PORT__", str(srv.server_address[1]))
            out = subprocess.run(["ego-browser", "nodejs"], input=script, capture_output=True, text=True, timeout=120)
        finally:
            srv.shutdown()
            srv.server_close()
    finally:
        mod.board_cache["items"] = []
        mod.board_cache["t"] = 0
    assert out.returncode == 0, out.stderr or out.stdout
    rows = json.loads(out.stderr.strip().splitlines()[-1])
    assert [r["row"] for r in rows] == ACTIVE, rows
    return rows


def _serve(mod):
    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                body, ct = mod.PAGE.encode(), "text/html; charset=utf-8"
            elif path == "/api/columns":
                body, ct = json.dumps(mod.columns_read()).encode(), "application/json"
            elif path == "/api/limits":
                body, ct = json.dumps(mod.limits_read()).encode(), "application/json"
            elif path == "/api/slots":
                body, ct = json.dumps(mod.slots_read()).encode(), "application/json"
            else:
                body, ct = b"{}", "application/json"
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.mark.heavy
@pytest.mark.slow(reason="drives ego-browser through a real render of the Settings page")
def test_the_label_and_the_bar_keep_a_gap_of_eight_px_at_1000_px(hub):
    mod, _ = hub
    for width in (1000, 1440):
        for row in _render_rows(mod, width):
            if row["gapToBar"] is not None:
                assert row["gapToBar"] >= 8, (width, row)


@pytest.mark.heavy
@pytest.mark.slow(reason="drives ego-browser through a real render of the Settings page")
def test_what_the_limit_stops_wraps_to_two_lines_at_1000_px(hub):
    mod, _ = hub
    for row in _render_rows(mod, 1000):
        assert row["stopsLines"] <= 2, row


@pytest.mark.heavy
@pytest.mark.slow(reason="drives ego-browser through a real render of the Settings page")
def test_the_limit_value_sits_at_the_left_edge_of_its_field(hub):
    mod, _ = hub
    for width in (1000, 1440):
        for row in _render_rows(mod, width):
            assert row["inputAlign"] == "left", (width, row)
