"""The Monitor leads with the machine instruments, then Throughput, and states the loop once (#1071, parent #1068).

The loop's state renders in the Loop panel only, through one switch that drains when turned off (ADR-0162). The Monitor
carries no loop tile and no loop words in its heading. The CPU, RAM and GPU gauges sit first in the Monitor, then the
throughput row, above the usage and spend sections, which keep the donut and the bar chart.
"""
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_CHECKOUT"] = tempfile.mkdtemp()
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    os.environ["SG_STATE"] = tempfile.mkdtemp()
    os.environ["SG_SHARED"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_monitor_throughput_first", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _block(page, start, end):
    s = page.index(start)
    return page[s:page.index(end, s)]


def _run(page, expr, blocks):
    if shutil.which("node") is None:
        pytest.skip("node is required")
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    js = "\n".join(["const LOAD_ERR=95;", esc, *blocks, f"console.log(JSON.stringify({expr}))"])
    out = subprocess.run(["node", "-"], input=js, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _monitor_blocks(page):
    return [_block(page, "// INST{", "// }INST"), _block(page, "// loop-switch:start", "// loop-switch:end")]


def _section(page, sid):
    m = re.search(rf"<details id={sid} class=sp[^>]*>(.*?)</details>", page, re.S)
    assert m, f"the page has no section #{sid}"
    return m.group(0)


SPEED = {"window": 10, "window_secs": 4 * 3600, "day": 10, "median": 9, "min": 4, "max": 21, "trend": "up",
         "stale": False, "age_secs": 60, "labels": {"age_secs": 60, "stale": False},
         "tasks": {"closed": 4, "total": 10, "value": "4 of 10 closed", "frac": 0.4},
         "arrivals": {"bugs": {"closed": 3, "total": 7, "value": "3 of 7 closed", "frac": 3 / 7},
                      "loop_bugs": {"closed": 2, "total": 4, "value": "2 of 4 closed", "frac": 0.5}},
         "specs": {"open": 3, "done": 5, "total": 9, "pct": 55, "frac": 5 / 9,
                   "value": "5 of 9 closed - 55%", "state": "info", "bar": True, "tip": ""}}
LOOP_VIEW = {"state": "on", "who": "owner", "when": "2026-11-02T09:00:00-03:00", "write": True, "reason": "", "tailnet": False,
             "access": {"loop_write": False, "terminals_send": False},
             "live": {"runs": ["/x/runs/run-20261102-1"], "gates": []}, "log": []}


def test_the_monitor_tiles_carry_no_loop_state(hub):
    page = hub.PAGE
    tiles = _run(page, "instTiles([]).map(t=>t.k)", _monitor_blocks(page))
    assert tiles == []   # no Monitor tile (#1260)


def test_the_monitor_heading_counts_failures_and_names_no_loop(hub):
    page = hub.PAGE
    assert _run(page, "instSum([])", _monitor_blocks(page)) == ""   # no tile, no count (#1260)


def test_the_loop_state_renders_once_in_its_panel(hub):
    page = hub.PAGE
    out = _run(page, f"loopSetHtml({json.dumps(LOOP_VIEW)})", _monitor_blocks(page))
    assert out.count("Loop on") == 1
    assert out.count('role=switch') == 1 and out.count("<button") == 1
    assert 'aria-checked="true"' in out and "Stop…" not in out and "Stop now…" not in out


def test_the_loop_switch_is_off_to_the_left_and_says_so(hub):
    page = hub.PAGE
    out = _run(page, f"loopSetHtml({json.dumps({**LOOP_VIEW, 'state': 'off'})})", _monitor_blocks(page))
    assert 'aria-checked="false"' in out and "Loop off" in out and "Start loop" not in out


def test_the_switch_is_one_key_and_carries_no_explanation(hub):
    """Owner: the Monitor shows one on/off key for the loop, never a paragraph beside it.

    The drain rule still names itself where the write happens, in the stop confirm, and the change log keeps the history.
    """
    page = hub.PAGE
    out = _run(page, f"loopSetHtml({json.dumps(LOOP_VIEW)})", _monitor_blocks(page))
    assert out.count('role=switch') == 1
    assert "drains" not in out and "<p" not in out and "<small" not in out, out
    log = _run(page, f"loopLogHtml({json.dumps(LOOP_VIEW)})", _monitor_blocks(page))
    assert "drains" not in log and "<h4>Changes</h4>" in log, log
    confirm = _run(page, f"loopConfirmHtml({json.dumps(LOOP_VIEW)})", _monitor_blocks(page))
    assert re.search(r'value=drain checked', confirm)


def test_the_monitor_carries_the_one_loop_switch_and_the_loop_tab_keeps_the_log(hub):
    """The owner moved the switch to the Monitor; the Loop tab carries the change log and the history, never a button."""
    page = hub.PAGE
    monitor = _section(page, "inst")
    assert "id=iloop" in monitor, "the Monitor carries the loop slot"
    assert "<details id=lop" not in page, "the Settings page still carries a Loop panel (#2515)"
    loop_tab = page.split("<section id=tab-prof", 1)[1].split("</section>", 1)[0]
    assert "id=lopaud" in loop_tab, "the Loop tab lost the change list"
    assert "id=histb" not in loop_tab and "id=list" not in loop_tab, "the run history left the Loop tab (#1277)"
    assert "role=switch" not in loop_tab, "the Loop tab carries a second switch"


def test_the_instruments_lead_the_monitor_and_throughput_follows(hub):
    page = hub.PAGE
    monitor = _section(page, "inst")
    assert monitor.index("id=igs") < monitor.index("id=ispeed")
    card = _run(page, f"speedRow({json.dumps(SPEED)})", _monitor_blocks(page))
    sentences = ["Tasks: 4 of 10 closed", "Bugs: 3 of 7 closed", "Loop-bugs: 2 of 4 closed",
                 "Specs: 5 of 9 closed - 55%"]
    positions = [card.index(f'aria-label="{s}"') for s in sentences]
    assert positions == sorted(positions), "the three rows are out of order"


def test_the_loop_switch_closes_the_monitor_card(hub):
    """The owner put the switch at the bottom of the Monitor: the gauges and the Throughput card sit above it."""
    monitor = _section(hub.PAGE, "inst")
    assert monitor.index("id=iloop") > monitor.index("id=ispeed"), "the loop switch is not the last row of the Monitor"


def test_the_throughput_row_sits_above_every_cost_and_subscription_section(hub):
    page = hub.PAGE
    after = page.index("id=ispeed")
    for sid in ("usage", "isub", "stats", "chart", "pie", "lim", "sub"):
        assert page.index(f"id={sid}") > after, sid


def test_the_donut_and_the_bar_chart_stay_in_the_spend_section(hub):
    page = hub.PAGE
    stats = _section(page, "stats")
    assert 'id=chart' in stats and 'id=pie' in stats
    assert "hidden" not in re.search(r"<div id=chart[^>]*>", stats).group(0)
    assert "hidden" not in re.search(r"<div id=pie[^>]*>", stats).group(0)

def test_the_monitor_carries_no_queue_tile(hub):
    """The owner asked for no queue in the Monitor: a queue fact stays in the footer, never as a Monitor tile."""
    page = hub.PAGE
    facts = [{"key": "queue", "text": "queue 5 held", "tone": "wait"}]
    tiles = _run(page, f"instTiles({json.dumps(facts)}).map(t=>t.k)", _monitor_blocks(page))
    assert tiles == []   # no Monitor tile (#1260), tiles

def test_a_reader_learns_the_throughput_and_its_direction_in_one_row(hub):
    page = hub.PAGE
    row = _run(page, f"speedRow({json.dumps(SPEED)})", _monitor_blocks(page))
    assert "accelerating" in row and "10" in row and "cards closed in the last 4 h" in row


PHONE_WIDTH = 390   # the hub's phone width: docs/evidence/926/README.md measures the phone at 390 px


def _rendered(hub, sp, measure):
    """Draws the card in the hub's own page at the phone width and returns what `measure` reads from it, in Chrome."""
    sync = pytest.importorskip("playwright.sync_api")
    with sync.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except sync.Error as e:   # no Chrome on this machine: the rendered check cannot run here
            pytest.skip(f"Chrome is not available: {' '.join(str(e).split())[:200]}")
        page = browser.new_page(viewport={"width": PHONE_WIDTH, "height": 844})
        page.set_content(hub.PAGE)
        page.evaluate("(sp) => { document.querySelector('#ispeed').innerHTML = speedRow(sp); }", sp)
        out = measure(page)
        browser.close()
    return out


@pytest.mark.heavy
def test_the_card_fits_the_sidebar_at_the_phone_width_on_rendered_dimensions(hub):
    def measure(page):
        return page.evaluate("""() => {
            const side = document.querySelector('#side').getBoundingClientRect();
            const card = document.querySelector('#ispeed .spd').getBoundingClientRect();
            const rows = [...document.querySelectorAll('#ispeed .spw')].map(e => e.scrollWidth - e.clientWidth);
            return {docW: document.documentElement.scrollWidth, sideRight: side.right, cardLeft: card.left,
                    cardRight: card.right, rowCount: rows.length, rowOverflow: Math.max(0, ...rows)};
        }""")
    got = _rendered(hub, SPEED, measure)
    assert got["rowCount"] == 4 and got["docW"] <= PHONE_WIDTH, got
    assert got["cardLeft"] >= 0 and got["cardRight"] <= got["sideRight"] + 0.5 <= PHONE_WIDTH + 0.5, got
    assert got["rowOverflow"] <= 0, got


@pytest.mark.heavy
def test_each_row_carries_its_full_sentence_in_its_accessible_name_on_the_rendered_page(hub):
    def measure(page):
        return [page.get_by_role("img", name=s, exact=True).count()
                for s in ("Tasks: 4 of 10 closed", "Bugs: 3 of 7 closed", "Loop-bugs: 2 of 4 closed")]
    assert _rendered(hub, SPEED, measure) == [1, 1, 1]

def test_the_monitor_carries_no_late_writes_row(hub):
    """The owner removed it: no late-writes row and no reader that builds one."""
    page = hub.PAGE
    assert "id=ilate" not in page and "lateRows" not in page
