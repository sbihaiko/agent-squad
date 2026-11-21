"""The phone page polls over a slow tailnet (#1004): one request per endpoint in flight, with a deadline; an answer older
than the endpoint's last request is dropped; a poll for a hidden app or a hidden panel does not run; the palette is
derived once per board read, and a card whose data did not change keeps its node.

The page is booted under node over the stub DOM of the move tests; its timers are recorded and fired by the driver.
"""
import json

import pytest

import tests.hub_mobile_stub as move
from tests.hub_mobile_stub import BOARD_ONE, hub  # noqa: F401  (hub is a fixture)

# The stub DOM with its intervals recorded, so a driver can fire every poll the page set.
INTERVALS = move.DOM_HARNESS.replace(
    "setInterval: () => 0,",
    "setInterval: (fn, ms) => { (box.__intervals = box.__intervals || []).push({ fn, ms }); return box.__intervals.length; },")

# A fetch the driver holds: a board read waits until the driver answers it; every GET is counted by its path.
HOLD = """
const held = [], gets = {}, real = fetch;
const answer = b => ({ ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => b, text: async () => JSON.stringify(b) });
globalThis.fetch = (p, o) => {
  const name = String(p).split("?")[0];
  if (!o || !o.method) gets[name] = (gets[name] || 0) + 1;
  if (name === "/api/board") return new Promise(r => held.push(r));
  return real(p, o);
};
"""


def _board(title):
    return {**BOARD_ONE, "groups": [{"name": "Backlog", "n": 1, "items": [{"n": 7, "t": title, "l": []}]},
                                    {"name": "Todo", "n": 0, "items": []}]}


@pytest.mark.node_bound
def test_an_older_answer_never_overwrites_a_newer_board(hub):  # noqa: F811
    out = move.run_page(hub.MOBILE_PAGE, HOLD + f"""
      show("board"); await __tick(5);
      held.splice(0).forEach(r => r(answer({json.dumps(_board("first"))})));   // the read the panel made at once
      await __tick(10);
      const poll = loadBoard(), write = loadBoard(true);   // the read after a write supersedes the poll in flight
      const sent = held.length;
      held[1](answer({json.dumps(_board("new"))}));
      await write; await __tick(5);
      held[0](answer({json.dumps(_board("old"))}));
      await __tick(10);
      return {{ sent, title: board.groups[0].items[0].t }};
    """)
    assert out["sent"] == 2, out
    assert out["title"] == "new", out


@pytest.mark.node_bound
def test_one_read_per_endpoint_is_in_flight(hub):  # noqa: F811
    out = move.run_page(hub.MOBILE_PAGE, HOLD + """
      show("board"); await __tick(5);
      const before = gets["/api/board"];
      loadBoard(); loadBoard(); loadBoard();
      await __tick(5);
      return { sent: gets["/api/board"] - before };
    """)
    assert out["sent"] == 0, "a poll joins the board read already in flight"


@pytest.mark.node_bound
def test_a_read_past_its_deadline_fails_and_frees_its_endpoint(hub):  # noqa: F811
    out = move.run_page(hub.MOBILE_PAGE, HOLD + """
      show("board"); await __tick(5);
      __fire(); await __tick(10);                // the deadline of the read in flight passes
      const msg = $("bmsg").textContent, before = gets["/api/board"];
      loadBoard(); await __tick(5);
      return { msg, sent: gets["/api/board"] - before };
    """)
    assert "did not answer" in out["msg"], out
    assert out["sent"] == 1, "the endpoint is free after the deadline"


@pytest.mark.node_bound
def test_a_poll_for_a_hidden_app_or_a_hidden_panel_does_not_run(hub, monkeypatch):  # noqa: F811
    monkeypatch.setattr(move, "DOM_HARNESS", INTERVALS)
    out = move.run_page(hub.MOBILE_PAGE, HOLD + f"const BOARD = {json.dumps(BOARD_ONE)};" + """
      const tick = () => { for (const k of Object.keys(gets)) delete gets[k]; for (const i of __intervals) i.fn(); return __tick(10).then(() => ({ ...gets })); };
      document.hidden = true;
      const away = await tick();
      document.hidden = false;
      const monitor = await tick();
      show("board"); await __tick(10);
      held.splice(0).forEach(r => r(answer(BOARD)));   // the read the panel made at once is answered, so the poll is free
      await __tick(10);
      const onBoard = await tick();
      return { away, monitor, onBoard };
    """)
    polled = ("/api/status", "/api/loop", "/api/load", "/api/board")
    assert not [p for p in polled if p in out["away"]], out["away"]
    assert {"/api/status", "/api/loop", "/api/load"} <= set(out["monitor"]), out["monitor"]
    assert "/api/board" not in out["monitor"], out["monitor"]
    assert "/api/board" in out["onBoard"] and not {"/api/loop", "/api/load"} & set(out["onBoard"]), out


@pytest.mark.node_bound
def test_the_reconnect_screen_clears_with_no_tap_on_another_tab(hub, monkeypatch):  # noqa: F811
    """`#reconnect` covers the whole page, so the status poll that hides it runs on every tab while the app is in view."""
    monkeypatch.setattr(move, "DOM_HARNESS", INTERVALS)
    out = move.run_page(hub.MOBILE_PAGE, """
      const real = fetch; let down = true;
      globalThis.fetch = (p, o) => down && String(p).startsWith("/api/status") ? Promise.reject(new Error("offline")) : real(p, o);
      show("board"); await __tick(5);
      await status(); await __tick(5);
      const shown = !$("reconnect").hidden;
      down = false;
      for (const i of __intervals) i.fn();
      await __tick(10);
      return { shown, after: !$("reconnect").hidden };
    """)
    assert out["shown"], "the hub is down: the reconnect screen shows"
    assert not out["after"], "the hub answers again: a poll on the Board tab clears the screen with no tap"


@pytest.mark.node_bound
def test_the_palette_is_derived_once_per_board_read(hub):  # noqa: F811
    out = move.run_page(hub.MOBILE_PAGE, """
      await loadBoard();
      return { same: mSlots() === mSlots() };
    """)
    assert out["same"], "the spec palette is recomputed per card"


@pytest.mark.node_bound
def test_only_a_changed_card_is_rebuilt(hub):  # noqa: F811
    two = {**BOARD_ONE, "groups": [{"name": "Backlog", "n": 2, "items": [{"n": 7, "t": "a", "l": []}, {"n": 8, "t": "b", "l": []}]},
                                   {"name": "Todo", "n": 0, "items": []}]}
    changed = {**two, "groups": [{**two["groups"][0], "items": [{"n": 7, "t": "a", "l": []}, {"n": 8, "t": "b2", "l": []}]},
                                 two["groups"][1]]}
    out = move.run_page(hub.MOBILE_PAGE, """
      const card = n => $("cards").querySelector('[data-role="card"][data-n="' + n + '"]');
      await loadBoard(); const a7 = card(7), a8 = card(8);
      await loadBoard(); const b7 = card(7), b8 = card(8);
      return { kept7: a7 === b7, kept8: a8 === b8, title8: b8.textContent };
    """, board=[two, changed])
    assert out["kept7"] and not out["kept8"], out
    assert "b2" in out["title8"], out
