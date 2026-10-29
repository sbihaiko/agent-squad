"""The run dashboard on a narrow screen: one column of node cards, the ended run's info, and the ended text (#830).

The layout runs under node alone, with no DOM. Prior art for the page checks: tests/test_dashboard_page.py.
"""
import json
import shutil
import subprocess

import pytest

from tests.dashboard_helpers import OUT, core_bundle
from squad.dashboard_server import RunServer

NODE = shutil.which("node")
DOC = {"root": "a", "nodes": [
    {"name": "a", "children": ["b", "c"]},
    {"name": "b", "children": ["d"]},
    {"name": "c", "children": ["d"]},
    {"name": "d", "children": []},
]}


def layout(narrow):
    flag = "true" if narrow else "false"
    script = f"{core_bundle()}\nconst L = layoutModule();\nconsole.log(JSON.stringify(L.autoLayout({json.dumps(DOC)}, {flag})));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.mark.skipif(NODE is None, reason="node is needed for the layout")
def test_a_narrow_screen_lays_every_node_in_one_column():
    pos = layout(narrow=True)
    assert len({p["x"] for p in pos.values()}) == 1
    assert len({p["y"] for p in pos.values()}) == len(DOC["nodes"])


@pytest.mark.skipif(NODE is None, reason="node is needed for the layout")
def test_a_narrow_screen_keeps_a_parent_above_its_children():
    pos = layout(narrow=True)
    assert pos["a"]["y"] < pos["b"]["y"] < pos["d"]["y"]
    assert pos["a"]["y"] < pos["c"]["y"] < pos["d"]["y"]


@pytest.mark.skipif(NODE is None, reason="node is needed for the layout")
def test_a_wide_screen_keeps_the_layered_columns():
    pos = layout(narrow=False)
    assert pos["b"]["x"] == pos["c"]["x"] != pos["a"]["x"]


def test_the_run_info_says_the_run_ended(tmp_path):
    folder = tmp_path / "run-x"
    folder.mkdir()
    (folder / "record.jsonl").write_text(
        json.dumps({"ts": 1.0, "event": "run_start", "pos": 1}) + "\n"
        + json.dumps({"ts": 2.0, "event": "verdict", "verdict": "completed", "reasons": [], "exit_code": 0, "pos": 2}) + "\n")
    srv = RunServer(folder)
    try:
        assert srv.info()["ended"] is True
    finally:
        srv.server_close()


def test_the_page_switches_to_one_column_under_a_narrow_width_and_says_an_ended_run_is_read_only():
    html = OUT.read_text()
    assert '"(max-width: 600px)"' in html
    assert "This run ended" in html


def test_a_refused_control_request_for_an_ended_run_shows_as_ended():
    html = OUT.read_text()
    assert "r.status === 409" in html
    assert "The run has ended" in html
