"""The Monitor leads with Throughput and states the loop once (#1071, parent #1068).

The loop's state renders in the Loop panel only, through one switch that drains when turned off (ADR-0162). The Monitor
carries no loop tile and no loop words in its heading. Throughput sits first in the Monitor, above the usage and spend
sections, which keep the donut and the bar chart.
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


def test_turning_the_switch_off_drains_and_the_page_says_so_before_it_happens(hub):
    page = hub.PAGE
    out = _run(page, f"loopSetHtml({json.dumps(LOOP_VIEW)})", _monitor_blocks(page))
    assert "Turning it off drains" in out
    confirm = _run(page, f"loopConfirmHtml({json.dumps(LOOP_VIEW)})", _monitor_blocks(page))
    assert re.search(r'value=drain checked', confirm)


def test_the_loop_switch_sits_in_the_loop_panel_and_the_monitor_has_none(hub):
    page = hub.PAGE
    monitor = _section(page, "inst")
    assert "role=switch" not in monitor and "loopSetHtml" not in monitor
    assert 'data-loop' not in monitor


def test_the_throughput_row_leads_the_monitor(hub):
    page = hub.PAGE
    monitor = _section(page, "inst")
    assert monitor.index("id=ispeed") < monitor.index("id=igs")
    assert monitor.index("id=ispeed") < monitor.index("id=ispec")


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


def test_a_reader_learns_the_throughput_and_its_direction_in_one_row(hub):
    page = hub.PAGE
    row = _run(page, 'speedRow({today:5,median:3,min:1,max:6,trend:"up"})', _monitor_blocks(page))
    assert "accelerating" in row and "5" in row and "cards closed today" in row
