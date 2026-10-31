"""The board header keeps one filter bar: waves and specs, multi-select (SPEC 889, #899).

Waves and specs filter the cards and the swimlanes, combined with AND; an empty selection means all, and the selection is
kept on the device. The labels filter, the three-state Specs button, the maximize and collapse controls, and the floating
run window (pip) are gone. A saved state from any of those controls is dropped on load.

The rules are `board-lanes.js` and `panes.js`, run in node as the page carries them.
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
PANES_JS = SCRIPTS / "panes.js"
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
                                    'id=lfl', 'id="pipf"', 'Specs: all', 'maximize the board'])
def test_the_removed_controls_are_not_on_the_page(hub, marker):
    assert marker not in hub.PAGE, f"{marker!r} is still on the page"


def test_the_filter_bar_has_a_waves_and_a_specs_multi_select(hub):
    page = hub.PAGE
    assert '<details id=fw' in page and "<summary" in page
    assert '<details id=fs' in page
    assert 'bo.fw' in page and 'bo.fs' in page, "the selections are not kept in bo"


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


def test_waves_and_specs_combine_with_and(hub):
    cases = {
        "wave 2 and spec 889": ({"n": 1, "l": ["wave:2"], "pa": 889}, ["2"], ["889"], True),
        "wave 2 and spec 7": ({"n": 2, "l": ["wave:2"], "pa": 7}, ["2"], ["889"], False),
        "wave 3 and spec 889": ({"n": 3, "l": ["wave:3"], "pa": 889}, ["2"], ["889"], False),
        "no wave and no spec": ({"n": 4, "l": []}, ["none"], ["none"], True),
        "wave 2, spec pending": ({"n": 5, "l": ["wave:2"]}, ["2"], ["none"], True),
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
    opts = {"mode": "spec", "fw": ["2"], "fs": ["889"], "lc": {}, "hideDone": False, "closed": "Closed"}
    model = _node(f"console.log(JSON.stringify(buildBoard({json.dumps(groups)},{json.dumps(opts)})));", LANES_JS)
    shown = [r["n"] for l in model["lanes"] for r in l["vis"]]
    assert shown == [890], f"the filters did not narrow the swimlanes: {shown}"


# ---- the saved state from removed controls ----

def test_drop_removed_clears_the_old_keys_and_keeps_the_kept_ones(hub):
    bo = {"labels": ["x"], "label": "y", "lmode": "all", "specs": 2, "max": True, "off": False, "pip": {"x": 1},
          "fw": ["2"], "fs": [], "lc": {"s1": True}, "lmode_kept": 1}
    out = _node(f"let bo={json.dumps(bo)};dropRemoved(bo);console.log(JSON.stringify(bo));", LANES_JS)
    for gone in ("labels", "label", "specs", "max", "off", "pip"):
        assert gone not in out, f"{gone} survived"
    assert out["fw"] == ["2"] and out["lc"] == {"s1": True}, "a kept filter or collapse state was dropped"


# ---- the run pane has no maximized mode ----

def test_the_run_pane_follows_its_focus_with_no_maximized_mode(hub):
    src = PANES_JS.read_text()
    assert "maximized" not in src and "pip" not in src, "the pane plan still carries a maximized mode or the pip"
    out = _node('console.log(JSON.stringify(embedPlan(["a","b"],"b")));', PANES_JS)
    assert out == {"pane": "b"}
