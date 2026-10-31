"""The flow history keeps each card's spec and wave, and the cumulative flow and the control chart filter by the bar's values (SPEC 889, #904).

The sample names a card's kind, spec and wave in `cells`. A sample written before that carries no `cells`: it answers no
spec and no wave, and the chart says so. The bar's values are the keys the board filter reads: a spec's number or `none`,
a wave's number or `none`, an empty list for all.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
FLOW_PY = SCRIPTS / "board_flow_history.py"
CFD_PY = SCRIPTS / "board_cfd.py"
OPENED = "2026-10-01T09:00:00Z"
COLUMNS = ["Todo", "Doing", "Closed"]


def _load(name, path):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _epoch(day):
    return int(datetime(2026, 10, day, 12, tzinfo=UTC).timestamp())


def _row(n, st="Todo", pa=None, labels=()):
    row = {"n": n, "t": f"card {n}", "s": "OPEN", "c": None, "st": st, "l": list(labels), "o": OPENED}
    if pa is not None:
        row["pa"] = pa
    return row


def _sample(day, cells):
    """A sample as board_flow_history writes it: the cells, and the counts, frozen and none they add up to."""
    counts, frozen, none = {}, 0, 0
    for kind, _spec, _wave, column, fz, n in cells:
        if column is None:
            none += n
        else:
            counts.setdefault(kind, {})[column] = counts.get(kind, {}).get(column, 0) + n
        if fz:
            frozen += n
    return {"t": _epoch(day), "counts": counts, "frozen": frozen, "none": none, "cells": [list(c) for c in cells]}


def _legacy(day, counts):
    """A sample written before the change: no cells."""
    return {"t": _epoch(day), "counts": counts, "frozen": 0, "none": 0}


@pytest.fixture(scope="module")
def flow():
    return _load("board_flow_history_filters", FLOW_PY)


@pytest.fixture(scope="module")
def cfd():
    return _load("board_cfd_filters", CFD_PY).cfd


# -- the sample format -------------------------------------------------------------------------------------------------

def test_a_sample_names_each_cards_kind_spec_and_wave(flow):
    s = flow.sample([_row(1, pa=5, labels=["wave:04", "bug"])], now=_epoch(20))
    assert ["issue", "5", "4", "Todo", 0, 1] in s["cells"]


def test_a_card_with_no_spec_and_no_wave_is_none(flow):
    s = flow.sample([_row(1)], now=_epoch(20))
    assert ["issue", "none", "none", "Todo", 0, 1] in s["cells"]


def test_the_lowest_wave_label_names_the_wave_without_leading_zeros(flow):
    s = flow.sample([_row(1, labels=["wave:10", "wave:02"])], now=_epoch(20))
    assert ["issue", "none", "2", "Todo", 0, 1] in s["cells"]


def test_a_card_in_no_column_has_no_column_name_in_its_cell(flow):
    s = flow.sample([_row(1, st="Nowhere")], now=_epoch(20))
    assert ["issue", "none", "none", None, 0, 1] in s["cells"]


def test_the_cells_add_up_to_the_counts_the_flow_already_keeps(flow):
    rows = [_row(1, pa=5, labels=["wave:1"]), _row(2), _row(3, st="Nowhere")]
    s = flow.sample(rows, now=_epoch(20))
    in_columns = sum(c[5] for c in s["cells"] if c[3] is not None)
    assert in_columns == sum(n for by in s["counts"].values() for n in by.values())
    assert sum(c[5] for c in s["cells"] if c[3] is None) == s["none"]


def test_the_spec_and_wave_keys_are_the_values_the_board_filter_offers(flow):
    assert flow.spec_key({"pa": None}) == "none"
    assert flow.spec_key({"pa": 5}) == "5"
    assert flow.wave_key(["bug"]) == "none"
    assert flow.wave_key(["wave:04"]) == "4"


# -- the cumulative flow's filter --------------------------------------------------------------------------------------

def test_a_spec_filter_keeps_only_that_specs_cards(cfd):
    samples = [_sample(20, [["issue", "5", "none", "Todo", 0, 2], ["issue", "6", "none", "Todo", 0, 3]])]
    assert cfd(samples, COLUMNS, spec=["5"])["points"][0]["bands"]["Todo"] == 2


def test_a_wave_filter_keeps_only_that_waves_cards(cfd):
    samples = [_sample(20, [["issue", "none", "4", "Todo", 0, 2], ["issue", "none", "5", "Todo", 0, 3]])]
    assert cfd(samples, COLUMNS, wave=["4"])["points"][0]["bands"]["Todo"] == 2


def test_spec_and_wave_filters_combine_with_and(cfd):
    samples = [_sample(20, [
        ["issue", "5", "4", "Todo", 0, 1],
        ["issue", "5", "none", "Todo", 0, 2],
        ["issue", "6", "4", "Todo", 0, 4],
    ])]
    assert cfd(samples, COLUMNS, spec=["5"], wave=["4"])["points"][0]["bands"]["Todo"] == 1
    assert cfd(samples, COLUMNS, spec=["5", "6"], wave=["4"])["points"][0]["bands"]["Todo"] == 5


def test_an_empty_filter_list_keeps_every_card(cfd):
    samples = [_sample(20, [["issue", "5", "4", "Todo", 0, 1], ["issue", "none", "none", "Todo", 0, 6]])]
    assert cfd(samples, COLUMNS, spec=[], wave=[])["points"][0]["bands"]["Todo"] == 7


def test_a_filter_takes_the_frozen_and_no_column_cards_of_the_kept_cards(cfd):
    samples = [_sample(20, [
        ["issue", "5", "none", "Todo", 1, 2],
        ["issue", "6", "none", "Todo", 0, 1],
        ["issue", "5", "none", None, 0, 1],
    ])]
    bands = cfd(samples, COLUMNS, spec=["5"])["points"][0]["bands"]
    assert bands["Todo"] == 2
    assert bands["frozen"] == 2
    assert bands["No column"] == 1


def test_the_kind_filter_still_holds_with_cells(cfd):
    samples = [_sample(20, [["issue", "none", "none", "Todo", 0, 2], ["pr", "none", "none", "Todo", 0, 3]])]
    assert cfd(samples, COLUMNS, kind="pr")["points"][0]["bands"]["Todo"] == 3


def test_a_sample_without_cells_answers_no_spec_and_no_wave(cfd):
    legacy = [_legacy(20, {"card": {"Todo": 4}})]
    assert cfd(legacy, COLUMNS, spec=["5"])["points"][0]["bands"]["Todo"] == 0
    assert cfd(legacy, COLUMNS, wave=["none"])["points"][0]["bands"]["Todo"] == 4
    assert cfd(legacy, COLUMNS)["points"][0]["bands"]["Todo"] == 4


def test_a_day_read_from_a_sample_without_cells_is_flagged(cfd):
    samples = [_legacy(20, {"card": {"Todo": 4}}), _sample(21, [["issue", "5", "none", "Todo", 0, 1]])]
    points = cfd(samples, COLUMNS)["points"]
    assert points[0].get("legacy") is True
    assert not points[1].get("legacy")


def test_cells_and_counts_give_the_same_bands_when_nothing_is_filtered(cfd):
    with_cells = _sample(20, [["issue", "5", "4", "Todo", 1, 2], ["issue", "none", "none", "Doing", 0, 1],
                              ["issue", "none", "none", None, 0, 3]])
    without = {k: v for k, v in with_cells.items() if k != "cells"}
    assert cfd([with_cells], COLUMNS)["points"][0]["bands"] == cfd([without], COLUMNS)["points"][0]["bands"]


# -- the hub: the board read takes the bar's values, and the page carries them ---------------------------------------

@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("hub_flow_filters")
    for name in ("checkout", "hub", "state"):
        (base / name).mkdir()
    mp.setenv("SG_REPO", "x/y")
    mp.setenv("SG_OWNER", "x")
    mp.setenv("SG_PROJECT", "4")
    mp.setenv("SG_CHECKOUT", str(base / "checkout"))
    mp.setenv("SG_HUB_DIR", str(base / "hub"))
    mp.setenv("SG_STATE", str(base / "state"))
    mp.delenv("SG_BOARD_CACHE", raising=False)
    try:
        yield _load("hub_flow_filters", HUB_PY)
    finally:
        mp.undo()


@pytest.fixture
def board(hub, tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "BOARD", tmp_path / "hub-board.json")
    monkeypatch.setattr(hub, "board_cache", {"t": time.time(), "rt": None, "items": [], "prs": [], "cases": [],
                                             "errs": [], "lc": {}, "err": None})
    monkeypatch.setattr(hub, "CHARTS", {})
    return tmp_path


def _item(hub_mod, n, st, pa=None, labels=(), closed_at=None):
    row = {"n": n, "i": f"PVT_{n}", "t": f"card {n}", "s": "CLOSED" if closed_at else "OPEN", "c": closed_at,
           "st": st, "k": None, "l": list(labels), "si": {"total": None, "completed": None}, "o": OPENED}
    if pa is not None:
        row["pa"] = pa
    return row


def _write_history(hub_mod, items, day=20):
    flow_mod = hub_mod._flow
    flow_mod.append(flow_mod.history_path(hub_mod.BOARD, hub_mod.REPO), items,
                    now=datetime(2026, 10, day, 12, tzinfo=UTC).timestamp())


def _backlog_counts(cfd):
    return {b["name"]: b for b in cfd["cfd"]["bands"]}


def test_the_flow_history_keeps_each_cards_spec_and_wave(hub, board):
    items = [_item(hub, 1, hub.COLUMN["unscheduled"], pa=5, labels=["wave:4"])]
    _write_history(hub, items)
    line = hub._flow.history_path(hub.BOARD, hub.REPO).read_text().splitlines()[-1]
    assert ["issue", "5", "4", hub.COLUMN["unscheduled"], 0, 1] in json.loads(line)["cells"]


def test_the_chart_route_filters_the_cumulative_flow_by_the_bar_values(hub, board):
    backlog = hub.COLUMN["unscheduled"]
    items = [_item(hub, 1, backlog, pa=5, labels=["wave:4"]),
             _item(hub, 2, backlog),
             _item(hub, 3, backlog, pa=6, labels=["wave:4"])]
    hub.board_cache["items"] = items
    _write_history(hub, items)

    def last(fw=(), fs=()):
        return _backlog_counts(hub.flow_cfd(list(fw), list(fs)))[backlog]["count"][-1]

    assert last() == 3
    assert last(fs=["5"]) == 1
    assert last(fw=["4"]) == 2
    assert last(fw=["4"], fs=["5"]) == 1
    assert last(fw=["none"]) == 1


def test_the_control_chart_takes_the_same_spec_filter(hub, board):
    closed = hub.COLUMN["issue_closed"]
    items = [_item(hub, 1, closed, pa=5, closed_at="2026-10-02T15:00:00Z"),
             _item(hub, 2, closed, pa=6, closed_at="2026-10-03T15:00:00Z")]
    hub.board_cache["items"] = items

    def dots(fs):
        return sorted(d["n"] for d in hub.flow_lead(fs=fs)["lead"]["control"]["jira"]["all"]["dots"])

    assert dots([]) == [1, 2]
    assert dots(["5"]) == [1]


def test_the_board_query_names_the_bar_values(hub):
    assert hub.board_filters("fw=4,none&fs=5") == (["4", "none"], ["5"])
    assert hub.board_filters("") == ([], [])


def test_the_page_sends_the_bar_values_with_the_board_read_both_charts_and_the_zoom(hub):
    assert "const barQuery=" in hub.PAGE, "the bar's values are written once, outside the flow block"
    assert "fetch(`/api/board?${barQuery()}`)" in hub.PAGE
    opener = next(line for line in hub.PAGE.splitlines() if line.startswith("const flowOpen="))
    assert "/api/chart/${k}?" in opener and "barQuery()" in opener, "each chart route reads the bar's values"
    zoom = next(line for line in hub.PAGE.splitlines() if line.startswith("const ltZoom="))
    assert "${barQuery()}" in zoom


def test_a_filter_change_drops_a_stale_zoom_and_both_stale_charts(hub):
    pick = next(line for line in hub.PAGE.splitlines() if line.startswith("const pickFilter="))
    assert pick.count("barMoved()") == 2, "a pick and a clear both tell the charts"
    moved = next(line for line in hub.PAGE.splitlines() if line.startswith("const barMoved="))
    assert all(part in moved for part in ("LT.zc=null", "LT.zr=null", "FLOWD={}", "flowOpen(FLOWK)"))


def test_both_chart_routes_read_the_bar_values_from_the_query():
    src = HUB_PY.read_text()
    for route, fn in (("/api/chart/cfd", "flow_cfd"), ("/api/chart/lead", "flow_lead")):
        served = next(line for line in src.splitlines() if f'startswith("{route}")' in line)
        assert f'{fn}(*board_filters(self.path.partition("?")[2]))' in served


def _flow_block(hub):
    m = re.search(r"// flow-charts:\n(.*?)// flow-charts:end", hub.PAGE, re.S)
    assert m, "the page carries no flow-charts block"
    return m.group(1)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_chart_says_which_days_answer_no_spec_and_no_wave(hub, board, tmp_path):
    history = hub._flow.history_path(hub.BOARD, hub.REPO)
    history.write_text(json.dumps({"t": _epoch(20), "counts": {"issue": {hub.COLUMN["unscheduled"]: 2}},
                                   "frozen": 0, "none": 0}) + "\n")
    cfd = hub.flow_cfd()["cfd"]
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join(["const COLORS={},COLNAME={};", "const esc=s=>String(s);", block,
                         f"process.stdout.write(flowCFD({json.dumps(cfd)}));"])
    path = tmp_path / "flow-cfd-legacy.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "answer no spec and no wave" in result.stdout
