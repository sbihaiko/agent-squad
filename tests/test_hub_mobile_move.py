"""The owner's move from the phone (#829): a drag of a promotable Backlog card past the marked threshold, the Undo that
delays the write, and the cancellation of a pending move when the app is hidden or the profile changes.

What may move and where it goes are the hub's facts on the row (`pm`, `pmt`): the page names no column of its own, and
the server side of the rule is proved beside the promote route (`test_hub_promote.py`).

The rules are one client module, run under node alone with no DOM and no server. The page is composed from its files
the way the hub composes it.
"""
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
MOVE_JS = SCRIPTS / "hub-mobile-move.js"
COLUMNS_PY = SCRIPTS / "board_columns.py"
NODE = shutil.which("node")


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_js(src, expr):
    script = f"{src}\nconsole.log(JSON.stringify({expr}));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    return _module("hub_mobile_move", HUB_PY)


@pytest.mark.node_bound
def test_a_release_before_the_threshold_does_nothing():
    src = MOVE_JS.read_text()
    assert run_js(src, "moveDrag({promotable:true,dx:MOVE_MIN-1,dy:2})") is None
    assert run_js(src, "moveDrag({promotable:true,dx:MOVE_MIN,dy:2})") == {"act": "stage"}


@pytest.mark.node_bound
def test_only_a_card_the_hub_marked_promotable_can_be_staged():
    src = MOVE_JS.read_text()
    assert run_js(src, "moveDrag({promotable:false,dx:500,dy:2})") is None
    assert run_js(src, "moveDrag({promotable:true,dx:-500,dy:2})") is None, "a drag left is no move forward"
    assert run_js(src, "moveDrag({promotable:true,dx:10,dy:200})") is None, "a vertical move is the list scrolling"


@pytest.mark.node_bound
def test_the_undo_delays_the_write():
    src = MOVE_JS.read_text()
    assert run_js(src, "moveAct(moveStage(7,'the row names it',1000),'write',1000+UNDO_MS-1).why") == ""
    assert run_js(src, "moveAct(moveStage(7,'the row names it',1000),'write',1000+UNDO_MS).why") == "written"
    assert run_js(src, "moveStage(7,'the row names it',1000).to") == "the row names it", \
        "the module names no column: the destination is the row's own fact"


@pytest.mark.node_bound
def test_an_undone_move_is_cancelled_and_never_written_later():
    src = MOVE_JS.read_text()
    prelude = "const p=moveAct(moveStage(7,'a column',1000),'undo',2000),w=moveAct(p,'write',99999);"
    assert run_js(src, "[w.why, w.live, w === p]", prelude) == ["undo", False, True]


@pytest.mark.node_bound
@pytest.mark.parametrize("act", ["hidden", "profile"])
def test_a_hidden_app_or_a_profile_change_cancels_the_move(act):
    src = MOVE_JS.read_text()
    prelude = f"const p=moveAct(moveStage(7,'a column',1000),{json.dumps(act)},2000),w=moveAct(p,'write',99999);"
    assert run_js(src, "[w.why, w.live]", prelude) == [act, False]


def test_the_move_module_names_no_column_and_makes_no_network_call():
    text = MOVE_JS.read_text()
    table = _module("board_columns_move", COLUMNS_PY)
    for name in [c["name"] for c in table.shipped_columns()] + list(table.ROLE.values()):
        assert name not in text, f"the move module names the column {name!r}: the row names it"
    for banned in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "import "):
        assert banned not in text, f"the move module holds {banned!r}"


def test_the_move_rules_are_inlined_not_fetched(hub):
    assert MOVE_JS.read_text() in hub.MOBILE_PAGE
    assert "/*MOVE*/" not in hub.MOBILE_PAGE
    assert "<script src=" not in hub.MOBILE_PAGE


def test_the_page_posts_the_promote_route_with_the_desktop_body(hub):
    page = hub.MOBILE_PAGE
    assert 'fetch("/api/promote"' in page, "the page does not call the promote route"
    assert 'method: "POST"' in page and '"Content-Type": "application/json"' in page, "the write is not a JSON POST"
    assert "JSON.stringify({ issue:" in page, "the body is not the desktop's `{issue: n}`"


def test_the_page_cancels_a_pending_move_on_hidden_and_on_a_profile_change(hub):
    page = hub.MOBILE_PAGE
    assert 'addEventListener("visibilitychange"' in page and 'if (document.hidden) cancelMove("hidden")' in page
    assert 'cancelMove("profile")' in page, "a profile change does not cancel a pending move"
    assert 'cancelMove("undo")' in page and 'move cancelled' in page, "the Undo does not cancel, or says nothing"


def test_a_refusal_is_shown_on_the_card(hub):
    assert "creason" in hub.MOBILE_PAGE and "d.err" in hub.MOBILE_PAGE, "a refusal is not shown on the card"


def test_the_card_takes_its_promotable_facts_from_the_row(hub):
    page = hub.MOBILE_PAGE
    assert "x.pm" in page and "x.pmt" in page, "the card does not read the row's own facts"
    assert "dataset.pm" in page, "the drag is not bound to the row's promotable fact"


def test_the_mobile_page_holds_no_column_list(hub):
    table = _module("board_columns_page", COLUMNS_PY)
    for name in [c["name"] for c in table.shipped_columns()] + list(table.ROLE.values()):
        assert name not in hub.MOBILE_PAGE, f"the mobile page names the column {name!r}: the rows name them"
