"""The Lead Time chart plots any closed card inside the window, and names every exclusion (#1354).

A card can close without ever entering the Todo column: a spec is a document and its card moves with its tickets
(ADR-0142), and a bug or a task can be born or moved past it. The Todo -> closed measure then found no start and every
such card closed inside the 48 hour window was dropped as `no_start`, with no reason shown. A closed card the history
read, whose default start column (Todo) it never entered but whose end column it did, is measured from its first time
on the board instead, whatever its Type, and the chart says so. A card that stays excluded states its reason in words
beside its count, never as a bare number.
"""
import datetime as dt
import json
import re
import shutil
import subprocess
from urllib.parse import quote

import pytest

from tests.test_hub_board_flow_charts import _card, _life, _seed, board, hub  # noqa: F401  (fixtures)


def _iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _spec_life(added, *steps):
    """A card that enters the board in Backlog, never in Todo, then takes each (instant, status) step."""
    events = [{"t": added, "type": "added"}, {"t": added, "type": "status", "status": "Backlog"}]
    for at, status in steps:
        events.append({"t": at, "type": "status", "status": status})
        if status == "Closed":
            events.append({"t": at, "type": "closed"})
    return events


def _spec_done(now, board_at, closed_at, labels=("spec",), st="Backlog", status="Closed"):
    """One closed card that entered the board at `board_at` and closed at `closed_at`, never entering Todo."""
    item = _card(1, st=st, s="CLOSED", c=_iso(closed_at), labels=labels)
    return {1: (item, _spec_life(_iso(board_at), (_iso(closed_at), status)))}


def _flow_block(module):
    m = re.search(r"// flow-charts:\n(.*?)// flow-charts:end", module.PAGE, re.S)
    assert m, "the page carries no flow-charts block"
    return m.group(1)


def _render_lead(module, lead, tf="all"):
    """The page's own lead chart code, run in node over the hub's payload."""
    block = _flow_block(module).split("const drawFlow")[0]
    script = "\n".join(["const COLORS={},COLNAME={};", "const esc=s=>String(s);", "const ls={};", block,
                        f'LT.tf="{tf}";', f"process.stdout.write(flowLead({json.dumps(lead)}));"])
    result = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_a_spec_closed_inside_the_48_hour_window_is_plotted(hub, board):  # noqa: F811
    now = dt.datetime.now(dt.UTC)
    closed, entered = now - dt.timedelta(hours=2), now - dt.timedelta(hours=26)
    _seed(hub, _spec_done(now, entered, closed))
    chart = hub.lead_zoom("tf=2&ty=spec&a=Todo")
    assert [d["n"] for d in chart["dots"]] == [1], "the spec closed inside the window is a dot, not an exclusion"
    assert chart["dots"][0]["fallback"] is True, "the dot names the measure it fell back to"
    assert chart["dots"][0]["hours"] == pytest.approx(24.0), "the wait on the board counts as the spec's lead time"
    assert chart["excluded_total"] == 0


def test_the_fallback_measure_is_named_in_the_chart(hub, board):  # noqa: F811
    now = dt.datetime.now(dt.UTC)
    closed, entered = now - dt.timedelta(hours=2), now - dt.timedelta(hours=26)
    _seed(hub, _spec_done(now, entered, closed))
    chart = hub.lead_zoom("tf=2&ty=spec&a=Todo")
    assert chart["fallback"]["count"] == 1 and chart["fallback"]["cards"] == [1]
    assert "1 card measured from its first time on the board" in chart["text"]
    assert "measured from its first time on the board" in chart["fallback"]["text"]


def test_a_bug_that_never_entered_todo_is_plotted_from_its_board_entry(hub, board):  # noqa: F811
    now = dt.datetime.now(dt.UTC)
    closed, entered = now - dt.timedelta(hours=2), now - dt.timedelta(hours=26)
    _seed(hub, _spec_done(now, entered, closed, labels=("bug",)))
    chart = hub.lead_zoom("tf=2&ty=bug&a=Todo")
    assert [d["n"] for d in chart["dots"]] == [1], "a bug closed inside the window is a dot at 48 hours too"
    assert chart["dots"][0]["fallback"] is True and chart["dots"][0]["hours"] == pytest.approx(24.0)
    assert chart["excluded_total"] == 0


