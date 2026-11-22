"""A card labelled `chart:exclude` stays out of every hub chart (#2743).

The owner marks a card that must never enter a lead-time dot, a control-chart mark or a cumulative-flow band; the label
keeps the issue, its card and its history unchanged. The label set is read the way the cumulative flow's `moved` filter
reads it (#1663). Each criterion is proved through the chart payload the hub serves, never through a helper's private read.
"""
import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
OPENED = "2026-10-01T09:00:00Z"
LABEL = "chart:exclude"


def _load(name, path):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _chart_days_end_in_utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("hub_chart_exclude")
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
        yield _load("hub_chart_exclude", HUB_PY)
    finally:
        mp.undo()


@pytest.fixture
def board(hub, tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "BOARD", tmp_path / "hub-board.json")
    monkeypatch.setattr(hub, "CHARTS", {})
    monkeypatch.setattr(hub, "board_cache", {"t": time.time(), "rt": None, "items": [], "prs": [], "cases": [],
                                             "errs": [], "lc": {}, "err": None})
    return tmp_path


def _card(n, st="Todo", s="OPEN", c=None, o=OPENED, labels=()):
    return {"n": n, "i": f"PVT_{n}", "t": f"card {n}", "s": s, "c": c, "st": st, "k": None, "l": list(labels),
            "si": {"total": None, "completed": None}, "o": o}


def _life(opened, *steps):
    """A card created in Todo at `opened`, then each (instant, status) step; a step into Closed also closes the card."""
    events = [{"t": opened, "type": "added"}, {"t": opened, "type": "status", "status": "Todo"}]
    for at, status in steps:
        events.append({"t": at, "type": "status", "status": status})
        if status == "Closed":
            events.append({"t": at, "type": "closed"})
    return events


def _done(n, closed_at, labels=(), events=()):
    return (_card(n, st="Closed", s="CLOSED", c=closed_at, labels=labels),
            _life(OPENED, (closed_at, "Closed")) + list(events))


def _seed(hub, cards):
    """The board's items and the history store's records for `cards`, a map of number to (item, events)."""
    path = hub._hist.history_path(hub.BOARD, hub.REPO)
    for n, (item, events) in cards.items():
        hub._hist.record_issue(path, n, OPENED, OPENED, events, True, now=time.time(), keys=hub.bar_keys(item))
    hub.board_cache["items"] = [item for item, _ in cards.values()]
    hub.board_cache["t"] = time.time() + 60   # a new board read: the chart cache key moves with it


def _flow(hub):
    """Both chart routes' answers, under the keys the tests read."""
    return {"cfd": hub.flow_cfd(ty=None)["cfd"], "lead": hub.flow_lead(ty=None)["lead"]}


def _band_count(cfd, name):
    band = next(b for b in cfd["bands"] if b["name"] == name)
    return band["count"][-1]


def test_a_chart_exclude_card_is_in_no_hub_chart(hub, board):
    """Criteria 1-4: the labelled card is in no lead-time dot, no control-chart mark and no cumulative-flow band, its
    count rides the Lead Time exclusion list under its own reason, and a card without the label keeps its place."""
    _seed(hub, {
        1: _done(1, "2026-10-01T15:00:00Z"),
        2: _done(2, "2026-10-02T09:00:00Z", labels=(LABEL,),
                 events=[{"t": OPENED, "type": "label", "name": LABEL}]),
    })
    flow = _flow(hub)
    lead, cfd, control = flow["lead"], flow["cfd"], flow["lead"]["control"]["all"]

    # 1. the lead-time chart and its control chart count no card carrying the label.
    assert [dot["n"] for dot in lead["series"]] == [1], "the labelled card is still a lead-time dot"
    assert [dot["n"] for dot in control["dots"]] == [1], "the labelled card is still a control-chart mark"
    assert control["summary"]["plotted"] == 1

    # 2. the Lead Time exclusion list names the label as its own reason, with its count.
    assert {"reason": LABEL, "count": 1} in lead["excluded"], lead["excluded"]
    assert lead["excluded_cards"][LABEL] == [2]
    assert LABEL in lead["excluded_reasons"], lead["excluded_reasons"]
    assert f"1 {lead['excluded_reasons'][LABEL]}" in lead["excluded_text"], lead["excluded_text"]
    assert control["excluded"][LABEL] == 1, control["excluded"]   # the control chart serves the reasons as a map

    # 3. the cumulative flow counts no card carrying the label.
    assert _band_count(cfd, hub.COLUMN["issue_closed"]) == 1, "the labelled card still fills the Closed band"
    assert sum(row["count"] for row in cfd["ranges"]["all"]["reads"][-1]["counts"]) == 1

    # 4. a card without the label keeps its place in both charts.
    assert lead["series"][0]["n"] == 1 and lead["summary"]["median"] == {"hours": 6.0, "text": "6h"}
    assert _band_count(cfd, hub.COLUMN["issue_closed"]) == 1


def test_a_chart_exclude_card_returns_to_both_charts_when_the_label_is_removed(hub, board):
    """Criterion 5: on the next read after the label is removed the card is back in the lead-time chart, its control
    chart and the cumulative flow."""
    labelled = {"t": OPENED, "type": "label", "name": LABEL}
    _seed(hub, {
        1: _done(1, "2026-10-01T15:00:00Z"),
        2: _done(2, "2026-10-02T09:00:00Z", labels=(LABEL,), events=[labelled]),
    })
    first = _flow(hub)
    assert [dot["n"] for dot in first["lead"]["series"]] == [1], "the labelled card is drawn before the removal"
    assert _band_count(first["cfd"], hub.COLUMN["issue_closed"]) == 1

    # The owner removes the label: the board read drops it and the history records the removal. The next read brings
    # the card back to both charts.
    _seed(hub, {
        1: _done(1, "2026-10-01T15:00:00Z"),
        2: _done(2, "2026-10-02T09:00:00Z",
                 events=[labelled, {"t": "2026-10-03T09:00:00Z", "type": "unlabel", "name": LABEL}]),
    })
    again = _flow(hub)
    assert [dot["n"] for dot in again["lead"]["series"]] == [1, 2], "the card does not return to the lead-time chart"
    assert [dot["n"] for dot in again["lead"]["control"]["all"]["dots"]] == [1, 2], "the card does not return to the control chart"
    assert again["lead"]["excluded"] == [], again["lead"]["excluded"]
    assert _band_count(again["cfd"], hub.COLUMN["issue_closed"]) == 2, "the card does not return to the cumulative flow"
