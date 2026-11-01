"""The phone board eases in and out (owner, 2026-11-01), in the motion language of the desktop lane fade.

A card the last render did not draw fades in with ease-out; a card the last render drew gets no entry class, so the 10 s
poll never replays the fade. A lane that shuts eases out with ease-in. The ticket sheet and the project sheet slide in with
ease-out and out with ease-in. Under `prefers-reduced-motion: reduce` every animation is off.

The rule that names the new cards runs under node with no DOM; the page runs over the stub DOM of the move tests; the CSS
facts are read from the page the hub serves.
"""
import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_hub_mobile_move import BOARD_ONE, hub, run_page  # noqa: F401  (hub is a fixture)

GESTURE_JS = Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts/hub-mobile-gesture.js"
NODE = shutil.which("node")


def fresh(seen, keys):
    script = f"{GESTURE_JS.read_text()}\nconsole.log(JSON.stringify(freshCards({json.dumps(seen)}, {json.dumps(keys)})));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.mark.node_bound
def test_the_first_render_marks_no_card_new():
    assert fresh(None, ["#7", "#8"]) == []


@pytest.mark.node_bound
def test_only_a_card_the_last_render_did_not_draw_is_new():
    assert fresh(["#7"], ["#7", "#8"]) == ["#8"]
    assert fresh(["#7", "#8"], ["#8", "#7"]) == []


def _board(*ns):
    b = copy.deepcopy(BOARD_ONE)
    b["groups"][0]["items"] = [{"n": n, "t": f"ticket {n}", "l": []} for n in ns]
    b["groups"][0]["n"] = len(ns)
    return b


@pytest.mark.node_bound
def test_a_poll_that_redraws_the_column_fades_in_only_the_new_card(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
      const cin = () => Object.fromEntries($("cards").querySelectorAll('[data-role="card"]').map(c => [c.dataset.n, c.classList.contains("cin")]));
      await loadBoard(); const first = cin();
      await loadBoard(); const second = cin();
      await loadBoard(); const third = cin();
      return { first, second, third };
    """, board=[_board(7), _board(7, 8), _board(7, 8)])
    assert out["first"] == {"7": False}, out
    assert out["second"] == {"7": False, "8": True}, out
    assert out["third"] == {"7": False, "8": False}, out


def _rule(css, selector):
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
    assert m, f"no rule for {selector}"
    return m.group(1)


def _css(page):
    return "\n".join(re.findall(r"<style>(.*?)</style>", page, re.S))


def test_a_new_card_fades_in_with_ease_out(hub):  # noqa: F811
    assert "ease-out" in _rule(_css(hub.MOBILE_PAGE), ".card.cin")


def test_a_lane_that_shuts_eases_out_with_ease_in(hub):  # noqa: F811
    assert "ease-in" in _rule(_css(hub.MOBILE_PAGE), ".lout").replace("ease-in-out", "")


@pytest.mark.parametrize("sheet", ["#sheet", "#psheet"])
def test_a_sheet_slides_in_with_ease_out_and_out_with_ease_in(hub, sheet):  # noqa: F811
    css = _css(hub.MOBILE_PAGE)
    shown = re.search(re.escape(sheet) + r"\{(position:fixed[^}]*)\}", css)
    assert shown and "ease-out" in shown.group(1), f"{sheet} does not slide in with ease-out"
    shut = _rule(css, sheet + "[hidden]")
    assert "ease-in" in shut and "allow-discrete" in shut, shut


def test_reduced_motion_turns_every_animation_off(hub):  # noqa: F811
    css = _css(hub.MOBILE_PAGE)
    blocks = re.findall(r"@media \(prefers-reduced-motion: ?reduce\)\{(.*?\})\}", css, re.S)
    assert blocks, "no reduced-motion rule"
    body = "".join(blocks)
    for sel in (".card.cin", ".lout", "#sheet", "#psheet"):
        assert sel in body, f"{sel} keeps its motion under reduced motion"
    assert "animation:none" in body and "transition:none" in body
