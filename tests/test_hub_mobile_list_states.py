"""The three list states on the phone's Runs, Terminals and Cost screens (#1445, part of SPEC 1115).

Each list draws 'loading' before its first reply, names an empty reply in one sentence, and draws one error line with
role 'status' under its own heading. A read that fails keeps what the list already drew, keeps the loading line away and
adds the error line below it. The Runs screen's history read takes the same four rules as its run list, and its error
line sits under the history heading.

The page boots under node over the stub DOM of the move tests, and every fact is read back as the owner sees it: the
error line, the line above it and the content the list still holds. Board and Loop keep their wording, and one test pins
it so a later change cannot take it away.
"""
import json
import pathlib

import pytest

import tests.test_hub_mobile_move as move
from tests.test_hub_mobile_move import hub  # noqa: F401  (hub is a fixture)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
GESTURE_JS = SCRIPTS / "hub-mobile-gesture.js"
PAGE = SCRIPTS / "hub-mobile.html"

CAPS = {"runs": 6, "children": 20}
BROKEN = {"body": "bad gate", "status": 500, "ct": "text/plain"}
ERR = "The hub answered 500."
LIVE_ROW = {"id": "run-20261108-101010-aaaa", "label": "implement #7", "state": "running", "badge": "running",
            "rank": 0, "live": True, "age": 12, "spend": 1.25, "kind": "code", "running": True}
HOUR_ROW = {"id": "run-20261108-090000-bbbb", "label": "fix #9", "state": "completed", "badge": "done",
            "rank": 1, "live": False, "age": 2500, "spend": 3.4, "kind": "code", "running": False}


RUNS_DRIVER = """
  await loadRuns();
  return { live: $("runs-live").textContent, ended: $("runs-ended").textContent,
           state: $("runs-state").textContent, stateHidden: $("runs-state").hidden,
           msg: $("runs-msg").textContent, msgRole: $("runs-msg").getAttribute("role"),
           hist: $("runs-history").textContent, histHidden: $("runs-history").hidden,
           head: $("runs-history-h").hidden,
           histState: $("runs-history-state").textContent, histStateHidden: $("runs-history-state").hidden,
           histMsg: $("runs-history-msg").textContent, histMsgRole: $("runs-history-msg").getAttribute("role") };
"""

REFRESH_DRIVER = """
  await loadRuns();
  const first = { live: $("runs-live").textContent, hist: $("runs-history").textContent, head: $("runs-history-h").hidden };
  await loadRuns();
  return { first: first, live: $("runs-live").textContent, state: $("runs-state").textContent,
           stateHidden: $("runs-state").hidden, msg: $("runs-msg").textContent,
           hist: $("runs-history").textContent, histHidden: $("runs-history").hidden,
           head: $("runs-history-h").hidden, histState: $("runs-history-state").textContent,
           histStateHidden: $("runs-history-state").hidden, histMsg: $("runs-history-msg").textContent };
"""


def boot(hub, driver, routes):  # noqa: F811
    return move.run_page(hub.MOBILE_PAGE, driver, routes=routes)


# --- loading before the first reply -----------------------------------------------------------------------------------

LOADING_DRIVER = """
  return [["runs", "runs-state"], ["terminals", "sessions-state"], ["cost", "cost-state"]].map(([k, id]) => ({
    key: k, text: $(id).textContent, hidden: $(id).hidden, role: $(id).getAttribute("role") }));
"""


@pytest.mark.node_bound
def test_each_list_says_loading_before_its_first_reply(hub):  # noqa: F811
    out = boot(hub, LOADING_DRIVER, {})
    assert [o["key"] for o in out] == ["runs", "terminals", "cost"]
    for o in out:
        assert "loading" in o["text"], o
        assert not o["hidden"], f"the {o['key']} loading line is drawn before its first reply"


@pytest.mark.node_bound
def test_the_history_says_loading_before_its_first_reply(hub):  # noqa: F811
    out = boot(hub, 'return { text: $("runs-history-state").textContent, hidden: $("runs-history-state").hidden };', {})
    assert "loading" in out["text"] and not out["hidden"]


# --- a successful empty reply names the missing data ------------------------------------------------------------------

@pytest.mark.node_bound
def test_an_empty_run_list_names_the_missing_data(hub):  # noqa: F811
    out = boot(hub, RUNS_DRIVER, {"/api/runs": [{"body": "[]"}], "/api/ended": [{"body": "[]"}]})
    assert "No runs" in out["state"] and not out["stateHidden"], out
    assert out["msg"] == "", "an empty reply is no error"


@pytest.mark.node_bound
def test_an_empty_history_names_the_missing_data(hub):  # noqa: F811
    empty = {"runs": [], "groups": [], "muted": ""}
    out = boot(hub, RUNS_DRIVER, {"/api/runs": [{"body": "[]"}, {"body": json.dumps(empty)}],
                                  "/api/ended": [{"body": "[]"}]})
    assert "No run history" in out["histState"] and not out["histStateHidden"], out
    assert out["histMsg"] == "" and out["histHidden"]


@pytest.mark.node_bound
def test_an_empty_terminal_list_names_the_missing_data(hub):  # noqa: F811
    out = boot(hub, 'await loadSessions(); return { text: $("sessions-state").textContent, '
                    'hidden: $("sessions-state").hidden, msg: $("sessions-msg").textContent };',
               {"/api/sessions": [{"body": json.dumps({"groups": []})}]})
    assert "No terminal sessions" in out["text"] and not out["hidden"], out
    assert out["msg"] == ""