def test_every_type_at_48_hours_plots_a_card_that_never_entered_todo(hub, board):  # noqa: F811
    now = dt.datetime.now(dt.UTC)
    closed, entered = now - dt.timedelta(hours=2), now - dt.timedelta(hours=26)
    _seed(hub, {
        1: (_card(1, st="Backlog", s="CLOSED", c=_iso(closed), labels=("spec",)),
            _spec_life(_iso(entered), (_iso(closed), "Closed"))),
        2: (_card(2, st="Backlog", s="CLOSED", c=_iso(closed), labels=("bug",)),
            _spec_life(_iso(entered), (_iso(closed), "Closed"))),
        3: (_card(3, st="Backlog", s="CLOSED", c=_iso(closed)),
            _spec_life(_iso(entered), (_iso(closed), "Closed"))),
    })
    spec = hub.lead_zoom("tf=2&ty=spec&a=Todo")
    bug = hub.lead_zoom("tf=2&ty=bug&a=Todo")
    task = hub.lead_zoom("tf=2&ty=task&a=Todo")
    assert [d["n"] for d in spec["dots"]] == [1] and spec["excluded_total"] == 0
    assert [d["n"] for d in bug["dots"]] == [2] and bug["excluded_total"] == 0
    assert [d["n"] for d in task["dots"]] == [3] and task["excluded_total"] == 0
    assert bug["dots"][0]["fallback"] is True and task["dots"][0]["fallback"] is True


def test_a_spec_that_entered_todo_but_never_closed_stays_a_named_exclusion(hub, board):  # noqa: F811
    now = dt.datetime.now(dt.UTC)
    closed = now - dt.timedelta(hours=2)
    item = _card(1, st="Doing", s="CLOSED", c=_iso(closed), labels=("spec",))
    events = [{"t": _iso(closed - dt.timedelta(hours=26)), "type": "added"},
              {"t": _iso(closed - dt.timedelta(hours=26)), "type": "status", "status": "Todo"},
              {"t": _iso(closed - dt.timedelta(hours=20)), "type": "status", "status": "Doing"},
              {"t": _iso(closed), "type": "removed"}]   # the card left the board without ever entering Closed
    _seed(hub, {1: (item, events)})
    chart = hub.lead_zoom("tf=2&ty=spec&a=Todo")
    assert chart["dots"] == [], "a spec with no entry into the end column is no dot"
    assert chart["excluded"] == {"no_transition": 1}
    assert chart["excluded_text"] == "1 no transition into the chosen columns"
    assert chart["fallback"]["count"] == 0, "the chosen span is not silently replaced"


def _moved_off_todo_life(added, closed):
    """A card that entered the board at Doing, reached Code Complete, and closed, never entering Todo or Code Review."""
    return [{"t": _iso(added), "type": "added"}, {"t": _iso(added), "type": "status", "status": "Doing"},
            {"t": _iso(added + dt.timedelta(hours=10)), "type": "status", "status": "Code Complete"},
            {"t": _iso(closed), "type": "status", "status": "Closed"},
            {"t": _iso(closed), "type": "closed"}]


def test_a_card_that_never_entered_the_chosen_end_column_is_never_measured_from_its_board_entry(hub, board):  # noqa: F811
    """A fallback replaces the START the reader did not reach, never the END they chose (#1248, #1354)."""
    now = dt.datetime.now(dt.UTC)
    closed, entered = now - dt.timedelta(hours=2), now - dt.timedelta(hours=26)
    _seed(hub, {1: (_card(1, st="Closed", s="CLOSED", c=_iso(closed), labels=("bug",)),
                    _moved_off_todo_life(entered, closed))})
    chart = hub.lead_zoom(f"tf=2&ty=bug&a=Todo&b={quote('Code Review')}")
    assert chart["dots"] == [], "a card whose chosen end column it never entered is no dot"
    assert chart["excluded"] == {"no_start": 1}
    assert chart["fallback"]["count"] == 0, "the chosen end column is never silently replaced"
    plotted = hub.lead_zoom(f"tf=2&ty=bug&a=Todo&b={quote('Code Complete')}")
    assert [d["n"] for d in plotted["dots"]] == [1], "the same card plots against an end column it did enter"
    assert plotted["dots"][0]["fallback"] is True and plotted["dots"][0]["hours"] == pytest.approx(10.0)


