"""The phone's control row, action row and six primary states (SPEC 1596, #1598): one control row on every root, one
action row on every detail, and one primary that carries its true state and never invites a second send.

The page is booted under the stub DOM of test_hub_mobile_move, so every state is read from fixture facts the hub serves:
the board row (`pm`, `pmt`, `pd`, `mr`), the promote route's answer, the issue read, and the page's own request record.
The card detail is the first consumer; no new write route is named.
"""
import importlib.util
import json
import os
import pathlib
import re
import tempfile

import pytest

from tests.test_hub_mobile_move import run_page

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
CARD_JS = SCRIPTS / "hub-mobile-card.js"
HUB_PY = SCRIPTS / "hub.py"
TABS = ["now", "work", "specs", "runs"]


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_mobile_action_row", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def board(row, column="Backlog"):
    return {"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None,
            "groups": [{"name": column, "n": 1, "items": [row]},
                       {"name": "Todo", "n": 0, "items": []}]}


CARD = {"n": 7, "t": "a ticket", "l": [], "pm": True, "pmt": "Todo"}
OPEN = """
    await loadBoard();
    openCard(7);
"""


# --- the control row (rule 4) --------------------------------------------------------------------------------------

@pytest.mark.node_bound
def test_every_root_draws_one_control_row(hub):
    out = run_page(hub.MOBILE_PAGE, f"""
        const seen = {{}};
        for (const t of {TABS!r}) {{
          show(t);
          seen[t] = {{ hidden: $("control-row").hidden,
                       seg: [...document.querySelectorAll("#cr-seg button")].map(b => b.textContent),
                       scope: $("cr-scope").textContent }};
        }}
        return seen;
    """)
    for tab in TABS:
        v = out[tab]
        assert v["hidden"] is False, f"{tab} draws no control row: {v}"
        if tab != "now":
            assert v["seg"], f"{tab} draws no segmented filter: {v}"
        assert v["scope"] in ("All projects", "x"), f"{tab} names no scope: {v}"


@pytest.mark.node_bound
def test_the_last_filter_option_says_what_it_includes(hub):
    out = run_page(hub.MOBILE_PAGE, f"""
        const seen = {{}};
        for (const t of {TABS!r}) {{ show(t); seen[t] = [...document.querySelectorAll("#cr-seg button")].map(b => b.textContent); }}
        return seen;
    """)
    for tab, options in out.items():
        assert not options or options[-1].startswith(("Any ", "Every ")), f"{tab} ends its filter with {options[-1]!r}"


def test_the_control_row_sits_above_the_tab_bar(hub):
    row = re.search(r"#control-row\{([^}]*)\}", hub.MOBILE_PAGE)
    assert row, "the page has no control row style"
    assert "bottom:calc(56px + env(safe-area-inset-bottom))" in row.group(1), \
        f"the control row does not sit on the tab bar: {row.group(1)}"


def _two_cards():
    return {"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None,
            "groups": [{"name": "Backlog", "n": 2, "items": [
                            {"n": 7, "t": "a live card", "l": [], "pm": True, "pmt": "Todo", "ho": True},
                            {"n": 8, "t": "a quiet card", "l": [], "pm": True, "pmt": "Todo"}]},
                       {"name": "Todo", "n": 0, "items": []}]}


@pytest.mark.node_bound
def test_the_work_segment_filters_the_card_list(hub):
    """The active segment is applied when the root paints: "Live" keeps only a card with a live run, and "Any card"
    brings the rest back (rule 6)."""
    out = run_page(hub.MOBILE_PAGE, """
        await loadBoard();
        show("work");
        const drawn = () => [...$("cards").querySelectorAll('[data-role="card"]')].map(c => +c.dataset.n);
        const all = drawn();
        [...document.querySelectorAll("#cr-seg button")].find(b => b.textContent === "Live").onclick();
        const live = drawn();
        [...document.querySelectorAll("#cr-seg button")].find(b => b.textContent === "Any card").onclick();
        return { all, live, back: drawn() };
    """, board=[_two_cards()])
    assert out["all"] == [7, 8], out
    assert out["live"] == [7], f"the Live segment changes nothing the list shows: {out}"
    assert out["back"] == [7, 8], out


