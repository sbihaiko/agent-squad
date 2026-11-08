"""The mobile Runs tab (#830): live runs first, then today's ended runs, a full-screen run view, an edge swipe back and a pull
down refresh. The gesture rules run under node alone, with no DOM and no server.

The run history at the bottom of the tab (#1337) draws the groups the opted-in answer carries, as the desktop tab does
(#1277): the page boots under node over the stub DOM of the move tests, and the words are read back as the owner sees them.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

import tests.test_hub_mobile_move as move
from tests.test_hub_mobile_move import hub  # noqa: F401  (hub is a fixture)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
PAGE = SCRIPTS / "hub-mobile.html"
GESTURE_JS = SCRIPTS / "hub-mobile-gesture.js"
BOARD_FILTER_JS = SCRIPTS / "board-filter.js"
RUN_JS = SCRIPTS / "hub-mobile-run.js"
NODE = shutil.which("node")


def page():
    return (PAGE.read_text().replace("/*GESTURE*/", GESTURE_JS.read_text())
            .replace("/*RUN*/", RUN_JS.read_text())
            .replace("/*BOARD_FILTER*/", BOARD_FILTER_JS.read_text()))


def run_js(src, expr):
    script = f"{src}\nconsole.log(JSON.stringify({expr}));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def runs_panel():
    m = re.search(r'<section data-panel="runs"[^>]*>(.*?)</section>', page(), re.S)
    assert m, "no runs panel"
    return m.group(0)


@pytest.mark.skipif(NODE is None, reason="node is needed for the gesture rules")
@pytest.mark.parametrize("g, act", [
    ({"x0": 10, "dx": 80, "dy": 4, "atTop": False, "thumb": True}, None),   # the left-edge swipe is the browser's Back
    ({"x0": 24, "dx": 60, "dy": -10, "atTop": True, "thumb": True}, None),
    ({"x0": 10, "dx": 80, "dy": 4, "atTop": False, "thumb": False}, None),
    ({"x0": 100, "dx": 80, "dy": 4, "atTop": False, "thumb": True}, None),
    ({"x0": 10, "dx": 30, "dy": 2, "atTop": False}, None),
    ({"x0": 10, "dx": 40, "dy": 90, "atTop": False}, None),
    ({"x0": 100, "dx": 0, "dy": 90, "atTop": True}, "refresh"),
    ({"x0": 100, "dx": 0, "dy": 90, "atTop": False}, None),
    ({"x0": 100, "dx": 0, "dy": 30, "atTop": True}, None),
    ({"x0": 100, "dx": 80, "dy": 70, "atTop": True}, None),
])
def test_the_runs_gesture_rules_give_back_or_refresh_or_nothing(g, act):
    got = run_js(GESTURE_JS.read_text(), f"runsGesture({json.dumps(g)})")
    assert (got or {}).get("act") == act


def test_the_runs_tab_has_no_placeholder():
    assert "Placeholder" not in runs_panel()


def test_live_runs_are_listed_before_the_ended_runs():
    panel = runs_panel()
    live, ended = panel.find('id="runs-live"'), panel.find('id="runs-ended"')
    assert live > 0 and ended > live


def test_the_runs_tab_reads_the_opted_in_run_list_and_the_ended_route_for_today():
    """The history groups ride the query the desktop tab asks (#1277, #1337); the ended route is the same read as ever."""
    script = page()
    assert '"/api/runs?groups=1"' in script
    assert '"/api/ended"' in script


def test_a_tap_opens_the_run_full_screen_in_one_frame():
    html = page()
    assert 'id="runview"' in html and 'id="runframe"' in html
    assert re.search(r"#runview\{[^}]*position:fixed[^}]*inset:0", html)


def test_the_run_view_has_no_edge_strip_and_no_back_swipe_handler():
    html = page()
    assert 'id="edge"' not in html and 'data-role="edge"' not in html, "the run view keeps an edge strip"
    assert "#edge{" not in html, "the run view keeps the edge strip's style"


def test_the_runs_code_holds_no_run_state_rule():
    assert not re.search(r"\.state\s*[!=]==?", page())


# --- the run history at the bottom of the tab (#1337): the phone draws the groups the opted-in answer carries ---

LIVE_ROW = {"id": "run-20261104-101010-aaaa", "label": "implement #7", "state": "running", "badge": "running",
            "rank": 0, "live": True, "age": 12, "spend": 1.25, "agents": 2, "kind": "code", "running": True}
HOUR_ROW = {"id": "run-20261104-090000-bbbb", "label": "fix #9", "state": "completed", "badge": "done",
            "rank": 1, "live": False, "age": 2500, "spend": 3.4, "agents": 0, "kind": "code", "running": False}
TWO_HOUR_ROW = dict(HOUR_ROW, id="run-20261104-080000-cccc", label="fix #11", age=4000, spend=0.5)
CAPS = {"runs": 6, "children": 20}

DRIVER = """
  show("runs"); await loadRuns();
  const h = $("runs-history"), ghs = h.querySelectorAll(".gh");
  return { hidden: h.hidden, head: $("runs-history-h").hidden, text: h.textContent,
           keys: ghs.map(e => e.dataset.g),
           span: ghs[0] ? ghs[0].querySelector(".sr").textContent : null,
           title: ghs[0] ? ghs[0].getAttribute("title") : null,
           kids: [...h.children].map(e => ({ g: e.dataset.g || null, t: e.textContent })),
           live: $("runs-live").textContent, msg: $("runs-msg").textContent,
           histState: $("runs-history-state").textContent, histMsg: $("runs-history-msg").textContent };
"""


def at(out, group):
    """The index of the drawn header for `group`, so a test can place a row against it."""
    return [i for i, k in enumerate(out["kids"]) if k["g"] == group][0]


def under(out, group):
    """The rows the drawn header for `group` carries, in order: every kid after it, up to the next header."""
    kids = out["kids"][at(out, group) + 1:]
    rows = []
    for k in kids:
        if k["g"]:
            break
        rows.append(k["t"])
    return rows


@pytest.mark.node_bound
def test_the_phone_draws_the_workers_header_with_its_occupancy(hub):  # noqa: F811
    """The phone reads the answer's own group entries: the WORKERS label, the text the hub built and its spend tooltip."""
    rows = [dict(LIVE_ROW, band="running"), dict(HOUR_ROW, band="1h")]
    groups = hub.run_groups([LIVE_ROW, HOUR_ROW], CAPS)
    workers = next(g for g in groups if g["key"] == "running")
    answer = {"runs": rows, "groups": groups, "muted": hub.run_muted(groups)}
    out = move.run_page(hub.MOBILE_PAGE, DRIVER,
                        routes={"/api/runs": [{"body": json.dumps(answer)}], "/api/ended": [{"body": "[]"}]})
    assert not out["hidden"] and not out["head"], "the run history is drawn at the bottom of the tab"
    assert out["keys"] == ["running", "1h"], "a filled band draws its header, in the answer's ladder order"
    assert "WORKERS" in out["text"]
    assert out["span"] == workers["text"], "the occupancy the hub built is not on the WORKERS header"
    assert out["title"] == workers["title"], "the header carries the group's spend as its tooltip"
    assert "implement #7" in out["live"], "Live still lists the run it always did"


@pytest.mark.node_bound
def test_the_phone_folds_the_empty_age_bands_into_one_muted_line(hub):  # noqa: F811
    """Six bands of 0 · $0.00 never push the rows down: the hub marks a band empty and sends the muted line (#1277)."""
    rows = [dict(TWO_HOUR_ROW, band="2h")]
    groups = hub.run_groups([TWO_HOUR_ROW], CAPS)
    answer = {"runs": rows, "groups": groups, "muted": hub.run_muted(groups)}
    out = move.run_page(hub.MOBILE_PAGE, DRIVER,
                        routes={"/api/runs": [{"body": json.dumps(answer)}], "/api/ended": [{"body": "[]"}]})
    assert out["keys"] == ["running", "2h"], "an empty band draws no header"
    assert "0 · $0.00" not in out["text"], "an empty band still draws a zero row"
    assert hub.run_muted(groups) in out["text"], "the empty bands are named once, in the words the hub sent"


@pytest.mark.node_bound
def test_a_hub_that_rejects_the_groups_query_still_lists_the_runs(hub):  # noqa: F811
    """The list is the bare route's read, made first (#1337): a rejected `?groups=1` costs the history alone, never the runs."""
    out = move.run_page(hub.MOBILE_PAGE, DRIVER,
                        routes={"/api/runs": [{"body": json.dumps([LIVE_ROW])},
                                              {"body": "bad request", "status": 400, "ct": "text/plain"}],
                                "/api/ended": [{"body": "[]"}]})
    assert "implement #7" in out["live"], "a hub that rejects the groups query still lists its runs, as main did"
    assert out["hidden"], "a rejected history read draws no group"
    assert not out["head"] and out["histMsg"], "its own error line sits under the history heading (#1445)"
    assert out["msg"] == "", "a rejected history read is not the runs list's error"


@pytest.mark.node_bound
def test_a_hub_that_sends_groups_without_the_row_band_still_places_every_row(hub):  # noqa: F811
    """A #1277 hub sends the groups but no per-row `band`: the page derives it from the same ladder, so no row vanishes."""
    rows = [LIVE_ROW, HOUR_ROW]
    groups = hub.run_groups(rows, CAPS)
    answer = {"runs": rows, "groups": groups}
    out = move.run_page(hub.MOBILE_PAGE, DRIVER,
                        routes={"/api/runs": [{"body": json.dumps([LIVE_ROW])}, {"body": json.dumps(answer)}],
                                "/api/ended": [{"body": "[]"}]})
    assert any("implement #7" in t for t in under(out, "running")), "a live row joins WORKERS"
    assert any("fix #9" in t for t in under(out, "1h")), "an ended row joins its age band"
    assert "no runs in" in out["text"], "a hub that sends no muted line still folds its empty bands"


@pytest.mark.node_bound
def test_an_old_hub_array_keeps_the_list_and_draws_no_history(hub):  # noqa: F811
    """A hub older than the page ignores the query and answers the bare array: the list still reads (#1277)."""
    out = move.run_page(hub.MOBILE_PAGE, DRIVER,
                        routes={"/api/runs": [{"body": json.dumps([LIVE_ROW])}], "/api/ended": [{"body": "[]"}]})
    assert "implement #7" in out["live"], "an old hub's array is read as the runs, never as an object with no `runs`"
    assert out["hidden"], "the history draws no group instead of showing a stale one"
    assert "No run history is drawn." in out["histState"], "an empty history names the missing data (#1445)"
    assert re.search(r"\[hidden\]\.hist\{display:none\}", page()), "the grid's own display rule outranks hidden"
