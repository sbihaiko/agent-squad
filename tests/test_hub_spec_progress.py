"""The Monitor card's spec bar: one bar totals the progress of every open spec. The hub counts it from the board cache
(ADR-0137); the page only draws the served value."""
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


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_CHECKOUT"] = tempfile.mkdtemp()
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    os.environ["SG_STATE"] = tempfile.mkdtemp()
    os.environ["SG_SHARED"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_spec_progress", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _item(n, st, s="OPEN", labels=("spec",), si=None, cr=None):
    i = {"n": n, "t": f"Spec {n}", "s": s, "st": st, "l": list(labels)}
    if si is not None:
        i["si"] = {"completed": si[0], "total": si[1]}
    if cr:
        i["cr"] = cr
    return i


BOARD = [
    _item(1, "Todo", si=(2, 5)),                                   # open spec
    _item(2, "In Progress", si=(3, 4)),                            # open spec
    _item(3, "Closed", s="CLOSED", si=(4, 4)),                     # closed spec: ignored
    _item(4, "Todo", s="CLOSED", si=(0, 9), cr="not_planned"),     # abandoned spec: ignored
    _item(5, "Abandoned", si=(1, 7)),                              # Abandoned column: ignored
    _item(6, "Todo", labels=("bug",), si=(1, 3)),                  # not a spec: ignored
    _item(7, "Backlog"),                                           # spec with no count: ignored
]


def test_the_bar_totals_open_specs_only(hub):
    p = hub.spec_progress(BOARD)
    assert (p["done"], p["total"], p["specs"], p["pct"]) == (5, 9, 2, 56)
    assert p["text"] == "Specs 5/9 · 56% · 2 open"
    assert "#1 Spec 1 2/5" in p["tip"] and "#2 Spec 2 3/4" in p["tip"]
    assert "#3" not in p["tip"] and "#7" not in p["tip"]


def test_no_open_spec_reads_no_open_spec(hub):
    for items in ([], [BOARD[2], BOARD[5], BOARD[6]], [_item(9, "Todo", si=(0, 0))]):
        p = hub.spec_progress(items)
        assert (p["done"], p["total"], p["pct"], p["text"]) == (0, 0, 0, "no open spec")


def test_the_load_route_serves_the_bar(hub):
    hub.board_cache["items"] = BOARD
    assert hub.load_payload()["specs"]["text"] == "Specs 5/9 · 56% · 2 open"


def _run(hub, expr):
    if shutil.which("node") is None:
        pytest.skip("node is required")
    m = re.search(r"^// INST\{.*?^// \}INST$", hub.PAGE, re.M | re.S)
    esc = re.search(r"^const esc=.*$", hub.PAGE, re.M).group(0)
    out = subprocess.run(["node", "-"], input=f"const LOAD_ERR=95;{esc}\n{m.group(0)}\nconsole.log(JSON.stringify({expr}))",
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_page_draws_the_label_and_the_width(hub):
    html = _run(hub, f"specBar({json.dumps(hub.spec_progress(BOARD))})")
    assert "Specs 5/9 · 56% · 2 open" in html and "width:56%" in html and 'title="' in html
    empty = _run(hub, f"specBar({json.dumps(hub.spec_progress([]))})")
    assert "no open spec" in empty and "width:0%" in empty
    assert "no open spec" in _run(hub, "specBar(undefined)")


def test_the_bar_sits_below_the_tiles_and_above_the_loop_row(hub):
    page = hub.PAGE
    assert page.index("id=ian") < page.index("id=ispec") < page.index("id=iloop")
