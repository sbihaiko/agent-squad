"""The card limit per board column on the hub (#2287).

The Settings page carries a Board theme with one row per column and its limit, and saves a change to the project
settings file. The Kanban page shows `n / max` in the header of each column that has a limit. The theme's pure rules
and the header's one function run in node as they ship.
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
    brd = re.search(r"^// BRD\{.*?^// \}BRD$", page, re.M | re.S)
    assert brd, "the page carries no BRD block"
    stub = 'const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:""});function setBar(){}function setChip(){}'
    out = subprocess.run(["node", "-"], input="\n".join([stub, esc, setv, sett, brd.group(0), body]),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# --- the settings route

def test_the_route_answers_every_column_and_its_limit(hub):
    mod, _ = hub
    got = mod.columns_read()
    assert [c["name"] for c in got["columns"]] == ["Todo", "Doing", "Review", "Landing", "Gating"]
    assert {c["name"]: c["max_cards"] for c in got["columns"]} == {"Todo": None, "Doing": 6, "Review": 4, "Landing": None, "Gating": None}
    assert got["file"].endswith("docs/squad/defaults.yaml")


def test_a_save_asks_for_the_diff_before_it_writes(hub):
    mod, co = hub
    out = mod.columns_set({"columns": {"Doing": "2"}, "confirm": False})
    assert out["diff"] == ["Board · Doing: 6 -> 2"]
    assert not (co / "docs" / "squad" / "defaults.yaml").exists()


def test_a_confirmed_save_writes_the_project_settings_file(hub):
    mod, co = hub
    out = mod.columns_set({"columns": {"Doing": "2", "Review": "none"}, "confirm": True})
    assert out["diff"] == ["Board · Doing: 6 -> 2", "Board · Review: 4 -> none"]
    data = yaml.safe_load((co / "docs" / "squad" / "defaults.yaml").read_text())
    assert data["columns"]["Doing"]["max_cards"] == 2
    assert data["columns"]["Review"]["max_cards"] is None
    assert {c["name"]: c["max_cards"] for c in mod.columns_read()["columns"]}["Doing"] == 2


def test_a_limit_that_breaks_its_rule_is_refused_and_writes_nothing(hub):
    mod, co = hub
    out = mod.columns_set({"columns": {"Doing": "0"}, "confirm": True})
    assert "err" in out
    assert not (co / "docs" / "squad" / "defaults.yaml").exists()


def test_a_write_that_fails_on_a_later_column_leaves_no_partial_file(hub, monkeypatch):
    # The save stages every column and writes the file once. A failure while writing must leave the file as it was, never
    # with the first column changed and the rest missing.
    mod, co = hub
    _, ps = mod._board_limit_parts()
    real = ps._write

    def boom(path, data):
        if isinstance((data.get("columns") or {}).get("Review"), dict):
            raise OSError("disk full")
        return real(path, data)

    monkeypatch.setattr(ps, "_write", boom)
    out = mod.columns_set({"columns": {"Doing": "2", "Review": "none"}, "confirm": True})
    assert "err" in out
    assert not (co / "docs" / "squad" / "defaults.yaml").exists(), "a failed save left a partial settings file"


# --- the page

def test_the_settings_page_carries_a_board_theme(hub):
    mod, _ = hub
    page = mod.PAGE
    nav = re.search(r'<nav class=snav aria-label="Settings themes">(.*?)</nav>', page, re.S).group(1)
    assert "data-sp=brd" in nav, "the theme list has no Board theme"
    sel = re.search(r"<select id=ssel[^>]*>(.*?)</select>", page, re.S).group(1)
    assert "value=brd" in sel, "the phone select has no Board theme"
    assert re.search(r'<details id=brd class=sp data-sp=brd>.*?id=brdb', page, re.S), "the Board panel has no body"


def test_the_board_theme_draws_one_row_per_column_with_its_limit(hub):
    mod, _ = hub
    got = _node(mod, "brdS={file:'/p',columns:[{name:'Todo',max_cards:null},{name:'Doing',max_cards:6}]};brdDraft=brdClone(brdS.columns);"
                      "brdRender();console.log(JSON.stringify({b:$('#brdb').innerHTML}))")
    b = got["b"]
    rows = re.findall(r'<span class=sl>(.*?)</span>', b)
    assert rows == ["Todo", "Doing"], rows
    assert 'data-col="Todo"' in b and 'data-col="Doing"' in b
    assert 'value=""' in b and 'value="6"' in b


def test_the_node_spawning_board_theme_test_is_marked_heavy():
    fn = globals()["test_the_board_theme_draws_one_row_per_column_with_its_limit"]
    assert any(m.name == "heavy" for m in getattr(fn, "pytestmark", [])), "a call that starts node carries no heavy mark"


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
    assert mod.board_column_limits() == {"Todo": None, "Doing": 6, "Review": 4, "Landing": None, "Gating": None}
