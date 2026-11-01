"""Machine and loop instruments (#983): the status strip left the Settings tab for a sidebar card drawn as calm flight
instruments. These pin the gauge geometry, the annunciator states, the collapsed summary, and where the card sits."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
NOW = 1_000_000.0
SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_CHECKOUT"] = tempfile.mkdtemp()
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    os.environ["SG_STATE"] = tempfile.mkdtemp()
    os.environ["SG_SHARED"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_instruments", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _js(hub):
    """The card's own script block as the page carries it, plus the cell helpers and `esc` it reads."""
    m = re.search(r"^// INST\{.*?^// \}INST$", hub.PAGE, re.M | re.S)
    assert m, "the page carries no instruments block"
    esc = re.search(r"^const esc=.*$", hub.PAGE, re.M).group(0)
    cells = re.findall(r"^function ld(?:Num|Round|Cell|Cells)\(.*$", hub.PAGE, re.M)
    assert len(cells) == 4, cells
    return "\n".join(['const LOAD_ERR=95;', esc, *cells, m.group(0)])


def _run(hub, expr):
    if shutil.which("node") is None:
        pytest.skip("node is required")
    out = subprocess.run(["node", "-"], input=f"{_js(hub)}\nconsole.log(JSON.stringify({expr}))",
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _facts(hub, **src):
    base = {"landing": {"issue": 73, "ts": NOW - 7200}, "quota": {"codex": None, "agy": None}}
    return hub.foot_items({**base, **src}, NOW)


GATE_FAIL = {"kind": "fast", "result": "fail", "sha": SHA, "ts": NOW - 300}
NIGHTLY_PASS = {"result": "pass", "sha": SHA, "parallel": "pass", "serial": "pass", "ids": [], "ts": NOW - 6 * 3600}


# ---------------------------------------------------------------- the gauges

def test_the_needle_sweeps_a_half_circle_from_left_to_right(hub):
    g = _run(hub, "[0,50,100].map(gaugeGeom)")
    assert [x["deg"] for x in g] == [-90, 0, 90]
    assert all(x["known"] for x in g)
    # the filled arc ends on the circle: left end, top, right end of the 60x36 box
    assert [(x["x"], x["y"]) for x in g] == [(4, 32), (30, 6), (56, 32)]


def test_a_missing_reading_draws_no_needle_and_a_high_one_is_clamped(hub):
    none, over = _run(hub, "[gaugeGeom(null), gaugeGeom(250)]")
    assert none["known"] is False
    assert over["deg"] == 90 and over["known"] is True


def test_a_gauge_fill_runs_from_green_to_red_with_its_reading(hub):
    """The owner: the gauge arc takes the quota bars' scale, green when idle and red at the top."""
    g = _run(hub, "[0,50,100,null].map(gaugeGeom).map(x=>x.col)")
    assert g == ["hsl(120 65% 42%)", "hsl(60 65% 42%)", "hsl(0 65% 42%)", "var(--line)"]
    html = _run(hub, 'instGauges({cpu:0,ram:100,gpu:null})')
    assert 'stroke="hsl(120 65% 42%)"' in html and 'stroke="hsl(0 65% 42%)"' in html


def test_the_gauge_svg_is_hidden_from_a_screen_reader_and_the_value_is_text(hub):
    html = _run(hub, 'instGauges({cpu:62.4,ram:null,gpu:97})')
    assert html.count("<svg") == 3 and html.count('aria-hidden="true"') >= 3
    assert "CPU" in html and "62%" in html and "RAM" in html and "?" in html
    assert "ldred" in html                       # a cell at the machine's ceiling keeps its red


# ---------------------------------------------------------------- the annunciators

def test_the_three_tiles_read_gate_loop_and_landed_in_that_order(hub):
    """The owner removed the Nightly tile from the card; the footer still carries the nightly fact."""
    facts = _facts(hub, gate=GATE_FAIL, nightly=NIGHTLY_PASS, loop=True)
    tiles = _run(hub, f"instTiles({json.dumps(facts)})")
    assert [t["k"] for t in tiles] == ["Gate", "Loop", "Landed"]
    gate, loop, landed = tiles
    assert (gate["tone"], gate["text"]) == ("err", "fast fail 5m ago")
    assert gate["tip"] == "ran at 0123456"
    assert (loop["tone"], loop["text"]) == ("ok", "ON")
    assert landed["text"] == "PR #73 2h ago" and landed["href"].endswith("/pull/73")


def test_the_gate_tile_shows_the_last_run_time_and_the_mean(hub):
    """The owner: the gate tile shows how long the last gate took and the mean of the recent ones."""
    facts = _facts(hub, gate={**GATE_FAIL, "took": 127, "avg": 169}, loop=True)
    gate = _run(hub, f"instTiles({json.dumps(facts)})[0]")
    assert gate["times"] == "took 2m07s · avg 2m49s"
    html = _run(hub, f"instTiles({json.dumps(facts)}).map(tileHtml).join('')")
    assert "took 2m07s · avg 2m49s" in html
    plain = _run(hub, f"instTiles({json.dumps(_facts(hub, gate=GATE_FAIL))})[0]")
    assert not plain.get("times")


def test_a_source_with_no_reading_says_so_and_never_draws_a_pass(hub):
    tiles = _run(hub, f"instTiles({json.dumps(_facts(hub))})")
    gate, loop, _ = tiles
    assert gate["tone"] == "neutral" and "pass" not in gate["text"]
    assert loop["text"] == "?" and loop["tone"] == "neutral"


def test_a_loop_that_is_off_reads_off_in_the_error_tone(hub):
    loop = _run(hub, f"instTiles({json.dumps(_facts(hub, loop=False))})[1]")
    assert (loop["text"], loop["tone"]) == ("OFF", "err")


# ---------------------------------------------------------------- the summary and the rows

def test_the_collapsed_summary_names_the_loop_state_and_the_failed_tiles(hub):
    one = _facts(hub, gate=GATE_FAIL, nightly=NIGHTLY_PASS, loop=True)
    two = _facts(hub, gate=GATE_FAIL, nightly={**NIGHTLY_PASS, "result": "fail", "serial": "fail"}, loop=False)
    assert _run(hub, f"instSum({json.dumps(one)})") == "ON · 1 fail"
    assert _run(hub, f"instSum({json.dumps(two)})") == "OFF · 1 fail"      # no nightly tile; the loop's OFF is not counted twice
    assert _run(hub, f"instSum({json.dumps(_facts(hub))})") == "? · 0 fail"


def test_the_cycle_countdown_parses_and_reformats_the_hubs_own_span(hub):
    assert _run(hub, '["cycle in 3m40s","cycle in 1h02m","cycle +1m05s late","x"].map(cycleLeft)') == [220, 3720, -65, None]
    assert _run(hub, "[220,3720,-65,0].map(cycleFmt)") == ["in 3m40s", "in 1h02m", "+1m05s late", "in 0m00s"]


def test_the_usage_row_states_tokens_then_calls_and_invents_no_budget(hub):
    burn = {"over": True, "threshold": 5000000, "window_hours": 24,
            "burners": [{"consumer": "a", "tokens": 6000000}, {"consumer": "b", "tokens": 7000000}]}
    assert _run(hub, f"instUsage({json.dumps(_facts(hub, burn=burn))})") == {"tok": "13M/24h", "calls": "2 over 5M"}
    assert _run(hub, f"instUsage({json.dumps(_facts(hub))})") is None


# ---------------------------------------------------------------- where the card sits

def test_the_card_sits_in_the_sidebar_and_the_settings_strip_is_gone(hub):
    page = hub.PAGE
    side = page[page.index("<aside id=side>"):page.index("<main id=content")]
    assert "<details id=inst class=sp open><summary>Monitor<span class=sr id=instsr></span></summary>" in side
    for gone in ("sts", "foot", "ldline", "ldfacts"):
        assert not re.search(rf"id={gone}\b", page), gone


def test_the_card_reads_the_one_load_route_and_stays_calm(hub):
    page = hub.PAGE
    assert page.count('fetch("/api/load")') == 1 and page.count("setInterval(loadPoll") == 1
    assert "instDraw(d)" in page
    assert re.search(r"prefers-reduced-motion:\s*reduce\)\s*\{[^}]*#inst", page)
    assert 'aria-live="off"' in page or "aria-live=off" in page
    assert "@keyframes" not in page[page.index("// INST{"):page.index("// }INST")]


def _rules(css):
    """The card's style rules as (selectors, declarations), comments stripped, in source order."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"@media[^{]*\{((?:[^{}]*\{[^{}]*\})*)[^{}]*\}", r"\1", css)
    return [([s.strip() for s in m.group(1).split(",")], m.group(2)) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)]


def test_every_card_selector_names_the_card_once(hub):
    """A stray `#inst` prefix nests the card in itself, so the rule matches nothing on either page."""
    for sels, _ in _rules(hub.INST_CSS):
        for s in sels:
            assert re.match(r"#(inst|ispec)\b", s), s
            assert len(re.findall(r"#inst\b", s)) == 1, s


def test_a_red_cell_draws_its_needle_and_its_value_red(hub):
    rules = _rules(hub.INST_CSS)
    red = lambda sel: any(sel in sels and "stroke:var(--err)" in d.replace(" ", "") for sels, d in rules)
    assert red("#inst .ig.ldred .gn"), "the overload needle is red"
    assert any("#inst .gv" in sels and "font-size:var(--fs-min)" in d for sels, d in rules), "the gauge value is small text"


def _spec(sel):
    return (sel.count("#"), sel.count(".") + sel.count(":"), len(re.findall(r"(?:^|\s)[a-z]", sel)))


def test_a_red_arc_stays_red_when_the_card_warns(hub):
    """A red cell sits above the machine-wide amber of `#inst.warn`, and never yields to it (review of #983)."""
    rules = _rules(hub.INST_CSS)
    reds = [(i, s) for i, (sels, d) in enumerate(rules) for s in sels
            if s.endswith(".ldred .gf") and "stroke:var(--err)" in d.replace(" ", "")]
    assert reds, "a red cell's arc is red"
    assert any(s.startswith("#inst.warn") for _, s in reds), "the red rule names the warning card too"
    for i, (sels, d) in enumerate(rules):
        for s in sels:
            if ".warn" in s and s.endswith(".gf") and "stroke:" in d:
                assert any(_spec(r) > _spec(s) or (_spec(r) == _spec(s) and j > i) for j, r in reds), s
