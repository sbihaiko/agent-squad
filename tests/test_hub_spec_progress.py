"""The Monitor card's spec bar: one bar totals the progress of every open spec. The hub counts it from the board cache
(ADR-0137); the page only draws the served value."""
import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
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
    """#2264: the row counts the sub-issues of open specs alone, and it carries no second line."""
    p = hub.spec_progress(BOARD, moves={})
    assert (p["done"], p["total"], p["open"], p["pct"], p["state"], p["bar"]) == (5, 9, 3, 55, "info", True)
    assert p["value"] == "5 of 9 closed - 55%"
    assert "so" not in p and "none" not in p, "the row keeps no second line and no No spec group"
    assert p["frac"] == pytest.approx(5 / 9)
    assert p["tip"].split("\n") == ["#7 Spec 7 — no sub-issue", "#1 Spec 1 — 2/5", "#2 Spec 2 — 3/4"]


def test_percent_rounds_down_and_reads_100_only_when_all_done(hub):
    p = hub.spec_progress([_item(1, "Todo", si=(199, 200))], moves={})
    assert (p["pct"], p["value"], p["state"]) == (99, "199 of 200 closed - 99%", "info")
    assert p["frac"] == pytest.approx(0.995)
    p = hub.spec_progress([_item(1, "Todo", si=(4, 4))], moves={})
    assert (p["pct"], p["value"], p["state"]) == (100, "4 of 4 closed - 100%", "ok")


def test_the_specs_row_reads_three_of_nine_as_thirty_three_percent(hub):
    """Owner, 2026-11-18: the row reads 'N of M closed - P%', and the percent rounds down."""
    p = hub.spec_progress([_item(1, "Todo", si=(3, 9))], moves={})
    assert (p["value"], p["pct"], p["state"]) == ("3 of 9 closed - 33%", 33, "info")


def test_no_open_spec_reads_no_open_spec(hub):
    for items in ([], [BOARD[2], BOARD[3], BOARD[4], BOARD[5]]):
        p = hub.spec_progress(items, moves={})
        assert (p["open"], p["value"], p["state"], p["bar"], p["tip"]) == (0, "no open spec", "none", False, "")


def test_open_specs_with_no_sub_issue_draw_an_empty_track(hub):
    p = hub.spec_progress([_item(9, "Todo", si=(0, 0)), _item(8, "Backlog")], moves={})
    assert (p["open"], p["value"], p["state"], p["bar"], p["frac"]) == (2, "0 of 0 closed", "info", True, 0)


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
    assert hub.load_payload()["specs"]["value"] == "5 of 9 closed - 55%"


