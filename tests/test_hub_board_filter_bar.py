"""The board header keeps one filter bar: specs and kinds, multi-select (SPEC 889, #899).

Specs and kinds filter the cards and the swimlanes, combined with AND; an empty selection means all, and the selection is
kept on the device. The wave filter is gone with the wave lane (SPEC 1965, #1988). The labels filter, the three-state Specs button, the maximize and collapse controls, and the floating
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
                                    'id=lfl', 'id="pipf"', 'maximize the board', 'id=fs', 'id=fk', 'id=lmode',
                                    'id=flowchips', 'ffchip'])   # the Specs, Kinds and Swimlanes controls left the page at the owner's request (SPEC 1965)
def test_the_removed_controls_are_not_on_the_page(hub, marker):
    assert marker not in hub.PAGE, f"{marker!r} is still on the page"


def test_the_board_reads_no_filter_state_the_page_no_longer_sets(hub):
    """A control that left the page must leave no reader: an undeclared name throws inside the board poll and the whole
    board stays blank, because the poll swallows the error (SPEC 1965)."""
    page = hub.PAGE
    for name in ("bo.fk", "bo.fs", "f||fs.length||fk.length", "boardOk({...x,kind:\"pr\"}", "specTitle"):
        assert name not in page, f"the board still reads {name!r}, which the page no longer sets"


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
    out = _node("console.log(JSON.stringify([boardOk({n:1,l:[]},[]),boardOk({n:2,l:['wave:03'],pa:9},[])]));", LANES_JS)
    assert out == [True, True]


def test_specs_and_kinds_combine_with_and(hub):
    cases = {
        "spec 889, a bug": ({"n": 1, "l": ["bug"], "pa": 889}, ["889"], ["bug"], True),
        "spec 7, a bug": ({"n": 2, "l": ["bug"], "pa": 7}, ["889"], ["bug"], False),
        "spec 889, an issue": ({"n": 3, "l": [], "pa": 889}, ["889"], ["bug"], False),
        "no spec, an issue": ({"n": 4, "l": []}, ["none"], ["issue"], True),
        "spec pending, an issue": ({"n": 5, "l": []}, ["none"], ["issue"], True),
    }
    script = "console.log(JSON.stringify({" + ",".join(
        f"{json.dumps(k)}:boardOk({json.dumps(x)},{json.dumps(fs)},{json.dumps(fk)})" for k, (x, fs, fk, _) in cases.items()
    ) + "}));"
    got = _node(script, LANES_JS)
    for name, (_, _, _, want) in cases.items():
        assert got[name] is want, name


def test_the_filters_apply_to_the_swimlanes(hub):
    groups = [{"name": "Doing", "items": [
        {"n": 889, "t": "spec", "l": ["spec"], "sp": True},
        {"n": 890, "t": "a", "l": [], "pa": 889},
        {"n": 891, "t": "b", "l": [], "pa": 889},
    ]}, {"name": "Todo", "items": [{"n": 900, "t": "c", "l": []}]}]
    opts = {"fs": ["889"], "fk": ["issue"], "lc": {}, "hideDone": False, "closed": "Closed"}
    model = _node(f"console.log(JSON.stringify(buildBoard({json.dumps(groups)},{json.dumps(opts)})));", LANES_JS)
    shown = [r["n"] for ln in model["lanes"] for r in ln["vis"]]
    assert shown == [890, 891], f"the filters did not narrow the swimlanes: {shown}"


# ---- the Specs options: an unfinished spec only (#981) ----

def _with_ends(groups):
    return [{**g, "end": g["name"] in ("Closed", "Abandoned")} for g in groups]


def _spec_options(groups, sum_=None):
    return _node(f"console.log(JSON.stringify(specOptions({json.dumps(_with_ends(groups))},"
                 f"{json.dumps(sum_ or {'specs': [], 'waves': []})})));", LANES_JS)


def test_a_closed_spec_is_not_an_option(hub):
    groups = [
        {"name": "Todo", "items": [{"n": 42, "t": "open ticket", "l": [], "pa": 41}]},
        {"name": "Closed", "items": []},
    ]
    summary = {"specs": [{"n": 41, "st": "Closed", "closed": 1, "total": 2}], "waves": []}
    assert "41" not in _spec_options(groups, summary)


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
        {"name": "Abandoned", "items": []},
    ]
    summary = {"specs": [{"n": 492, "st": "Abandoned", "closed": 0, "total": 1}], "waves": []}
    assert "492" not in _spec_options(groups, summary)


def test_the_page_builds_the_specs_options_from_the_unfinished_rule(hub):
    """#981: a finished spec is never an option. The Specs filter left the page (SPEC 1965), so the rule that keeps a saved
    key alive went with it, and the options alone are built from the page's read."""
    page = hub.PAGE
    assert "specOptions(boardGroups" in page, "the page builds no spec options"
    assert "const gone=goneSpecsOf(groups,sum)" in page, "the options rule does not read the finished specs"
    assert "keptSpecs(" not in page, "the page still carries the saved-key keeper of a removed control"


def test_the_page_offers_the_specs_and_kinds_of_open_cards_only(hub):
    assert "allRows=boardGroups.filter(g=>!g.end).flatMap(g=>g.items)" in hub.PAGE, "a closed card feeds the options"


# ---- the saved state from removed controls ----

def test_drop_removed_clears_the_old_keys_and_keeps_the_kept_ones(hub):
    bo = {"labels": ["x"], "label": "y", "lmode": "all", "specs": 2, "max": True, "off": False, "pip": {"x": 1},
          "fw": ["889/2"], "fs": ["889"], "fk": ["bug"], "lc": {"s1": True}}
    out = _node(f"let bo={json.dumps(bo)};dropRemoved(bo);console.log(JSON.stringify(bo));", LANES_JS)
    for gone in ("labels", "label", "specs", "max", "off", "pip", "fw", "lmode", "fs", "fk"):
        assert gone not in out, f"{gone} survived"
    assert out["lc"] == {"s1": True}, "the collapse state a kept control writes was dropped"
