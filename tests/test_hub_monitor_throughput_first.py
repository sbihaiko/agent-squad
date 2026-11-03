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


LOOP_VIEW = {"state": "on", "who": "owner", "when": "2026-11-02T09:00:00-03:00", "write": True, "reason": "", "tailnet": False,
             "access": {"loop_write": False, "terminals_send": False},
             "live": {"runs": ["/x/runs/run-20261102-1"], "gates": []}, "log": []}


def test_the_monitor_tiles_carry_no_loop_state(hub):
    page = hub.PAGE
    tiles = _run(page, "instTiles([]).map(t=>t.k)", _monitor_blocks(page))
    assert tiles == ["Gate"]


def test_the_monitor_heading_counts_failures_and_names_no_loop(hub):
    page = hub.PAGE
    assert _run(page, "instSum([])", _monitor_blocks(page)) == "0 fail"


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


def test_the_monitor_carries_the_one_loop_switch_and_the_panel_keeps_the_log(hub):
    """The owner moved the switch to the Monitor; the Loop panel carries the change log and the history, never a button."""
    page = hub.PAGE
    monitor = _section(page, "inst")
    assert "id=iloop" in monitor, "the Monitor carries the loop slot"
    panel = _section(page, "lop")
    assert "id=lopb" in panel and "id=histb" in panel, panel[:200]
    assert "role=switch" not in panel, "the Loop panel carries no second switch"


def test_the_instruments_lead_the_monitor_and_throughput_follows(hub):
    page = hub.PAGE
    monitor = _section(page, "inst")
    assert monitor.index("id=igs") < monitor.index("id=ispeed")
    assert monitor.index("id=ispeed") < monitor.index("id=ispec")


def test_the_loop_switch_closes_the_monitor_card(hub):
    """The owner put the switch at the bottom of the Monitor: every instrument and the Specs tile sit above it."""
    monitor = _section(hub.PAGE, "inst")
    assert monitor.index("id=iloop") > monitor.index("id=ispec"), "the loop switch is not the last row of the Monitor"


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
    assert tiles == ["Gate"], tiles

def test_a_reader_learns_the_throughput_and_its_direction_in_one_row(hub):
    page = hub.PAGE
    row = _run(page, 'speedRow({today:5,median:3,min:1,max:6,trend:"up"})', _monitor_blocks(page))
    assert "accelerating" in row and "5" in row and "cards closed today" in row

def test_the_monitor_carries_no_late_writes_row(hub):
    """The owner removed it: no late-writes row and no reader that builds one."""
    page = hub.PAGE
    assert "id=ilate" not in page and "lateRows" not in page
