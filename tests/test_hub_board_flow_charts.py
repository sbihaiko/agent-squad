"""The TASKS header opens two diagrams: the cumulative flow bands and the lead-time control chart (SPEC 111, #712).

The hub computes every number with board_cfd and board_leadtime; the page draws them and does no counting. Both
diagrams ride the board read the hub already makes: no GitHub call, no timer, no process of their own.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
OPENED = "2026-10-01T09:00:00Z"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("hub_flow_charts")
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
        yield _load("hub_flow_charts", HUB_PY)
    finally:
        mp.undo()


@pytest.fixture
def board(hub, tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "BOARD", tmp_path / "hub-board.json")
    monkeypatch.setattr(hub, "board_cache", {"t": time.time(), "rt": None, "items": [], "prs": [], "cases": [],
                                             "errs": [], "lc": {}, "err": None})
    return tmp_path


def _card(n, st="Todo", s="OPEN", c=None, o=OPENED, labels=()):
    return {"n": n, "i": f"PVT_{n}", "t": f"card {n}", "s": s, "c": c, "st": st, "k": None, "l": list(labels),
            "si": {"total": None, "completed": None}, "o": o}


def _sample(day, counts, frozen=0, none=0):
    return {"t": int(datetime(2026, 10, day, 12, tzinfo=UTC).timestamp()), "counts": counts, "frozen": frozen, "none": none}


def _history(directory, samples, repo="x_y"):
    (directory / f"hub-flow.{repo}.jsonl").write_text("".join(json.dumps(s) + "\n" for s in samples))


def _flow_block(hub):
    """The page's diagram script, between its two markers."""
    m = re.search(r"// flow-charts:\n(.*?)// flow-charts:end", hub.PAGE, re.S)
    assert m, "the page carries no flow-charts block"
    return m.group(1)


def test_tasks_header_has_flow_and_lead_time_buttons_and_keeps_its_controls(hub):
    head = hub.PAGE[hub.PAGE.index("<div id=bhead>"):hub.PAGE.index('<div id="pip"', hub.PAGE.index("<div id=bhead>"))]
    assert re.search(r"<button id=bflow[^>]*>Flow</button>", head)
    assert re.search(r"<button id=blead[^>]*>Lead time</button>", head)
    assert "<b>TASKS <span id=bt class=m></span></b>" in head
    assert '<summary title="filter cards by label">labels</summary>' in head
    assert 'id=bsp' in head
    assert 'id=bz title="maximize the board over the run pane"' in head
    assert 'id=bx title="collapse"' in head


def test_cfd_bands_follow_the_hub_order_and_name_the_start_of_history(hub, board):
    _history(board, [
        _sample(1, {"issue": {hub.COLUMN["unscheduled"]: 2, hub.COLUMN["issue_closed"]: 1}}),
        _sample(2, {"issue": {hub.COLUMN["unscheduled"]: 1, hub.COLUMN["issue_closed"]: 2}}),
    ])
    cfd = hub.board()["flow"]["cfd"]
    assert [b["name"] for b in cfd["bands"]] == hub.ORDER
    assert cfd["start"] == "2026-10-01"
    assert len(cfd["days"]) == 2
    assert "history from " in hub.PAGE


def test_cfd_band_extents_are_the_board_cfd_counts(hub, board):
    _history(board, [_sample(1, {"issue": {hub.COLUMN["unscheduled"]: 2, hub.COLUMN["issue_closed"]: 1}})])
    bands = {b["name"]: b for b in hub.board()["flow"]["cfd"]["bands"]}
    backlog = bands[hub.COLUMN["unscheduled"]]
    closed = bands[hub.COLUMN["issue_closed"]]
    assert backlog["upper"][0] - backlog["lower"][0] == 2
    assert closed["upper"][0] - closed["lower"][0] == 1
    assert closed["lower"][0] == 0


def test_another_repositorys_history_is_not_drawn(hub, board):
    _history(board, [_sample(1, {"issue": {hub.COLUMN["unscheduled"]: 5}})], repo="x_other")
    cfd = hub.board()["flow"]["cfd"]
    assert cfd["start"] is None
    assert cfd["bands"] == []