@pytest.mark.node_bound
def test_a_root_with_one_view_offers_one_filter_option(hub):
    """A root the page cannot filter presents no second segment: no button promises a filter that does not exist. Specs
    offers one; Now offers none, because its waiting rows left the page with the chat (#2490)."""
    out = run_page(hub.MOBILE_PAGE, """
        show("now");
        const now = [...document.querySelectorAll("#cr-seg button")].map(b => b.textContent);
        show("specs");
        return { now, specs: [...document.querySelectorAll("#cr-seg button")].map(b => b.textContent) };
    """)
    assert out == {"now": [], "specs": ["Any spec"]}, out


@pytest.mark.node_bound
def test_a_detail_hides_the_control_row(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN + 'return $("control-row").hidden;', board=[board(CARD)])
    assert out is True, "the control row is a root's, never a detail's"


# --- the action row (rule 7) ---------------------------------------------------------------------------------------

@pytest.mark.node_bound
def test_the_action_row_holds_the_actions_alone(hub):
    """Back lives at the top of the screen, so the card's action row carries its actions alone (SPEC 2498, #2502)."""
    out = run_page(hub.MOBILE_PAGE, OPEN + """
        return { row: [...$("sheet-actions").children].map(c => ({ id: c.id, tag: c.tagName })),
                 backs: $("sheet-actions").querySelectorAll("[data-actback]").length,
                 bar: $("sheet-actions").hidden };
    """, board=[board(CARD)])
    assert [c["id"] for c in out["row"]] == ["sh-dots", "sh-close", "sh-todo"], out
    assert out["backs"] == 0, f"the card's action row still holds a back control: {out}"
    assert out["bar"] is False, f"the row holds an action and is not drawn: {out}"


def test_a_screen_with_no_action_draws_no_action_row(hub):
    """An action bar stays only where it holds an action (SPEC 2498, #2502): a screen whose only control was Back
    carries its one back at the top and no footer."""
    page = hub.MOBILE_PAGE
    for gone in ("loops-actions", "prof-actions", "lead-actions", "bugs-actions"):
        assert not re.search(rf'id="{gone}"|id={gone}\b', page), f"the page still draws the empty action row {gone}"
    for kept in ("sheet-actions", "psw-footer", "runview-actions", "loop-actions"):
        assert re.search(rf'id="{kept}"|id={kept}\b', page), f"the action row {kept} left a screen that holds an action"
    assert "[data-actback]" not in page and "data-actback" not in page, \
        "a control still marks itself as a footer Back; every detail carries one back at the top"


# --- the six primary states (rule 7) -------------------------------------------------------------------------------

def _primary(driver):
    return driver + """
        return { label: $("sh-todo-label").textContent, sub: $("sh-todo-sub").textContent,
                 disabled: $("sh-todo").disabled, hidden: $("sh-todo").hidden };
    """


@pytest.mark.node_bound
def test_the_primary_reads_available_from_the_rows_move_facts(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN + _primary(""), board=[board(CARD)])
    assert out == {"label": "Move to Todo", "sub": "", "disabled": False, "hidden": False}, out


@pytest.mark.node_bound
def test_a_requested_move_is_disabled_and_names_the_request_time(hub):
    """A tap stages the move under the Undo; when the undo window ends the write is the one request, the primary is
    disabled until the board answers, and it names the time."""
    out = run_page(hub.MOBILE_PAGE, OPEN + """
        $("sh-todo").onclick();
        const staged = { posts: __state.posts.length, undo: $("undo").hidden === false };
        __skip(5000); __fire();                        // the undo window ends; only the confirmed write calls sendMove
        const requested = { label: $("sh-todo-label").textContent, sub: $("sh-todo-sub").textContent, disabled: $("sh-todo").disabled };
        return { staged, requested };
    """, board=[board(CARD)], promote={"status": 200, "ct": "application/json", "body": "{}"})
    assert out["staged"] == {"posts": 0, "undo": True}, f"a tap wrote before the undo window ended: {out}"
    req = out["requested"]
    assert req["label"] == "Move to Todo", out
    assert re.search(r"requested \d\d:\d\d · board pending", req["sub"]), f"the request time is not named: {out}"
    assert req["disabled"] is True, f"a requested move invites a second send: {out}"


