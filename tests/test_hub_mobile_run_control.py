"""The phone's run screen carries pause and resume (#831): the request goes through the hub's `/dash/` forward route to the
run's own control route, as the desktop page does, and the screen states the two limits of ADR-0118 — a pause lets a running
call finish, and a resume does not restart a run whose process ended.

The rules are one client module (`hub-mobile-run.js`), run under node alone with no DOM and no server. The page is composed
from its files the way the hub composes it, then booted under node over the stub DOM of `tests/hub_mobile_stub.py`,
whose `fetch` answers the run list and the forwarded control route: the write is proved by the request the page makes, never
by a substring of its text. The module names no loopback address and no tailnet host: the forward route is the only address
the phone knows, and the tailnet entry stays disabled until #713 proves the forwarded headers.
"""
import json
import os
import pathlib
import shutil
import tempfile

import pytest

from tests.hub_mobile_stub import _module, run_js, run_page

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
RUN_JS = SCRIPTS / "hub-mobile-run.js"
COLUMNS_PY = SCRIPTS / "board_columns.py"
NODE = shutil.which("node")

RUN = "run-20261021-032628-72d18f"
LIVE = {"id": RUN, "running": True, "label": "run one", "badge": "live", "age": 60, "spend": 0.5}
OPEN = {"whole": False, "nodes": [], "released": []}
ASKED = {"whole": True, "nodes": [], "released": []}


def routes(info=None, control=None, reply=None):
    """The forward route's answers, keyed by the exact path the phone asks."""
    return {
        "/api/runs": [{"body": json.dumps([LIVE]), "status": 200}],
        "/api/ended": [{"body": json.dumps([]), "status": 200}],
        f"/dash/{RUN}/run/info.json": [{"body": json.dumps({"ended": False} if info is None else info), "status": 200}],
        f"/dash/{RUN}/run/control.json": [{"body": json.dumps(OPEN if control is None else control), "status": 200}],
        f"/dash/{RUN}/run/control": [reply or {"body": json.dumps({"ok": True, "request": {"action": "pause"}}), "status": 200}],
    }


OPEN_RUN = """
  await loadRuns();
  const row = $("runs-live").querySelector(".runrow");
  $("runs-live").onclick({ target: row });
  await __tick(20);
  const btn = $("runctl-btn");
"""


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    return _module("hub_mobile_run_control", HUB_PY)


@pytest.mark.node_bound
def test_every_request_goes_to_the_runs_own_control_route_through_the_hub_forward():
    u = run_js(RUN_JS.read_text(), f"runURLs({json.dumps(RUN)})")
    assert u["control"] == f"/dash/{RUN}/run/control"
    assert u["state"] == f"/dash/{RUN}/run/control.json"
    assert u["info"] == f"/dash/{RUN}/run/info.json"
    assert u["page"] == f"/dash/{RUN}/"
    assert run_js(RUN_JS.read_text(), 'runURLs("a/b").control') == "/dash/a%2Fb/run/control"


@pytest.mark.node_bound
def test_the_body_is_the_desktop_clients_body():
    src = RUN_JS.read_text()
    assert run_js(src, 'controlBody("pause", null)') == '{"action":"pause"}'
    assert run_js(src, 'controlBody("pause", [])') == '{"action":"pause"}'
    assert run_js(src, 'controlBody("resume", ["a","b"])') == '{"action":"resume","nodes":["a","b"]}'


@pytest.mark.node_bound
def test_a_live_run_is_offered_the_pause_and_the_screen_states_the_two_limits():
    v = run_js(RUN_JS.read_text(), "runControlView({ended:false}, {whole:false,nodes:[],released:[]})")
    assert v["act"] == "pause" and v["label"]
    limits = run_js(RUN_JS.read_text(), "RUN_LIMITS")
    assert "running call finish" in limits and "does not restart" in limits


@pytest.mark.node_bound
def test_a_run_asked_to_pause_is_offered_the_resume():
    v = run_js(RUN_JS.read_text(), f"runControlView({{ended:false}}, {json.dumps(ASKED)})")
    assert v["act"] == "resume" and v["label"] == "Ask to resume"
    assert "squad resume" in v["note"]
    nodes = run_js(RUN_JS.read_text(), 'runControlView({ended:false}, {whole:false,nodes:["a"],released:[]}).act')
    assert nodes == "resume", "a held node is a pause the phone may release too"


@pytest.mark.node_bound
def test_an_ended_run_and_an_unreadable_dashboard_are_offered_no_request():
    assert run_js(RUN_JS.read_text(), f"runControlView({{ended:true}}, {json.dumps(OPEN)}).act") is None
    assert run_js(RUN_JS.read_text(), "runControlView(null, null).act") is None


