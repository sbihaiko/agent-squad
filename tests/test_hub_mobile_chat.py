"""Chat per project on the phone (SPEC 1596, frames B, C, D, 16, 18 and rules 9 and 10; #1601): the Chat root lists one
row per project with the loop dot, the last message, the time and the waiting mark; a conversation header names the
project, its loop state and its live runs; quick answers are 44 px chips above the composer and above the keyboard; a
send shows the protocol's receipt and links the run it reached; one function counts the Chat badge, Now › Needs you lists
the same waiting rows inside the scope with no badge, and answering clears both.

The rules are one client module (`hub-mobile-chat.js`) and the page binds them. The page is booted under node over the stub
DOM of `tests/test_hub_mobile_move.py`, whose `fetch` answers `/api/sessions`, `/api/sessions/card`,
`/api/sessions/conversation`, `/api/sessions/card/answer` and `/api/loop` and records every write. The tests assert what
the screens show and what the page sends, never a substring of the code that draws them.
"""
import json
import re

from tests.test_hub_mobile_move import hub, run_page  # noqa: F401  (hub is the page fixture)

RUN_ALPHA = "run-20261101-101500-aaaaaa"
T_ALPHA = 1_700_000_000    # 2023-11-14 22:13 UTC
T_BETA = 1_700_003_600

SESSIONS = {"waiting": 2, "groups": [
    {"project": "alpha", "sessions": [
        {"sid": "s1", "name": "run node one", "status": "waiting", "waiting": True,
         "last": "Which scope?", "t": T_ALPHA, "run": RUN_ALPHA},
        {"sid": "s2", "name": "run node two", "status": "busy", "waiting": False, "last": "Landing it", "t": T_ALPHA - 60, "run": RUN_ALPHA},
    ]},
    {"project": "beta", "sessions": [
        {"sid": "s3", "name": "owner chat", "status": "waiting", "waiting": True, "last": "Thanks", "t": T_BETA, "run": ""},
    ]},
    {"project": "gamma", "sessions": [
        {"sid": "s4", "name": "quiet chat", "status": "busy", "waiting": False, "last": "All clear", "t": T_BETA - 60, "run": ""},
    ]},
]}

LOOP = {"projects": [
    {"slug": "alpha", "repo": "x/a", "state": "on", "live": {"runs": ["/r/a/run-1", "/r/a/run-2"], "gates": []}},
    {"slug": "beta", "repo": "x/b", "state": "off", "live": {"runs": [], "gates": []}},
], "changes": []}

CARD = {"card": {"id": "c1", "questions": [{"header": "Which scope?", "body": "Two projects wait.",
         "options": [{"label": "All projects", "description": "count every project"},
                     {"label": "One project", "description": "count the open one"}]}]}}

CONVERSATION = {"turns": [], "next": None, "activity": {}, "epoch": 9, "end": 0}

ROUTES = {
    "/api/sessions": [{"body": json.dumps(SESSIONS)}],
    "/api/loop": [{"body": json.dumps(LOOP)}],
    "/api/sessions/card": [{"body": json.dumps(CARD)}],
    "/api/sessions/conversation": [{"body": json.dumps(CONVERSATION)}],
    "/api/sessions/card/answer": [{"body": json.dumps({"status": "sent"})}],
}


def test_the_chat_root_lists_one_row_per_project_with_its_loop_dot_last_message_time_and_waiting_mark(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        show("chat");
        await loadSessions();
        await __tick(10);
        return $("sessions").querySelectorAll(".chatrow").map(b => ({
          project: b.dataset.project, sid: b.dataset.sid, loop: b.dataset.loop, waiting: b.dataset.waiting,
          dot: b.querySelector(".loopdot").className, text: b.textContent }));
    """, routes=ROUTES)
    assert [r["project"] for r in out] == ["alpha", "beta", "gamma"], out
    alpha, beta, gamma = out
    assert alpha["sid"] == "s1", "the row opens the project's waiting session: " + repr(alpha)
    assert alpha["waiting"] == "1" and "Waiting for you" in alpha["text"], alpha
    assert alpha["loop"] == "on" and "loopdot on" in alpha["dot"], alpha
    assert "Which scope?" in alpha["text"] and "2023-11-14 22:13" in alpha["text"], alpha
    assert beta["waiting"] == "1" and "Waiting for you" in beta["text"], beta
    assert beta["loop"] == "off" and "loopdot off" in beta["dot"], beta
    assert "Thanks" in beta["text"] and "2023-11-14 23:13" in beta["text"], beta
    assert gamma["waiting"] == "0" and "Waiting for you" not in gamma["text"], gamma
    assert gamma["loop"] == "" and "loopdot unknown" in gamma["dot"], gamma
    assert "All clear" in gamma["text"], gamma


def test_one_function_counts_the_badge_and_the_scope_narrows_it(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, f"""
        show("chat");
        await loadSessions();
        await __tick(10);
        const rows = chatRows({json.dumps(SESSIONS)}, {json.dumps(LOOP)}.projects);
        return {{ all: chatBadge(rows, ""), alpha: chatBadge(rows, "alpha"), beta: chatBadge(rows, "beta"),
                  badge: document.querySelector('nav [data-tab=\\"chat\\"]').dataset.badge }};
    """, routes=ROUTES)
    assert out == {"all": 2, "alpha": 1, "beta": 1, "badge": "2"}, out


def test_answering_clears_the_row_and_the_badge(hub):  # noqa: F811
    cleared = json.loads(json.dumps(SESSIONS))
    cleared["waiting"] = 0
    cleared["groups"][0]["sessions"][0]["waiting"] = False
    cleared["groups"][1]["sessions"][0]["waiting"] = False
    out = run_page(hub.MOBILE_PAGE, f"""
        show("chat");
        await loadSessions();
        await __tick(10);
        const before = document.querySelector('nav [data-tab="chat"]').dataset.badge;
        applySessions({json.dumps(cleared)});
        await __tick(5);
        return {{ before, after: document.querySelector('nav [data-tab="chat"]').dataset.badge,
                  wait: $("sessions").querySelectorAll(".chatrow").map(b => b.dataset.waiting) }};
    """, routes=ROUTES)
    assert out["before"] == "2", out
    assert out["after"] == "" and out["wait"] == ["0", "0", "0"], out


def test_now_needs_you_lists_the_same_waiting_rows_in_scope_and_shows_no_badge(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        await loadSessions();
        show("now");
        const seg = [...document.querySelectorAll("#cr-seg button")].map(b => b.textContent);
        [...document.querySelectorAll("#cr-seg button")].find(b => b.textContent === "Needs you").onclick();
        return { seg, rows: $("needs-you").querySelectorAll(".chatrow").map(b => b.dataset.project),
                 badges: $("needs-you").querySelectorAll("[data-badge]").length };
    """, routes=ROUTES)
    assert out["seg"] == ["Needs you", "Any item"], out
    assert out["rows"] == ["alpha", "beta"], "Needs you lists only the rows that wait: " + repr(out)
    assert out["badges"] == 0, "the Needs you list carries no badge of its own: " + repr(out)


