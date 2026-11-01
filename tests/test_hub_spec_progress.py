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
    p = hub.spec_progress(BOARD, moves={})
    assert (p["done"], p["total"], p["open"], p["pct"], p["state"], p["bar"]) == (5, 9, 3, 55, "info", True)
    assert p["value"] == "5/9 · 55%" and p["so"] == "3 open"
    assert p["frac"] == pytest.approx(5 / 9)
    assert p["tip"].split("\n") == ["#7 Spec 7 — no sub-issue", "#1 Spec 1 — 2/5", "#2 Spec 2 — 3/4"]


def test_percent_rounds_down_and_reads_100_only_when_all_done(hub):
    p = hub.spec_progress([_item(1, "Todo", si=(199, 200))], moves={})
    assert (p["pct"], p["value"], p["state"]) == (99, "199/200 · 99%", "info")
    assert p["frac"] == pytest.approx(0.995)
    p = hub.spec_progress([_item(1, "Todo", si=(4, 4))], moves={})
    assert (p["pct"], p["value"], p["state"]) == (100, "4/4 · 100%", "ok")


def test_no_open_spec_reads_no_open_spec(hub):
    for items in ([], [BOARD[2], BOARD[5], BOARD[6]]):
        p = hub.spec_progress(items, moves={})
        assert (p["open"], p["value"], p["state"], p["bar"], p["tip"]) == (0, "no open spec", "none", False, "")


def test_open_specs_with_no_sub_issue_draw_an_empty_track(hub):
    p = hub.spec_progress([_item(9, "Todo", si=(0, 0)), _item(8, "Backlog")], moves={})
    assert (p["open"], p["value"], p["state"], p["bar"], p["frac"]) == (2, "2 open · no sub-issue yet", "info", True, 0)


def test_the_tip_keeps_eight_lines_then_counts_the_rest(hub):
    p = hub.spec_progress([_item(n, "Todo", si=(n, 20)) for n in range(1, 12)], moves={})
    lines = p["tip"].split("\n")
    assert lines[0] == "#1 Spec 1 — 1/20" and lines[7] == "#8 Spec 8 — 8/20" and lines[8] == "+3 more" and len(lines) == 9


def test_a_pending_move_to_abandoned_is_not_counted(hub, monkeypatch):
    spec = dict(_item(1, "Todo", si=(2, 5)), i="PVTI_1")
    other = _item(2, "Todo", si=(1, 4))
    moves = {"PVTI_1": {"column": "Abandoned", "at": "2099-01-01T00:00:00Z", "by": "owner"}}
    p = hub.spec_progress([spec, other], moves=moves, read_at=0, window=120.0)
    assert (p["open"], p["done"], p["total"]) == (1, 1, 4)
    monkeypatch.setattr(hub, "read_moves", lambda: moves)
    monkeypatch.setattr(hub, "board_cache", {"items": [spec, other], "rt": 0})
    assert hub.spec_progress([spec, other])["open"] == 1


def test_the_load_route_serves_the_bar(hub, monkeypatch):
    monkeypatch.setattr(hub, "board_cache", dict(hub.board_cache, items=BOARD))
    monkeypatch.setattr(hub, "read_moves", lambda: {})
    assert hub.load_payload()["specs"]["value"] == "5/9 · 55%"


def _run(hub, expr):
    if shutil.which("node") is None:
        pytest.skip("node is required")
    m = re.search(r"^// INST\{.*?^// \}INST$", hub.PAGE, re.M | re.S)
    esc = re.search(r"^const esc=.*$", hub.PAGE, re.M).group(0)
    out = subprocess.run(["node", "-"], input=f"const LOAD_ERR=95;{esc}\n{m.group(0)}\nconsole.log(JSON.stringify({expr}))",
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_page_draws_the_tile(hub):
    html = _run(hub, f"specBar({json.dumps(hub.spec_progress(BOARD, moves={}))})")
    assert 'class="at info"' in html and "<span class=so>3 open</span>" in html and "5/9 · 55%" in html
    assert "role=progressbar" in html and "aria-valuemax=9" in html and "aria-valuenow=5" in html
    assert f"width:{100 * 5 / 9}%" in html and 'title="#7 Spec 7' in html
    done = _run(hub, f"specBar({json.dumps(hub.spec_progress([_item(1, 'Todo', si=(4, 4))], moves={}))})")
    assert 'class="at ok"' in done and "width:100%" in done
    empty = _run(hub, f"specBar({json.dumps(hub.spec_progress([], moves={}))})")
    assert "no open spec" in empty and "progressbar" not in empty and 'class="at"' in empty
    assert "no open spec" in _run(hub, "specBar(undefined)")


def test_the_fill_has_a_reduced_motion_rule(hub):
    assert re.search(r"@media \(prefers-reduced-motion: reduce\)\{#inst \.pf\{transition:none\}\}", hub.PAGE)


def test_the_bar_sits_below_the_tiles_and_above_the_loop_row(hub):
    page = hub.PAGE
    assert page.index("id=ian") < page.index("id=ispec") < page.index("id=iloop")
