"""The card limit of each board column on the hub (#2432).

Spend & limits holds a Kanban sub-group: one row for each active column, its limit, the cards in the column now and the
pass that stops at the limit. The Settings theme list carries no Board theme. The Kanban page shows `n / max` in the
header of each column that has a limit. The sub-group's pure rules and the header's one function run in node as they ship.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
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
    assert all(isinstance(c["count"], int) for c in got["columns"])
    assert got["file"].endswith("docs/squad/defaults.yaml")


def test_each_column_count_comes_from_the_loop_board_cache(hub):
    mod, _ = hub
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
    assert got == {"Backlog": 1, "Todo": 1, "Doing": 2, "Reviewing": 0, "Batching": 0, "Testing": 1}, got


def test_a_row_states_what_its_limit_stops_or_that_no_pass_reads_it(hub):
    mod, _ = hub
    stops = {c["name"]: c["stops"] for c in mod.columns_read()["columns"]}
    assert "publish" in stops["Backlog"], stops["Backlog"]
    assert "autofrontier" in stops["Doing"], stops["Doing"]
    assert "Backlog for Todo" in stops["Todo"], stops["Todo"]
    assert "autofrontier" in stops["Batching"], stops["Batching"]
    assert "branch review pass" in stops["Reviewing"], stops["Reviewing"]
    assert "wave" in stops["Testing"], stops["Testing"]


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
    rows = re.findall(r'<span class=sl>(.*?)</span>', h)
    assert rows == ["Backlog · 2 cards", "Doing · 3 cards"], rows
    assert 'data-kan="Backlog"' in h and 'data-kan="Doing"' in h
    assert "value=\"6\"" in h
    assert "autofrontier" in h, "the Doing row does not state what its limit stops"
    assert "Closed" not in h and "Abandoned" not in h, "a finished column carries a field"


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
