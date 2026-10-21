"""The cumulative flow bands are computed from the samples, host-free (#710)."""
import ast
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
CFD_PY = SCRIPTS / "board_cfd.py"
COLUMNS = ["Todo", "Doing", "Closed"]


def _epoch(*args, tz=timezone.utc):
    return datetime(*args, tzinfo=tz).timestamp()


@pytest.fixture(scope="module")
def cfd():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("board_cfd_under_test", CFD_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.cfd


def _sample(t, counts, frozen=0, none=0):
    return {"t": t, "counts": counts, "frozen": frozen, "none": none}


def test_bands_follow_column_order_then_frozen_then_no_column(cfd):
    samples = [_sample(_epoch(2026, 10, 20, 12), {"card": {"Closed": 1, "Todo": 2, "Doing": 3}}, frozen=1, none=2)]
    result = cfd(samples, COLUMNS)
    point = result["points"][0]
    assert list(point["bands"]) == ["Todo", "Doing", "Closed", "frozen", "No column"]
    assert point["bands"] == {"Todo": 2, "Doing": 3, "Closed": 1, "frozen": 1, "No column": 2}


def test_missing_column_reads_zero_and_unknown_column_folds_into_no_column(cfd):
    samples = [_sample(_epoch(2026, 10, 20, 12), {"card": {"Todo": 4, "Legacy": 5}}, none=1)]
    point = cfd(samples, COLUMNS)["points"][0]
    assert point["bands"]["Doing"] == 0
    assert point["bands"]["Closed"] == 0
    assert point["bands"]["No column"] == 6


def test_counts_sum_over_kinds_and_kind_filter_keeps_one(cfd):
    samples = [_sample(_epoch(2026, 10, 20, 12), {"issue": {"Todo": 2}, "pr": {"Todo": 3, "Doing": 1}})]
    everything = cfd(samples, COLUMNS)["points"][0]["bands"]
    only_pr = cfd(samples, COLUMNS, kind="pr")["points"][0]["bands"]
    assert everything["Todo"] == 5
    assert everything["Doing"] == 1
    assert only_pr["Todo"] == 3
    assert only_pr["Doing"] == 1


def test_several_samples_in_one_day_keep_the_last_in_the_named_zone(cfd):
    # 02:30 UTC on 2026-10-21 is 23:30 on 2026-10-20 in Sao Paulo (UTC-3): the day is the zone's day.
    early = _sample(_epoch(2026, 10, 20, 15), {"card": {"Todo": 1}})
    late_in_zone = _sample(_epoch(2026, 10, 21, 2, 30), {"card": {"Todo": 9}})
    noon_next_day = _sample(_epoch(2026, 10, 21, 12), {"card": {"Todo": 7}})
    result = cfd([early, late_in_zone, noon_next_day], COLUMNS, zone="America/Sao_Paulo")
    assert [p["day"] for p in result["points"]] == ["2026-10-20", "2026-10-21"]
    assert result["points"][0]["bands"]["Todo"] == 9
    assert result["points"][1]["bands"]["Todo"] == 7


def test_wip_is_the_non_terminal_columns_and_throughput_is_the_closed_increase(cfd):
    samples = [
        _sample(_epoch(2026, 10, 20, 12), {"card": {"Todo": 2, "Doing": 1, "Closed": 3}}),
        _sample(_epoch(2026, 10, 21, 12), {"card": {"Todo": 1, "Doing": 4, "Closed": 6}}),
        _sample(_epoch(2026, 10, 22, 12), {"card": {"Todo": 0, "Doing": 2, "Closed": 7}}),
    ]
    result = cfd(samples, COLUMNS)
    assert [p["wip"] for p in result["points"]] == [3, 5, 2]
    assert [p["throughput"] for p in result["points"]] == [None, 3, 1]
    assert result["average_throughput"] == 2


def test_start_is_the_first_sample_time_and_no_samples_gives_an_empty_series(cfd):
    first = _epoch(2026, 10, 20, 12)
    samples = [_sample(first + 86400, {"card": {"Todo": 1}}), _sample(first, {"card": {"Todo": 1}})]
    assert cfd(samples, COLUMNS)["start"] == first
    empty = cfd([], COLUMNS)
    assert empty == {"start": None, "points": [], "average_throughput": None}


def test_a_day_with_no_sample_is_absent_not_zero(cfd):
    samples = [
        _sample(_epoch(2026, 10, 20, 12), {"card": {"Todo": 1}}),
        _sample(_epoch(2026, 10, 23, 12), {"card": {"Todo": 2}}),
    ]
    result = cfd(samples, COLUMNS)
    assert [p["day"] for p in result["points"]] == ["2026-10-20", "2026-10-23"]
    assert result["points"][1]["throughput"] is None


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
