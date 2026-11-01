"""The Throughput tab's burndown: the hub answers one open spec's series, and the page draws it with its gaps and guide (#1048)."""
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
OPENED = "2026-10-01T09:00:00Z"
TODAY = date(2026, 10, 21)


def _load(name, path):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("hub_burndown")
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
        yield _load("hub_burndown", HUB_PY)
    finally:
        mp.undo()


@pytest.fixture
def board(hub, tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "BOARD", tmp_path / "hub-board.json")
    monkeypatch.setattr(hub, "board_cache", {"t": time.time(), "rt": None, "items": [], "prs": [], "cases": [],
                                             "errs": [], "lc": {}, "err": None})
    monkeypatch.setattr(hub, "CHARTS", {})
    return tmp_path


def _item(hub, n, st, pa=None, labels=(), closed_at=None):
    row = {"n": n, "i": f"PVT_{n}", "t": f"card {n}", "s": "CLOSED" if closed_at else "OPEN", "c": closed_at,
           "st": st, "k": None, "l": list(labels), "si": {"total": None, "completed": None}, "o": OPENED}
    if pa is not None:
        row["pa"] = pa
    return row


def _epoch(day):
    return datetime(2026, 10, day, 12, tzinfo=UTC).timestamp()


def _write_reads(hub, items, days):
    for day in days:
        hub._flow.append(hub._flow.history_path(hub.BOARD, hub.REPO), items, now=_epoch(day))


def _at(day):
    return f"2026-10-{day:02d}T12:00:00Z"


def _store(hub):
    """Child 2 joins the spec on the 19th (no read that day) and closes on the 21st; child 3 joins on the 20th."""
    path = hub._hist.history_path(hub.BOARD, hub.REPO)
    hub._hist.record_issue(path, 2, OPENED, OPENED,
                           [{"t": _at(19), "type": "spec", "spec": 1045}, {"t": _at(21), "type": "closed"}], True, _epoch(21))
    hub._hist.record_issue(path, 3, OPENED, OPENED, [{"t": _at(20), "type": "spec", "spec": 1045}], True, _epoch(21))


def _items(hub):
    backlog = hub.COLUMN["unscheduled"]
    return [_item(hub, 1045, backlog, labels=["spec"]),
            _item(hub, 1000, backlog, labels=["spec"], closed_at=OPENED),
            _item(hub, 2, backlog, pa=1045, closed_at=_at(21)),
            _item(hub, 3, backlog, pa=1045)]


def _burn(hub, items, spec, days=(20, 21), today=TODAY):
    hub.board_cache["items"] = items
    _write_reads(hub, items, days)
    _store(hub)
    return hub._burndown_payload(spec, today=today)


def test_the_burndown_names_the_open_specs_and_answers_the_selected_one(hub, board):
    payload = _burn(hub, _items(hub), 1045)
    assert payload["specs"] == [{"n": 1045, "t": "card 1045"}], "a closed spec is not offered"
    assert payload["spec"] == 1045
    burn = payload["burn"]
    assert [(p["day"], p["remaining"], p["closed"], p["added"]) for p in burn["points"]] == [
        ("2026-10-20", 2, 0, 2), ("2026-10-21", 1, 1, 0)]
    assert burn["gaps"] == ["2026-10-19"], "a day with no whole read is a gap, never a zero"


def test_a_point_names_the_tickets_that_closed_and_joined_that_day(hub, board):
    burn = _burn(hub, _items(hub), 1045)["burn"]
    by_day = {p["day"]: p for p in burn["points"]}
    assert by_day["2026-10-21"]["closed_tickets"] == [2]
    assert by_day["2026-10-20"]["added_tickets"] == [2, 3], "a ticket that joined on a gap day is named on the next point"


def test_the_numbers_under_the_chart_agree_with_the_line(hub, board):
    burn = _burn(hub, _items(hub), 1045)["burn"]
    assert (burn["remaining"], burn["as_of"]) == (1, "2026-10-21")
    assert (burn["closed_week"], burn["added_week"]) == (1, 2)
    assert burn["guide"] == {"source": "trailing", "from": {"day": "2026-10-21", "remaining": 1},
                             "to": {"day": "2026-10-28", "remaining": 0}}


def test_a_spec_with_no_child_answers_its_reason(hub, board):
    items = [_item(hub, 1045, hub.COLUMN["unscheduled"], labels=["spec"])]
    hub.board_cache["items"] = items
    _write_reads(hub, items, (20,))
    burn = hub._burndown_payload(1045, today=TODAY)["burn"]
    assert burn["points"] == []
    assert burn["reason"] == "no child ticket"


def test_the_server_answers_the_burndown_route_and_the_page_calls_it():
    source = HUB_PY.read_text()
    assert 'self.path.startswith("/api/chart/burndown")' in source
    assert "const flowBurn=" in source
    assert "id=flowv" in source, "the burndown is a view of the Throughput tab, never a fourth tab"


def _page_block(hub, prefix):
    page = hub.PAGE.splitlines()
    i = next(k for k, line in enumerate(page) if line.startswith(prefix))
    block = [page[i]]
    for line in page[i + 1:]:
        if not line.startswith(" "):
            break
        block.append(line)
    return "\n".join(block)


def _draw(hub, payload):
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    lines = "\n".join(_page_block(hub, p) for p in ("let flowW=", "const flowX=", "const flowY=", "const flowViews=",
                                                    "const flowBurn="))
    script = f"const esc=t=>String(t);\n{lines}\nconsole.log(flowBurn({json.dumps(payload)}));"
    result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_the_page_draws_the_gap_the_guide_and_the_numbers(hub, board):
    out = _draw(hub, _burn(hub, _items(hub), 1045))
    assert "2026-10-19: no whole read this day" in out, "a gap is drawn and named"
    assert "2026-10-21: 1 remaining" in out and "closed that day #2" in out, "a point names its count and its closed tickets"
    assert "stroke-dasharray" in out, "the guide is a dashed line"
    assert "remaining 1 as of 2026-10-21" in out
    assert "closed this week 1" in out and "added this week 2" in out
    assert "guide to 2026-10-28" in out
