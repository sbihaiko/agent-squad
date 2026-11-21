"""The phone page shows the board, the runs, the usage and the specs, and no chat (#2490).

The chat, voice, photo and terminal controls left the phone page with their scripts. A run keeps its dashboard and its
pause and resume controls, and it loses the session action. The page fetches no card and no conversation, and a reload
reads no removed composer.

The page is the served phone page, booted under node over the move test's stub DOM, whose `fetch` records every read and
write. The assertions read what the page carries and what it asks the hub for, never a substring of the code that draws a
kept control.
"""
import json
import re
import time

import pytest

from tests.test_hub_mobile_move import hub, run_page  # noqa: F401  (hub is the page fixture)

RUN = "run-20261120-100000-abcd"
SID = "11111111-2222-3333-4444-555555555555"

# The elements the removed controls owned: the chat root and its badge, the conversation screen and its composer, the
# recording row, the photo row and the attach sheet, the run's session row and the Now › Needs you rows.
REMOVED_IDS = [
    "termview", "termview-head", "termview-back", "termview-turns", "termview-text", "termview-send", "termview-mic",
    "termview-file", "termview-photo", "termview-attach", "termview-rec", "termview-chips", "termview-older",
    "termview-acted", "voice-time", "voice-wave", "voice-cancel", "voice-hints",
    "sessions", "sessions-state", "sessions-msg", "chat-rest", "needs-you", "now-rest", "needs-you-msg",
    "attach-sheet", "runov-open", "runov-session-row", "runov-tail",
]

# The names only the removed rule modules declare: the page loaded each one, so none of them survives on the page.
REMOVED_RULES = ["chatRows(", "chatRowNode(", "chatBadge(", "chatImage(", "chatAttach(", "chatDraftLine(",
                 "voiceStart(", "voiceRow(", "voiceWave(", "clipRow(", "drawQuestionCard(", "cardStart("]


def live_row():
    now = time.time()
    return {"id": RUN, "running": True, "live": True, "label": "implement #7", "state": "running", "badge": "running",
            "issues": [7], "titles": {"7": "Run detail"}, "age": 60, "spend": 1.25, "rank": 0, "kind": "code",
            "started": now - 1800, "ended": None}


def routes():
    return {
        "/api/runs": [{"body": json.dumps([live_row()]), "status": 200}],
        "/api/ended": [{"body": json.dumps([]), "status": 200}],
        "/dash/" + RUN + "/run/info.json": [{"body": json.dumps({"ended": False}), "status": 200}],
        "/dash/" + RUN + "/run/control.json": [{"body": json.dumps({"whole": False, "nodes": [], "released": []}), "status": 200}],
    }


OPEN_LIVE = """
  await loadRuns();
  $("runs-live").onclick({ target: $("runs-live").querySelector(".runrow") });
  await __tick(20);
"""


def page(hub_):
    return hub_.mobile_html().decode()


# --- 1. no chat box, voice button, photo row or terminal control ------------------------------------------------------

@pytest.mark.parametrize("id_", REMOVED_IDS)
def test_the_phone_page_holds_no_removed_control(hub, id_):  # noqa: F811
    body = page(hub)
    assert f'id="{id_}"' not in body, f"the phone page still holds #{id_}"


def test_the_phone_page_has_no_chat_tab_and_no_composer(hub):  # noqa: F811
    body = page(hub)
    assert 'data-tab="chat"' not in body, "the phone page still shows a Chat tab"
    assert "<textarea" not in body, "the phone page still holds a composer"
    assert "Terminals" not in body, "the phone page still names the terminals screen"
    assert re.search(r"<section id=\"termview\"", body) is None, "the conversation screen still rides on the page"


# --- 2. no chat script and no voice script ----------------------------------------------------------------------------

@pytest.mark.parametrize("name", REMOVED_RULES)
def test_the_phone_page_loads_no_removed_rule_module(hub, name):  # noqa: F811
    assert name not in page(hub), f"the phone page still carries {name} from a removed module"


def test_the_phone_page_loads_no_script_from_an_address(hub):  # noqa: F811
    assert "<script src=" not in page(hub), "the phone page loads a script it does not inline"


# --- 3. a run keeps its dashboard, pause and resume controls ----------------------------------------------------------

@pytest.mark.node_bound
def test_a_run_shows_its_dashboard_and_its_pause_control(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, OPEN_LIVE + """
        return { primary: $("run-primary-label").textContent, primaryHidden: $("run-primary").hidden,
                 secondary: $("runctl-btn").textContent, secondaryHidden: $("runctl-btn").hidden };
    """, routes=routes())
    assert out["primaryHidden"] is False and out["primary"] == "Desktop dashboard", out
    assert out["secondaryHidden"] is False and out["secondary"] == "Pause the run", out


@pytest.mark.node_bound
def test_a_held_run_shows_its_resume_control(hub):  # noqa: F811
    held = routes()
    held["/dash/" + RUN + "/run/control.json"] = [{"body": json.dumps({"whole": True, "nodes": [], "released": []}), "status": 200}]
    out = run_page(hub.MOBILE_PAGE, OPEN_LIVE + """
        return { secondary: $("runctl-btn").textContent, hidden: $("runctl-btn").hidden };
    """, routes=held)
    assert out["hidden"] is False and out["secondary"] == "Ask to resume", out


@pytest.mark.node_bound
def test_the_desktop_dashboard_opens_behind_the_run(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, OPEN_LIVE + """
        $("run-primary").onclick();
        return { frame: !$("runframe").hidden, overview: $("runoverview").hidden };
    """, routes=routes())
    assert out == {"frame": True, "overview": True}, out


# --- 4. a run shows no session action ---------------------------------------------------------------------------------

@pytest.mark.node_bound
def test_a_run_shows_no_session_action(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, OPEN_LIVE + """
        return { acts: [...$("runview-actions").querySelectorAll("button")].map(b => b.dataset.act || b.id),
                 primaryAct: $("run-primary").dataset.act };
    """, routes=routes())
    assert "runov-open" not in out["acts"], out
    assert out["primaryAct"] == "desk", out


# --- 5. the phone fetches no card and no conversation -----------------------------------------------------------------

@pytest.mark.node_bound
def test_opening_a_run_fetches_no_card_and_no_conversation(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, OPEN_LIVE + """
        await __tick(20);
        return { gets: Object.keys(__state.gets) };
    """, routes=routes())
    for path in ("/api/sessions", "/api/sessions/card", "/api/sessions/conversation"):
        assert path not in out["gets"], f"the phone page reads {path}: {sorted(out['gets'])}"


# --- 6. a reload reads no removed composer and logs no error ----------------------------------------------------------

@pytest.mark.node_bound
def test_a_reload_of_the_run_screen_reads_no_removed_composer(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        return { runHidden: $("runview").hidden, name: $("runview-name").textContent, passes: true };
    """, routes=routes(), hash=f"#/runs/run={RUN}?name=Run%20one", history_state={"tab": "runs", "layer": "run", "depth": 1, "id": RUN, "url": "", "name": "Run one"})
    assert out["runHidden"] is False and out["name"] == "Run one", out


@pytest.mark.node_bound
def test_a_reload_of_a_removed_session_address_opens_the_default_tab(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        return { tab: tabActive, term: document.getElementById("termview") };
    """, routes=routes(), hash=f"#/chat/session={SID}?name=x")
    assert out == {"tab": "now", "term": None}, out
