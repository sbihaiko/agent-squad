"""A saved lane mode that no option names reads as Spec (#978).

The page saves the Swimlanes mode in local storage. An older page saved "all", which no option names, so the select drew
blank. A saved mode the page no longer offers, `wave` included (SPEC 1965, #1988), reads as spec before the select and the
lane model see it.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"
NODE = shutil.which("node")


def _bo_line():
    line = next(ln for ln in HUB_PY.read_text().splitlines() if ln.startswith("const bo=ls.bo||{}"))
    return line.split(",saveB=")[0] + ";" + next(
        (s for s in HUB_PY.read_text().splitlines() if s.startswith("if(![\"spec\",\"none\"].includes(bo.lmode))")), "")


@pytest.mark.parametrize("saved,want", [("all", "spec"), (None, "spec"), ("wave", "spec"), ("none", "none")])
def test_a_saved_lane_mode_always_names_an_option(saved, want):
    if NODE is None:
        pytest.skip("node is required")
    ls = "{}" if saved is None else '{"bo":{"lmode":"' + saved + '"}}'
    src = f"const ls={ls};{_bo_line()}\nconsole.log(bo.lmode);"
    p = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == want
