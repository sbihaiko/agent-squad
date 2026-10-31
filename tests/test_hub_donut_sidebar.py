"""Hub sidebar (#922): the model donut carries a swatch, dollars and percent per row, a hover or focus on a slice or a row
highlights both, one model keeps its colour when the set changes, and a quota bar under its limit is neutral."""
import re

from tests.test_hub_cost_usage_panel import SRC, _run_js

ROW = re.compile(
    r'<div[^>]*data-k="([^"]+)"[^>]*><i style="background:[^"]+"></i>'
    r'<span class=ln>([^<]+)</span><span class=usd>([^<]+)</span><b>([^<]+)</b></div>'
)


def test_donut_legend_row_reads_swatch_name_dollars_and_percent():
    out = _run_js("""
const out={html:pie([
  {model:'big',in:9000,out:0,cache:0,spend:12.5,calls:1},
  {model:'mid',in:1000,out:0,cache:0,spend:2.25,calls:1},
])};
""")
    rows = ROW.findall(out["html"])
    assert rows == [("big", "big", "$12.50", "90%"), ("mid", "mid", "$2.25", "10%")], out["html"]


def test_donut_slices_share_the_key_of_their_legend_row():
    out = _run_js("""
const out={html:pie([
  {model:'big',in:9000,out:0,cache:0,spend:1,calls:1},
  {model:'mid',in:1000,out:0,cache:0,spend:1,calls:1},
])};
""")
    rows = [k for k, *_ in ROW.findall(out["html"])]
    slices = re.findall(r'<(?:path|circle)[^>]*data-k="([^"]+)"', out["html"])
    assert rows == ["big", "mid"] and slices == rows, (rows, slices)


def test_donut_legend_row_is_focusable():
    out = _run_js("const out={html:pie([{model:'m',in:10,out:0,cache:0,spend:1,calls:1}])};")
    assert re.search(r'<div[^>]*tabindex=0[^>]*data-k=|<div[^>]*data-k="[^"]+"[^>]*tabindex=0', out["html"]), out["html"]


def test_donut_model_keeps_its_colour_when_the_set_changes():
    both = _run_js("""
const out={html:pie([
  {model:'alpha',in:9000,out:0,cache:0,spend:1,calls:1},
  {model:'beta',in:1000,out:0,cache:0,spend:1,calls:1},
])};
""")
    alone = _run_js("const out={html:pie([{model:'beta',in:1000,out:0,cache:0,spend:1,calls:1}])};")

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


def test_quota_bar_under_its_limit_is_neutral_not_green():
    rule = re.search(r"\.sub \.fl\.lv-ok\{background:([^}]*)\}", SRC)
    assert rule, "the under-limit quota bar rule is missing"
    assert not re.search(r"var\(--ok\)|#3ecf8e|var\(--warn\)|var\(--err\)", rule.group(1)), rule.group(1)
