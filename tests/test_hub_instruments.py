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


def test_a_gauge_sweep_runs_from_ok_through_warn_to_err(hub):
    """SPEC 1123 #1141: the sweep is one gradient drawn from the state tones, the reading moves only its end, and the
    shipped scale marks stay on every gauge, so the gauge reads as a scale and not as three lights."""
    reads = _run(hub, "[0,50,100].map(v=>gaugeSvg(v,'CPU'))")
    stops, arcs, marks = [], [], []
    for svg in reads:
        grad = re.search(r"<linearGradient [^>]*>(.*?)</linearGradient>", svg)
        assert grad, "the gauge draws no gradient sweep"
        stops.append(re.findall(r'<stop offset=(\S+) stop-color="var\((--[\w-]+)\)"', grad.group(1)))
        arcs.append(re.search(r"<path class=gf [^>]*>", svg).group(0))
        marks.append(re.findall(r"<line class=gm ", svg))
    assert stops[0] == [("0", "--ok"), (".5", "--warn"), ("1", "--err")], \
        "the sweep does not run from ok through warn to err"
    assert stops[0] == stops[1] == stops[2], "the sweep changes with the reading instead of staying one scale"
    assert all('stroke="url(#g-CPU)"' in arc for arc in arcs), "the value arc does not take the gradient"
    assert [len(m) for m in marks] == [5, 5, 5], "the shipped scale marks are not kept"
    html = _run(hub, 'instGauges({cpu:0,ram:100,gpu:null})')
    assert 'stroke="none"' in html, "a missing reading draws no value arc"
    assert html.count("<linearGradient") == 3, html
    assert len(set(re.findall(r"<linearGradient id=(\S+)", html))) == 3, "two gauges share one gradient id"
    assert html.count("<line class=gm ") == 15, "a gauge lost its scale marks"
    rules = {s: d.replace(" ", "") for sels, d in _rules(hub.INST_CSS) for s in sels}
    assert "stroke:var(--mute)" in rules.get("#inst .ig .gm", ""), "a scale mark is not the muted axis ink"
    assert "stroke:var(--fg)" in rules.get("#inst .ig .gn", ""), "the needle is not fg"


def test_the_gauge_svg_is_hidden_from_a_screen_reader_and_the_value_is_text(hub):
    html = _run(hub, 'instGauges({cpu:62.4,ram:null,gpu:97})')
    assert html.count("<svg") == 3 and html.count('aria-hidden="true"') >= 3
    assert "CPU" in html and "62%" in html and "RAM" in html and "?" in html
    assert "ldred" in html                       # a cell at the machine's ceiling keeps its red


# ---------------------------------------------------------------- the annunciators

def test_the_monitor_draws_no_tile_not_even_the_gate(hub):
    """The owner removed Nightly, then Landed and GraphQL, the loop state moved to its own panel (#1071), and then the
    owner removed the Gate tile (#1260): the card draws no tile, even for a red gate."""
    facts = _facts(hub, gate=GATE_FAIL, nightly=NIGHTLY_PASS, loop=True)
    assert _run(hub, f"instTiles({json.dumps(facts)})") == []


def test_the_hub_still_serves_the_gate_fact_the_tile_no_longer_draws(hub):
    """#1260: the Gate tile is gone, the gate fact is not. The hub serves it, red included, so the footer keeps `gate …`."""
    facts = _facts(hub, gate={**GATE_FAIL, "took": 127, "avg": 169}, loop=True)
    gate = next(f for f in facts if f["key"] == "gate")
    assert gate["text"].startswith("gate ") and any(p["tone"] == "err" for p in gate["parts"])
    assert _run(hub, f"instTiles({json.dumps(facts)}).map(tileHtml).join('')") == ""


def test_a_source_with_no_reading_says_so_and_never_draws_a_pass(hub):
    assert _run(hub, f"instTiles({json.dumps(_facts(hub))})") == []   # no tile, so no pass is drawn (#1260)


# ---------------------------------------------------------------- the summary and the rows

def test_the_collapsed_summary_counts_the_failed_tiles_and_names_no_loop_state(hub):
    one = _facts(hub, gate=GATE_FAIL, nightly=NIGHTLY_PASS, loop=True)
    two = _facts(hub, gate=GATE_FAIL, nightly={**NIGHTLY_PASS, "result": "fail", "serial": "fail"}, loop=False)
    # #1260: no tile counts, so the summary is empty even for a red gate; the footer still shows the red gate
    assert _run(hub, f"instSum({json.dumps(one)})") == ""
    assert _run(hub, f"instSum({json.dumps(two)})") == ""      # no nightly tile; the loop's state is not in the summary
    assert _run(hub, f"instSum({json.dumps(_facts(hub))})") == ""


def test_the_card_carries_no_cycle_countdown(hub):
    """Owner, 2026-11-01: Next cycle left the card, and the countdown went with it."""
    assert "cycleLeft" not in hub.PAGE and "cycleFmt" not in hub.PAGE and "#icyc" not in hub.PAGE


def test_the_usage_reading_states_tokens_and_no_call_count(hub):
    """Owner, 2026-11-01: the Tokens row moved to Cost & usage and the over-cap call count is gone."""
    burn = {"over": True, "threshold": 5000000, "window_hours": 24,
            "burners": [{"consumer": "a", "tokens": 6000000}, {"consumer": "b", "tokens": 7000000}]}
    assert _run(hub, f"instUsage({json.dumps(_facts(hub, burn=burn))})")["tok"] == "13M/24h"
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
    assert page.count('fetch("/api/load")') == 1 and page.count("setInterval(pollIfVisible(loadPoll)") == 1
    assert "instDraw(d)" in page
    reduced = next(line for line in (ROOT / "contrib/squad-goal/scripts/web-tokens.css").read_text().splitlines()
                   if line.startswith("@media (prefers-reduced-motion: reduce)"))
    assert "#inst" in reduced
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
            assert re.match(r"#inst\b", s), s
            assert len(re.findall(r"#inst\b", s)) <= 1, s


def test_a_red_cell_draws_its_needle_and_its_value_red(hub):
    rules = _rules(hub.INST_CSS)
    assert any("#inst .ig.ldred .gn" in sels and "stroke:var(--err)" in d.replace(" ", "") for sels, d in rules), "the overload needle is red"
    assert any("#inst .gv" in sels and "font-size:var(--fs-caption)" in d for sels, d in rules), "the gauge value is small text"


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
            if ".warn" in s and ".ldred" not in s and s.endswith(".gf") and "stroke:" in d:
                assert any(_spec(r) > _spec(s) or (_spec(r) == _spec(s) and j > i) for j, r in reds), s
