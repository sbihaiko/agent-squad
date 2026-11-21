"""The run detail on the phone (SPEC 1596, #1604): one action row — Back, •••, Pause as the secondary and one primary
that opens the run's dashboard — over a body that names the run's state, its age, its spend and its issues.

The run's session actions left the page with the chat (#2490), so the page reads no record, no card and no conversation,
and its primary opens the dashboard the run still has. The rules are `hub-mobile-run.js`, run under node alone with no
DOM; the page is booted over the stub DOM of `tests/test_hub_mobile_move.py`, and every fact is read back as the owner
sees it.
"""
import json
import os
import pathlib
import shutil
import tempfile
import time

import pytest

from tests.test_hub_mobile_move import _module, run_js, run_page

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
RUN_JS = SCRIPTS / "hub-mobile-run.js"
NODE = shutil.which("node")

RUN = "run-20261108-100000-abcd"
ENDED = "run-20261108-090000-ffff"
OPEN = {"whole": False, "nodes": [], "released": []}
HELD = {"whole": True, "nodes": [], "released": []}


def live_row(now):
    return {"id": RUN, "running": True, "live": True, "label": "implement #7", "state": "running", "badge": "running",
            "issues": [7], "titles": {"7": "Run detail"}, "age": 60, "spend": 1.25, "rank": 0, "kind": "code",
            "started": now - 1800, "ended": None}


def ended_row(now):
    return {"id": ENDED, "running": False, "live": True, "label": "fix #9", "state": "completed", "badge": "done",
            "verdict": "completed", "issues": [9], "titles": {"9": "Old fix"}, "age": 2500, "spend": 3.4, "rank": 1,
            "kind": "code", "started": now - 5400, "ended": now - 2500}


def routes(runs=None, ended=None, control=None):
    body = {
        "/api/runs": [{"body": json.dumps(runs if runs is not None else [live_row(time.time())]), "status": 200}],
        "/api/ended": [{"body": json.dumps(ended if ended is not None else []), "status": 200}],
        "/dash/" + RUN + "/run/info.json": [{"body": json.dumps({"ended": False}), "status": 200}],
        "/dash/" + RUN + "/run/control.json": [{"body": json.dumps(control or OPEN), "status": 200}],
        "/dash/" + ENDED + "/run/info.json": [{"body": json.dumps({"ended": True}), "status": 200}],
        "/dash/" + ENDED + "/run/control.json": [{"body": json.dumps(OPEN), "status": 200}],
    }
    return body


OPEN_LIVE = """
  await loadRuns();
  $("runs-live").onclick({ target: $("runs-live").querySelector(".runrow") });
  await __tick(20);
"""
OPEN_ENDED = """
  await loadRuns();
  $("runs-ended").onclick({ target: $("runs-ended").querySelector(".runrow") });
  await __tick(20);
"""


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    return _module("hub_mobile_run_detail", HUB_PY)


# --- the primary opens the dashboard a run still has (AC3) -----------------------------------------------------------

def test_a_live_run_offers_its_dashboard():
    out = run_js(RUN_JS.read_text(), f"runPrimary({json.dumps(live_row(0))})")
    assert out == {"act": "desk", "label": "Desktop dashboard"}, out


