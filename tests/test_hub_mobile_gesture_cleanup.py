"""The phone board's gestures leave no stale state (#1007): a tab change cancels the pending move, a swipe that starts in
a nested scroller or a control of the sheet never steps the card, and one cleanup runs on touchcancel, pagehide and the
end of a gesture, so an interrupted drag never leaves a card translated and armed.

The pure rules run under node with no DOM; the page runs over the stub DOM of the move tests.
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


def js(name, expr):
    script = f"{(SCRIPTS / name).read_text()}\nconsole.log(JSON.stringify({expr}));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _board(*ns):
    b = copy.deepcopy(BOARD_ONE)
    b["groups"][0]["items"] = [{"n": n, "t": f"ticket {n}", "l": [], "pm": True, "pmt": "Todo"} for n in ns]
    b["groups"][0]["n"] = len(ns)
    return b


@pytest.mark.node_bound
def test_a_tab_change_is_a_cancel_reason():
    assert js("hub-mobile-move.js", "moveAct(moveStage(7,'a column',1000),'tab',2000).why") == "tab"


@pytest.mark.node_bound
def test_a_tab_change_cancels_the_pending_move(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
      await loadBoard();
      stageMove({ n: 7, pmt: "Todo" }, $("cards").querySelector('[data-role="card"][data-n="7"]'));
      show("runs");
      const after = { live: pending && pending.live, why: pending && pending.why, timer: pendingTimer };
      __skip(6000); __fire(); await __tick(20);
      return { ...after, still: pending && pending.n };
    """)
    assert out["live"] is False and out["why"] == "tab", out
    assert out["timer"] == 0, f"the move timer still runs after the tab change: {out}"


@pytest.mark.node_bound
def test_a_sheet_swipe_from_a_nested_scroller_or_a_control_is_no_card_step():
    expr = "gestureAct({owner:'sheet',dx:-100,dy:2,atTop:true,nested:true})"
    assert js("hub-mobile-gesture.js", expr) is None
    assert js("hub-mobile-gesture.js", expr.replace("nested:true", "nested:false")) == {"act": "card", "step": 1}


@pytest.mark.node_bound
@pytest.mark.parametrize("where", ["pre", "set", "button", "link"])
def test_the_page_ignores_a_sheet_swipe_that_starts_in_a_nested_scroller_or_control(hub, where):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
      await loadBoard();
      view = afterOpen(view, 0); refresh();
      const where = __ARG__;
      let target;
      if (where === "set") { target = document.createElement("span"); $("sh-set").append(target); }
      else {
        const box = document.createElement(where === "pre" ? "pre" : where === "button" ? "button" : "a");
        target = document.createElement("span"); box.append(target); $("sh-body").append(box);
      }
      const before = view.card;
      __dispatch("touchstart", { target, touches: [{ clientX: 200, clientY: 10 }] });
      __dispatch("touchend", { changedTouches: [{ clientX: 50, clientY: 12 }] });
      const nested = view.card;
      const plain = document.createElement("span"); $("sh-body").append(plain);
      __dispatch("touchstart", { target: plain, touches: [{ clientX: 200, clientY: 10 }] });
      __dispatch("touchend", { changedTouches: [{ clientX: 50, clientY: 12 }] });
      return { before, nested, plain: view.card };
    """.replace("__ARG__", json.dumps(where)), board=[_board(7, 8)])
    assert out["nested"] == out["before"], f"a swipe in a nested {where} stepped the card: {out}"
    assert out["plain"] != out["before"], f"a plain sheet swipe no longer steps the card: {out}"


@pytest.mark.node_bound
@pytest.mark.parametrize("event", ["touchcancel", "pagehide"])
def test_an_interrupted_drag_leaves_the_card_in_place_and_unarmed(hub, event):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
      await loadBoard();
      const card = $("cards").querySelector('[data-role="card"][data-n="7"]');
      __dispatch("touchstart", { target: card, touches: [{ clientX: 0, clientY: 700 }] });
      __dispatch("touchmove", { touches: [{ clientX: 100, clientY: 700 }] });
      const mid = { t: card.style.transform, armed: card.classList.contains("armed") };
      __dispatch(__ARG__, { touches: [], changedTouches: [{ clientX: 100, clientY: 700 }] });
      const after = { t: card.style.transform || "", armed: card.classList.contains("armed"), touch: touch === null ? null : "held" };
      __dispatch("touchend", { changedTouches: [{ clientX: 100, clientY: 700 }] });
      return { mid, after, pending: pending && pending.n };
    """.replace("__ARG__", json.dumps(event)))
    assert out["mid"]["armed"] and out["mid"]["t"], f"the drag never armed the card: {out}"
    assert out["after"] == {"t": "", "armed": False, "touch": None}, f"{event} left the card displaced or armed: {out}"
    assert out["pending"] is None, f"a release after {event} staged a move: {out}"