def _run(hub, expr):
    if shutil.which("node") is None:
        pytest.skip("node is required")
    m = re.search(r"^// INST\{.*?^// \}INST$", hub.PAGE, re.M | re.S)
    esc = re.search(r"^const esc=.*$", hub.PAGE, re.M).group(0)
    out = subprocess.run(["node", "-"], input=f"const LOAD_ERR=95;{esc}\n{m.group(0)}\nconsole.log(JSON.stringify({expr}))",
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _specs_row(hub, p):
    """The Throughput card's Specs row for one spec fact, drawn by the page's own speedRow."""
    none = {"closed": 0, "total": 0, "value": "none", "frac": 0}
    sp = {"window": 0, "window_secs": 4 * 3600, "day": 0, "median": None, "min": None, "max": 0, "trend": None,
          "stale": False, "age_secs": 60, "labels": {"age_secs": 60, "stale": False},
          "tasks": none, "arrivals": {"bugs": none, "loop_bugs": none}, "specs": p}
    return _run(hub, f"speedRow({json.dumps(sp)})")


def test_the_throughput_card_draws_the_specs_row_with_the_closed_of_total_text(hub):
    """#2264: the Specs row reads closed of total for open specs, with no second line."""
    p = hub.spec_progress(BOARD, moves={})
    html = _specs_row(hub, p)
    assert 'class="spw spec"' in html and "<span class=spwv>5 of 9 closed - 55%</span>" in html, html
    assert f"width:{100 * p['frac']}%" in html, html
    assert 'role=img aria-label="Specs: 5 of 9 closed - 55%"' in html, html
    assert "spws" not in html and "open</span>" not in html, html


def test_the_card_draws_the_specs_row_the_hub_serves_for_one_fixture(hub, monkeypatch):
    """#1424: the row is drawn from the payload the hub serves, over the same fixture the old Specs card read, so a
    wiring gap between `spec_progress` and the card cannot pass: the served counts are the computed counts."""
    monkeypatch.setattr(hub, "board_cache", dict(hub.board_cache, items=BOARD, t=time.time()))
    monkeypatch.setattr(hub, "read_moves", lambda: {})
    payload = hub.load_payload()
    assert payload["specs"] == hub.spec_progress(BOARD, moves={})
    html = _run(hub, f"speedRow({json.dumps(payload['speed'])}, {json.dumps(payload['specs'])})")
    assert 'class="spw spec"' in html, html
    assert "<span class=spwv>5 of 9 closed - 55%</span>" in html, html
    assert "spws" not in html and "open</span>" not in html, html


def test_the_specs_row_survives_a_hub_with_no_throughput_history(hub):
    """#1424: the Specs row comes off the board cache, never off the optional speed fact: a hub with no slot history
    still draws it."""
    p = hub.spec_progress(BOARD, moves={})
    html = _run(hub, f"speedRow(null, {json.dumps(p)})")
    assert "no history yet" in html and 'class="spw spec"' in html, html
    assert "5 of 9 closed - 55%" in html, html


def test_no_separate_specs_card_is_drawn(hub):
    """The separate Specs card is gone: the page holds no spec tile slot and no specBar renderer."""
    assert "id=ispec" not in hub.PAGE
    assert "function specBar(" not in hub.PAGE


def test_the_summary_serves_the_furthest_column_the_tickets_reached(hub):
    """A spec's card keeps its own column; how far its tickets got is a count the summary serves beside it (#1906)."""
    drawn = [(1, "Todo", None, None, True),          # the spec's own card
             (2, "Doing", 1, None, False),
             (3, "Code Complete", 1, None, False),
             (4, "Code Complete", 1, None, False),
             (5, "Backlog", 1, None, False),
             (6, "Closed", 1, None, False)]
    spec = hub.board_summary(drawn)["specs"][0]
    assert (spec["st"], spec["far"], spec["far_n"]) == ("Todo", "Closed", 1), spec
    assert (spec["closed"], spec["total"]) == (1, 5), spec


def test_the_furthest_column_counts_the_tickets_that_reached_it(hub):
    drawn = [(1, "Todo", None, None, True),
             (2, "Testing", 1, None, False),
             (3, "Testing", 1, None, False),
             (4, "Code Review", 1, None, False),
             (5, "Abandoned", 1, None, False)]   # a given-up ticket did not reach past the work it left
    spec = hub.board_summary(drawn)["specs"][0]
    assert (spec["far"], spec["far_n"]) == ("Testing", 2), spec


def test_a_spec_with_no_ticket_serves_no_furthest_column(hub):
    spec = hub.board_summary([(1, "Backlog", None, None, True)])["specs"][0]
    assert spec["far"] is None and spec["far_n"] == 0, spec


def test_the_cache_row_column_is_the_kanban_column(hub):
    """The Specs row counts the column the kanban draws, so the cache wrapper never reshapes a row's facts."""
    for i in BOARD:
        assert hub.cache_row_column(i, {}, 120.0) == hub.drawn_column(i, {}, 120.0)["st"]


def test_the_fill_has_a_reduced_motion_rule(hub):
    """#1140: no partial reduced-motion block survives: the one shared block is all the card needs."""
    blocks = re.findall(r"@media \(prefers-reduced-motion: reduce\)\{(.*?)\}\}", hub.PAGE, re.S)
    assert len(blocks) == 1, "the page carries the one shared reduced-motion block"
    assert "@media" not in hub.INST_CSS, "a partial reduced-motion block survives in the Instruments card"


def test_the_throughput_card_sits_above_the_load_lines(hub):
    """The Monitor carries the one loop switch and no late-writes row (owner): the instruments lead, the switch closes the card."""
    page = hub.PAGE
    assert "id=iloop" in page and "id=ilate" not in page
    assert page.index("id=igs") < page.index("id=ispeed") < page.index("id=ian") < page.index("id=iloop")


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


def test_a_class_label_card_leaves_the_no_spec_count(hub):
    """#2283: the loop-bug mark is the prefix rule the dispatcher reads (ADR-0146), so `loop-bug:landing` has its own
    lane and never counts as a No spec card."""
    items = [_item(50, "Todo", labels=("bug", "loop-bug:landing"), o=_iso(NOW - 3600)),
             _item(51, "Todo", labels=(), o=_iso(NOW - 3600))]
    assert hub.nospec_count(items, moves={}, now=NOW) == {"closed": 0, "total": 1}


def test_the_specs_row_leaves_the_no_spec_group_out(hub):
    """Owner, 2026-11-18: the Specs row counts only the sub-issues of open specs; the No spec group is in no count."""
    p = hub.spec_progress(BOARD + NOSPEC, moves={}, now=NOW)
    assert (p["done"], p["total"], p["open"]) == (5, 9, 3)
    assert p["value"] == "5 of 9 closed - 55%" and "so" not in p and "none" not in p
    assert p["tip"].split("\n") == ["#7 Spec 7 — no sub-issue", "#1 Spec 1 — 2/5", "#2 Spec 2 — 3/4"]


def test_the_specs_row_reads_no_open_spec_when_only_the_no_spec_group_is_open(hub):
    p = hub.spec_progress(NOSPEC, moves={}, now=NOW)
    assert (p["open"], p["value"], p["state"], p["bar"], p["tip"]) == (0, "no open spec", "none", False, "")


def test_the_board_summary_serves_the_no_spec_count(hub, monkeypatch):
    monkeypatch.setattr(hub, "read_moves", lambda: {})
    monkeypatch.setattr(hub, "save_board", lambda: None)
    monkeypatch.setattr(hub.time, "time", lambda: NOW)
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "rt": 1.0, "err": None, "lc": {}, "prs": [], "errs": [], "cases": [],
                                             "items": [dict(i, i=f"PVT_{i['n']}") for i in NOSPEC]})
    assert hub.board()["sum"]["none"] == {"closed": 2, "total": 4}


def test_an_open_spec_shows_its_age_since_its_first_landed_ticket(hub):
    now = dt.datetime(2026, 11, 11, tzinfo=dt.UTC).timestamp()
    items = [_item(1, "Todo", si=(2, 5)), _item(6, "Closed", s="CLOSED", labels=("bug",), pa=1, c="2026-11-01T00:00:00Z")]
    p = hub.spec_progress(items, moves={}, now=now)
    assert p["tip"] == "#1 Spec 1 — 2/5 · open 10 d"
