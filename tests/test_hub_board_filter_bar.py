"""The board header keeps one filter bar: waves and specs, multi-select (SPEC 889, #899).

Waves and specs filter the cards and the swimlanes, combined with AND; an empty selection means all, and the selection is
kept on the device. The labels filter, the three-state Specs button, the maximize and collapse controls, and the floating
run window (pip) are gone. A saved state from any of those controls is dropped on load.

The rules are `board-lanes.js`, run in node as the page carries them.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
LANES_JS = SCRIPTS / "board-lanes.js"
NODE = shutil.which("node")


@pytest.fixture(scope="module")
def hub():
    for k, v in {"SG_REPO": "x/y", "SG_OWNER": "x", "SG_PROJECT": "4", "SG_CHECKOUT": tempfile.mkdtemp(),
                 "SG_HUB_DIR": tempfile.mkdtemp()}.items():
        os.environ.setdefault(k, v)
    spec = importlib.util.spec_from_file_location("hub_filter_bar", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _node(script, *files):
    if NODE is None:
        pytest.skip("node is required")
    src = "\n".join(f.read_text() for f in files) + "\n" + script
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


# ---- the removed controls are gone from the page ----

@pytest.mark.parametrize("marker", ['id=lf>', 'id=lf ', 'id=bsp', 'id=bz', 'id=bx', 'id="pip"', 'id=pip', 'id=pipf',
                                    'id=lfl', 'id="pipf"', 'maximize the board'])   # 'Specs: all' left the list (#889): the mock's filter summaries read `Waves: all` and `Specs: all`
def test_the_removed_controls_are_not_on_the_page(hub, marker):
    assert marker not in hub.PAGE, f"{marker!r} is still on the page"


def test_the_filter_bar_has_a_waves_and_a_specs_multi_select(hub):
    page = hub.PAGE
    assert '<details id=fw' in page and "<summary" in page
    assert '<details id=fs' in page
    assert 'bo.fw' in page and 'bo.fs' in page, "the selections are not kept in bo"
    assert "<summary>Waves: all</summary>" in page and "<summary>Specs: all</summary>" in page, "the mock's filter summaries (#889)"


def test_a_saved_state_from_a_removed_control_is_dropped_on_load(hub):
    page = hub.PAGE
    assert "dropRemoved(bo)" in page, "the page does not drop the removed controls' state on load"


# ---- the rule: AND across the two filters, empty means all ----

def _card(n, labels=(), pa=None, ho=None):
    row = {"n": n, "t": f"card {n}", "l": list(labels), "c": None}
    if pa is not None:
        row["pa"] = pa
    if ho:
        row["ho"] = ho
    return row


def test_an_empty_selection_shows_every_card(hub):
    out = _node("console.log(JSON.stringify([boardOk({n:1,l:[]},[],[]),boardOk({n:2,l:['wave:03'],pa:9},[],[])]));", LANES_JS)
    assert out == [True, True]


def test_each_waves_option_names_its_spec_and_the_same_wave_of_two_specs_is_two_options(hub):
    rows = [
        {"n": 10, "l": ["wave:01"], "pa": 889}, {"n": 11, "l": ["wave:01"], "pa": 900},
        {"n": 12, "l": ["wave:02"]}, {"n": 13, "l": ["wave:01"], "pa": 889}, {"n": 14, "sp": True, "l": ["spec"]},
    ]
    opts = _node(f"console.log(JSON.stringify(waveOptions({json.dumps(rows)})));", LANES_JS)
    assert [o["label"] for o in opts] == ["SPEC 889 · Wave 1", "SPEC 900 · Wave 1", "No spec · Wave 2"]
    assert len({o["key"] for o in opts}) == 3
    picked = _node(
        f"console.log(JSON.stringify([boardOk({json.dumps(rows[0])},[{json.dumps(opts[1]['key'])}],[]),"
        f"boardOk({json.dumps(rows[1])},[{json.dumps(opts[1]['key'])}],[])]));", LANES_JS)
    assert picked == [False, True], "an option keeps only its own spec's wave"


def test_a_saved_waves_selection_from_before_the_spec_option_is_dropped(hub):
    out = _node("console.log(JSON.stringify(dropRemoved({fw:['2','none','889/2'],fs:['889']})));", LANES_JS)
    assert out["fw"] == ["889/2"] and out["fs"] == ["889"]


def test_waves_and_specs_combine_with_and(hub):
    cases = {
        "wave 2 and spec 889": ({"n": 1, "l": ["wave:2"], "pa": 889}, ["889/2"], ["889"], True),
        "wave 2 and spec 7": ({"n": 2, "l": ["wave:2"], "pa": 7}, ["889/2"], ["889"], False),
        "wave 3 and spec 889": ({"n": 3, "l": ["wave:3"], "pa": 889}, ["889/2"], ["889"], False),
        "no wave and no spec": ({"n": 4, "l": []}, ["none/none"], ["none"], True),
        "wave 2, spec pending": ({"n": 5, "l": ["wave:2"]}, ["none/2"], ["none"], True),
    }
    script = "console.log(JSON.stringify({" + ",".join(
        f"{json.dumps(k)}:boardOk({json.dumps(x)},{json.dumps(fw)},{json.dumps(fs)})" for k, (x, fw, fs, _) in cases.items()
    ) + "}));"
    got = _node(script, LANES_JS)
    for name, (_, _, _, want) in cases.items():
        assert got[name] is want, name


def test_the_filters_apply_to_the_swimlanes(hub):
    groups = [{"name": "Doing", "items": [
        {"n": 889, "t": "spec", "l": ["spec"], "sp": True},
        {"n": 890, "t": "a", "l": ["wave:2"], "pa": 889},
        {"n": 891, "t": "b", "l": ["wave:3"], "pa": 889},
    ]}, {"name": "Todo", "items": [{"n": 900, "t": "c", "l": ["wave:2"]}]}]
    opts = {"mode": "spec", "fw": ["889/2"], "fs": ["889"], "lc": {}, "hideDone": False, "closed": "Closed"}
    model = _node(f"console.log(JSON.stringify(buildBoard({json.dumps(groups)},{json.dumps(opts)})));", LANES_JS)
    shown = [r["n"] for ln in model["lanes"] for r in ln["vis"]]
    assert shown == [890], f"the filters did not narrow the swimlanes: {shown}"


# ---- the Specs options: an unfinished spec only (#981) ----

def _with_ends(groups):
    return [{**g, "end": g["name"] in ("Closed", "Abandoned")} for g in groups]


def _spec_options(groups):
    return _node(f"console.log(JSON.stringify(specOptions({json.dumps(_with_ends(groups))})));", LANES_JS)


def test_a_closed_spec_is_not_an_option(hub):
    groups = [
        {"name": "Todo", "items": [{"n": 42, "t": "open ticket", "l": [], "pa": 41}]},
        {"name": "Closed", "items": [{"n": 41, "t": "SPEC 41", "l": ["spec"], "sp": True}]},
    ]
    assert "41" not in _spec_options(groups)


def test_an_open_spec_whose_tickets_are_all_closed_is_not_an_option(hub):
    groups = [
        {"name": "Doing", "items": [{"n": 889, "t": "SPEC 889", "l": ["spec"], "sp": True}]},
        {"name": "Closed", "items": [{"n": 890, "t": "done", "l": [], "pa": 889}]},
    ]
    assert "889" not in _spec_options(groups)


def test_an_open_spec_with_an_open_ticket_is_an_option(hub):
    groups = [
        {"name": "Doing", "items": [{"n": 889, "t": "SPEC 889", "l": ["spec"], "sp": True}]},
        {"name": "Todo", "items": [{"n": 890, "t": "open", "l": [], "pa": 889}]},
        {"name": "Closed", "items": [{"n": 891, "t": "done", "l": [], "pa": 889}]},
    ]
    assert _spec_options(groups) == ["889"]


def test_an_abandoned_spec_is_not_an_option(hub):
    groups = [
        {"name": "Todo", "items": [{"n": 494, "t": "open ticket", "l": [], "pa": 492}]},
        {"name": "Abandoned", "items": [{"n": 492, "t": "SPEC 492", "l": ["spec"], "sp": True}]},
    ]
    assert "492" not in _spec_options(groups)


def test_a_saved_spec_filter_naming_a_finished_spec_is_dropped_on_load(hub):
    groups = [
        {"name": "Doing", "items": [{"n": 889, "t": "SPEC 889", "l": ["spec"], "sp": True}]},
        {"name": "Todo", "items": [{"n": 890, "t": "open", "l": [], "pa": 889}, {"n": 900, "t": "no spec", "l": []}]},
        {"name": "Closed", "items": [{"n": 41, "t": "SPEC 41", "l": ["spec"], "sp": True}]},
    ]
    kept = _node(f"console.log(JSON.stringify(keptSpecs({json.dumps(['41', '889', 'none'])},"
                 f"{json.dumps(_with_ends(groups))})));", LANES_JS)
    assert kept == ["889", "none"], "a finished spec's key survived the load"


def test_a_saved_spec_filter_of_only_finished_specs_is_empty(hub):
    groups = [{"name": "Closed", "items": [{"n": 41, "t": "SPEC 41", "l": ["spec"], "sp": True}]}]
    kept = _node(f"console.log(JSON.stringify(keptSpecs(['41'],{json.dumps(_with_ends(groups))})));", LANES_JS)
    assert kept == [], "the board would filter to nothing"


def test_the_page_builds_the_specs_options_from_the_unfinished_rule(hub):
    assert "specOptions(" in hub.PAGE and "keptSpecs(" in hub.PAGE


def test_a_wave_held_only_by_closed_cards_is_not_an_option(hub):
    groups = _with_ends([
        {"name": "Todo", "items": [{"n": 1, "t": "open", "l": ["wave:1"], "pa": 889}]},
        {"name": "Closed", "items": [{"n": 2, "t": "done", "l": ["wave:2"], "pa": 889}]},
    ])
    open_rows = [x for g in groups if not g["end"] for x in g["items"]]
    opts = _node(f"console.log(JSON.stringify(waveOptions({json.dumps(open_rows)})));", LANES_JS)
    assert [o["label"] for o in opts] == ["SPEC 889 · Wave 1"], opts


def test_the_page_offers_the_specs_and_waves_of_open_cards_only(hub):
    assert "allRows=boardGroups.filter(g=>!g.end).flatMap(g=>g.items)" in hub.PAGE, "a closed card feeds the Waves options"


# ---- the saved state from removed controls ----

def test_drop_removed_clears_the_old_keys_and_keeps_the_kept_ones(hub):
    bo = {"labels": ["x"], "label": "y", "lmode": "all", "specs": 2, "max": True, "off": False, "pip": {"x": 1},
          "fw": ["889/2"], "fs": [], "lc": {"s1": True}, "lmode_kept": 1}
    out = _node(f"let bo={json.dumps(bo)};dropRemoved(bo);console.log(JSON.stringify(bo));", LANES_JS)
    for gone in ("labels", "label", "specs", "max", "off", "pip"):
        assert gone not in out, f"{gone} survived"
    assert out["fw"] == ["889/2"] and out["lc"] == {"s1": True}, "a kept filter or collapse state was dropped"
