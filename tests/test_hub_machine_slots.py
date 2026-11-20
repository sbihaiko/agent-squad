"""The machine slots sub-group on the hub (#2435).

Spend & limits holds a Machine slots sub-group, labelled "this machine · all projects": a Runs row and a Gate workers
row, each with its slots in use, its cap and the holder of each slot, and each cap editable into the project settings
file. The gate-worker row divides the cap by the workers of one gate, as the loop gates that fit at once. The sub-group
states that a run carries one or more cards, and names children and reviews as file-only settings with their values. The
sub-group's pure rules run in node as they ship.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
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
                 ("SG_CHECKOUT", str(co)), ("SG_HUB_DIR", str(tmp_path)), ("SG_STATE", str(tmp_path / "st")),
                 ("SQUAD_CAPS_DIR", str(tmp_path / "caps"))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SG_SQUAD", "SG_GATE_WORKERS"):
        monkeypatch.delenv(k, raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", SCRIPTS / "ghread.py")
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_machine_slots", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hub_machine_slots"] = mod
    spec.loader.exec_module(mod)
    return mod, co


def _lease(caps_dir, kind, stem, holder):
    d = Path(caps_dir) / kind
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stem}.json").write_text(json.dumps({"holder": holder, "pid": os.getpid(), "since": time.time()}))


def _node(mod, body):
    if shutil.which("node") is None:
        pytest.skip("node is required")
    page = mod.PAGE
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    sett = page[page.index("// SETT{"):page.index("// }SETT") + len("// }SETT")]
    slot = re.search(r"^// SLOT\{.*?^// \}SLOT$", page, re.M | re.S)
    assert slot, "the page carries no SLOT block"
    stub = 'const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:""});function setBar(){}function setChip(){}'
    out = subprocess.run(["node", "-"], input="\n".join([stub, esc, sett, slot.group(0), body]),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# --- the slots route

def test_the_route_answers_runs_and_gate_workers_with_each_holder(hub, tmp_path):
    mod, _ = hub
    _lease(tmp_path / "caps", "runs", "run-a", "run-a")
    _lease(tmp_path / "caps", "gates", "gate-11.slot0", "gate-11")
    got = mod.slots_read()
    assert got["runs"]["cap"] == 10 and got["runs"]["in_use"] == 1
    assert [h["name"] for h in got["runs"]["holders"]] == ["run-a"]
    assert got["gates"]["cap"] == 12 and got["gates"]["in_use"] == 1
    assert got["gates"]["holders"] == ["gate-11"]
    assert got["gates"]["gates_at_once"] == 2, "12 slots of 6 workers per gate fit two gates at once"
    assert got["children"] == 20 and got["reviews"] == 4
    assert got["ceiling"] == {"runs": 10, "children": 20, "gates": 12, "reviews": 4}, "the page bounds a cap by this ceiling"


def test_a_cap_save_asks_for_the_diff_before_it_writes(hub):
    mod, co = hub
    out = mod.slots_set({"runs": "4", "gates": "3", "confirm": False})
    assert out["diff"] == ["Machine slots · Runs: 10 -> 4", "Machine slots · Gate workers: 12 -> 3"]
    assert not (co / "docs" / "squad" / "defaults.yaml").exists()


def test_a_confirmed_cap_save_writes_the_project_settings_file(hub):
    mod, co = hub
    out = mod.slots_set({"runs": "4", "gates": "3", "confirm": True})
    assert out["diff"] == ["Machine slots · Runs: 10 -> 4", "Machine slots · Gate workers: 12 -> 3"]
    data = yaml.safe_load((co / "docs" / "squad" / "defaults.yaml").read_text())
    assert data["caps"] == {"runs": 4, "gates": 3}
    assert mod.slots_read()["runs"]["cap"] == 4
    assert mod.slots_read()["gates"]["cap"] == 3


def test_a_cap_above_the_plugin_default_is_refused_and_writes_nothing(hub):
    mod, co = hub
    out = mod.slots_set({"runs": "11", "gates": "3", "confirm": True})
    assert "err" in out and "caps.runs" in out["err"], out
    assert not (co / "docs" / "squad" / "defaults.yaml").exists()


def test_a_missing_plugin_ceiling_bounds_the_save_at_the_catalog_high(hub, monkeypatch):
    """A plugin file that omits a cap leaves no ceiling to compare, so the catalog's high bound answers for it, never no
    bound at all."""
    mod, co = hub
    ms, _ = mod._slots_parts()
    monkeypatch.setattr(ms, "plugin", lambda path=None: {"runs": None, "children": 20, "gates": 12, "reviews": 4})
    out = mod.slots_set({"runs": "1001", "confirm": True})
    assert "err" in out and "1000" in out["err"], out
    assert not (co / "docs" / "squad" / "defaults.yaml").exists()
    assert "err" not in mod.slots_set({"runs": "4", "confirm": True}), "a save under the bound was refused"


def test_a_cap_that_is_not_a_count_is_refused_and_writes_nothing(hub):
    mod, co = hub
    for bad in ("0", "-2", "six", ""):
        out = mod.slots_set({"runs": bad, "confirm": True})
        assert "err" in out, (bad, out)
    assert not (co / "docs" / "squad" / "defaults.yaml").exists()


# --- the Settings page

def test_the_sub_group_is_labelled_this_machine_all_projects(hub):
    mod, _ = hub
    got = _node(mod, "slotS={file_label:'docs/squad/defaults.yaml',runs:{cap:4,in_use:4,holders:[{name:'a',issues:[2374]}]},"
                      "gates:{cap:3,in_use:0,holders:[],workers:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "console.log(JSON.stringify({h:slotsHtml()}))")
    h = got["h"]
    assert "Machine slots" in h and "this machine · all projects" in h
    assert "4 of 4" in h and "0 of 3" in h
    assert "#2374" in h, "the runs row does not name the issues that hold its slots"


def test_the_gate_row_reads_the_slots_as_loop_gates_at_once(hub):
    mod, _ = hub
    got = _node(mod, "console.log(JSON.stringify(["
                      "slotGateLine({cap:3,in_use:0,workers:3,gates_at_once:1,gates_running:0}),"
                      "slotGateLine({cap:12,in_use:7,workers:6,gates_at_once:2,gates_running:1})]))")
    assert got[0] == "3 slots ÷ 3 workers per gate = 1 gate at a time · none running", got[0]
    assert "2 gates at a time" in got[1] and "1 gate running" in got[1], got[1]


def test_the_sub_group_states_a_run_carries_one_or_more_cards(hub):
    mod, _ = hub
    got = _node(mod, "slotS={file_label:'docs/squad/defaults.yaml',runs:{cap:4,in_use:0,holders:[]},"
                      "gates:{cap:3,in_use:0,holders:[],workers:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "console.log(JSON.stringify({h:slotsHtml()}))")
    assert "A run carries one or more cards" in got["h"], got["h"]


def test_one_line_names_children_and_reviews_as_file_only_with_their_values(hub):
    mod, _ = hub
    got = _node(mod, "slotS={file_label:'docs/squad/defaults.yaml',runs:{cap:4,in_use:0,holders:[]},"
                      "gates:{cap:3,in_use:0,holders:[],workers:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "console.log(JSON.stringify({h:slotsHtml()}))")
    h = got["h"]
    assert re.search(r"File only[^<]*children 12 · reviews 4", h), h
    assert "docs/squad/defaults.yaml" in h


def test_a_typed_cap_stages_and_makes_the_theme_dirty(hub):
    mod, _ = hub
    got = _node(mod, "slotS={file:'/p',file_label:'p',runs:{cap:4,in_use:0,holders:[]},"
                      "gates:{cap:3,in_use:0,holders:[],workers:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "slotDraft=slotClone(slotS);const clean=slotDirty();slotDraft.runs.cap=6;"
                      "console.log(JSON.stringify({clean,dirty:slotDirty(),caps:slotCaps()}))")
    assert got["clean"] is False
    assert got["dirty"] is True
    assert got["caps"] == {"runs": "6", "gates": "3"}, got["caps"]


def test_a_cleared_or_above_the_ceiling_cap_is_refused_before_it_is_staged(hub):
    mod, _ = hub
    got = _node(mod, "slotS={file:'/p',file_label:'p',ceiling:{runs:10,gates:12},"
                      "runs:{cap:4,in_use:0,holders:[]},gates:{cap:3,in_use:0,holders:[],workers:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "slotDraft=slotClone(slotS);"
                      "const empty=slotStage('runs','');const afterEmpty=slotDirty();"
                      "const high=slotStage('gates','13');"
                      "console.log(JSON.stringify({empty,afterEmpty,high,dirty:slotDirty(),caps:slotCaps(),msg:slotMsg}))")
    assert got["empty"] is False and got["afterEmpty"] is False, "a cleared cap was staged"
    assert got["high"] is False, "a cap above the ceiling was staged"
    assert got["dirty"] is False
    assert got["caps"] == {"runs": "4", "gates": "3"}, got["caps"]
    assert got["msg"].startswith("!"), got["msg"]


def test_a_valid_cap_is_staged_and_makes_the_theme_dirty(hub):
    mod, _ = hub
    got = _node(mod, "slotS={file:'/p',file_label:'p',ceiling:{runs:10,gates:12},"
                      "runs:{cap:4,in_use:0,holders:[]},gates:{cap:3,in_use:0,holders:[],workers:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "slotDraft=slotClone(slotS);"
                      "const ok=slotStage('runs','6');"
                      "console.log(JSON.stringify({ok,dirty:slotDirty(),caps:slotCaps(),msg:slotMsg}))")
    assert got["ok"] is True and got["dirty"] is True
    assert got["caps"] == {"runs": "6", "gates": "3"}
    assert got["msg"] == ""


def test_the_staged_cap_diff_is_shown_in_the_sub_group(hub):
    mod, _ = hub
    got = _node(mod, "slotS={file_label:'docs/squad/defaults.yaml',runs:{cap:4,in_use:0,holders:[]},"
                      "gates:{cap:3,in_use:0,holders:[],workers:3,gates_at_once:1,gates_running:0},children:12,reviews:4};"
                      "slotDiff=['Machine slots · Runs: 4 -> 6'];"
                      "console.log(JSON.stringify({h:slotsHtml()}))")
    assert "Runs: 4 -&gt; 6" in got["h"] or "Runs: 4 -> 6" in got["h"], got["h"]


def test_the_sub_group_chip_names_a_full_row(hub):
    mod, _ = hub
    got = _node(mod, "const mk=(r,g)=>({runs:{cap:4,in_use:r,holders:[]},gates:{cap:3,in_use:g,holders:[],workers:3,gates_at_once:1,gates_running:0}});"
                      "console.log(JSON.stringify([slotChip(mk(4,0))[0],slotChip(mk(2,0))[0],slotChip(mk(1,3))[0]]))")
    assert got == ["runs full", "under the caps", "gate workers full"], got
