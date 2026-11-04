"""The Lead Time view: its four defaults (#1247), the start of the clock (#1248), and the Kaplan-Meier median over the
arrival cohort (#1250).

The hub computes every number (ADR-0137); the page draws what the hub serves.
"""
import importlib.util
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.test_hub_board_flow_charts import _card, _flow_block, _life, _seed, board, hub  # noqa: F401  (fixtures)

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"{name}_survival", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lt = _load("board_leadtime")
hist = _load("board_history")
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def _iso(moment):
    return moment.isoformat()


def _row(n, start_hours_ago, hours, censored=False, unknown=False, early=None):
    """One hub row with its observation: the start `start_hours_ago` before NOW, and `hours` to its end or to NOW."""
    start = NOW - timedelta(hours=start_hours_ago)
    lo = start - timedelta(hours=early) if early else start
    closed = not censored
    return {"n": n, "column": "Closed" if closed else "Doing", "reason": None,
            "c": _iso(start + timedelta(hours=hours)) if closed else None, "o": _iso(start),
            "hours": hours if closed else 0.0, "flowing": hours, "held": 0.0, "approx": False, "unknown_first": unknown,
            "obs": {"start_lo": _iso(lo), "start_hi": _iso(start), "hours": hours, "censored": censored,
                    "unknown_start": unknown}}


# --- #1250: the Kaplan-Meier estimate ------------------------------------------------------------------------------

def test_the_median_is_the_first_time_the_curve_falls_to_one_half_not_the_closed_card_median():
    rows = [_row(n, 10, h) for n, h in enumerate((1, 2, 3, 4), start=1)]
    km = lt.survival(rows, now=NOW)
    assert km["median"]["hours"] == 2, "the curve reaches 0.5 at the second of four events; the closed median is 2.5"
    assert km["estimable"] is True


def test_the_median_carries_a_95_percent_interval_from_the_confidence_band():
    rows = [_row(n, 10, h) for n, h in enumerate((1, 2, 3, 4, 5), start=1)]
    km = lt.survival(rows, now=NOW)
    assert km["median"]["hours"] == 3
    assert km["ci"]["level"] == 0.95
    assert km["ci"]["low"]["hours"] == 1 and km["ci"]["high"]["hours"] == 5


def test_an_open_card_enters_the_risk_set_censored_and_is_never_a_finished_card():
    rows = [_row(1, 10, 1), _row(2, 10, 2), _row(3, 6, 6, censored=True), _row(4, 10, 8)]
    km = lt.survival(rows, now=NOW)
    assert km["cohort"] == 4 and km["censored"] == 1 and km["events"] == 3
    # S(1)=3/4, S(2)=2/4, so the median is 2h; a closed-only median of 1, 2, 8 would read 2h too, so test the curve.
    assert km["median"]["hours"] == 2
    two = lt.survival([_row(1, 10, 1), _row(2, 9, 9, censored=True), _row(3, 9, 9, censored=True)], now=NOW)
    assert two["median"] is None and two["estimable"] is False, "one finish of three never brings the curve to one half"


def test_a_curve_that_never_reaches_one_half_is_not_estimable_and_never_prints_the_largest_time():
    rows = [_row(1, 30, 1), _row(2, 30, 30, censored=True), _row(3, 30, 30, censored=True)]
    km = lt.survival(rows, now=NOW)
    assert km["median"] is None and km["estimable"] is False
    assert "not estimable" in km["text"]
    assert "30h" not in km["text"] and "1d 6h" not in km["text"], "the largest observed time is printed as a median"


def test_the_cohort_is_the_cards_whose_start_falls_inside_the_window_and_names_its_dates():
    rows = [_row(1, 10, 1), _row(2, 20, 2), _row(3, 100, 3)]
    km = lt.survival(rows, start=NOW - timedelta(hours=48), end=NOW, now=NOW)
    assert km["cohort"] == 2, "a card that arrived before the window is outside its cohort"
    assert km["cohort_start"] == "2026-11-01" and km["cohort_end"] == "2026-11-03"
    assert "2 cards" in km["text"] and "Nov 1" in km["text"] and "Nov 3" in km["text"]


def test_an_unknown_start_is_flagged_and_never_timed():
    rows = [_row(1, 10, 1), _row(2, 10, 2), _row(3, 10, 3, unknown=True)]
    km = lt.survival(rows, now=NOW)
    assert km["cohort"] == 2
    assert km["unknown_start"] == {"count": 1, "cards": [3]}
    assert "1 unknown start" in km["text"]