@pytest.mark.node_bound
def test_the_primary_stages_the_move_so_the_undo_cancels_the_write(hub):
    """The Undo window main always had on this button stays: a tap stages, the Undo cancels, and no write is ever sent."""
    out = run_page(hub.MOBILE_PAGE, OPEN + """
        $("sh-todo").onclick();
        $("undo-btn").onclick();
        __skip(6000); __fire();
        await __tick(30);
        return { posts: __state.posts.filter(p => p.path === "/api/promote").length,
                 card: $("cards").querySelector('[data-role="card"][data-n="7"]') !== null };
    """, board=[board(CARD)])
    assert out["posts"] == 0, f"an undone move was written: {out}"
    assert out["card"] is True, out


@pytest.mark.node_bound
def test_a_pending_board_row_reads_requested_after_a_reload(hub):
    row = {**CARD, "pd": True}
    out = run_page(hub.MOBILE_PAGE, OPEN + _primary(""), board=[board(row)])
    assert out["label"] == "Move to Todo", out
    assert "requested" in out["sub"] and "board pending" in out["sub"], out
    assert out["disabled"] is True, out


@pytest.mark.node_bound
def test_a_failed_move_names_the_cause_and_retries_on_one_tap(hub):
    """Only a write error is failed: the hub's unreadable answer names its status and one tap retries it."""
    out = run_page(hub.MOBILE_PAGE, OPEN + """
        $("sh-todo").onclick(); __skip(5000); __fire(); await __tick(30);
        const failed = { label: $("sh-todo-label").textContent, sub: $("sh-todo-sub").textContent, disabled: $("sh-todo").disabled };
        $("sh-todo").onclick(); await __tick(30);       // a failed move retries on one tap
        return { failed, posts: __state.posts.filter(p => p.path === "/api/promote").length };
    """, board=[board(CARD)], promote={"status": 500, "ct": "application/json", "body": "{}"})
    assert out["failed"]["label"] == "Move failed · Retry", out
    assert "500" in out["failed"]["sub"], f"the cause is not named: {out}"
    assert out["failed"]["disabled"] is False, f"a failed move cannot retry: {out}"
    assert out["posts"] == 2, f"one tap did not retry the failed move: {out}"


@pytest.mark.node_bound
def test_a_board_refusal_reads_refused_and_stays_disabled(hub):
    """The row's own refusal (`mr`) is the board's answer: it names the rule, stays disabled and offers it as the
    secondary. It is never drawn as a failed write that invites Retry."""
    rule = "the card is a ticket of a spec: it moves with its spec, never alone (ADR-0142)"
    row = {**CARD, "mr": rule}
    out = run_page(hub.MOBILE_PAGE, OPEN + """
        return { label: $("sh-todo-label").textContent, sub: $("sh-todo-sub").textContent,
                 disabled: $("sh-todo").disabled, second: $("sh-close").textContent };
    """, board=[board(row)])
    assert out["label"] == "Move to Todo", out
    assert "refused" in out["sub"] and rule in out["sub"], f"the rule is not named: {out}"
    assert out["disabled"] is True, f"a refused move invites a retry: {out}"
    assert out["second"] == rule, f"the rule is not offered as the secondary: {out}"


@pytest.mark.node_bound
def test_a_confirmed_move_reads_moved_for_two_seconds_then_never_re_invites_it(hub):
    """Two seconds of "Moved to", then the next action. While the board still names the move the page just took, the
    primary stays disabled on it: it never invites a second send of the same move."""
    out = run_page(hub.MOBILE_PAGE, OPEN + """
        $("sh-todo").onclick(); __skip(5000); __fire(); await __tick(30);
        const confirmed = { label: $("sh-todo-label").textContent, disabled: $("sh-todo").disabled };
        __skip(2000); __fire();
        const after = { label: $("sh-todo-label").textContent, disabled: $("sh-todo").disabled };
        $("sh-todo").onclick(); await __tick(30);       // a tap on the board's same move sends nothing
        return { confirmed, after, posts: __state.posts.filter(p => p.path === "/api/promote").length };
    """, board=[board(CARD)], promote={"status": 200, "ct": "application/json",
                                       "body": json.dumps({"ok": True, "issue": 7, "to": "Todo", "pending": [7]})})
    assert out["confirmed"] == {"label": "Moved to Todo", "disabled": True}, out
    assert out["after"] == {"label": "Move to Todo", "disabled": True}, \
        f"the confirmed primary re-offers the move it just took: {out}"
    assert out["posts"] == 1, f"the confirmed move sent twice: {out}"