def test_an_empty_start_selection_is_created_never_a_column_named_empty(hub, board):  # noqa: F811
    """The page sends `a=` for Created; the route reads it as no start. An empty string is that same selection, never a
    column no card ever entered, so the two reads agree (#1354). Created is the card's first time on the board with the
    parked wait in neither column: the default start's fallback is what counts it, and that is Todo's own measure."""
    now = dt.datetime.now(dt.UTC)
    closed, entered = now - dt.timedelta(hours=2), now - dt.timedelta(hours=26)
    _seed(hub, _spec_done(now, entered, closed, labels=("bug",)))
    empty, created = hub.lead_rows("", None, now), hub.lead_rows(None, None, now)
    assert [(r["n"], r["reason"], round(r["hours"], 1)) for r in empty] == \
        [(r["n"], r["reason"], round(r["hours"], 1)) for r in created]
    assert empty[0]["reason"] is None and empty[0]["fallback"] is False, "Created measures the card, not the fallback"
    todo = hub.lead_rows(hub.LEAD_START, None, now)[0]
    assert todo["fallback"] is True and todo["hours"] == pytest.approx(24.0), "Todo counts the board wait it fell back to"


def test_a_chosen_start_column_is_never_replaced_by_the_board_entry(hub, board):  # noqa: F811
    """Only the default start falls back. A start the reader chose keeps its no_start exclusion (#1248, #1354)."""
    now = dt.datetime.now(dt.UTC)
    closed, entered = now - dt.timedelta(hours=2), now - dt.timedelta(hours=26)
    _seed(hub, {1: (_card(1, st="Closed", s="CLOSED", c=_iso(closed), labels=("bug",)),
                    _moved_off_todo_life(entered, closed))})
    chart = hub.lead_zoom(f"tf=2&ty=bug&a={quote('Backlog')}")
    assert chart["dots"] == [] and chart["excluded"] == {"no_start": 1}
    assert chart["fallback"]["count"] == 0


def test_a_spec_the_history_never_read_is_excluded_with_its_reason(hub, board):  # noqa: F811
    now = dt.datetime.now(dt.UTC)
    _seed(hub, {})
    hub.board_cache["items"] = [_card(1, st="Backlog", s="CLOSED", c=_iso(now - dt.timedelta(hours=2)), labels=("spec",))]
    chart = hub.lead_zoom("tf=2&ty=spec&a=Todo")
    assert chart["dots"] == [] and chart["excluded"] == {"not_read": 1}
    assert chart["excluded_text"] == "1 not yet read by the history pass"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_states_an_exclusion_reason_in_words(hub, board):  # noqa: F811
    now = dt.datetime.now(dt.UTC)
    _seed(hub, {})
    hub.board_cache["items"] = [_card(1, st="Todo", s="CLOSED", c=_iso(now - dt.timedelta(hours=2)), labels=("bug",))]
    chart = hub.lead_zoom("tf=2&ty=bug&a=Todo")
    lead = {"type": "bug", "measure": chart["measure"], "window": {"size": 20, "unit": "issues"},
            "excluded": [{"reason": r, "count": c} for r, c in chart["excluded"].items()],
            "excluded_cards": chart["excluded_cards"], "excluded_text": chart["excluded_text"],
            "excluded_reasons": chart["excluded_reasons"],
            "excluded_total": chart["excluded_total"], "control": {"2": chart}, "columns": []}
    out = _render_lead(hub, lead, tf="2")
    assert re.search(r"excluded 1: 1 not yet read by the history pass", out), \
        "the head line states the exclusion's reason beside its count, in the payload's own words"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_names_a_spec_measured_from_its_board_entry(hub, board):  # noqa: F811
    now = dt.datetime.now(dt.UTC)
    closed, entered = now - dt.timedelta(hours=2), now - dt.timedelta(hours=26)
    _seed(hub, _spec_done(now, entered, closed))
    chart = hub.lead_zoom("tf=2&ty=spec&a=Todo")
    lead = {"type": "spec", "measure": chart["measure"], "window": {"size": 20, "unit": "issues"},
            "excluded": [], "excluded_cards": {}, "excluded_text": "", "excluded_reasons": {},
            "excluded_total": 0, "control": {"2": chart}, "columns": []}
    out = _render_lead(hub, lead, tf="2")
    assert "1 card measured from its first time on the board" in out, "the head line names the fallback measure"
    titles = re.findall(r"<circle[^>]*><title>([^<]*)</title>", out)
    assert titles and any("measured from its first time on the board" in t for t in titles), \
        "the dot's tooltip names the fallback measure"