def test_a_start_read_between_two_polls_is_kept_as_an_interval():
    rows = [_row(1, 10, 1), _row(2, 10, 2, early=3), _row(3, 10, 4), _row(4, 10, 6)]
    km = lt.survival(rows, now=NOW)
    assert km["interval_starts"] == 1
    assert km["median"]["hours"] == 2, "the median reads from the late bound of each start"
    assert km["median_range"] == {"low": {"hours": 2, "text": "2h"}, "high": {"hours": 4, "text": "4h"}}, \
        "the early bound of the interval start moves the median: the reading keeps both"


def test_the_work_in_progress_is_its_own_number_with_the_oldest_age():
    rows = [_row(1, 10, 1), _row(2, 55, 55, censored=True), _row(3, 4, 4, censored=True)]
    wip = lt.wip(rows)
    assert wip["count"] == 2 and wip["oldest"]["hours"] == 55
    assert wip["text"] == "WIP 2 waiting, oldest 2d 7h"


def test_a_zoneless_timestamp_is_read_as_utc_before_any_subtraction():
    events = [{"t": "2026-10-01T00:00:00", "type": "added"}, {"t": "2026-10-01T00:00:00", "type": "status", "status": "Todo"},
              {"t": "2026-10-02T00:00:00+02:00", "type": "status", "status": "Closed"},
              {"t": "2026-10-02T00:00:00+02:00", "type": "closed"}]
    span = hist.lead_span(hist.intervals(events), start="Todo")
    assert span["flowing"] == 22.0, "a zoneless instant is UTC; an offset instant is converted to UTC"
    assert lt.utc("2026-10-01T00:00:00").tzinfo is not None


def test_a_move_seen_twice_in_one_poll_counts_once():
    once = _life("2026-10-01T00:00:00Z", ("2026-10-02T00:00:00Z", "Doing"), ("2026-10-02T00:00:00Z", "Todo"),
                 ("2026-10-03T00:00:00Z", "Closed"))
    twice = once + once[2:4]
    assert hist.intervals(twice) == hist.intervals(once)


# --- #1248: the start of the clock --------------------------------------------------------------------------------

def test_a_return_to_todo_never_restarts_the_clock():
    events = _life("2026-10-01T00:00:00Z", ("2026-10-02T00:00:00Z", "Doing"), ("2026-10-03T00:00:00Z", "Todo"),
                   ("2026-10-04T00:00:00Z", "Closed"))
    span = hist.lead_span(hist.intervals(events), start="Todo")
    assert span["start"] == "2026-10-01T00:00:00Z" and span["flowing"] == 72.0


def test_a_card_created_in_todo_at_its_add_is_a_known_start():
    """The status event of a card created in Todo may sort before its add in the same second: no move is needed."""
    events = [{"t": "2026-10-01T00:00:00Z", "type": "status", "status": "Todo"}, {"t": "2026-10-01T00:00:00Z", "type": "added"},
              {"t": "2026-10-02T00:00:00Z", "type": "status", "status": "Closed"}, {"t": "2026-10-02T00:00:00Z", "type": "closed"}]
    span = hist.lead_span(hist.intervals(events), start="Todo")
    assert span["flowing"] == 24.0 and span["unknown_first"] is False


def test_an_open_card_is_observed_to_the_reading_instant():
    events = _life("2026-10-01T00:00:00Z", ("2026-10-02T00:00:00Z", "Doing"))
    obs = hist.lead_observation(hist.intervals(events), start="Todo", now=datetime(2026, 10, 3, tzinfo=UTC))
    assert obs["censored"] is True and obs["flowing"] == 48.0 and obs["end"] is None


def test_a_card_that_never_entered_todo_is_excluded_and_named(hub, board):  # noqa: F811
    never = [{"t": "2026-10-01T00:00:00Z", "type": "added"}, {"t": "2026-10-01T00:00:00Z", "type": "status", "status": "Doing"},
             {"t": "2026-10-02T00:00:00Z", "type": "status", "status": "Closed"}, {"t": "2026-10-02T00:00:00Z", "type": "closed"}]
    _seed(hub, {1: (_card(1, st="Closed", s="CLOSED", c="2026-10-02T00:00:00Z"), never),
                2: (_card(2, st="Closed", s="CLOSED", c="2026-10-03T00:00:00Z"),
                    _life("2026-10-01T00:00:00Z", ("2026-10-03T00:00:00Z", "Closed")))})
    chart = hub.lead_zoom("tf=all&a=Todo")
    assert [d["n"] for d in chart["dots"]] == [2]
    assert chart["excluded"] == {"no_start": 1}
    assert chart["excluded_cards"] == {"no_start": [1]}


