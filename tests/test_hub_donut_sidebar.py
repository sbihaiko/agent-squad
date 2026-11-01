"""Hub sidebar (#922): the model donut carries a swatch and dollars per row, a hover or focus on a slice or a row
highlights both, one model keeps its colour when the set changes, and a quota bar under its limit is neutral."""
import re

import pytest

from tests.test_hub_cost_usage_panel import SRC, _run_js

ROW = re.compile(
    r'<div[^>]*data-k="([^"]+)"[^>]*><i style="background:[^"]+"></i>'
    r'<span class=ln>([^<]+)</span><span class=usd>([^<]+)</span></div>'
)


def test_donut_legend_row_reads_swatch_name_and_dollars_only():
    out = _run_js("""
const out={html:pie([
  {model:'big',in:9000,out:0,cache:0,spend:12.5,month_cost:12.5,calls:1},
  {model:'mid',in:1000,out:0,cache:0,spend:2.25,month_cost:2.25,calls:1},
])};
""")
    rows = ROW.findall(out["html"])
    assert rows == [("big", "big", "$12.50"), ("mid", "mid", "$2.25")], out["html"]
    assert "%" not in out["html"], out["html"]


def test_donut_slices_share_the_key_of_their_legend_row():
    out = _run_js("""
const out={html:pie([
  {model:'big',in:9000,out:0,cache:0,spend:1,month_cost:1,calls:1},
  {model:'mid',in:1000,out:0,cache:0,spend:1,month_cost:1,calls:1},
])};
""")
    rows = [k for k, *_ in ROW.findall(out["html"])]
    slices = re.findall(r'<(?:path|circle)[^>]*data-k="([^"]+)"', out["html"])
    assert rows == ["big", "mid"] and slices == rows, (rows, slices)


def test_donut_legend_row_is_focusable():
    out = _run_js("const out={html:pie([{model:'m',in:10,out:0,cache:0,spend:1,month_cost:1,calls:1}])};")
    assert re.search(r'<div[^>]*tabindex=0[^>]*data-k=|<div[^>]*data-k="[^"]+"[^>]*tabindex=0', out["html"]), out["html"]


def test_donut_model_keeps_its_colour_when_the_set_changes():
    both = _run_js("""
const out={html:pie([
  {model:'alpha',in:9000,out:0,cache:0,spend:1,month_cost:1,calls:1},
  {model:'beta',in:1000,out:0,cache:0,spend:1,month_cost:1,calls:1},
])};
""")
    alone = _run_js("const out={html:pie([{model:'beta',in:1000,out:0,cache:0,spend:1,month_cost:1,calls:1}])};")

    def colour(html, key):
        m = re.search(rf'data-k="{key}"[^>]*><i style="background:([^"]+)"', html)
        assert m, html
        return m.group(1)

    assert colour(both["html"], "beta") == colour(alone["html"], "beta")


def test_donut_highlight_links_slice_and_row_on_hover_or_focus():
    assert '$("#pie").addEventListener("mouseover"' in SRC
    assert '$("#pie").addEventListener("focusin"' in SRC
    assert "function hlPie(" in SRC
    assert re.search(r"\.pie \[data-k\]\.hl\{", SRC), "the highlighted slice and row need one rule"


def test_a_quota_bar_fill_runs_from_green_to_red_and_its_text_stays_neutral():
    """The owner wants colour on the consumption bar only: green when empty, red when full (#922 re-anchored)."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required")
    line = next(ln for ln in SRC.splitlines() if ln.startswith("const qcol="))
    src = line + "\nconsole.log([0,50,100,140,-5].map(qcol).join('|'));"
    out = subprocess.run([node, "-"], input=src, capture_output=True, text=True, timeout=30).stdout.strip().split("|")
    assert out == ["hsl(120 65% 42%)", "hsl(60 65% 42%)", "hsl(0 65% 42%)", "hsl(0 65% 42%)", "hsl(120 65% 42%)"], out
    assert 'style="width:${Math.min(pct,100)}%;background:${qcol(pct)}"' in SRC, "the bar fill does not take the scale"
    assert not re.search(r"\.sub \.ql[^{]*\{[^}]*color:var\(--(ok|warn|err)\)", SRC), "the bar text took a colour"