def test_no_history_file_says_no_sample_and_draws_no_band(hub, board):
    cfd = hub.board()["flow"]["cfd"]
    assert cfd["start"] is None
    assert cfd["days"] == []
    assert cfd["bands"] == []
    assert "the flow history has no sample yet" in hub.PAGE


def test_lead_time_summary_measure_window_and_excluded_count(hub, board):
    board_cache = hub.board_cache
    board_cache["items"] = [
        _card(1, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-01T15:00:00Z"),
        _card(2, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-02T09:00:00Z"),
        _card(3, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-04T09:00:00Z"),
        _card(4, st="Todo", s="OPEN", c=None),
    ]
    lead = hub.board()["flow"]["lead"]
    assert lead["measure"] == "opened -> closed"
    assert lead["window"] == {"size": 20, "unit": "issues"}
    assert lead["summary"]["average"]["text"] == "1d 10h"
    assert lead["summary"]["median"]["text"] == "1d"
    assert lead["summary"]["minimum"]["text"] == "6h"
    assert lead["summary"]["maximum"]["text"] == "3d"
    assert lead["excluded"] == [{"reason": "open", "count": 1}]
    assert lead["excluded_total"] == 1


def test_opening_the_diagrams_makes_no_gh_call(hub, board, monkeypatch):
    board_cache = hub.board_cache
    board_cache["items"] = [_card(1, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-02T09:00:00Z")]
    _history(board, [_sample(1, {"issue": {hub.COLUMN["unscheduled"]: 1}})])
    calls = []

    def gh_fails(cmd, **kw):
        calls.append(cmd)
        raise FileNotFoundError("gh is not on PATH")

    monkeypatch.setattr(hub.subprocess, "run", gh_fails)
    flow = hub.board()["flow"]
    assert calls == []
    assert flow["cfd"]["bands"] and flow["lead"]["summary"] is not None
    assert 'id=flowov hidden' in hub.PAGE


def _skip(day):
    return {"flow_skip": 2, "t": int(datetime(2026, 10, day, 12, tzinfo=UTC).timestamp()), "reason": "items read incomplete"}


def test_a_quiet_day_with_a_whole_read_is_a_point(hub, board):
    _history(board, [
        _sample(1, {"issue": {hub.COLUMN["unscheduled"]: 2}}),
        _sample(2, {"issue": {hub.COLUMN["unscheduled"]: 2}}),
        _sample(3, {"issue": {hub.COLUMN["unscheduled"]: 1}}),
    ])
    cfd = hub.board()["flow"]["cfd"]
    assert len(cfd["days"]) == 3, "the calendar runs over the quiet day, so points sit by date"
    for band in cfd["bands"]:
        assert band["upper"][1] is not None and band["lower"][1] is not None, "the quiet day has its own sample: a point"
    assert [b["upper"][1] for b in cfd["bands"] if b["name"] == hub.COLUMN["unscheduled"]] == [2]


def test_a_day_with_no_record_at_all_is_a_gap_and_never_a_carried_value(hub, board):
    _history(board, [
        _sample(1, {"issue": {hub.COLUMN["unscheduled"]: 2}}),
        _sample(3, {"issue": {hub.COLUMN["unscheduled"]: 1}}),
    ])
    cfd = hub.board()["flow"]["cfd"]
    assert len(cfd["days"]) == 3
    for band in cfd["bands"]:
        assert band["upper"][1] is None and band["lower"][1] is None, "no record: the hub was down, its count is unknown"


def test_a_skipped_read_is_a_calendar_day_with_no_band_values(hub, board):
    _history(board, [
        _sample(1, {"issue": {hub.COLUMN["unscheduled"]: 2}}),
        _skip(2),
        _sample(3, {"issue": {hub.COLUMN["unscheduled"]: 1}}),
    ])
    cfd = hub.board()["flow"]["cfd"]
    assert len(cfd["days"]) == 3
    for band in cfd["bands"]:
        assert band["upper"][1] is None and band["lower"][1] is None, "a gap carries no value to interpolate"
        assert band["upper"][0] is not None and band["upper"][2] is not None


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_one_point_run_is_drawn_as_a_visible_mark(hub, tmp_path):
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={};", "const esc=s=>String(s);", block,
        'process.stdout.write(flowCFD({days:["a","b","c"],ymax:4,start:"2026-10-01",'
        'bands:[{name:"Todo",lower:[0,null,0],upper:[2,null,1]}]}));',
    ])
    path = tmp_path / "flow-one.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    polygons = [[float(p.split(",")[0]) for p in m.split()] for m in re.findall(r'points="([^"]*)"', result.stdout)]
    assert len(polygons) == 2
    for xs in polygons:
        assert max(xs) - min(xs) > 0, "a one-point run has width: it is a mark, not a zero-width line"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_splits_each_band_at_a_gap(hub, tmp_path):
    block = _flow_block(hub).split("const drawFlow")[0]
    drawing = "\n".join([
        "const COLORS={};", "const esc=s=>String(s);", block,
        'process.stdout.write(flowCFD({days:["a","b","c","d","e"],ymax:4,start:"2026-10-01",'
        'bands:[{name:"Todo",lower:[0,0,null,0,0],upper:[2,3,null,1,2]}]}));',
    ])
    path = tmp_path / "flow-cfd.js"
    path.write_text(drawing)
    result = subprocess.run(["node", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    polygons = [[float(p.split(",")[0]) for p in m.split()] for m in re.findall(r'points="([^"]*)"', result.stdout)]
    assert len(polygons) == 2, "one polygon per run of sampled days, never one across the gap"
    x = {i: 36 + i * (800 - 2 * 36) / 4 for i in range(5)}
    assert all(v in (x[0], x[1]) for v in polygons[0]) and all(v in (x[3], x[4]) for v in polygons[1])


def test_page_draws_the_hub_numbers_and_does_no_arithmetic(hub, board):
    block = _flow_block(hub)
    for forbidden in ("reduce(", "Math.", "+=", "-=", "fmean(", "median(", "average(", "saveB(", "bo.", "#bbody"):
        assert forbidden not in block, f"the flow script names {forbidden}"


def test_drawn_cfd_and_lead_time_equal_what_board_cfd_and_board_leadtime_return(hub, board):
    cfd_mod = _load("board_cfd_check", SCRIPTS / "board_cfd.py")
    lt_mod = _load("board_leadtime_check", SCRIPTS / "board_leadtime.py")
    samples = [
        _sample(1, {"issue": {hub.COLUMN["unscheduled"]: 2, hub.COLUMN["issue_closed"]: 1}}),
        _sample(2, {"issue": {hub.COLUMN["unscheduled"]: 1, hub.COLUMN["issue_closed"]: 2}}),
    ]
    _history(board, samples)
    items = [_card(1, st=hub.COLUMN["issue_closed"], s="CLOSED", c="2026-10-01T15:00:00Z"),
             _card(2, st="Todo", s="OPEN", c=None)]
    hub.board_cache["items"] = items

    flow = hub.board()["flow"]
    direct = cfd_mod.cfd(samples, hub.ORDER)
    for band in flow["cfd"]["bands"]:
        for i, point in enumerate(direct["points"]):
            assert band["upper"][i] - band["lower"][i] == point["bands"][band["name"]]

    direct_lead = lt_mod.lead_time([
        {"n": 1, "column": hub.COLUMN["issue_closed"], "c": "2026-10-01T15:00:00Z", "o": OPENED},
        {"n": 2, "column": "Todo", "c": None, "o": OPENED},
    ])
    assert flow["lead"]["summary"]["average"]["hours"] == direct_lead["summary"]["average"]["hours"]
    assert flow["lead"]["excluded"] == [{"reason": "open", "count": 1}]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_page_script_parses(hub, tmp_path):
    scripts = re.findall(r"<script>(.*?)</script>", hub.PAGE, re.S)
    assert scripts
    for i, text in enumerate(scripts):
        path = tmp_path / f"hub-page-{i}.js"
        path.write_text(text)
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
