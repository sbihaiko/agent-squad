"""The phone's card link (#1459, SPEC 2498 #2501): `#issue=N` opens Work at that card once the board has read.

The rule is one client module, run under node alone with no DOM and no server: it reads the one address the page binds.
The page opens the column that holds the card, opens its folded lane, and marks the card; a card the board does not hold
names itself on the board's own line. Each case is driven through the page's hash handler over the shared stub DOM.
"""
import json
import pathlib
import shutil
import subprocess

import pytest

from tests.hub_mobile_stub import hub, run_page  # noqa: F401  (hub is a fixture)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
LINK_JS = SCRIPTS / "hub-mobile-link.js"
NODE = shutil.which("node")

BOARD = {"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None,
         "groups": [{"name": "Doing", "n": 1, "items": [{"n": 1, "t": "one", "l": []}]},
                    {"name": "Todo", "n": 2, "items": [{"n": 2, "t": "two", "l": []},
                                                       {"n": 4, "t": "four", "l": [], "pa": 3}]}]}


def run_js(src, expr, prelude=""):
    script = f"{src}\n{prelude}\nconsole.log(JSON.stringify({expr}));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.mark.node_bound
def test_the_rule_reads_one_card_address_and_nothing_else():
    src = LINK_JS.read_text()
    assert run_js(src, "linkHash('#issue=12')") == 12
    for other in ("#/work", "#issue=", "#card=7", "#issue=12x", ""):
        assert run_js(src, f"linkHash({json.dumps(other)})") is None, other


def test_the_link_module_is_inlined_not_fetched(hub):  # noqa: F811
    assert LINK_JS.read_text() in hub.MOBILE_PAGE
    assert "/*LINK*/" not in hub.MOBILE_PAGE
    assert 'addEventListener("hashchange", linkOpen)' in hub.MOBILE_PAGE, "the page binds no hash handler"


@pytest.mark.node_bound
def test_a_link_opens_work_at_its_card_and_opens_the_folded_lane(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        await __tick(0);
        location.hash = "#issue=4"; __dispatch("hashchange", {});
        await __tick(0);
        const el = document.querySelector('#cards [data-role="card"][data-n="4"]');
        return { tab: tabActive, col: document.getElementById("col-name").textContent, focus: !!(el && el.classList.contains("focus")) };
    """, board=[BOARD])
    assert out == {"tab": "work", "col": "Todo", "focus": True}, out


@pytest.mark.node_bound
def test_a_card_the_board_does_not_hold_names_itself_on_the_board_line(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        await __tick(0);
        location.hash = "#issue=99"; __dispatch("hashchange", {});
        await __tick(0);
        return { tab: tabActive, msg: document.getElementById("bmsg").textContent };
    """, board=[BOARD])
    assert out["tab"] == "work", out
    assert out["msg"] == "#99 is not on the board: its issue is read on GitHub.", out