@pytest.mark.node_bound
def test_an_empty_cost_list_names_the_missing_data(hub):  # noqa: F811
    stats = {"buckets": [], "totals": {"today": 0, "month": 0, "all": 0, "unpriced": 0, "uncounted": 0}, "models": []}
    out = boot(hub, 'openLayer("cost"); await loadMore(); return { text: $("cost-state").textContent, '
                    'hidden: $("cost-state").hidden, msg: $("cost-msg").textContent };',
               {"/api/stats": [{"body": json.dumps(stats)}]})
    assert "No model spend" in out["text"] and not out["hidden"], out
    assert out["msg"] == ""


# --- a failed read draws one error line with role status --------------------------------------------------------------

@pytest.mark.node_bound
def test_a_failed_first_run_list_read_draws_one_error_line(hub):  # noqa: F811
    out = boot(hub, RUNS_DRIVER, {"/api/runs": [BROKEN], "/api/ended": [{"body": "[]"}]})
    assert out["msg"] == ERR and not out["state"], "the error line replaces the loading line"
    assert out["msgRole"] == "status", "the error line is announced as a status"


@pytest.mark.node_bound
def test_a_rejected_groups_read_draws_the_history_error_line_under_its_heading(hub):  # noqa: F811
    out = boot(hub, RUNS_DRIVER, {"/api/runs": [{"body": json.dumps([LIVE_ROW])}, BROKEN],
                                  "/api/ended": [{"body": "[]"}]})
    assert out["histMsg"] == ERR and out["histMsgRole"] == "status", out
    assert not out["head"], "the history heading stays up so its error line sits under it"
    assert "implement #7" in out["live"], "a rejected history read never costs the run list"
    assert out["msg"] == "", "the history error is never the run list's error"


@pytest.mark.node_bound
def test_a_failed_first_terminal_read_draws_one_error_line(hub):  # noqa: F811
    out = boot(hub, 'await loadSessions(); return { state: $("sessions-state").textContent, '
                    'stateHidden: $("sessions-state").hidden, msg: $("sessions-msg").textContent, '
                    'role: $("sessions-msg").getAttribute("role") };',
               {"/api/sessions": [BROKEN]})
    assert out["msg"] == ERR and out["stateHidden"], out
    assert out["role"] == "status"


@pytest.mark.node_bound
def test_a_failed_first_cost_read_draws_one_error_line(hub):  # noqa: F811
    out = boot(hub, 'openLayer("cost"); await loadMore(); return { state: $("cost-state").textContent, '
                    'stateHidden: $("cost-state").hidden, msg: $("cost-msg").textContent, '
                    'role: $("cost-msg").getAttribute("role") };',
               {"/api/stats": [BROKEN]})
    assert out["msg"] == ERR and out["stateHidden"], out
    assert out["role"] == "status"


# --- the three timings are one rule: the run list and the history ------------------------------------------------------

@pytest.mark.node_bound
def test_a_failed_read_after_an_empty_reply_keeps_the_empty_sentence_beside_the_error_line(hub):  # noqa: F811
    out = boot(hub, REFRESH_DRIVER, {"/api/runs": [{"body": "[]"}, BROKEN], "/api/ended": [{"body": "[]"}]})
    assert "No runs" in out["state"] and not out["stateHidden"], "the empty sentence stays beside the error line"
    assert out["msg"] == ERR


@pytest.mark.node_bound
def test_a_failed_read_after_an_empty_history_reply_keeps_the_empty_sentence_beside_the_error_line(hub):  # noqa: F811
    empty = {"runs": [], "groups": [], "muted": ""}
    out = boot(hub, REFRESH_DRIVER, {"/api/runs": [{"body": "[]"}, {"body": json.dumps(empty)}, {"body": "[]"}, BROKEN],
                                     "/api/ended": [{"body": "[]"}]})
    assert "No run history" in out["histState"] and not out["histStateHidden"], out
    assert out["histMsg"] == ERR, out


@pytest.mark.node_bound
def test_a_failed_refresh_keeps_the_drawn_run_list_and_never_returns_to_loading(hub):  # noqa: F811
    out = boot(hub, REFRESH_DRIVER, {"/api/runs": [{"body": json.dumps([LIVE_ROW])}, BROKEN],
                                     "/api/ended": [{"body": "[]"}]})
    assert "implement #7" in out["first"]["live"], "the first read drew the run"
    assert "implement #7" in out["live"], "a failed refresh keeps the last good content drawn"
    assert out["msg"] == ERR and out["stateHidden"], "the error line joins the kept content, and loading never returns"


@pytest.mark.node_bound
def test_a_rejected_history_refresh_keeps_the_drawn_history_and_never_returns_to_loading(hub):  # noqa: F811
    rows = [dict(LIVE_ROW, band="running", live=True), dict(HOUR_ROW, band="1h")]
    good = {"runs": rows, "groups": hub.run_groups(rows, CAPS), "muted": hub.run_muted(hub.run_groups(rows, CAPS))}
    out = boot(hub, REFRESH_DRIVER, {"/api/runs": [{"body": json.dumps([LIVE_ROW])}, {"body": json.dumps(good)},
                                                   {"body": json.dumps([LIVE_ROW])}, BROKEN],
                                     "/api/ended": [{"body": "[]"}]})
    assert "WORKERS" in out["first"]["hist"], "the first read drew the history"
    assert not out["first"]["head"]
    assert "WORKERS" in out["hist"], "a rejected history refresh keeps the last drawn history"
    assert not out["histHidden"]
    assert not out["head"], "the heading stays up under the history error line"
    assert out["histMsg"] == ERR and out["histStateHidden"], "the error line joins the held history; loading never returns"


# --- board and loop keep their wording --------------------------------------------------------------------------------

def test_the_board_and_the_loop_keep_their_wording():
    gesture = GESTURE_JS.read_text()
    assert '"Loading the board."' in gesture
    assert '"No column holds a card."' in gesture
    assert "Loading the loop status." in PAGE.read_text()