def test_an_ended_run_offers_the_dashboard_it_still_has():
    out = run_js(RUN_JS.read_text(), f"runPrimary({json.dumps(ended_row(0))})")
    assert out == {"act": "desk", "label": "Desktop dashboard"}, out


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_the_run_screen_shows_one_back_at_the_top_and_the_row_holds_only_actions(hub):
    """One back control, at the top of the screen (SPEC 2498, #2502): the action row carries the actions alone."""
    out = run_page(hub.MOBILE_PAGE, OPEN_LIVE + """
        const visible = els => els.filter(b => !b.hidden).map(b => b.id);
        return { top: visible($("runview").querySelectorAll("[data-topback]")),
                 rowBacks: $("runview-actions").querySelectorAll("[data-actback]").length,
                 head: visible($("runview-head").querySelectorAll("[data-topback]")),
                 row: [...$("runview-actions").children].map(c => ({ id: c.id, tag: c.tagName })),
                 bar: $("runview-actions").hidden };
    """, routes=routes())
    assert out["top"] == ["run-backtop"], f"the run screen shows more than one back control: {out}"
    assert out["head"] == ["run-backtop"], f"the run's one back control is not at the top: {out}"
    assert out["rowBacks"] == 0, f"the action row still holds a back control: {out}"
    assert [c["id"] for c in out["row"]] == ["runctl", "run-primary"], out
    assert out["bar"] is False, f"the row holds an action and is not drawn: {out}"


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_an_ended_run_keeps_one_back_at_the_top(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_ENDED + """
        return { top: $("runview").querySelectorAll("[data-topback]").filter(b => !b.hidden).map(b => b.id),
                 rowBacks: $("runview-actions").querySelectorAll("[data-actback]").length };
    """, routes=routes(runs=[], ended=[ended_row(time.time())]))
    assert out == {"top": ["run-backtop"], "rowBacks": 0}, out


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_a_held_run_reads_its_resume_on_the_secondary(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_LIVE + """
        return { secondary: $("runctl-btn").textContent, label: $("run-primary-label").textContent };
    """, routes=routes(control=HELD))
    assert out["secondary"] == "Ask to resume", out
    assert out["label"] == "Desktop dashboard", out


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_an_ended_run_offers_the_dashboard_as_its_primary(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_ENDED + """
        return { label: $("run-primary-label").textContent, secondaryHidden: $("runctl-btn").hidden,
                 limits: $("runctl-limits").textContent };
    """, routes=routes(runs=[], ended=[ended_row(time.time())]))
    assert out["label"] == "Desktop dashboard", out
    assert out["secondaryHidden"] is True, out
    assert out["limits"] == "", f"the ended run draws the rule of a pause it does not offer: {out}"


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_the_one_primary_names_the_screen_it_opens_back_to(hub):
    """The dashboard action is one button that names the other screen, so the bar never needs a second menu (#2502)."""
    out = run_page(hub.MOBILE_PAGE, OPEN_LIVE + """
        const before = $("run-primary-label").textContent;
        $("run-primary").onclick();
        const desk = $("run-primary-label").textContent;
        $("run-primary").onclick();
        return { before, desk, back: $("run-primary-label").textContent };
    """, routes=routes())
    assert out == {"before": "Desktop dashboard", "desk": "Overview", "back": "Desktop dashboard"}, out


# --- the run body: the state, the age, the spend and the issues (AC3, AC4) -------------------------------------------

@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_the_run_body_names_the_state_the_age_the_spend_and_the_issues(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_LIVE + """
        return { state: $("runov-state").textContent, age: $("runov-age").textContent,
                 spend: $("runov-spend").textContent, issues: $("runov-issues").textContent };
    """, routes=routes())
    assert out["state"] == "running", out
    assert out["age"], out
    assert out["spend"] == "$1.25", out
    assert out["issues"] == "#7 Run detail", out


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_the_primary_opens_the_dashboard_frame(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_LIVE + """
        $("run-primary").onclick();
        await __tick(20);
        return { frame: !$("runframe").hidden, overview: $("runoverview").hidden, src: $("runframe").src };
    """, routes=routes())
    assert out["frame"] is True and out["overview"] is True, out
    assert RUN in out["src"], out


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_the_run_shows_no_session_action(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_LIVE + """
        return { actions: [...document.querySelectorAll("#runview [data-act]")].map(e => e.dataset.act),
                 gets: Object.keys(__state.gets) };
    """, routes=routes())
    assert "desk" in out["actions"], out
    assert "session" not in out["actions"], f"the run still holds a session action: {out}"
    for path in out["gets"]:
        assert "/api/sessions" not in path and "record.jsonl" not in path, f"the run reads a session: {path}"
