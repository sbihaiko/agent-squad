"""The phone's run screen is an overview of the hub's run row (SPEC 1115, #1458): the issues, the age, the spend and the state
the row carries, a pause or resume only while the run is live, the verdict of an ended run, and the desktop dashboard one tap
away. The rules are `hub-mobile-run.js`, run under node alone; the screen is booted over the stub DOM of
`tests/test_hub_mobile_move.py`, and every fact it shows is read back as the owner sees it.
"""
import json
import pathlib
import shutil
import time

import pytest

from tests.test_hub_mobile_move import _module, run_js, run_page

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
RUN_JS = SCRIPTS / "hub-mobile-run.js"
NODE = shutil.which("node")

RUN = "run-20261106-101010-aaaa"
ENDED = "run-20261106-090000-bbbb"
NOW = time.time()
LIVE = {"id": RUN, "running": True, "live": True, "label": "implement #7", "state": "running", "badge": "running",
        "issues": [7], "titles": {"7": "Phone run overview"}, "age": 60, "spend": 1.25, "rank": 0, "kind": "code",
        "started": NOW - 1800, "ended": None}
DONE = {"id": ENDED, "running": False, "live": False, "label": "fix #9", "state": "completed", "badge": "done", "verdict": "completed",
        "tip": "the run finished its verdict", "issues": [9], "titles": {"9": "Old fix"}, "age": 2500, "spend": 3.4,
        "rank": 1, "kind": "code", "started": NOW - 5400, "ended": NOW - 2500}
OPEN = {"whole": False, "nodes": [], "released": []}


def routes(runs=None, ended=None, info=None):
    return {
        "/api/runs": [{"body": json.dumps(runs if runs is not None else [LIVE]), "status": 200}],
        "/api/ended": [{"body": json.dumps(ended if ended is not None else []), "status": 200}],
        f"/dash/{RUN}/run/info.json": [{"body": json.dumps(info or {"ended": False}), "status": 200}],
        f"/dash/{RUN}/run/control.json": [{"body": json.dumps(OPEN), "status": 200}],
        f"/dash/{RUN}/run/control": [{"body": json.dumps({"ok": True}), "status": 200}],
        f"/dash/{ENDED}/run/info.json": [{"body": json.dumps({"ended": True}), "status": 200}],
        f"/dash/{ENDED}/run/control.json": [{"body": json.dumps(OPEN), "status": 200}],
    }


OPEN_LIVE_BODY = """
  await loadRuns();
  $("runs-live").onclick({ target: $("runs-live").querySelector(".runrow") });
  await __tick(20);
"""
OPEN_LIVE = OPEN_LIVE_BODY + "\n  return facts();\n"
OPEN_ENDED = """
  await loadRuns();
  $("runs-ended").onclick({ target: $("runs-ended").querySelector(".runrow") });
  await __tick(20);
  return facts();
"""
FACTS = """
  const facts = () => ({
    overview: !$("runoverview").hidden,
    frameHidden: $("runframe").hidden,
    state: $("runov-state").textContent,
    issues: $("runov-issues").textContent,
    spend: $("runov-spend").textContent,
    age: $("runov-age").textContent,
    verdict: $("runov-verdict").textContent,
    verdictHidden: $("runov-verdict").hidden,
    pauseHidden: $("runctl-btn").hidden,
    pauseText: $("runctl-btn").textContent,
  });
"""


@pytest.fixture(scope="module")
def hub():
    import os
    import tempfile
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    return _module("hub_mobile_run_overview", HUB_PY)


def open_run(hub, body, run_routes):
    return run_page(hub.MOBILE_PAGE, FACTS + body, routes=run_routes)


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run rules")
def test_the_age_says_the_time_since_start_while_live_and_since_end_once_over():
    src = RUN_JS.read_text()
    live = run_js(src, f"runAgeLabel({json.dumps(LIVE)}, {NOW})")
    assert live == "running for 30 min"
    ended = run_js(src, f"runAgeLabel({json.dumps(DONE)}, {NOW})")
    assert ended == "ended 41 min ago"
    assert run_js(src, f"runAgeLabel({json.dumps(dict(LIVE, started=None))}, {NOW})") == "no start recorded"


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run rules")
def test_a_run_the_row_names_as_over_is_offered_no_pause_whatever_the_dashboard_says():
    src = RUN_JS.read_text()
    assert run_js(src, f"runControlView({{ended:false}}, {json.dumps(OPEN)}, false).act") is None
    assert run_js(src, f"runControlView({{ended:false}}, {json.dumps(OPEN)}, true).act") == "pause"


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_a_live_run_opens_on_its_overview_with_its_issues_age_spend_and_state(hub):
    out = open_run(hub, OPEN_LIVE, routes())
    assert out["overview"] and out["frameHidden"], "the overview is the screen; the desktop frame waits behind a tap"
    assert out["issues"] == "#7 Phone run overview"
    assert out["spend"] == "$1.25"
    assert out["age"] == "running for 30 min"
    assert out["state"] == "running"


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_a_live_run_offers_the_pause_on_its_overview(hub):
    out = open_run(hub, OPEN_LIVE, routes())
    assert out["pauseHidden"] is False and out["pauseText"] == "Pause the run"


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_an_ended_run_draws_no_pause_and_shows_its_verdict(hub):
    out = open_run(hub, OPEN_ENDED, routes(runs=[], ended=[DONE]))
    assert out["overview"] is True
    assert out["pauseHidden"] is True, "an ended run draws no pause or resume"
    assert out["age"].startswith("ended ") and "ago" in out["age"]
    assert out["verdictHidden"] is False and out["verdict"] == "completed"


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_the_desktop_dashboard_is_one_tap_from_the_overview(hub):
    body = OPEN_LIVE_BODY + """
  $("rundesk-btn").onclick({ target: $("rundesk-btn") });
  return { ...facts(), src: $("runframe").src };
"""
    out = open_run(hub, body, routes())
    assert out["overview"] is False and out["frameHidden"] is False
    assert out["src"] == f"/dash/{RUN}/"
