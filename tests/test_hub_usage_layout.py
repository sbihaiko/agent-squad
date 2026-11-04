"""The Usage card (.isub, shared by the sidebar and the Subscriptions theme): one row per subscription in a sidebar about 255 px wide. A row with no reading carries its
reason in the reset slot, and a reason is a sentence ("no live Kimi token: the bridge refreshes it on the next kimi
call", "free tier: ..."). Kept on one line in the `auto` column, it widened every row past the card: the shares and
resets of all rows were cut off, and the plan's own name shrank to nothing. These pin the rules that stop it."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


def _isub_rules():
    """Every `.isub` style rule in the page as {selector: declarations}, no space around a separator."""
    text = HUB_PY.read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(r"(\.isub[^{}]*)\{([^{}]*)\}", text):
        for sel in m.group(1).split(","):
            decl = re.sub(r"\s*([:;,()])\s*", r"\1", m.group(2))
            out[sel.strip()] = out.get(sel.strip(), "") + decl + ";"
    return out


def test_no_row_can_widen_the_card():
    """The card's one column shrinks to the card, so a long row text cannot push the rows past its edge."""
    d = _isub_rules().get(".isub", "")
    assert "grid-template-columns:minmax(0,1fr)" in d, d


def test_a_reason_takes_its_own_line_across_the_row():
    """A row with no reading puts its reason under the bar, across both columns, so the name and the `?` keep theirs."""
    d = _isub_rules().get(".isub .srow.na", "")
    areas = re.search(r'grid-template-areas:((?:\s*"[^"]*")+)', d)
    assert areas, d
    lines = re.findall(r'"([^"]*)"', areas.group(1))
    assert lines[0].split() == ["n", "p"], lines
    assert ["r", "r"] in [x.split() for x in lines], lines
    assert ["b", "b"] in [x.split() for x in lines], lines


def test_a_reason_wraps_and_a_reset_does_not():
    rules = _isub_rules()
    assert "white-space:nowrap" in rules.get(".isub .sr2", ""), "a reset stays on one line"
    na = rules.get(".isub .srow.na .sr2", "")
    assert "white-space:normal" in na, na
    assert "text-align:left" in na, na
