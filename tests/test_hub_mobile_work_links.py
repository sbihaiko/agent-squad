"""The phone's Work and Specs screens after SPEC 2498 (#2501): cards are links to GitHub, spec groups lead, and the
sheet and the filters left the page.

Every expected value below comes from the fixture board the hub serves, read where the owner sees it: the page is booted
under the stub DOM of `tests/hub_mobile_stub.py`, and each assertion reads an element, its href, its chips or its
text. A link that leaves the page is a plain anchor, and a card carries only the chips the hub's row facts name.
"""
import json
import re

import pytest

from tests.hub_mobile_stub import hub, run_page  # noqa: F401  (hub is a fixture)

SPEC = 2489
BOARD = {"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None, "done": "Done",
         "sum": {"specs": [{"n": SPEC, "st": "Backlog", "closed": 0, "total": 4}], "waves": []},
         "groups": [
             {"name": "Backlog", "n": 2, "items": [
                 {"n": SPEC, "t": "[SPEC 2498] Remove the hub chat", "l": ["spec"], "sp": True, "pm": True, "pmt": "Todo"},
                 {"n": 2491, "t": "The hub serves no session", "l": [], "pa": SPEC, "b": [2490],
                  "ho": "run-1", "hs": "running", "hh": True, "gchip": "gate try 2"}]},
             {"name": "Todo", "n": 0, "items": []},
             {"name": "Doing", "n": 0, "items": []}]}

GROUPS = BOARD["groups"]


@pytest.mark.node_bound
def test_work_has_no_filter_controls(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        show("work");
        await __tick(0);
        return {
          bfilter: !!document.getElementById("bfilter"),
          spec: !!document.getElementById("bsp"),
          labels: !!document.getElementById("lf"),
          grouping: !!document.getElementById("lmode"),
          empty: !!document.getElementById("hide-empty"),
          seg: [...document.querySelectorAll("#cr-seg button")].map(b => b.textContent),
          controlHidden: document.getElementById("control-row").hidden,
        };
    """, board=[BOARD])
    assert out == {"bfilter": False, "spec": False, "labels": False, "grouping": False, "empty": False,
                   "seg": [], "controlHidden": True}, out


@pytest.mark.node_bound
def test_every_column_is_drawn_with_no_empty_column_filter(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        show("work");
        await __tick(0);
        return [...document.querySelectorAll("#col-tabs button")].map(b => b.textContent);
    """, board=[BOARD])
    assert out == ["Backlog", "Todo", "Doing"], out   # every column, the empty ones included


@pytest.mark.node_bound
def test_a_card_is_a_plain_anchor_to_its_issue(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        show("work");
        await __tick(0);
        return [...document.querySelectorAll("#cards .card")].map(a => ({tag: a.tagName, href: a.href, role: a.getAttribute("role")}));
    """, board=[BOARD])
    assert [c["tag"] for c in out] == ["A", "A"], out
    assert "https://github.com/" + hub.REPO + "/issues/2491" in [c["href"] for c in out], out
    for c in out:
        assert "?" not in c["href"], c            # no query string: iOS opens the app on a tap
        assert c["role"] is None, c


@pytest.mark.node_bound
def test_a_card_shows_only_the_four_hub_chips(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        show("work");
        await __tick(0);
        const card = [...document.querySelectorAll("#cards .card")].find(a => a.dataset.n === "2491");
        return [...card.querySelectorAll(".lchip")].map(c => c.textContent);
    """, board=[BOARD])
    assert out == ["hand hold", "gate try 2", "live run", "blocked by #2490"], out


@pytest.mark.node_bound
def test_the_spec_row_is_an_anchor_to_its_issue_with_its_progress(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        show("specs");
        await __tick(0);
        const row = document.querySelector("#specs-list a.row");
        return row && {tag: row.tagName, href: row.href, text: row.textContent};
    """, board=[BOARD])
    assert out["tag"] == "A", out
    assert out["href"] == "https://github.com/" + hub.REPO + "/issues/2489", out
    assert re.search(r"\d+/\d+ · 0%", out["text"]), out


@pytest.mark.node_bound
def test_a_spec_group_header_promotes_and_names_its_pending_state(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
        show("work");
        await __tick(0);
        const head = () => document.querySelector('#cards .lane-h[data-lane="2489"]');
        const before = head().querySelector(".lane-promote").textContent;
        head().querySelector(".lane-promote").onclick();
        const pending = head().querySelector(".lane-promote").textContent;
        await __tick(0);
        return {before, pending, after: head().querySelector(".lane-promote").textContent, reason: head().querySelector(".lane-reason").textContent};
    """, board=[BOARD], promote={"status": 200, "ct": "application/json", "body": '{"err": "the card is in Doing, not Backlog: only a Backlog card moves"}'})
    assert out["before"] == "→ Todo", out
    assert out["pending"] == "pending", out
    assert out["reason"] == "the card is in Doing, not Backlog: only a Backlog card moves", out


@pytest.mark.node_bound
def test_the_card_sheet_is_gone(hub):  # noqa: F811
    assert 'id="sheet"' not in hub.MOBILE_PAGE
    assert "sh-todo" not in hub.MOBILE_PAGE
    assert "/api/issue" not in hub.MOBILE_PAGE


@pytest.mark.node_bound
def test_a_saved_card_address_opens_now(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, 'return {tab: tabActive, hash: location.hash};', board=[BOARD], hash="#/work/card=7")
    assert out["tab"] == "now", out
    assert out["hash"].startswith("#/now"), out


@pytest.mark.node_bound
def test_every_link_that_leaves_the_page_is_a_plain_anchor(hub):  # noqa: F811
    # The screens are booted and every element whose href leaves the origin is read: it must be an anchor with that href.
    # A same-origin link under the profile prefix passes, as the rule allows.
    out = run_page(hub.MOBILE_PAGE, """
        const out = [];
        const scan = () => { for (const tag of ["a", "area", "link", "form", "iframe", "img", "base"]) {
          for (const e of document.querySelectorAll(tag)) {
            const href = e.href || e.getAttribute("href") || e.getAttribute("action") || e.getAttribute("src") || "";
            if (/^https?:\\/\\//.test(href)) out.push({tag: e.tagName, href});
          }
        } };
        show("work"); await __tick(0); scan();
        show("specs"); await __tick(0); scan();
        return out;
    """, board=[BOARD])
    outside = [x for x in out if not x["href"].startswith("http://localhost") and not x["href"].startswith("http://127.0.0.1")]
    assert outside, out
    assert all(x["tag"] == "A" for x in outside), outside
    # A script redirect is no plain anchor: the page carries no call that leaves the origin on the owner's behalf.
    assert not re.search(r"location\\.href\\s*=", hub.MOBILE_PAGE), "a script redirect leaves the page"
    assert "window.open(" not in hub.MOBILE_PAGE, "a script redirect leaves the page"
    assert "location.assign(" not in hub.MOBILE_PAGE, "a script redirect leaves the page"