@pytest.mark.node_bound
def test_a_confirmed_move_that_the_board_no_longer_offers_ends_the_request(hub):
    """The next action is real only when the board drops the move or names another one; a dropped move hides the primary."""
    gone = {"n": 7, "t": "a ticket", "l": [], "pm": False}
    out = run_page(hub.MOBILE_PAGE, OPEN + """
        $("sh-todo").onclick(); __skip(5000); __fire(); await __tick(30);
        __skip(2000); __fire();
        return { hidden: $("sh-todo").hidden, disabled: $("sh-todo").disabled };
    """, board=[board(CARD), board(gone)], promote={"status": 200, "ct": "application/json",
                                                    "body": json.dumps({"ok": True, "issue": 7, "to": "Todo"})})
    assert out["hidden"] is True, f"a move the board no longer offers still draws a primary: {out}"


@pytest.mark.node_bound
def test_the_confirmed_primary_names_an_off_loop_switch_fact(hub):
    """The loop-switch fact the status read serves is threaded into the primary: a card moved into Todo says it waits
    while its loop is off, and says nothing of the loop when it is on."""
    off = run_page(hub.MOBILE_PAGE, OPEN + """
        $("sh-todo").onclick(); __skip(5000); __fire(); await __tick(30);
        return { on: loopFact && loopFact.on, sub: $("sh-todo-sub").textContent };
    """, board=[board(CARD)], promote={"status": 200, "ct": "application/json", "body": "{}"},
        routes={"/api/status": [{"status": 200, "ct": "application/json", "body": json.dumps({"loop": {"on": False, "since": "1h"}, "live": 0, "spend_today": 0, "open_bugs": 0, "warnings": [], "load": {"line": ""}})}]})
    assert off["on"] is False, off
    assert "the loop is off" in off["sub"], f"the primary does not name the loop-switch fact: {off}"
    on = run_page(hub.MOBILE_PAGE, OPEN + """
        $("sh-todo").onclick(); __skip(5000); __fire(); await __tick(30);
        return { on: loopFact && loopFact.on, sub: $("sh-todo-sub").textContent };
    """, board=[board(CARD)], promote={"status": 200, "ct": "application/json", "body": "{}"},
        routes={"/api/status": [{"status": 200, "ct": "application/json", "body": json.dumps({"loop": {"on": True, "since": None}, "live": 0, "spend_today": 0, "open_bugs": 0, "warnings": [], "load": {"line": ""}})}]})
    assert on["on"] is True, on
    assert "the loop is off" not in on["sub"], f"an on loop is drawn as off: {on}"


@pytest.mark.node_bound
def test_an_unanswered_move_reads_unknown_and_never_re_sends(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN + """
        $("sh-todo").onclick(); __skip(5000); __fire();
        await __tick(30);
        const first = { label: $("sh-todo-label").textContent, sub: $("sh-todo-sub").textContent, disabled: $("sh-todo").disabled };
        $("sh-todo").onclick();   // a lost reply never sends the request again
        await __tick(30);
        return { first, posts: __state.posts.filter(p => p.path === "/api/promote").length };
    """, board=[board(CARD)], promote={"throw": "Failed to fetch"})
    assert out["first"]["label"] == "Board unreachable", out
    assert "checking" in out["first"]["sub"], out
    assert out["first"]["disabled"] is True, out
    assert out["posts"] == 1, f"an unknown move sent twice: {out}"


