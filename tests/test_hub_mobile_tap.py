"""One rule set for a drag on the phone board (#1453): a horizontal swipe changes the column on a plain card, a rightward
drag stages the move on a promotable card past 56 px, a leftward drag changes the column on a promotable card, and a
click after a release is suppressed exactly when the drag passed the threshold of the gesture the card offers.

The pure rules run under node with no DOM. The click runs over the stub DOM of the move tests.
"""
import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_hub_mobile_move import BOARD_ONE, hub, run_page  # noqa: F401  (hub is a fixture)

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts"
NODE = shutil.which("node")
RULES = (SCRIPTS / "hub-mobile-gesture.js").read_text() + "\n" + (SCRIPTS / "hub-mobile-move.js").read_text()
BOUNDARY = ["just-under", "at", "just-over"]


def rule(expr):
    script = f"{RULES}\nconsole.log(JSON.stringify({expr}));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def gesture(dx, dy=0, promotable=False):
    flag = "true" if promotable else "false"
    return f'gestureAct({{owner:"card",dx:{dx},dy:{dy},atTop:false,thumb:true,promotable:{flag}}})'


def move(dx, dy=0):
    return f"moveDrag({{promotable:true,dx:{dx},dy:{dy}}})"


def column(step):
    return {"act": "column", "step": step}


STAGE = {"act": "stage"}


@pytest.mark.parametrize("dx, want", [(-39, None), (-40, column(1)), (-41, column(1))], ids=BOUNDARY)
def test_a_leftward_swipe_on_a_plain_card_changes_the_column(dx, want):
    assert rule(gesture(dx)) == want


@pytest.mark.parametrize("dx, want", [(39, None), (40, column(-1)), (41, column(-1))], ids=BOUNDARY)
def test_a_rightward_swipe_on_a_plain_card_changes_the_column(dx, want):
    assert rule(gesture(dx)) == want


@pytest.mark.parametrize("dx, want", [(-39, None), (-40, column(1)), (-41, column(1))], ids=BOUNDARY)
def test_a_leftward_swipe_on_a_promotable_card_changes_the_column(dx, want):
    assert rule(gesture(dx, promotable=True)) == want


@pytest.mark.parametrize("dx, want", [(55, [None, None]), (56, [None, STAGE]), (57, [None, STAGE])], ids=BOUNDARY)
def test_a_rightward_drag_on_a_promotable_card_stages_only_past_56_px(dx, want):
    assert rule(f"[{gesture(dx, promotable=True)},{move(dx)}]") == want


@pytest.mark.parametrize("dx, dy, want", [(-50, -45, column(1)), (-30, -100, None), (-39, -100, None)],
                         ids=["horizontal-passes", "vertical-dominates", "horizontal-under-vertical-passes"])
def test_a_diagonal_drag_on_a_promotable_card_resolves_on_its_horizontal_component(dx, dy, want):
    assert rule(gesture(dx, dy, promotable=True)) == want


@pytest.mark.parametrize("dx, dy", [(60, 50), (60, -20)], ids=["down-right", "up-right"])
def test_a_drag_past_both_thresholds_resolves_to_the_move(dx, dy):
    assert rule(f"[{gesture(dx, dy, promotable=True)},{move(dx, dy)}]") == [None, STAGE]


def _card_board(pm):
    b = copy.deepcopy(BOARD_ONE)
    b["groups"][0]["items"] = [{"n": 7, "t": "a ticket", "l": [], "pm": pm, "pmt": "Todo"}]
    return b


CLICK_AFTER_RELEASE = """
  await loadBoard();
  const card = $("cards").querySelector('[data-role="card"][data-n="7"]');
  __dispatch("touchstart", { target: card, touches: [{ clientX: 200, clientY: 700 }] });
  __dispatch("touchend", { changedTouches: [{ clientX: 200 + __DX__, clientY: 700 + __DY__ }] });
  const suppressed = suppressClick;
  $("cards").onclick({ target: card });
  return { suppressed, opened: view.sheet };
"""


@pytest.mark.node_bound
@pytest.mark.parametrize("pm, dx, suppressed", [
    (False, -39, False), (False, -40, True), (False, -41, True),
    (True, 55, False), (True, 56, True), (True, 57, True),
    (True, -39, False), (True, -40, True), (True, -41, True),
], ids=["plain-col-just-under", "plain-col-at", "plain-col-just-over",
        "promo-right-just-under", "promo-right-at", "promo-right-just-over",
        "promo-left-just-under", "promo-left-at", "promo-left-just-over"])
def test_a_click_after_release_is_suppressed_exactly_past_the_offered_threshold(hub, pm, dx, suppressed):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, CLICK_AFTER_RELEASE.replace("__DX__", str(dx)).replace("__DY__", "0"),
                   board=[_card_board(pm)])
    assert out == {"suppressed": suppressed, "opened": not suppressed}, f"pm={pm} dx={dx}: {out}"


@pytest.mark.node_bound
@pytest.mark.parametrize("pm", [True, False])
@pytest.mark.parametrize("dx, dy", [(30, 0), (-30, 0), (0, 30), (0, -30)], ids=["right", "left", "down", "up"])
def test_a_tap_below_the_offered_threshold_opens_the_card_in_any_direction(hub, pm, dx, dy):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, CLICK_AFTER_RELEASE.replace("__DX__", str(dx)).replace("__DY__", str(dy)),
                   board=[_card_board(pm)])
    assert out == {"suppressed": False, "opened": True}, f"pm={pm} dx={dx} dy={dy}: {out}"