@pytest.mark.node_bound
def test_the_answer_of_an_ended_run_says_a_resume_does_not_restart_it():
    src = RUN_JS.read_text()
    ended = run_js(src, 'runControlReply("resume", 409)')
    assert ended["ok"] is False and "does not restart" in ended["text"]
    paused = run_js(src, 'runControlReply("pause", 200)')
    assert paused["ok"] is True and "finishes first" in paused["text"]
    resumed = run_js(src, 'runControlReply("resume", 200)')
    assert resumed["ok"] is True and "squad resume" in resumed["text"], "a resume request never starts the run process"
    assert run_js(src, 'runControlReply("pause", 403).text') == "The hub refused the request (403)."


def test_the_run_module_names_no_loopback_address_and_no_tailnet_host():
    text = RUN_JS.read_text()
    for banned in ("127.0.0.1", "localhost", ".ts.net", "tailnet.host", "fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "import "):
        assert banned not in text, f"the run module holds {banned!r}: the forward route is its only address"


def test_the_run_module_names_no_column_and_no_run_state_rule():
    text = RUN_JS.read_text()
    table = _module("board_columns_run", COLUMNS_PY)
    for name in [c["name"] for c in table.shipped_columns()] + list(table.ROLE.values()):
        assert name not in text, f"the run module names the column {name!r}"
    assert "paused:" not in text and "run_start" not in text, "the page works out no run state: it reads the server's facts"


def test_the_run_rules_are_inlined_not_fetched(hub):
    assert RUN_JS.read_text() in hub.MOBILE_PAGE
    assert "/*RUN*/" not in hub.MOBILE_PAGE
    assert "<script src=" not in hub.MOBILE_PAGE


def test_the_path_the_phone_posts_is_the_hubs_forward_route_to_the_control_route(hub):
    """The page's address and the hub's forward are one seam: the path the phone posts is the run's own control route."""
    assert hub.dash_target(f"/dash/{RUN}/run/control") == (RUN, "/run/control")
    assert hub.dash_target(f"/dash/{RUN}/run/control.json") == (RUN, "/run/control.json")
    assert hub.dash_target(f"/dash/{RUN}/run/info.json") == (RUN, "/run/info.json")


@pytest.mark.node_bound
def test_opening_a_live_run_offers_the_pause_and_states_the_limits(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_RUN + """
      return { open: !$("runview").hidden, name: $("runview-name").textContent, src: $("runframe").src,
               act: btn.dataset.act, label: btn.textContent, hidden: btn.hidden,
               limits: $("runctl-limits").textContent, note: $("runctl-msg").textContent };
    """, routes=routes())
    assert out["open"] is True and out["name"] == "run one"
    assert out["src"] == f"/dash/{RUN}/"
    assert (out["act"], out["label"], out["hidden"]) == ("pause", "Pause the run", False)
    assert "running call finish" in out["limits"] and "does not restart" in out["limits"]


@pytest.mark.node_bound
def test_a_pause_from_the_phone_posts_the_hub_forward_route_with_the_desktop_body(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_RUN + """
      await btn.onclick();
      await __tick(20);
      return { posts: __state.posts, msg: $("runctl-msg").textContent };
    """, routes=routes())
    assert len(out["posts"]) == 1, out
    post = out["posts"][0]
    assert post["path"] == f"/dash/{RUN}/run/control"
    assert post["opts"]["method"] == "POST"
    assert post["opts"]["headers"]["Content-Type"] == "application/json"
    assert post["opts"]["body"] == '{"action":"pause"}'
    assert "finishes first" in out["msg"]


@pytest.mark.node_bound
def test_a_run_asked_to_pause_posts_the_resume_and_says_the_process_is_not_restarted(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN_RUN + """
      await btn.onclick();
      await __tick(20);
      return { act: btn.dataset.act, label: btn.textContent, posts: __state.posts, msg: $("runctl-msg").textContent };
    """, routes=routes(control=ASKED))
    assert (out["act"], out["label"]) == ("resume", "Ask to resume")
    assert [p["opts"]["body"] for p in out["posts"]] == ['{"action":"resume"}']
    assert "squad resume" in out["msg"]


@pytest.mark.node_bound
def test_a_run_that_ended_offers_no_button_and_a_forwarded_409_states_the_limit(hub):
    ended = run_page(hub.MOBILE_PAGE, OPEN_RUN + """
      return { act: btn.dataset.act, hidden: btn.hidden, note: $("runctl-msg").textContent };
    """, routes=routes(info={"ended": True}))
    assert ended["hidden"] is True and ended["act"] == ""
    assert "has ended" in ended["note"]
    refused = run_page(hub.MOBILE_PAGE, OPEN_RUN + """
      await btn.onclick();
      await __tick(20);
      return { msg: $("runctl-msg").textContent };
    """, routes=routes(reply={"body": json.dumps({"ok": False, "error": "run_ended"}), "status": 409}))
    assert "does not restart" in refused["msg"]
