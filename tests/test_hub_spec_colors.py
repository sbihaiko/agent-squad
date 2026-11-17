"""Cards show their spec in its color (SPEC 889, #917).

The color comes from the spec number through the eight palette slots `--s1`..`--s8`. A card with no spec takes its own
number's slot, so every card carries a color; grey (`var(--mute)`) is the stale board's cue alone (#1640). The wave chip
is gone with the wave lane (SPEC 1965, #1988). No card carries its column's left border. The spec filter shows each
spec's color.

The rules are `board-lanes.js`, run in node as the page carries them; the page text is checked for their use.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests.test_hub_mobile_move import _module, run_page

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
LANES_JS = SCRIPTS / "board-lanes.js"
NODE = shutil.which("node")


def _node(script):
    if NODE is None:
        pytest.skip("node is required")
    p = subprocess.run([NODE, "-"], input=LANES_JS.read_text() + "\n" + script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_two_cards_of_one_spec_share_a_color():
    out = _node('const s=specSlots([889,890]);'
                'console.log(JSON.stringify([specColor(s,cardSpec({pa:889})),specColor(s,cardSpec({pa:889})),'
                'specColor(s,cardSpec({sp:true,n:889}))]))')
    assert out[0] == out[1] == out[2], out
    assert re.fullmatch(r"var\(--s[1-8]\)", out[0]), out


def test_two_specs_differ_while_the_palette_has_room():
    """889 and 897 land on one slot by number alone; the second takes the next free slot."""
    out = _node('const n=[889,897,890,891,892,893,894,895],s=specSlots(n);'
                'console.log(JSON.stringify(n.map(x=>specColor(s,x))))')
    assert len(set(out)) == 8, out


def test_the_slots_do_not_depend_on_the_order_the_board_lists_them():
    out = _node('console.log(JSON.stringify([specColor(specSlots([897,889]),889),specColor(specSlots([889,897]),889)]))')
    assert out[0] == out[1], out


def test_a_ninth_spec_still_gets_a_palette_color():
    out = _node('const n=[1,2,3,4,5,6,7,8,9],s=specSlots(n);console.log(JSON.stringify(n.map(x=>specColor(s,x))))')
    assert all(re.fullmatch(r"var\(--s[1-8]\)", c) for c in out), out


def test_a_card_with_no_spec_takes_a_palette_color():
    """Grey says the board read is stale, never that a card belongs to no spec (#1640): the on-call read a grey card as
    a dead board. A card of no spec wears its own number's slot."""
    out = _node('const s=specSlots([889]);console.log(JSON.stringify([specColor(s,cardSpec({n:5})),cardColor(s,{n:7})]))')
    assert all(re.fullmatch(r"var\(--s[1-8]\)", c) for c in out), out
    assert not [c for c in out if c == "var(--mute)"], out


def test_a_loop_bug_card_wears_the_loop_bugs_lane_red():
    """A loop-bug has no parent spec; it sits in the Loop bugs lane, so it wears that lane's red, never grey."""
    out = _node('const s=specSlots([889]);console.log(JSON.stringify([cardColor(s,{n:5,l:["loop-bug"]}),'
                'cardColor(s,{n:6,l:["loop-bug:landing"],pa:889}),cardColor(s,{pa:889}),cardColor(s,{n:7})]))')
    assert out[0] == out[1] == "var(--err)", out
    assert re.fullmatch(r"var\(--s[1-8]\)", out[2]) and re.fullmatch(r"var\(--s[1-8]\)", out[3]), out


def test_the_card_wears_its_own_spec_color_and_no_wave_chip():
    page = HUB_PY.read_text()
    card = re.search(r"const cards=\(g,it,shown\)=>.*", page).group(0)
    assert "--lc:${cardColor(SLOTS,x)}" in card, "the card does not set its spec color"
    assert "waveChip" not in card and "data-k=wave" not in card, "the card still draws a wave chip"


def test_the_spec_lane_header_takes_the_spec_color():
    page = HUB_PY.read_text()
    lane = re.search(r"const laneHtml=m=>\{.*?\n\s*h\+=`<section", page, re.S).group(0)
    assert "specColor(SLOTS,l.num)" in lane, "the spec lane header color does not come from the spec number"


def test_the_no_spec_lane_header_is_not_the_stale_grey():
    """The `No spec` lane holds cards of several slots, so its header names no spec; it takes the accent, because grey
    is the stale board's cue alone (#1640)."""
    page = HUB_PY.read_text()
    lane = re.search(r"const laneHtml=m=>\{.*?\n\s*h\+=`<section", page, re.S).group(0)
    assert "SPEC_GREY" not in lane, "the No spec lane header still wears the stale grey"
    assert '"var(--acc)"' in lane, "the No spec lane header takes no color of its own"


def test_no_card_carries_the_column_left_border():
    """The card's lines take the spec color or grey; the column color `--cc` never reaches a card border."""
    css = HUB_PY.read_text() + (SCRIPTS / "board-lanes.css").read_text()
    parts = re.split(r"([{}])", css)   # one linear pass over the braces: a regex rule match backtracks over the page script
    for i in range(0, len(parts) - 4, 2):
        if parts[i + 1] != "{" or parts[i + 3] != "}":
            continue
        sel, body = parts[i], parts[i + 2]
        rule = sel + "{" + body + "}"
        if ".it" not in sel or len(sel) > 400 or not any(re.search(r"\.it(\.[\w-]+|\[[^]]*\]|:[\w-]+)*$", s.strip()) for s in sel.split(",")):
            continue   # a rule for a part inside the card, not the card itself
        assert not re.search(r"border(-left|-top)?(-color)?:[^;}]*var\(--cc", body), rule.strip()


def test_the_spec_filter_shows_each_spec_color():
    page = HUB_PY.read_text()
    bar = re.search(r"const fillBar=.*?\n.*?\n", page).group(0)
    assert "class=sw" in bar, "the filter options draw no swatch"
    assert re.search(r'fillBar\("fsl",.*specColor\(SLOTS,', page), "the spec filter passes no spec color"


TOKENS_CSS = SCRIPTS / "web-tokens.css"
MOBILE = SCRIPTS / "hub-mobile.html"
SLOTS8 = [f"--s{i}" for i in range(1, 9)]


def test_the_palette_is_one_block_in_the_shared_tokens():
    """The eight slots live in the shared tokens, so every surface reads one palette; the hub page writes none again."""
    tokens = TOKENS_CSS.read_text()
    for s in SLOTS8:
        assert re.search(re.escape(s) + r":#[0-9a-fA-F]{6}", tokens), f"{s} is not in the shared tokens"
        assert not re.search(re.escape(s) + r":#", HUB_PY.read_text()), f"hub.py writes {s} a second time"


def test_the_mobile_page_carries_the_lane_rules():
    page = MOBILE.read_text()
    assert "/*CARD_RULES*/" in page, "the mobile page does not take the card rules"
    assert re.search(r'MOBILE_PAGE = .*\.replace\("/\*CARD_RULES\*/", CARD_RULES_JS\)', HUB_PY.read_text()), \
        "the hub does not fill the card rules into the mobile page"


def test_a_mobile_card_wears_its_spec_color_and_no_wave_chip():
    page = MOBILE.read_text()
    card = re.search(r"function cardButton\(x, i\) \{.*?\n\}", page, re.S).group(0)
    assert "cardColor(mSlots(), x)" in card, "the mobile card does not set its spec color"
    assert "laneWaveOf" not in card and 'chip("wave"' not in card, "the mobile card still draws a wave chip"
    assert re.search(r"\.card\{[^}]*border-top:3px solid var\(--lc", page), "the mobile card has no top line in its color"
    head = re.search(r"function laneHeader\(l\) \{.*?\n\}", page, re.S).group(0)
    assert "specColor(mSlots(), l.num)" in head, "the mobile lane header does not take the spec color"
    lid = re.findall(r"\.lane-h \.lid\{([^}]*)\}", page)
    assert lid and "color:var(--lc" in lid[-1], "the last lane id rule does not draw the spec color"


SPEC_BOARD = {"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None,
              "groups": [{"name": "Todo", "n": 2, "items": [
                  {"n": 918, "t": "a ticket of 917", "pa": 917, "l": ["wave:1"]},
                  {"n": 891, "t": "a ticket of 890", "pa": 890, "l": ["wave:1"]}]}]}


@pytest.fixture(scope="module")
def mobile_hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    return _module("hub_spec_colors_mobile", HUB_PY)


@pytest.mark.node_bound
def test_a_mobile_lane_id_wears_the_color_its_cards_wear(mobile_hub):
    """The served page draws a board whose specs are not in slot order (890 and 917 take --s3 and --s6, not --s1 and
    --s2). Each lane id resolves to the same slot as the cards of its lane: an inline color on the id beats --lc."""
    out = run_page(mobile_hub.MOBILE_PAGE, """
      await loadBoard();
      const res = {};
      let lane = null;
      for (const el of $("cards").children) {
        if (el.classList.contains("lane-h")) {
          const lid = el.querySelector(".lid");
          const lc = ((el.getAttribute("style") || "").match(/--lc:([^;]*)/) || [])[1] || null;
          lane = el.dataset.lane;
          res[lane] = { id: lid.style.color || lc, cards: [] };
        } else if (el.dataset.role === "card") {
          res[lane].cards.push(((el.getAttribute("style") || "").match(/--lc:([^;]*)/) || [])[1] || null);
        }
      }
      return res;
    """, board=[SPEC_BOARD])
    assert len(out) == 2, out
    for lane, r in out.items():
        assert r["cards"] and all(c == r["id"] for c in r["cards"]), f"lane {lane}: id {r['id']} vs cards {r['cards']}"
    assert {r["id"] for r in out.values()} == {"var(--s3)", "var(--s6)"}, out


def test_no_lane_model_carries_a_color_slot_of_its_own():
    """One source: both pages color a lane by specColor over the spec number, so neither lane model writes a slot index
    (`pi`) that no page reads and that could drift from the cards (#917)."""
    groups = [{"name": "Todo", "items": [{"n": 918, "t": "a", "pa": 917, "l": []}, {"n": 891, "t": "b", "pa": 890, "l": []}]}]
    opts = {"fs": [], "lc": {}, "hideDone": False, "closed": "Closed"}
    desk = _node(f"console.log(JSON.stringify(buildBoard({json.dumps(groups)},{json.dumps(opts)}).lanes))")
    gesture = (SCRIPTS / "hub-mobile-gesture.js").read_text()
    p = subprocess.run([NODE, "-"], capture_output=True, text=True, timeout=30, input=gesture + "\nconsole.log(JSON.stringify("
                       f"columnLanes({json.dumps({'groups': groups, 'cards': groups[0]['items'], 'lc': {}})}).lanes))")
    assert p.returncode == 0, p.stderr
    mobile = json.loads(p.stdout)
    assert desk and mobile
    assert not [ln for ln in desk + mobile if "pi" in ln], "a lane model still writes its own color slot"
