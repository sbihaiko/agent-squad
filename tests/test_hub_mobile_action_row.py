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
TABS = ["now", "work", "specs", "chat", "runs"]


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
        assert options[-1].startswith(("Any ", "Every ")), f"{tab} ends its filter with {options[-1]!r}"


def test_the_control_row_sits_above_the_tab_bar(hub):
    row = re.search(r"#control-row\{([^}]*)\}", hub.MOBILE_PAGE)
    assert row, "the page has no control row style"
    assert "bottom:calc(56px + env(safe-area-inset-bottom))" in row.group(1), \
        f"the control row does not sit on the tab bar: {row.group(1)}"


@pytest.mark.node_bound
def test_a_detail_hides_the_control_row(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN + 'return $("control-row").hidden;', board=[board(CARD)])
    assert out is True, "the control row is a root's, never a detail's"


# --- the action row (rule 7) ---------------------------------------------------------------------------------------

@pytest.mark.node_bound
def test_the_action_row_is_back_dots_one_secondary_and_one_primary(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN + """
        return [...$("sheet-actions").children].map(c => ({ id: c.id, tag: c.tagName, back: "actback" in c.dataset }));
    """, board=[board(CARD)])
    assert out[0] == {"id": "sh-backact", "tag": "BUTTON", "back": True}, f"the first button is no Back: {out}"
    assert out[1]["id"] == "sh-dots" and out[1]["tag"] == "BUTTON", out
    assert [c["id"] for c in out[2:]] == ["sh-close", "sh-todo"], f"the row is not one secondary then one primary: {out}"


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
    """A tap sends once: the primary is disabled from the tap until the board answers, and it names the time."""
    out = run_page(hub.MOBILE_PAGE, OPEN + """
        $("sh-todo").onclick();
        return { label: $("sh-todo-label").textContent, sub: $("sh-todo-sub").textContent, disabled: $("sh-todo").disabled };
    """, board=[board(CARD)])
    assert out["label"] == "Move to Todo", out
    assert re.search(r"requested \d\d:\d\d · board pending", out["sub"]), f"the request time is not named: {out}"
    assert out["disabled"] is True, f"a requested move invites a second send: {out}"


@pytest.mark.node_bound
def test_a_pending_board_row_reads_requested_after_a_reload(hub):
    row = {**CARD, "pd": True}
    out = run_page(hub.MOBILE_PAGE, OPEN + _primary(""), board=[board(row)])
    assert out["label"] == "Move to Todo", out
    assert "requested" in out["sub"] and "board pending" in out["sub"], out
    assert out["disabled"] is True, out


@pytest.mark.node_bound
def test_a_failed_move_names_the_cause_and_retries_on_one_tap(hub):
    row = {**CARD, "mr": "the writer did not answer"}
    out = run_page(hub.MOBILE_PAGE, OPEN + _primary(""), board=[board(row)])
    assert out["label"] == "Move failed · Retry", out
    assert "the writer did not answer" in out["sub"], f"the cause is not named: {out}"
    assert out["disabled"] is False, f"a failed move cannot retry: {out}"


@pytest.mark.node_bound
def test_a_confirmed_move_reads_moved_for_two_seconds_then_the_next_action(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN + """
        $("sh-todo").onclick();
        await __tick(20);
        const confirmed = { label: $("sh-todo-label").textContent, disabled: $("sh-todo").disabled };
        __skip(2000); __fire();
        return { confirmed, next: $("sh-todo-label").textContent };
    """, board=[board(CARD)], promote={"status": 200, "ct": "application/json",
                                       "body": json.dumps({"ok": True, "issue": 7, "to": "Todo", "pending": [7]})})
    assert out["confirmed"] == {"label": "Moved to Todo", "disabled": True}, out
    assert out["next"] == "Move to Todo", f"the confirmed primary does not turn into the next action: {out}"


@pytest.mark.node_bound
def test_an_unanswered_move_reads_unknown_and_never_re_sends(hub):
    out = run_page(hub.MOBILE_PAGE, OPEN + """
        $("sh-todo").onclick();
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
        $("sh-todo").onclick();
        await __tick(20);
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
