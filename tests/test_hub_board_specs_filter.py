"""The board's Specs control carries three modes: every card, only the specs, or every card but the specs (#456).

The two modes it had were one question asked two ways -- "are the specs in the way?" and "am I looking for them?" -- so
the control is one button that cycles the three, and its own label states the mode it holds. The rule lives in `specMode`
(the stored value, with the boolean an older page wrote read as the two modes it named) and `spOk` (one card against
that mode); the columns ask `spOk` once per card, so no column is named in the rule.

The three functions are the page's own: this file runs them in node, exactly as the page carries them.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
NODE = shutil.which("node")


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_specs_filter", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _decl(page, name):
    """One declaration of the page, whole: from `const <name>=` to its own `;`, however many lines it takes."""
    i = page.index(f"const {name}=")
    j, depth = i + len(f"const {name}="), 0
    while j < len(page):
        c = page[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ";" and depth == 0:
            break
        j += 1
    return page[i : j + 1]


def _rule(hub):
    """The mode rule exactly as the page carries it: the three declarations, and nothing of the DOM."""
    return "\n".join(_decl(hub.PAGE, n) for n in ("SP", "specMode", "spOk"))


def _ask(hub, stored, cards):
    """`specMode()` for one stored value and `spOk` for each card, both run by node over the page's own code."""
    if NODE is None:
        pytest.skip("node is required")
    script = f"{_rule(hub)}\nlet bo={json.dumps(stored)};\n" \
             f"console.log(JSON.stringify({{mode: specMode(), keep: {json.dumps(cards)}.map(spOk)}}))"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


SPEC = {"n": 3, "sp": True}          # the card the hub marks: it carries the table's SPEC_LABEL
PLAIN = {"n": 4}                     # a card with no such label
FALSE = {"n": 5, "sp": False}        # and one the hub marked false, which is not a spec either


def test_the_control_cycles_the_three_modes_and_names_the_one_it_holds(hub):
    assert re.search(r'<button id=bsp[^>]*>Specs: all</button>', hub.PAGE), "the button does not state a mode"
    assert 'b.dataset.m=SP[m][0];b.textContent="Specs: "+SP[m][0]' in hub.PAGE, "the label is not the mode it holds"
    assert "$(\"#bsp\").onclick=()=>{bo.specs=(specMode()+1)%3;saveB();spPaint();pollBoard()}" in hub.PAGE, \
        "a press does not move the mode on by one and wrap"
    assert "spPaint();" in hub.PAGE, "the label is not painted before the first draw"


def test_the_stored_value_is_the_mode_and_the_old_boolean_still_opens_the_board(hub):
    """`true` was "only" and `false` was "all" on the pages that stored one: the board opens where it was left."""
    assert _ask(hub, {}, [PLAIN])["mode"] == 0, "an unset value is not `all`"
    assert _ask(hub, {"specs": False}, [PLAIN])["mode"] == 0
    assert _ask(hub, {"specs": None}, [PLAIN])["mode"] == 0
    assert _ask(hub, {"specs": True}, [PLAIN])["mode"] == 1, "the old `true` was `only`"
    assert _ask(hub, {"specs": 2}, [PLAIN])["mode"] == 2
    assert _ask(hub, {"specs": "1"}, [PLAIN])["mode"] == 1, "a value written as a string is the mode it names"
    assert _ask(hub, {"specs": 3}, [PLAIN])["mode"] == 0, "a value outside the three is `all`"
    assert _ask(hub, {"specs": -1}, [PLAIN])["mode"] == 0, "and so is a negative one: a value no press can reach draws every card, never a silent hide"


def test_each_mode_draws_its_own_set_of_cards(hub):
    cards = [SPEC, PLAIN, FALSE]
    assert _ask(hub, {}, cards)["keep"] == [True, True, True], "`all` draws every card the other filters leave"
    assert _ask(hub, {"specs": 1}, cards)["keep"] == [True, False, False], "`only` draws the spec-labelled cards"
    assert _ask(hub, {"specs": 2}, cards)["keep"] == [False, True, True], "`hidden` draws every card but those"


def test_the_pull_request_lane_is_asked_no_question(hub):
    """The filter is about the spec label on issue cards: the lane is drawn through it, in all three modes."""
    assert "isPR(g.name)||spOk(x)" in hub.PAGE, "the lane is not drawn through the filter"
    for stored in ({}, {"specs": 1}, {"specs": 2}):
        assert "isPR" not in _rule(hub), stored   # the rule itself names no lane and no column
