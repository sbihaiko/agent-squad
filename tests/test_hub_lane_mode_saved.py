"""A saved lane mode the board no longer offers is dropped on load (SPEC 1965).

The page once saved the Swimlanes mode in local storage, and an older page saved "all", which no option names. The owner
removed the control, so the saved key is dropped with the other removed controls and the page reads no lane mode: the
lanes are always the model the board drew.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
LANES_JS = ROOT / "contrib" / "squad-goal" / "scripts" / "board-lanes.js"
NODE = shutil.which("node")


@pytest.mark.parametrize("saved", ["spec", "all", "wave", "none"])
def test_a_saved_lane_mode_leaves_the_board_record_on_load(saved):
    """The one page line that reads the saved board record runs the rule's own drop, so no mode reaches the lanes."""
    if NODE is None:
        pytest.skip("node is required")
    drop = next(ln for ln in HUB_PY.read_text().splitlines() if ln.startswith("dropRemoved(bo);"))
    src = "\n".join([LANES_JS.read_text(),
                     f"const bo={{lmode:{saved!r},lc:{{s1:true}}}};", drop,
                     "console.log(JSON.stringify(bo));"])
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    out = p.stdout.strip()
    assert "lmode" not in out, f"the saved mode {saved!r} survived the load"
    assert '"lc":{"s1":true}' in out.replace(" ", ""), "the collapse state a kept control writes was dropped"


def test_the_page_reads_no_lane_mode():
    page = HUB_PY.read_text()
    assert "bo.lmode" not in page, "a page statement still reads a lane mode"
    assert 'if(!["spec","none"].includes(bo.lmode))' not in page, "the lane-mode default still runs at load"
