"""The phone page draws what the push stream pushes (SPEC 1085, #1091): a conversation turn, a board change and a session-list
change come from the pushed payload, with no read of their route; a gap clears the drawn turns. The page is booted under the
node stub DOM of test_hub_mobile_move.py, whose EventSource records the stream the page opens; the driver emits its events.
"""
import json

from tests.test_hub_mobile_move import hub, run_page  # noqa: F401  (hub is the module fixture)

SID = "sid-1"
CONV_EMPTY = {"turns": [], "next": None, "activity": {}, "epoch": 9, "end": 0}
BOARD_TWO = {"t": 2, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None,
             "groups": [{"name": "Backlog", "n": 0, "items": []},
                        {"name": "Todo", "n": 2, "items": [{"n": 8, "t": "pushed ticket", "l": [], "pm": False, "pmt": None},
                                                           {"n": 9, "t": "second", "l": [], "pm": False, "pmt": None}]}]}

EMIT = """
const stream = () => __state.streams[__state.streams.length - 1];
const emit = (name, d) => stream().handlers[name].forEach(fn => fn({ data: JSON.stringify(d) }));
const turnLine = (uid, text) => JSON.stringify({ id: uid, role: "owner", text, at: "", m: null }) + "\\n";
"""


def stream_page(mod, monkeypatch):
    monkeypatch.setenv("SQUAD_HUB_STREAM", "1")
    return mod.mobile_html().decode()


def test_a_pushed_turn_draws_with_no_conversation_read(hub, monkeypatch):  # noqa: F811
    page = stream_page(hub, monkeypatch)
    driver = EMIT + """
      openConversation(%(sid)s, "Session one");
      await __tick(5);
      const before = __state.gets["/api/sessions/conversation"] || 0;
      const opened = stream().url;
      emit("conversation", { sid: %(sid)s, epoch: 9, at: 0, end: turnLine("t1", "hello").length, lines: turnLine("t1", "hello") });
      await loadConversation();
      await __tick(5);
      const drawn = $("termview-turns").children.map(el => el.textContent);
      return { opened, drawn, reads: (__state.gets["/api/sessions/conversation"] || 0) - before, live: squadStream.live() };
    """.replace("%(sid)s", json.dumps(SID))
    out = run_page(page, driver, routes={"/api/sessions/conversation": [{"body": json.dumps(CONV_EMPTY)}]})
    assert "conv=sid-1:9:0" in out["opened"], out["opened"]
    assert out["drawn"] == ["ownerhello"], out["drawn"]
    assert out["reads"] == 0, "a pushed turn must draw with no request to the conversation route"
    assert out["live"] is True


def test_a_pushed_board_redraws_from_the_payload_with_no_board_read(hub, monkeypatch):  # noqa: F811
    page = stream_page(hub, monkeypatch)
    driver = EMIT + """
      await __tick(5);
      const reads = __state.reads;
      emit("board", { v: 5, data: %(board)s });
      await __tick(5);
      return { groups: board.groups.map(g => g.items.length), reads: __state.reads - reads };
    """.replace("%(board)s", json.dumps(BOARD_TWO))
    out = run_page(page, driver)
    assert out["groups"] == [0, 2]
    assert out["reads"] == 0


def test_a_pushed_session_list_redraws_from_the_payload_with_no_list_read(hub, monkeypatch):  # noqa: F811
    page = stream_page(hub, monkeypatch)
    sessions = {"groups": [{"project": "proj", "sessions": [{"sid": SID, "name": "Session one", "status": "idle", "last": ""}]}]}
    driver = EMIT + """
      await __tick(5);
      const before = __state.gets["/api/sessions"] || 0;
      emit("sessions", { v: 3, data: %(sessions)s });
      await __tick(5);
      return { rows: $("sessions").children.length, reads: (__state.gets["/api/sessions"] || 0) - before };
    """.replace("%(sessions)s", json.dumps(sessions))
    out = run_page(page, driver)
    assert out["rows"] == 1
    assert out["reads"] == 0


def test_a_gap_clears_the_drawn_turns_and_the_window_redraws_from_its_start(hub, monkeypatch):  # noqa: F811
    page = stream_page(hub, monkeypatch)
    driver = EMIT + """
      openConversation(%(sid)s, "Session one");
      await __tick(5);
      emit("conversation", { sid: %(sid)s, epoch: 9, at: 0, end: 1, lines: turnLine("t1", "first") });
      emit("gap", { subject: "conversation", sid: %(sid)s, epoch: 9, start: 40 });
      emit("conversation", { sid: %(sid)s, epoch: 9, at: 40, end: 80, lines: turnLine("t2", "second") });
      await __tick(5);
      return { drawn: $("termview-turns").children.map(el => el.textContent) };
    """.replace("%(sid)s", json.dumps(SID))
    out = run_page(page, driver, routes={"/api/sessions/conversation": [{"body": json.dumps(CONV_EMPTY)}]})
    assert out["drawn"] == ["ownersecond"], out["drawn"]


def test_the_polls_stay_as_the_fallback_when_no_stream_is_live(hub):  # noqa: F811
    driver = """
      openConversation(%(sid)s, "Session one");
      await __tick(5);
      const before = __state.gets["/api/sessions/conversation"] || 0;
      await loadConversation();
      return { reads: (__state.gets["/api/sessions/conversation"] || 0) - before, streams: __state.streams.length };
    """.replace("%(sid)s", json.dumps(SID))
    page = hub.mobile_html().decode()
    out = run_page(page, driver, routes={"/api/sessions/conversation": [{"body": json.dumps(CONV_EMPTY)}]})
    assert out["streams"] == 0
    assert out["reads"] == 1