@pytest.mark.node_bound
def test_a_refused_move_names_the_rule_and_offers_it_as_the_secondary(hub):
    rule = "the card is already moving to Todo: the writer has not answered yet"
    out = run_page(hub.MOBILE_PAGE, OPEN + """
        $("sh-todo").onclick(); __skip(5000); __fire();
        await __tick(30);
        return { label: $("sh-todo-label").textContent, sub: $("sh-todo-sub").textContent,
                 disabled: $("sh-todo").disabled, second: $("sh-close").textContent, secondHidden: $("sh-close").hidden };
    """, board=[board(CARD)], promote={"status": 400, "ct": "application/json",
                                       "body": json.dumps({"err": rule})})
    assert out["label"] == "Move to Todo", out
    assert "refused" in out["sub"] and rule in out["sub"], f"the rule is not named: {out}"
    assert out["disabled"] is True, out
    assert out["second"] == rule and out["secondHidden"] is False, f"the rule is not offered as the secondary: {out}"


# --- the card body (rule 7, user story 25) -------------------------------------------------------------------------

ISSUE = {"n": 7, "r": "x/y", "title": "a ticket", "state": "open", "url": "https://github.com/x/y/issues/7",
         "body": "the body", "html": "<p>the body</p>", "total": 14,
         "comments": [{"by": "reviewer", "at": "2026-11-08T09:40:00Z", "body": "the latest word"}],
         "last": {"by": "reviewer", "at": "2026-11-08T09:40:00Z", "html": "<p>the latest word</p>"}}


@pytest.mark.node_bound
def test_a_card_shows_next_step_latest_update_related_and_two_rows(hub):
    row = {**CARD, "pa": 1540, "hr": "run-a", "prf": [{"n": 31, "ci": "pass"}]}
    out = run_page(hub.MOBILE_PAGE, OPEN + """
        await __tick(30);
        return { next: $("sh-next-text").textContent, update: $("sh-update").textContent,
                 related: $("sh-related").textContent,
                 desc: $("sh-desc-row").textContent, act: $("sh-act-row").textContent };
    """, board=[board(row)], routes={"/api/issue": [{"status": 200, "ct": "application/json", "body": json.dumps(ISSUE)}]})
    assert out["next"] == "The card is in Backlog.", out
    for heading in ("Next step", "Latest update", "Related"):
        assert f"<h3>{heading}</h3>" in hub.MOBILE_PAGE, f"the card body draws no {heading!r} row"
    assert "reviewer" in out["update"] and "the latest word" in out["update"], out
    assert "SPEC 1540" in out["related"] and "run-a" in out["related"] and "#31 · CI pass" in out["related"], out
    assert out["desc"].startswith("Description"), out
    assert out["act"].startswith("Activity") and "14" in out["act"], out


@pytest.mark.parametrize("row,said", [
    ({"n": 7, "t": "a ticket", "l": [], "b": [9, 12]}, "Blocked by #9, #12."),
    ({"n": 7, "t": "a ticket", "l": [], "prf": [{"n": 31, "ci": "fail"}]}, "Fix the failing CI on #31."),
    ({"n": 7, "t": "a ticket", "l": [], "prf": [{"n": 31, "ci": "pending"}]}, "Wait for CI on #31."),
    ({"n": 7, "t": "a ticket", "l": []}, "The card is in Backlog."),
])
@pytest.mark.node_bound
def test_the_next_step_names_one_served_fact(hub, row, said):
    out = run_page(hub.MOBILE_PAGE, OPEN + 'return $("sh-next-text").textContent;', board=[board(row)])
    assert out == said, out


# --- the rule module ------------------------------------------------------------------------------------------------

def test_the_card_rules_are_inlined_not_fetched(hub):
    assert CARD_JS.read_text() in hub.MOBILE_PAGE, "the mobile page does not carry the card detail rules"
    assert "/*CARD_DETAIL*/" not in hub.MOBILE_PAGE
    assert "<script src=" not in hub.MOBILE_PAGE


def test_the_card_rules_hold_no_dom_and_no_network():
    text = CARD_JS.read_text()
    for banned in ("document", "fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "import "):
        assert banned not in text, f"the card rule module holds {banned!r}"
