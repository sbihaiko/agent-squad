"""The cumulative flow counts each day from the history store's column intervals, sampled at the local day end (#1024)."""
import ast
import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
CFD_PY = SCRIPTS / "board_cfd.py"
COLUMNS = ["Todo", "Doing", "Closed"]


def _iso(*args, tz=UTC):
    return datetime(*args, tzinfo=tz).isoformat()


def _run(start, end, column, hold=None):
    return {"from": start, "to": end, "on_board": True, "column": column, "hold": hold, "spec": None, "approx": False}


def _off(start, end):
    return {"from": start, "to": end, "on_board": False, "column": None, "hold": None, "spec": None, "approx": False}


def test_a_card_moved_off_the_board_and_readded_stays_in_its_column(cfd):
    """#1663: a card removed and later re-added was moved, not lost. Its absence is no band, so the chart has no valley
    where the move took it off the board and no wall where it came back."""
    card = [_run(_iso(2026, 10, 1, 9), _iso(2026, 10, 2, 9), "Todo"), _off(_iso(2026, 10, 2, 9), _iso(2026, 10, 4, 9)),
            _run(_iso(2026, 10, 4, 9), None, "Todo")]
    result = cfd([card], COLUMNS, now=datetime(2026, 10, 5, 12, tzinfo=UTC))
    assert [p["bands"]["Todo"] for p in result["points"]] == [0, 1, 1, 1, 1, 1]   # the first point is the empty day


def test_a_card_that_leaves_for_good_counts_in_no_band_after_it_leaves(cfd):
    """The other side of #1663: a removal nothing re-adds is a card that left, and the bands drop it for good."""
    card = [_run(_iso(2026, 10, 1, 9), _iso(2026, 10, 2, 9), "Todo"), _off(_iso(2026, 10, 2, 9), None)]
    result = cfd([card], COLUMNS, now=datetime(2026, 10, 4, 12, tzinfo=UTC))
    assert [p["bands"]["Todo"] for p in result["points"]] == [0, 1, 0, 0, 0]   # it left at 09:00 on 10-02


@pytest.fixture(scope="module")
def cfd():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("board_cfd_under_test", CFD_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.cfd


def test_the_chart_opens_one_empty_day_before_the_first_card(cfd):
    # A drawn cumulative flow begins at zero: the first point is the day before the earliest card opened, and it counts nothing.
    card = [_run(_iso(2026, 10, 1, 12), None, "Todo")]
    result = cfd([card], COLUMNS, now=datetime(2026, 10, 2, 12, tzinfo=UTC))
    assert [p["day"] for p in result["points"]] == ["2026-09-30", "2026-10-01", "2026-10-02"]
    assert result["points"][0]["bands"]["Todo"] == 0 and result["points"][0]["wip"] == 0


def test_each_day_counts_the_column_at_its_local_day_end(cfd):
    # 02:00 UTC on 2026-10-01 is 23:00 on 2026-09-30 in Sao Paulo (UTC-3): the card's first day is 09-30.
    card = [_run(_iso(2026, 10, 1, 2), _iso(2026, 10, 1, 3, 30), "Todo"), _run(_iso(2026, 10, 1, 3, 30), None, "Doing")]
    result = cfd([card], COLUMNS, zone="America/Sao_Paulo", now=datetime(2026, 10, 2, 12, tzinfo=UTC))
    assert [p["day"] for p in result["points"]] == ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]
    assert result["points"][0]["bands"]["Todo"] == 0
    assert result["points"][1]["bands"]["Todo"] == 1
    assert result["points"][2]["bands"]["Doing"] == 1
    assert result["points"][3]["bands"]["Doing"] == 1


def test_a_day_with_no_movement_still_has_a_count(cfd):
    card = [_run(_iso(2026, 10, 1, 12), None, "Todo")]
    result = cfd([card], COLUMNS, now=datetime(2026, 10, 3, 12, tzinfo=UTC))
    assert [p["day"] for p in result["points"]] == ["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03"]
    assert [p["bands"]["Todo"] for p in result["points"]] == [0, 1, 1, 1]


def test_a_held_card_counts_in_its_column_and_in_the_held_share(cfd):
    held = [_run(_iso(2026, 10, 1, 10), None, "Doing", hold="frozen:owner")]
    free = [_run(_iso(2026, 10, 1, 10), None, "Doing")]
    point = cfd([held, free], COLUMNS, now=datetime(2026, 10, 2, 12, tzinfo=UTC))["points"][1]
    assert point["bands"]["Doing"] == 2
    assert point["held"]["Doing"] == 1


def test_parked_backlog_counts_in_no_band_and_no_wip(cfd):
    parked = [_run(_iso(2026, 10, 1, 10), None, "Backlog")]
    todo = [_run(_iso(2026, 10, 1, 10), None, "Todo")]
    point = cfd([parked, todo], COLUMNS, now=datetime(2026, 10, 2, 12, tzinfo=UTC))["points"][1]
    assert sum(point["bands"].values()) == 1
    assert point["wip"] == 1


def test_wip_is_the_open_columns_and_throughput_is_the_closed_increase(cfd):
    finished = [_run(_iso(2026, 10, 1, 9), _iso(2026, 10, 2, 10), "Todo"), _run(_iso(2026, 10, 2, 10), None, "Closed")]
    working = [_run(_iso(2026, 10, 1, 9), None, "Doing")]
    result = cfd([finished, working], COLUMNS, now=datetime(2026, 10, 3, 12, tzinfo=UTC))
    assert [p["wip"] for p in result["points"]] == [0, 2, 1, 1]
    assert [p["throughput"] for p in result["points"]] == [None, 0, 1, 0]
    assert result["average_throughput"] == pytest.approx(1 / 3)


def test_no_cards_gives_an_empty_series(cfd):
    assert cfd([], COLUMNS, now=datetime(2026, 10, 3, tzinfo=UTC)) == {"start": None, "points": [], "average_throughput": None}


def test_module_imports_no_hub_github_client_or_file_api():
    tree = ast.parse(CFD_PY.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    forbidden = {"hub", "hub_client", "github", "gh", "subprocess", "requests", "urllib", "http", "pathlib", "os", "io", "shutil"}
    assert not names & forbidden, sorted(names & forbidden)