def test_the_hub_serves_the_lead_view_from_todo_with_open_cards_censored(hub, board):  # noqa: F811
    now = datetime.now(UTC)
    t = lambda h: (now - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    _seed(hub, {1: (_card(1, st="Closed", s="CLOSED", c=t(10)), _life(t(12), (t(10), "Closed"))),
                2: (_card(2, st="Todo", s="OPEN"), _life(t(30))),
                3: (_card(3, st="Closed", s="CLOSED", c=t(1)), _life(t(5), (t(4), "Doing"), (t(2), "Todo"), (t(1), "Closed")))})
    lead = hub.flow_lead(ty=None)["lead"]
    assert lead["start"] == hub.COLUMN["issue_todo"], "the payload measures from the default start, Todo"
    chart = lead["control"]["log"]["2"]
    assert [d["n"] for d in chart["dots"]] == [1, 3], "an open card is never a dot"
    assert chart["survival"]["cohort"] == 3 and chart["survival"]["censored"] == 1
    assert chart["median"]["hours"] == pytest.approx(4.0, abs=0.01), "card 3 starts at its first Todo arrival, 4h"
    assert chart["wip"]["count"] == 1 and chart["wip"]["oldest"]["hours"] == pytest.approx(30.0, abs=0.01)


# --- #1247: the defaults ------------------------------------------------------------------------------------------

def _node(script):
    result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _page_line(hub, prefix):  # noqa: F811
    return next(line for line in hub.PAGE.splitlines() if line.startswith(prefix))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_view_opens_on_48_hours_bugs_todo_and_log_scale_on_a_fresh_page(hub):  # noqa: F811
    block = _flow_block(hub).split("const drawFlow")[0]
    for stored in ({}, {"lt": {"ty": "task", "tf": "30", "log": False}}):
        out = _node("\n".join([f"const ls={json.dumps(stored)};", "const esc=s=>String(s);", block,
                               _page_line(hub, "const ltQ="),
                               "process.stdout.write(JSON.stringify({tf:LT.tf,ty:LT.ty,a:LT.a,log:LT.log,own:ltOwn()}));"]))
        assert json.loads(out) == {"tf": "2", "ty": "bug", "a": "Todo", "log": True, "own": False}, \
            "a fresh page opens on the four defaults, whatever an older visit stored"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_choice_stays_for_the_visit_and_is_never_stored(hub):  # noqa: F811
    assert "saveLT" not in hub.PAGE and "ls.lt=" not in hub.PAGE, "a choice outlives the visit"
    assert re.search(r'id==="ltr"\)\{LT\.tf=e\.target\.value', hub.PAGE), "a Timeframe choice is not kept for the visit"


def test_the_default_window_is_in_the_one_read_the_page_makes(hub, board):  # noqa: F811
    lead = hub.flow_lead(ty="bug")["lead"]
    assert "2" in lead["control"]["log"], "the default window is not in the payload the page already reads"
    assert lead["start"] == "Todo"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_legend_carries_the_km_median_its_interval_the_cohort_wip_and_the_exclusions(hub, board, tmp_path):  # noqa: F811
    from tests.test_hub_board_flow_charts import _lt_chart
    now = datetime.now(UTC)
    t = lambda h: (now - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    never = [{"t": t(20), "type": "added"}, {"t": t(20), "type": "status", "status": "Doing"},
             {"t": t(3), "type": "status", "status": "Closed"}, {"t": t(3), "type": "closed"}]
    _seed(hub, {1: (_card(1, st="Closed", s="CLOSED", c=t(10)), _life(t(12), (t(10), "Closed"))),
                2: (_card(2, st="Todo", s="OPEN"), _life(t(30))),
                3: (_card(3, st="Todo", s="OPEN"), _life(t(40))),
                4: (_card(4, st="Closed", s="CLOSED", c=t(3)), never)})
    chart = hub.flow_lead(ty=None)["lead"]["control"]["log"]["2"]
    out = _lt_chart(hub, tmp_path, "lt-km.js", chart)
    legend = "".join(re.findall(r'<p class="flg ltlg">(.*?)</p>', out))
    assert "not estimable" in legend, "a curve that never reaches one half reads not estimable"
    assert chart["survival"]["text"] in out and chart["wip"]["text"] in out
    assert out.count("excluded") == 1 and re.search(r'title="1 never entered the start column: #4"[^>]*>excluded 1<', out), \
        "the excluded count sits beside the percentile and names the card"
