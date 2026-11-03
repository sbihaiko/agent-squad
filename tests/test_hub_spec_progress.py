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


def _item(n, st, s="OPEN", labels=("spec",), si=None, cr=None, pa=None, c=None, o=None):
    i = {"n": n, "t": f"Spec {n}", "s": s, "st": st, "l": list(labels), "c": c}
    if o is not None:
        i["o"] = o
    if pa is not None:
        i["pa"] = pa
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
    _item(6, "Todo", labels=("bug",), si=(1, 3), pa=1),            # a ticket of spec 1, not a spec: ignored
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
    for items in ([], [BOARD[2], BOARD[3], BOARD[4], BOARD[5]]):
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
    p = hub.spec_progress([spec, other], moves=moves, window=120.0)
    assert (p["open"], p["done"], p["total"]) == (1, 1, 4)
    monkeypatch.setattr(hub, "read_moves", lambda: moves)
    monkeypatch.setattr(hub, "board_cache", {"items": [spec, other]})
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


def test_the_bar_sits_below_the_tiles_and_above_the_load_lines(hub):
    """The Monitor carries the one loop switch and no late-writes row (owner): the instruments lead, the switch closes the card."""
    page = hub.PAGE
    assert "id=iloop" in page and "id=ilate" not in page
    assert page.index("id=igs") < page.index("id=ian") < page.index("id=ispec") < page.index("id=iloop")


# ---- the No spec group: bugs and single tickets (owner) ----

NOW = 1_800_000_000   # 2027-01-15T08:00:00Z
DAY = 86400


def _iso(t):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


NOSPEC = [
    _item(20, "Todo", labels=("bug",), o=_iso(NOW - 30 * DAY)),                   # open, opened long ago: counts, open
    _item(21, "Doing", labels=(), o=_iso(NOW - 3 * DAY)),                         # open single ticket: counts, open
    _item(22, "Closed", s="CLOSED", labels=("bug",), o=_iso(NOW - 3600), c=_iso(NOW - 600)),   # closed 10 min ago: counts, done
    _item(23, "Closed", s="CLOSED", labels=(), o=_iso(NOW - 2 * DAY), c=_iso(NOW - 3600)),     # opened 2 days ago, closed 1 h ago: counts, done
    _item(24, "Todo", s="CLOSED", labels=(), cr="not_planned", o=_iso(NOW - 3600), c=_iso(NOW - 600)),   # abandoned: never counted
    _item(25, "Todo", labels=("bug", "loop-bug"), o=_iso(NOW - 3600)),           # a loop bug has its own lane
    _item(26, "Todo", labels=(), pa=1, o=_iso(NOW - 3600)),                      # a spec ticket
    _item(27, "Closed", s="CLOSED", labels=()),                                  # closed with no closed-at: no instant to place it
]


def test_no_spec_counts_open_cards_and_the_ones_closed_in_the_last_four_hours(hub):
    """The window reads the closing (owner, 2026-11-02): the work takes hours, so a card opened in it is rarely closed in it."""
    assert hub.nospec_count(NOSPEC, moves={}, now=NOW) == {"closed": 2, "total": 4}


def test_a_card_closed_before_the_window_leaves_the_count(hub):
    old = [_item(40, "Closed", s="CLOSED", labels=(), o=_iso(NOW - 2 * DAY), c=_iso(NOW - 5 * 3600))]
    assert hub.nospec_count(old, moves={}, now=NOW) == {"closed": 0, "total": 0}


def test_the_open_card_of_a_closed_spec_counts_as_no_spec(hub):
    items = [_item(30, "Closed", s="CLOSED", si=(1, 2), c=_iso(NOW - DAY)),
             _item(31, "Todo", labels=(), pa=30), _item(32, "Closed", s="CLOSED", labels=(), pa=30, o=_iso(NOW - 3600), c=_iso(NOW - 60))]
    assert hub.nospec_count(items, moves={}, now=NOW) == {"closed": 0, "total": 1}


def test_the_monitor_tile_adds_the_no_spec_group(hub):
    p = hub.spec_progress(BOARD + NOSPEC, moves={}, now=NOW)
    assert (p["done"], p["total"], p["open"]) == (7, 13, 3)
    assert p["so"] == "3 open + no spec" and p["value"] == "7/13 · 53%"
    assert p["none"] == {"closed": 2, "total": 4}
    assert p["tip"].split("\n")[-1] == "No spec — 2/4"


def test_the_tile_draws_no_spec_alone(hub):
    p = hub.spec_progress(NOSPEC, moves={}, now=NOW)
    assert (p["open"], p["so"], p["value"], p["state"], p["bar"]) == (0, "no spec", "2/4 · 50%", "info", True)
    assert p["tip"] == "No spec — 2/4"


def test_the_board_summary_serves_the_no_spec_count(hub, monkeypatch):
    monkeypatch.setattr(hub, "read_moves", lambda: {})
    monkeypatch.setattr(hub, "save_board", lambda: None)
    monkeypatch.setattr(hub.time, "time", lambda: NOW)
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "rt": 1.0, "err": None, "lc": {}, "prs": [], "errs": [], "cases": [],
                                             "items": [dict(i, i=f"PVT_{i['n']}") for i in NOSPEC]})
    assert hub.board()["sum"]["none"] == {"closed": 2, "total": 4}