def test_quick_answers_are_chips_at_least_44_px_tall_above_the_composer(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        openConversation("s1", "alpha");
        await __tick(30);
        const chips = $("termview-chips").querySelectorAll(".qchip");
        return { labels: chips.map(c => c.textContent), index: chips.map(c => c.dataset.index),
                 order: $("termview").children.map(e => e.id).filter(Boolean) };
    """, routes=ROUTES)
    assert out["labels"] == ["All projects", "One project"], out
    assert out["index"] == ["0", "1"], out
    assert out["order"] == ["termview-head", "termview-older", "termview-turns", "termview-chips", "termview-msg", "termview-compose"], \
        "the chips sit between the thread and the composer, so they ride above it: " + repr(out)
    rule = re.search(r"#termview-chips \.qchip\{([^}]*)\}", hub.MOBILE_PAGE)
    assert rule and "min-height:44px" in rule.group(1), rule


def test_the_chips_stay_above_the_keyboard_while_typing(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        openConversation("s1", "alpha");
        await __tick(30);
        window.visualViewport = { height: 420, offsetTop: 60, addEventListener() {} };
        fitKeyboard();
        return { height: $("termview").style.height, top: $("termview").style.top };
    """, routes=ROUTES)
    assert out == {"height": "420px", "top": "60px"}, out


def test_a_chip_tap_answers_through_the_card_answer_route(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        openConversation("s1", "alpha");
        await __tick(30);
        $("termview-chips").querySelectorAll(".qchip")[1].onclick();
        await __tick(30);
        const post = __state.posts.find(p => p.path === "/api/sessions/card/answer");
        return { body: post ? JSON.parse(post.opts.body) : null, chips: $("termview-chips").children.length,
                 sent: $("termview-sent").textContent };
    """, routes=ROUTES)
    assert out["body"] == {"sid": "s1", "card_id": "c1", "answers": [1]}, out
    assert out["chips"] == 0, "the answered card leaves no chip to tap again: " + repr(out)
    assert out["sent"].startswith("Sent"), out


def test_the_conversation_header_names_the_project_its_loop_state_and_its_live_runs(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        show("chat");
        await loadSessions();
        await __tick(10);
        $("sessions").querySelectorAll(".chatrow")[0].onclick();
        await __tick(30);
        return { name: $("termview-name").textContent, facts: $("termview-facts").textContent };
    """, routes=ROUTES)
    assert out["name"] == "alpha", out
    assert out["facts"] == "Loop on · 2 live runs", out


def test_a_sent_message_shows_the_receipt_and_links_the_run_it_reached(hub):  # noqa: F811
    routes = dict(ROUTES)
    routes["/api/sessions/send"] = [{"body": json.dumps({"status": "sent"})}]
    out = run_page(hub.MOBILE_PAGE, """
        show("chat");
        await loadSessions();
        await __tick(10);
        $("sessions").querySelectorAll(".chatrow")[0].onclick();
        await __tick(30);
        window.crypto = { randomUUID: () => "cid-0000-0000-0000" };   // the stub DOM has no Web Crypto
        $("termview-text").value = "count every project";
        $("termview-send").onclick();
        await __tick(30);
        return { receipt: $("termview-sent").textContent, acted: $("termview-acted").textContent,
                 run: $("termview-acted").dataset.run, hidden: $("termview-acted").hidden };
    """, routes=routes)
    assert out["receipt"].startswith("Sent"), out
    assert out["hidden"] is False and out["run"] == RUN_ALPHA, out
    assert "Acted on" in out["acted"] and RUN_ALPHA[-6:] in out["acted"], out
