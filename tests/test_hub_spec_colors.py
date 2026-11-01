"""Cards show their spec in its color and their wave as a chip (SPEC 889, #917).

The color comes from the spec number through the eight palette slots `--s1`..`--s8`. A card with no spec is grey. The
wave is a text chip, never a shade. No card carries its column's left border. The spec filter shows each spec's color.

The rules are `board-lanes.js`, run in node as the page carries them; the page text is checked for their use.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

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


def test_a_card_with_no_spec_is_grey():
    out = _node('const s=specSlots([889]);console.log(JSON.stringify([specColor(s,cardSpec({n:5})),SPEC_GREY]))')
    assert out[0] == out[1] == "var(--mute)", out


def test_the_wave_chip_names_the_wave_and_a_card_with_no_wave_shows_none():
    out = _node('console.log(JSON.stringify([waveChip({l:["wave:2"]}),waveChip({l:["bug"]}),waveChip({})]))')
    assert ">W2<" in out[0] and "data-k=wave" in out[0], out
    assert out[1] == "" and out[2] == "", out


def test_the_card_wears_its_own_spec_color_and_wave_chip():
    page = HUB_PY.read_text()
    card = re.search(r"const cards=\(g,it,shown\)=>.*", page).group(0)
    assert "--lc:${specColor(SLOTS,cardSpec(x))}" in card, "the card does not set its spec color"
    assert "${waveChip(x)}" in card, "the card does not draw the wave chip from the lane rule"


def test_the_spec_lane_header_takes_the_spec_color():
    page = HUB_PY.read_text()
    lane = re.search(r"const laneHtml=m=>\{.*?\n\s*h\+=`<section", page, re.S).group(0)
    assert "specColor(SLOTS,l.num)" in lane, "the spec lane header color does not come from the spec number"


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


def test_a_mobile_card_wears_its_spec_color_and_wave_chip():
    page = MOBILE.read_text()
    card = re.search(r"function cardButton\(x, i\) \{.*?\n\}", page, re.S).group(0)
    assert "specColor(mSlots(), cardSpec(x))" in card, "the mobile card does not set its spec color"
    assert "laneWaveOf(x)" in card, "the mobile card draws no wave chip from the lane rule"
    assert re.search(r"\.card\{[^}]*border-top:3px solid var\(--lc", page), "the mobile card has no top line in its color"
    head = re.search(r"function laneHeader\(l\) \{.*?\n\}", page, re.S).group(0)
    assert "specColor(mSlots(), l.num)" in head, "the mobile lane header does not take the spec color"
    lid = re.findall(r"\.lane-h \.lid\{([^}]*)\}", page)
    assert lid and "color:var(--lc" in lid[-1], "the last lane id rule does not draw the spec color"
