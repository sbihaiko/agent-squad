"""The run detail on the phone (SPEC 1596, #1604): one action row — Back, •••, Pause as the secondary and one primary
that follows the run's state — and a body that names the run's issues, the last lines of its session and one row that
opens the full session.

The session is read from the routes the page already has: the run's record (`/run/record.jsonl`, through the hub's
`/dash/` forward) names the session the run last worked in, and the session route (`/api/sessions/conversation`) is the
conversation the chat screen reads. The rules are `hub-mobile-run.js`, run under node alone with no DOM; the page is
booted over the stub DOM of `tests/test_hub_mobile_move.py`, and every fact is read back as the owner sees it.
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
SID = "11111111-2222-3333-4444-555555555555"
SID_OLD = "99999999-8888-7777-6666-555555555555"
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


# The run's own record, as two calls left it: the older session first, the newer one last, with one line that is no JSON.
RECORD = "\n".join([
    json.dumps({"pos": 1, "kind": "call_start", "node": "execute", "entry": "coder", "session": SID_OLD,
                "pid": 1, "limit": None, "started_at": "2026-11-08T10:00:00Z"}),
    json.dumps({"pos": 2, "kind": "progress", "node": "execute", "entry": "coder", "item": 1, "session": SID,
                "messages": 4, "context_tokens": 1000, "tool": "Edit"}),
    json.dumps({"pos": 3, "kind": "progress", "node": "execute", "entry": "gate", "item": 1, "session": SID,
                "messages": 9, "context_tokens": 2000, "tool": "Bash"}),
    "not json at all",
]) + "\n"

TURNS = {"turns": [
    {"id": "a", "k": "0:0", "role": "owner", "text": "Fix the run screen", "at": "", "m": ""},
    {"id": "b", "k": "1:0", "role": "answer", "text": "Reading the issue.", "at": "", "m": "claude-sonnet"},
    {"id": "c", "k": "2:0", "role": "answer", "text": "Writing the view.\nSecond line.", "at": "", "m": "claude-sonnet"},
    {"id": "d", "k": "3:0", "role": "system", "text": "   ", "at": "", "m": ""},
], "next": None}
CARD = {"card": {"id": "c1", "questions": [{"id": "q1", "text": "Which branch?"}]}}


def routes(runs=None, ended=None, control=None, record=RECORD, card=None, turns=None):
    body = {
        "/api/runs": [{"body": json.dumps(runs if runs is not None else [live_row(time.time())]), "status": 200}],
        "/api/ended": [{"body": json.dumps(ended if ended is not None else []), "status": 200}],
        "/dash/" + RUN + "/run/info.json": [{"body": json.dumps({"ended": False}), "status": 200}],
        "/dash/" + RUN + "/run/control.json": [{"body": json.dumps(control or OPEN), "status": 200}],
        "/dash/" + RUN + "/run/record.jsonl": [{"body": record, "status": 200, "ct": "text/plain; charset=utf-8"}],
        "/dash/" + ENDED + "/run/info.json": [{"body": json.dumps({"ended": True}), "status": 200}],
        "/dash/" + ENDED + "/run/control.json": [{"body": json.dumps(OPEN), "status": 200}],
        "/dash/" + ENDED + "/run/record.jsonl": [{"body": record, "status": 200, "ct": "text/plain; charset=utf-8"}],
        "/api/sessions/card": [{"body": json.dumps(card if card is not None else {"card": None}), "status": 200}],
        "/api/sessions/conversation": [{"body": json.dumps(turns if turns is not None else TURNS), "status": 200}],
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


# --- the primary that follows the run's state (AC1) ------------------------------------------------------------------

def test_the_primary_answers_the_coder_while_a_question_waits():
    out = run_js(RUN_JS.read_text(), f"runPrimary({json.dumps(live_row(0))}, {{id:'c1'}}, {json.dumps(SID)})")
    assert out == {"act": "session", "label": "Answer the coder"}, out


def test_a_live_run_without_a_question_opens_its_session():
    out = run_js(RUN_JS.read_text(), f"runPrimary({json.dumps(live_row(0))}, null, {json.dumps(SID)})")
    assert out == {"act": "session", "label": "Open the session"}, out


def test_an_ended_run_offers_the_dashboard_it_still_has():
    out = run_js(RUN_JS.read_text(), f"runPrimary({json.dumps(ended_row(0))}, null, {json.dumps(SID)})")
    assert out == {"act": "desk", "label": "Desktop dashboard"}, out


def test_a_live_run_that_named_no_session_offers_its_dashboard():
    out = run_js(RUN_JS.read_text(), f"runPrimary({json.dumps(live_row(0))}, null, '')")
    assert out == {"act": "desk", "label": "Desktop dashboard"}, out


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_the_action_row_is_back_dots_pause_and_one_primary(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_LIVE + """
        return [...$("runview-actions").children].map(c => ({ id: c.id, tag: c.tagName, back: "actback" in c.dataset }));
    """, routes=routes())
    assert out[0] == {"id": "runview-back", "tag": "BUTTON", "back": True}, f"the first button is no Back: {out}"
    assert out[1]["id"] == "run-dots" and out[1]["tag"] == "BUTTON", out
    assert [c["id"] for c in out[2:]] == ["runctl", "run-primary"], f"the row is not one secondary then one primary: {out}"


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_a_waiting_question_makes_the_primary_answer_the_coder(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_LIVE + """
        return { hidden: $("run-primary").hidden, label: $("run-primary-label").textContent,
                 secondary: $("runctl-btn").textContent, secondaryHidden: $("runctl-btn").hidden };
    """, routes=routes(card=CARD))
    assert out["hidden"] is False and out["label"] == "Answer the coder", out
    assert out["secondaryHidden"] is False and out["secondary"] == "Pause the run", out


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_the_primary_opens_the_full_session(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_LIVE + """
        $("run-primary").onclick();
        await __tick(20);
        return { open: !$("termview").hidden, sid: termSid, name: $("termview-name").textContent };
    """, routes=routes())
    assert out["open"] is True and out["sid"] == SID, out


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_a_held_run_reads_its_resume_on_the_secondary(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_LIVE + """
        return { secondary: $("runctl-btn").textContent, label: $("run-primary-label").textContent };
    """, routes=routes(control=HELD))
    assert out["secondary"] == "Ask to resume", out
    assert out["label"] == "Open the session", out


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_an_ended_run_offers_the_dashboard_as_its_primary(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_ENDED + """
        return { label: $("run-primary-label").textContent, secondaryHidden: $("runctl-btn").hidden };
    """, routes=routes(runs=[], ended=[ended_row(time.time())]))
    assert out["label"] == "Desktop dashboard", out
    assert out["secondaryHidden"] is True, out


# --- the run body: the issues, the session's last lines and the row that opens it (AC2) ------------------------------

def test_the_runs_session_is_the_last_one_its_record_names():
    assert run_js(RUN_JS.read_text(), f"runSessionId({json.dumps(RECORD)})") == SID
    assert run_js(RUN_JS.read_text(), "runSessionId('')") == ""
    assert run_js(RUN_JS.read_text(), "runSessionId('[1,2]')") == ""


def test_the_tail_is_the_last_lines_of_the_conversation_with_who_spoke():
    got = run_js(RUN_JS.read_text(), f"runTailLines({json.dumps(TURNS['turns'])}, 2)")
    assert got == ["Coder: Reading the issue.", "Coder: Writing the view. Second line."], got


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_the_run_body_names_the_issues_the_session_lines_and_the_row_that_opens_it(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_LIVE + """
        return { issues: $("runov-issues").textContent, tail: $("runov-tail").textContent,
                 open: $("runov-open").textContent, openHidden: $("runov-open").hidden };
    """, routes=routes())
    assert out["issues"] == "#7 Run detail", out
    assert out["tail"] == "You: Fix the run screen\nCoder: Reading the issue.\nCoder: Writing the view. Second line.", out
    assert out["openHidden"] is False and out["open"] == "Open the session", out


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_the_row_opens_the_full_session_on_the_chat_screen(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_LIVE + """
        $("runov-open").onclick();
        await __tick(20);
        return { open: !$("termview").hidden, sid: termSid, tab: tabActive };
    """, routes=routes())
    assert out["open"] is True and out["sid"] == SID and out["tab"] == "chat", out


@pytest.mark.node_bound
@pytest.mark.skipif(NODE is None, reason="node is needed for the run view")
def test_a_run_whose_record_names_no_session_draws_no_row(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_LIVE + """
        return { openHidden: $("runov-open").hidden, tail: $("runov-tail").textContent };
    """, routes=routes(record=""))
    assert out["openHidden"] is True and out["tail"] == "", out
