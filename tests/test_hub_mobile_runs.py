"""The mobile Runs tab (#830): Live first, then today's ended runs, a full-screen run view, an edge swipe back and a pull
down refresh. The gesture rules run under node alone, with no DOM and no server.

Live draws every live row once, as the desktop tab does (#1770, #1778): the childcc pass first, then the live runs and the
hand holds, under one heading that carries the occupancy the answer built. The age bands, the WORKERS group and their
muted line left the page. The page boots under node over the stub DOM of the move tests, and every fact is read back as
the owner sees it.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

import tests.hub_mobile_stub as move
from tests.hub_mobile_stub import hub  # noqa: F401  (hub is a fixture)

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
    """The Live group rides the query the desktop tab asks (#1277); the ended route is the same read as ever."""
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


LOOP_LINE = re.compile(r"\bloop[A-Z]\w*|loops?-|loopOf\b|\bloopV\b")   # the loop page's own names (#1599)


def test_the_runs_code_holds_no_run_state_rule():
    """A run's state is the server's: the runs code never compares one (ADR-0137). The loop page reads its own project's
    loop state from the hub's fact (`loopPrimary` picks its label), so only the lines that name a loop rule are cut — every
    other line of the page, the loop block included, is still searched (#1599)."""
    html = page()
    rest = "\n".join(ln for ln in html.splitlines() if not LOOP_LINE.search(ln))
    assert not re.search(r"\.state\s*[!=]==?", rest)
    assert LOOP_LINE.search('const v = loopPrimary(p, true, "")'), "the exclusion really covers the loop rule lines"


# --- Live and Ended today (#1778): the phone draws the rows the answer carries, never a band ---

LIVE_ROW = {"id": "run-20261104-101010-aaaa", "label": "implement #7", "state": "running", "badge": "running",
            "rank": 0, "live": True, "age": 12, "spend": 1.25, "agents": 2, "kind": "code", "running": True}
ENDED_ROW = {"id": "run-20261104-090000-bbbb", "label": "fix #9", "state": "completed", "badge": "done",
             "rank": 1, "live": False, "age": 2500, "spend": 3.4, "agents": 0, "kind": "code", "running": False}
CC_ROW = {"id": "childcc", "label": "childcc pass · pid 4242", "state": "running", "badge": "childcc", "rank": 0,
          "live": True, "age": 300, "spend": 0, "agents": 0, "kind": "childcc", "running": False,
          "held": LIVE_ROW["id"], "sub": ""}
HOLD_ROW = {"id": "hold-42", "label": "Manual · #42", "state": "held", "badge": "held", "rank": 0, "live": True,
            "age": 60, "spend": 0, "agents": 0, "kind": "hold", "running": False}
CAPS = {"runs": 6, "children": 20}

DRIVER = """
  show("runs"); await loadRuns();
  const rows = id => [...$(id).children].map(c => ({ id: c.dataset.id || null, url: c.dataset.url || null, text: c.textContent }));
  return { live: rows("runs-live"), ended: rows("runs-ended"),
           occ: $("runs-occ").textContent, occTitle: $("runs-occ").getAttribute("title"),
           text: $("runs-live").textContent + $("runs-ended").textContent, msg: $("runs-msg").textContent };
"""


def draw(hub, rows, ended=None):  # noqa: F811
    answer = {"runs": rows, "groups": hub.run_groups(rows, CAPS)}
    return move.run_page(hub.MOBILE_PAGE, DRIVER,
                         routes={"/api/runs": [{"body": json.dumps(answer)}],
                                 "/api/ended": [{"body": json.dumps(ended or [])}]})


@pytest.mark.node_bound
def test_live_draws_the_childcc_row_first_then_every_live_row_once(hub):  # noqa: F811
    """Live carries the rows the runs answer marks live, the pass first, as the desktop tab draws them (#1770)."""
    out = draw(hub, [LIVE_ROW, CC_ROW, HOLD_ROW])
    texts = [r["text"] for r in out["live"]]
    assert len(texts) == 3, f"a live row is drawn twice or lost: {texts}"
    assert texts[0].startswith("childcc pass · pid 4242"), "the pass is not the first Live row"
    assert texts[1].startswith("implement #7") and texts[2].startswith("Manual · #42"), texts
    assert out["ended"] == [], "no ended row hides under Live"


@pytest.mark.node_bound
def test_live_carries_the_occupancy_the_hub_built_beside_its_heading(hub):  # noqa: F811
    """The heading prints the Live group's own text and spend title; the page builds neither (#1277)."""
    groups = hub.run_groups([LIVE_ROW, CC_ROW], CAPS)
    running = next(g for g in groups if g["key"] == "running")
    out = draw(hub, [LIVE_ROW, CC_ROW])
    assert out["occ"] == running["text"] == "RUNS 1/6 · AGENTS 2/20", "the occupancy counts the run, never the pass"
    assert out["occTitle"] == running["title"] == "$1.25"


def test_the_now_live_tile_and_the_runs_heading_count_one_source(hub, monkeypatch):  # noqa: F811
    """The Now tile and the Runs heading read one count (SPEC 2498, #2502): a live run is one with a live process, and
    neither a hand hold nor the childcc pass is a run, so the two numbers never contradict."""
    rows = [LIVE_ROW, HOLD_ROW, CC_ROW, ENDED_ROW]
    monkeypatch.setattr(hub, "run_list", lambda: rows)
    monkeypatch.setattr(hub, "board_cache", {"items": []})
    monkeypatch.setattr(hub, "spend_today", lambda: 0.0)
    monkeypatch.setattr(hub, "loop_status", lambda: {"on": True, "since": None})
    monkeypatch.setattr(hub, "load_payload", lambda: {})
    tile = hub.status_payload()["live"]
    heading = hub.run_groups(rows, CAPS)[0]["text"]
    assert tile == 1, f"the Now tile counts {tile} live runs, not the one live run: {rows}"
    assert heading.startswith(f"RUNS {tile}/6"), f"the tile counts {tile} and the heading says {heading!r}"


@pytest.mark.node_bound
def test_ended_today_lists_the_ended_runs_and_never_the_live_ones(hub):  # noqa: F811
    out = draw(hub, [LIVE_ROW], ended=[ENDED_ROW, dict(LIVE_ROW, running=False)])
    assert [r["id"] for r in out["ended"]] == [ENDED_ROW["id"]], "a live row is drawn once, under Live"
    assert "fix #9" in out["ended"][0]["text"]


@pytest.mark.node_bound
def test_the_tab_draws_no_band_and_no_muted_line(hub):  # noqa: F811
    """The age bands, the WORKERS header and their 'no runs in ...' line leave the phone with the behavior (#1778)."""
    out = draw(hub, [LIVE_ROW, ENDED_ROW], ended=[ENDED_ROW])
    for gone in ("WORKERS", "≤ 1 h", "24 h+ (days)", "no runs in", "Run history"):
        assert gone not in out["text"], f"the tab still draws {gone}"
    assert not re.search(r"ageBand|rowBand|runHistory|drawHistory", page()), "the band rules stay in the page"
    assert "runs-history" not in runs_panel(), "the history markup stays in the tab"


@pytest.mark.node_bound
def test_a_run_row_and_the_childcc_row_open_the_run_and_the_hold_opens_none(hub):  # noqa: F811
    """Each row opens what it names: the run itself, the run the pass holds, and nothing for a hand hold (#598, #689)."""
    out = draw(hub, [LIVE_ROW, CC_ROW, HOLD_ROW])
    assert [(r["id"], r["url"]) for r in out["live"]] == [
        (LIVE_ROW["id"], "/dash/" + LIVE_ROW["id"] + "/"),   # the pass opens the run it holds
        (LIVE_ROW["id"], "/dash/" + LIVE_ROW["id"] + "/"),
        (None, None)], out["live"]


@pytest.mark.node_bound
def test_a_hub_that_rejects_the_groups_query_still_lists_the_runs(hub):  # noqa: F811
    """The list is the bare route's read, made first (#1337): a rejected `?groups=1` costs the occupancy alone."""
    out = move.run_page(hub.MOBILE_PAGE, DRIVER,
                        routes={"/api/runs": [{"body": json.dumps([LIVE_ROW])},
                                              {"body": "bad request", "status": 400, "ct": "text/plain"}],
                                "/api/ended": [{"body": "[]"}]})
    assert "implement #7" in out["live"][0]["text"], "a hub that rejects the groups query still lists its runs, as main did"
    assert out["occ"] == "", "a rejected group read draws no occupancy"
    assert out["msg"] == "", "a rejected group read is not the runs list's error"


@pytest.mark.node_bound
def test_an_old_hub_array_keeps_the_list_and_draws_no_occupancy(hub):  # noqa: F811
    """A hub older than the page ignores the query and answers the bare array: the list still reads with no occupancy."""
    out = move.run_page(hub.MOBILE_PAGE, DRIVER,
                        routes={"/api/runs": [{"body": json.dumps([LIVE_ROW])}], "/api/ended": [{"body": "[]"}]})
    assert "implement #7" in out["live"][0]["text"], "an old hub's array is read as the runs, never as an object with no `runs`"
    assert out["occ"] == "" and out["occTitle"] is None, "an old hub's array carries no occupancy"
