"""The phone page draws what the push stream pushes (SPEC 1085, #1091): a board change comes from the pushed payload, with
no read of its route, and a pushed conversation turn reaches no screen.

The chat left the page with its rule module (#2490), so the page asks the stream for the board alone and holds no thread
to draw a turn into. The page is booted under the node stub DOM of hub_mobile_stub.py, whose EventSource records the
stream the page opens; the driver emits its events.
"""
import json

from tests.hub_mobile_stub import hub, run_page  # noqa: F401  (hub is the module fixture)

BOARD_TWO = {"t": 2, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None,
             "groups": [{"name": "Backlog", "n": 0, "items": []},
                        {"name": "Todo", "n": 2, "items": [{"n": 8, "t": "pushed ticket", "l": [], "pm": False, "pmt": None},
                                                           {"n": 9, "t": "second", "l": [], "pm": False, "pmt": None}]}]}

EMIT = """
const stream = () => __state.streams[__state.streams.length - 1];
const emit = (name, d) => stream().handlers[name].forEach(fn => fn({ data: JSON.stringify(d) }));
const turnLine = (uid, text, k) => JSON.stringify({ id: uid, k: k || uid, role: "owner", text, at: "", m: null }) + "\\n";
"""


def stream_page(mod, monkeypatch):
    monkeypatch.setenv("SQUAD_HUB_STREAM", "1")
    return mod.mobile_html().decode()


def test_the_phone_page_takes_no_session_or_conversation_subject(hub, monkeypatch):  # noqa: F811
    page = stream_page(hub, monkeypatch)
    driver = EMIT + """
      await __tick(5);
      emit("ping", {});
      return { subjects: Object.keys(stream().handlers),
               reads: Object.keys(__state.gets).filter(p => p.startsWith("/api/sessions")),
               term: !!document.getElementById("termview"), live: squadStream.live() };
    """
    out = run_page(page, driver)
    assert out["reads"] == [], f"the phone page reads a session route: {out}"
    assert "sessions" not in out["subjects"] and "conversation" not in out["subjects"], out
    assert "board" in out["subjects"], out
    assert out["term"] is False, "the phone page still holds a chat thread"
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


def test_the_phone_page_takes_the_desktop_chart_and_loss_events_without_error(hub, monkeypatch):  # noqa: F811
    page = stream_page(hub, monkeypatch)
    driver = EMIT + """
      await __tick(5);
      emit("cfd", { v: 3 });
      emit("lead", { v: 4 });
      emit("ping", {});
      stream().onerror();
      stream().onopen();
      await __tick(5);
      return { live: squadStream.live() };
    """
    out = run_page(page, driver)
    assert out["live"] is True


def test_a_board_that_is_not_ready_names_itself_and_the_other_listeners_still_run(hub, monkeypatch):  # noqa: F811
    page = stream_page(hub, monkeypatch)
    driver = EMIT + """
      squadStream.on("board", () => { __state.later = true; });
      emit("board", { v: 6, data: { t: 3, err: "board not ready" } });
      await __tick(5);
      return { msg: $("bmsg").textContent, later: __state.later === true };
    """
    out = run_page(page, driver)
    assert out["msg"] == "The board is not ready.", out
    assert out["later"] is True


def test_the_stream_names_the_board_alone(hub, monkeypatch):  # noqa: F811
    page = stream_page(hub, monkeypatch)
    driver = EMIT + """
      await __tick(5);
      return { url: stream().url };
    """
    out = run_page(page, driver)
    assert out["url"].endswith("want=board"), out["url"]
    assert "sessions" not in out["url"] and "conversation" not in out["url"], out["url"]
