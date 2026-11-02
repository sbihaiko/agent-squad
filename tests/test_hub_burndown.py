"""The Throughput tab's burndown: the hub answers one open spec's series, and the page draws it with its gaps and guide (#1048)."""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import UTC, date, datetime
from http.server import ThreadingHTTPServer
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
    monkeypatch.setattr(hub, "sub_issues", lambda issue, repo: ([], None))
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


def _sub(number, created, closed=None):
    return {"number": number, "created_at": created, "closed_at": closed, "state": "closed" if closed else "open",
            "repository_url": "https://api.github.com/repos/x/y"}


def test_a_spec_with_sub_issues_draws_the_burndown_from_their_instants(hub, board, monkeypatch):
    children = [_sub(1064, _at(19), _at(21)), _sub(1065, _at(20), _at(22))]
    monkeypatch.setattr(hub, "sub_issues", lambda issue, repo: (children, None))
    _write_reads(hub, [], (19, 20, 21, 22))
    burn = hub.flow_burn(1063)["burn"]
    assert [(p["day"], p["remaining"], p["added"], p["closed"]) for p in burn["points"]] == [
        ("2026-10-19", 1, 1, 0), ("2026-10-20", 2, 1, 0), ("2026-10-21", 1, 0, 1), ("2026-10-22", 0, 0, 1)]
    assert burn["reason"] is None and "err" not in burn


def test_a_failed_sub_issue_read_on_the_burndown_is_named_apart(hub, board, monkeypatch):
    monkeypatch.setattr(hub, "sub_issues", lambda issue, repo: (None, "sub-issues: refused"))
    burn = hub._burndown_payload(1063, today=TODAY)["burn"]
    assert burn["points"] == [] and burn["err"] == "sub-issues: refused"
    assert burn["reason"] != "no child ticket"


def test_the_cache_reads_anew_when_the_history_changes_on_the_same_board_read(hub, board):
    _burn(hub, _items(hub), 1045)
    hub.CHARTS.clear()
    first = hub.flow_burn(1045)["burn"]
    assert first["remaining"] == 1
    hub._hist.record_issue(hub._hist.history_path(hub.BOARD, hub.REPO), 3, OPENED, OPENED,
                           [{"t": _at(20), "type": "spec", "spec": 1045}, {"t": _at(22), "type": "closed"}], True, _epoch(22))
    _write_reads(hub, hub.board_cache["items"], (22,))
    again = hub.flow_burn(1045)["burn"]
    assert (again["remaining"], again["as_of"]) == (0, "2026-10-22"), "a stale answer names the old count and day"


NODE = shutil.which("node")


def _page_url(hub):
    """The URL the page's Burndown view fetches, built by the page's own flowOpen."""
    assert NODE is not None, "node is a test dependency of the burndown page"
    lines = "\n".join(_page_block(hub, p) for p in ("let FLOWK=", "let flowW=", "const flowX=", "const flowY=",
                                                    "const flowBurn=", "const drawFlow=", "const flowOpen="))
    script = (f"const calls=[];globalThis.fetch=async u=>{{calls.push(u);return {{json:async()=>({{}})}}}};"
              "const el={hidden:true,clientWidth:800,innerHTML:''};const $=()=>el;const esc=t=>String(t);"
              "const barQuery=()=>'';let BURN={s:1045};const flowTabs=()=>{};\n"
              f"{lines}\nflowOpen(VIEW).then(()=>console.log(calls[0]));")
    return subprocess.run([NODE, "-e", script.replace("VIEW", json.dumps(_view_key(hub)))], capture_output=True, text=True,
                          timeout=30).stdout.strip()


def _view_key(hub):
    """The Burndown tab's own key: one tab per chart (#1080), opened by the tab itself."""
    assert 'flowOpen("burndown")' in hub.PAGE
    return "burndown"


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return r.headers.get("Content-Type"), r.read().decode()


def test_the_page_fetches_the_route_the_server_answers(hub, board):
    _burn(hub, _items(hub), 1045)
    url = _page_url(hub)
    assert url.startswith("/api/chart/burndown?"), f"the page fetched {url!r}"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ctype, body = _get(srv.server_port, url)
    finally:
        srv.shutdown()
        srv.server_close()
    assert ctype == "application/json", "the page's fetch reached the page, not the chart"
    assert json.loads(body)["spec"] == 1045


def test_the_burndown_is_its_own_throughput_tab(hub):
    """Owner, #1080: one tab per chart, so the burndown is never drawn inside the cumulative flow."""
    assert 'id=bburn' in hub.PAGE and 'id=flowv' not in hub.PAGE
    assert 'flowOpen("burndown")' in hub.PAGE, "the Burndown tab opens the burndown view"


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
    assert NODE is not None, "node is a test dependency of the burndown page"
    lines = "\n".join(_page_block(hub, p) for p in ("let flowW=", "const flowX=", "const flowY=",
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


def test_a_child_on_both_the_store_and_the_relation_is_counted_once(hub, board, monkeypatch):
    path = hub._hist.history_path(hub.BOARD, hub.REPO)
    hub._hist.record_issue(path, 1064, OPENED, OPENED, [{"t": _at(19), "type": "spec", "spec": 1063}], True, _epoch(19))
    monkeypatch.setattr(hub, "sub_issues", lambda issue, repo: ([_sub(1064, _at(19), _at(21)), _sub(1065, _at(20), _at(22))], None))
    _write_reads(hub, [], (19, 20, 21, 22))
    burn = hub.flow_burn(1063)["burn"]
    assert [(p["day"], p["remaining"], p["added"], p["closed"]) for p in burn["points"]] == [
        ("2026-10-19", 1, 1, 0), ("2026-10-20", 2, 1, 0), ("2026-10-21", 1, 0, 1), ("2026-10-22", 0, 0, 1)]


def test_both_chart_routes_read_a_specs_children_once_per_board_read(hub, board, monkeypatch):
    reads = []

    def children(issue, repo):
        reads.append(issue)
        return [_sub(1064, _at(19), _at(21))], None

    monkeypatch.setattr(hub, "sub_issues", children)
    _write_reads(hub, [], (19, 20, 21))
    hub.flow_burnup(1063)
    hub.flow_burn(1063)
    assert reads == [1063]


def test_a_failed_sub_issue_read_is_not_kept_for_the_next_request(hub, board, monkeypatch):
    monkeypatch.setattr(hub, "sub_issues", lambda issue, repo: (None, "sub-issues: refused"))
    assert hub.flow_burnup(1063)["err"] == "sub-issues: refused"
    monkeypatch.setattr(hub, "sub_issues", lambda issue, repo: ([_sub(1064, _at(19), _at(21))], None))
    _write_reads(hub, [], (19, 20, 21))
    assert hub.flow_burnup(1063)["points"] != []
