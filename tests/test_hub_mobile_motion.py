"""The phone board eases in and out (owner, 2026-11-01), in the motion language of the desktop lane fade.

A card new to the board on a board read fades in with ease-out; the seen set holds every card a read gave, so the 10 s
poll, a column switch and a filter change never replay the fade. A lane that shuts eases out with ease-in. The ticket sheet and the project sheet slide in with
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

from tests.hub_mobile_stub import BOARD_ONE, hub, run_page  # noqa: F401  (hub is a fixture)

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
def test_only_a_card_no_earlier_read_gave_is_new():
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
    assert "var(--motion-entry-easing)" in _rule(_css(hub.MOBILE_PAGE), ".card.cin")


def test_a_lane_that_shuts_eases_out_with_ease_in(hub):  # noqa: F811
    assert "animation:lout var(--motion-duration-exit) var(--motion-exit-easing)" in _css(hub.MOBILE_PAGE)


def test_the_project_sheet_slides_in_with_ease_out_and_out_with_ease_in(hub):  # noqa: F811
    sheet = "#psheet"
    css = _css(hub.MOBILE_PAGE)
    shown = re.search(re.escape(sheet) + r"\{(position:fixed[^}]*)\}", css)
    assert shown and "var(--motion-entry-easing)" in shown.group(1), f"{sheet} does not slide in with ease-out"
    shut = _rule(css, sheet + "[hidden]")
    assert "var(--motion-exit-easing)" in shut and "allow-discrete" in shut, shut


def test_reduced_motion_turns_every_animation_off(hub):  # noqa: F811
    css = _css(hub.MOBILE_PAGE)
    blocks = re.findall(r"@media \(prefers-reduced-motion: ?reduce\)\{(.*?\})\}", css, re.S)
    assert blocks, "no reduced-motion rule"
    body = "".join(blocks)
    for sel in (".card.cin", ".lout", "#psheet"):
        assert sel in body, f"{sel} keeps its motion under reduced motion"
    assert "animation:none" in body and "transition:none" in body


def _two_columns(*extra):
    b = copy.deepcopy(BOARD_ONE)
    b["labels"] = ["x"]
    b["groups"][0]["items"] = [{"n": 7, "t": "ticket 7", "l": []}, {"n": 8, "t": "ticket 8", "l": ["x"]}]
    b["groups"][0]["items"] += [{"n": n, "t": f"ticket {n}", "l": []} for n in extra]
    b["groups"][0]["n"] = len(b["groups"][0]["items"])
    b["groups"][1]["items"] = [{"n": 9, "t": "ticket 9", "l": []}]
    b["groups"][1]["n"] = 1
    return b


@pytest.mark.node_bound
def test_reduced_motion_keeps_folded_cards_out_of_the_exit_clone(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
      globalThis.matchMedia = () => ({ matches: true });
      await loadBoard();
      $("cards").onclick({ target: $("cards").querySelector(".lane-h") });
      return { outs: $("cards").querySelectorAll(".lout").length,
               cards: $("cards").querySelectorAll('[data-role="card"]').length };
    """, board=[_board(7, 8)])
    assert out == {"outs": 0, "cards": 0}, out


@pytest.mark.node_bound
def test_a_column_switch_fades_no_card(hub):  # noqa: F811
    """The seen set is the board's, kept across renders: only a card new to the board after a read fades in."""
    out = run_page(hub.MOBILE_PAGE, """
      const cin = () => $("cards").querySelectorAll('[data-role="card"]').filter(c => c.classList.contains("cin")).map(c => String(c.dataset.n));
      await loadBoard(); await loadBoard();
      $("col-next").onclick(); const b = cin();
      $("col-prev").onclick(); const a = cin();
      await loadBoard(); const read = cin();
      $("col-next").onclick(); $("col-prev").onclick(); const after = cin();
      return { b, a, read, after };
    """, board=[_two_columns(), _two_columns(), _two_columns(10)])
    assert out == {"b": [], "a": [], "read": ["10"], "after": []}, out


@pytest.mark.node_bound
def test_a_folded_lane_clone_carries_no_card_lookup_key(hub):  # noqa: F811
    """A lane that shuts folds its old cards in a .lout clone; no clone keeps data-role, data-n or data-i."""
    out = run_page(hub.MOBILE_PAGE, """
      globalThis.matchMedia = () => ({ matches: false });
      const P = Object.getPrototypeOf($("cards"));
      Object.defineProperty(P, "nextElementSibling", { get() {
        const s = this.parentElement && this.parentElement.children; return s ? s[s.indexOf(this) + 1] || null : null; } });
      P.after = function (n) { const s = this.parentElement.children; n.parentElement = this.parentElement; s.splice(s.indexOf(this) + 1, 0, n); };
      P.remove = function () { const s = this.parentElement && this.parentElement.children; if (s) s.splice(s.indexOf(this), 1); };
      await loadBoard();
      const before = $("cards").querySelectorAll('[data-role="card"]').length;
      $("cards").onclick({ target: $("cards").querySelector(".lane-h") });
      const outs = $("cards").querySelectorAll(".lout");
      const folded = outs.flatMap(g => g.querySelectorAll(".card"));
      const keyed = folded.filter(c => "role" in c.dataset || "n" in c.dataset || "i" in c.dataset).length;
      return { before, outs: outs.length, folded: folded.length, keyed,
               live: $("cards").querySelectorAll('[data-role="card"]').length,
               hit: $("cards").querySelectorAll('[data-n="7"]').length };
    """, board=[_board(7, 8)])
    assert out == {"before": 2, "outs": 1, "folded": 2, "keyed": 0, "live": 0, "hit": 0}, out
