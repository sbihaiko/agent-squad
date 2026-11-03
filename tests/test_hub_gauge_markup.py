"""The Monitor gauge draws its sweep and needle: no element of the gauge is swallowed by an unclosed tag (#1141)."""
import re
from pathlib import Path

HUB = Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts/hub.py"


def test_no_template_puts_a_self_close_against_an_unquoted_value():
    """`y2=${y2}/>` parses as y2="32/" and leaves the tag open, so every later element nests inside it and is not drawn."""
    bad = [n for n, line in enumerate(HUB.read_text().splitlines(), 1) if re.search(r"=\$\{[^}]*\}/>", line)]
    assert not bad, f"unquoted value against '/>' on hub.py lines {bad}"


def test_the_gauge_marks_close_before_the_sweep():
    src = HUB.read_text()
    body = src[src.index("function gaugeSvg("):]
    body = body[:body.index("</svg>`}")]
    assert '<line class=gm x1=${x1} y1=${y1} x2=${x2} y2=${y2} />' in body or '<line class=gm x1=${x1} y1=${y1} x2=${x2} y2=${y2}></line>' in body
