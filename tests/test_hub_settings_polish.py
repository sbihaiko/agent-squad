"""The Settings controls show their whole value (Settings polish).

A chain row's effort select fits "medium" and its hours field grows with the row; a narrow Kanban limits table moves
what the limit stops to its own line; the currency field keeps room for a 3-letter code; a settings row label starts
with a capital. The checks read the CSS the hub page ships.
"""
import re
from pathlib import Path

HUB = Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts/hub.py"


def css():
    s = HUB.read_text()
    return s[:s.index("<header class=top>")]


def rule(text, selector):
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", text)
    assert m, selector
    return m.group(1)


def test_the_effort_select_fits_medium_and_the_hours_field_grows():
    c = css()
    assert int(re.search(r"width:(\d+)px", rule(c, ".mr .ef")).group(1)) >= 84
    hr = rule(c, ".mr .hr")
    assert "flex:1 1" in hr and "min-width:9ch" in hr and "text-align:left" in hr
    assert "flex:0 1 auto" in rule(c, ".mr label.el:has(.hr)")


def test_a_narrow_kanban_table_puts_the_stop_on_its_own_line():
    c = css()
    q = c[c.index("@container (max-width:520px){"):]
    q = q[:q.index("\n}\n")]
    assert "#tab-set .ktab colgroup,#tab-set .ktab thead{display:none}" in q
    assert "grid-column:1/-1" in rule(q, "#tab-set .ktab td.ks")


def test_the_currency_field_keeps_a_three_letter_code_and_labels_start_capitalized():
    c = css()
    assert "width:8ch" in rule(c, ".sub .pr input[data-f=cur]")
    assert "text-transform:uppercase" in rule(c, "#tab-set .setrow .sl::first-letter")
    assert "text-transform:none" in rule(c, "#tab-set .sllegend>.blkh")
