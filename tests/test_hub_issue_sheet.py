"""The ticket sheet inside the hub (#919): the card's own data, GitHub's rendered HTML, and one read on open.

The sheet is one panel on the desktop page and one bottom sheet on the phone page. It draws the card data the page already
holds (column, labels, blocked by, blocks, the run that holds it, the pull request that closes it); the body and the last
comment come from GitHub's own rendered HTML (`bodyHTML`), read on open through the hub. REST carries no rendered body, so
the read is one GraphQL call. The route is driven over HTTP through the hub; no network and no browser.
"""
import http.client
import importlib.util
import json
import re
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"
GHREAD_PY = SCRIPTS / "ghread.py"


def get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse()
    body = r.read().decode()
    c.close()
    return r.status, r.getheader("Content-Type") or "", body


def _rest(path, timeout=30):
    """The REST read `issue_detail` makes, stubbed: one open issue with one comment."""
    if path.endswith("/comments?per_page=100"):
        return [{"user": {"login": "ann"}, "created_at": "2026-10-31T09:00:00Z", "body": "last comment, plain"}]
    return {"number": 7, "title": "a ticket", "state": "open", "html_url": "https://github.com/x/y/issues/7",
            "user": {"login": "ann"}, "created_at": "2026-10-30T08:00:00Z", "updated_at": "2026-10-31T09:00:00Z",
            "labels": [{"name": "bug"}], "body": "a body, plain"}


def _graphql(query, timeout=30, **vars):
    """The one GraphQL read, stubbed: GitHub's rendered HTML of the body and of the last comment."""
    return {"data": {"repository": {"issueOrPullRequest": {
        "bodyHTML": '<p>a body, <code>rendered</code></p><ul class="contains-task-list">'
                    '<li><input type="checkbox" disabled> one</li></ul>',
        "comments": {"totalCount": 3, "nodes": [
            {"author": {"login": "bo"}, "createdAt": "2026-10-31T10:00:00Z", "bodyHTML": "<p>last comment, rendered</p>"}]}}}}}


@pytest.fixture
def hub(monkeypatch, tmp_path):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE", "SG_STATE_BASE", "SG_SHARED", "SG_PROFILE", "SG_FORK_DIR", "SG_PROJECTS", "SG_TOOLING_REPO"):
        monkeypatch.delenv(k, raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", GHREAD_PY)
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_issue_sheet", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.board_cache = {"t": 1, "items": [], "prs": [], "err": None, "lc": {}, "errs": [], "cases": []}
    monkeypatch.setattr(mod, "gh_json", _rest)
    monkeypatch.setattr(mod, "gh_graphql", _graphql)
    monkeypatch.setattr(mod, "_issue_cache", {})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, srv.server_port
    srv.shutdown()
    srv.server_close()


# --- the read on open: GitHub's own rendered HTML, and the last comment -------------------------------------------------

def test_the_issue_read_carries_githubs_rendered_body_and_last_comment(hub):
    mod, _ = hub
    d = mod.issue_detail(7)
    assert "<code>rendered</code>" in d["html"], f"the body is GitHub's rendered HTML: {d['html']!r}"
    assert d["last"]["html"] == "<p>last comment, rendered</p>", d["last"]
    assert d["last"]["by"] == "bo" and d["last"]["at"] == "2026-10-31T10:00:00Z", d["last"]


def test_the_read_route_serves_that_html(hub):
    _, port = hub
    status, ct, body = get(port, "/api/issue?n=7")
    assert status == 200 and ct == "application/json"
    d = json.loads(body)
    assert "<code>rendered</code>" in d["html"], f"the route serves the rendered body: {d}"
    assert d["last"]["html"] == "<p>last comment, rendered</p>", d.get("last")


def test_a_graphql_read_that_fails_leaves_the_plain_body_and_never_the_panel_error(hub, monkeypatch):
    mod, _ = hub
    monkeypatch.setattr(mod, "gh_graphql", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_issue_cache", {})
    d = mod.issue_detail(7)
    assert "err" not in d, "a failed rendered read is not a failed issue read"
    assert d["html"] == "" and d["body"] == "a body, plain", d


# --- GitHub's rendered HTML, allowed through one allow-list ---------------------------------------------------------------

def test_the_rendered_html_keeps_githubs_structure_and_drops_what_can_run(hub):
    """The body is GitHub's own HTML (body_html); the hub keeps its structure in its own type and colors and drops every
    tag or attribute that could run, so an issue body never reaches the page's origin with a script."""
    mod, _ = hub
    out = mod.render_html(
        '<div class="markdown-body"><h2>Build</h2><p>a <code>x</code></p>'
        '<ul class="contains-task-list"><li><input type="checkbox" checked disabled> done</li></ul>'
        '<script>alert(1)</script><style>p{color:red}</style><svg><path d="M0"/></svg>'
        '<a href="javascript:alert(1)">bad</a><a href="https://github.com/x/y/issues/2">good</a>'
        '<iframe src="https://x"></iframe><img src="https://x/a.png" onerror="go()"></div>')
    for gone in ("<script", "<style", "<svg", "<iframe", "javascript:", "onerror", "class=", "style="):
        assert gone not in out, f"the rendered body still carries {gone!r}: {out}"
    assert "<h2>Build</h2>" in out and "<code>x</code>" in out, out
    assert '<a href="https://github.com/x/y/issues/2" target="_blank" rel="noopener">good</a>' in out, out
    assert '<img src="https://x/a.png"' in out, out


def test_a_task_list_checkbox_keeps_its_look(hub):
    mod, _ = hub
    out = mod.render_html('<ul><li><input type="checkbox" disabled> open</li><li><input type="checkbox" checked disabled> done</li></ul>')
    assert out.count('<input type="checkbox"') == 2, out
    assert "checked" in out.split("</li>")[1], "a checked task keeps its check"


def test_a_relative_link_is_dropped_instead_of_pointing_at_the_hub(hub):
    mod, _ = hub
    out = mod.render_html('<a href="/x/y/issues/3">mention</a>')
    assert "href" not in out and "mention" in out, out


def test_an_unterminated_tag_is_escaped_not_passed_through(hub):
    mod, _ = hub
    out = mod.render_html("<img src=x onerror=alert(1)<p>text</p>")
    assert not re.search(r"<[^<>]*\bonerror", out), f"a live handler survives: {out}"
    assert re.sub(r"</?[a-zA-Z][^<>]*>", "", out).count("<") == 0, f"a '<' is left outside a tag: {out}"


# --- the card data the page already holds, served on the row -------------------------------------------------------------

def _item(n, title="a ticket", status="Todo", **extra):
    return {"n": n, "t": title, "s": "open", "st": status, "l": ["wave:1"], **extra}


def _row(data, n):
    return next(x for g in data["groups"] for x in g["items"] if x["n"] == n)


def test_the_board_names_the_cards_this_one_blocks(hub):
    """`blocks` is the reverse of `blocked by`, so the sheet needs no rule of its own (ADR-0137)."""
    mod, _ = hub
    mod.board_cache["items"] = [_item(910), _item(911, b=[910]), _item(912, b=[910])]
    data = mod.board()
    assert _row(data, 910)["bl"] == [911, 912], "the card carries the cards it blocks"
    assert not _row(data, 911).get("bl"), "a card that blocks nothing carries no row"


def test_the_board_names_the_run_that_holds_a_card(hub):
    mod, _ = hub
    mod.board_cache["items"] = [_item(920, status="Doing", h={"run": "run-20261031-132937-e1eead", "started": "2026-10-31T10:00:00Z"})]
    row = _row(mod.board(), 920)
    assert row["hr"] == "run-20261031-132937-e1eead", row
    assert row["ho"].startswith("held \u00b7 run run-20261031-132937-e1eead"), row   # the Live work reading: what holds it, and how old


def test_the_board_names_the_pull_request_that_closes_a_card(hub):
    mod, _ = hub
    mod.board_cache["items"] = [_item(930)]
    mod.board_cache["prs"] = [{"n": 940, "t": "the fix", "u": "2026-10-31T10:00:00Z", "ps": ["running", "CI running"],
                               "rollup": "pass", "cl": [930], "h": "a" * 40, "ci": ["success"], "r": "x/y", "facts": True}]
    prf = _row(mod.board(), 930)["prf"]
    assert [p["n"] for p in prf] == [940], prf
    assert prf[0]["ci"] == "pass" and prf[0]["st"], prf


# --- the desktop sheet: one side panel that pushes the board --------------------------------------------------------------

SHEET_IDS = ("tk", "tkchips", "tknav", "tkset", "tkb", "tkgh", "tktodo", "tkrel", "tkx")
SHEET_FIELDS = ("Column", "Labels", "Blocked by", "Blocks", "Runs", "PR · CI", "Opened")


def test_the_served_page_carries_the_ticket_sheet(hub):
    _, port = hub
    status, ct, body = get(port, "/")
    assert status == 200 and ct.startswith("text/html")
    for ident in SHEET_IDS:
        assert re.search(rf'id=["\']?{ident}["\'\s>]', body), f"the served page has no #{ident}"


def test_the_sheet_is_a_side_panel_that_pushes_the_board(hub):
    page = hub[0].PAGE
    assert "#tk{position:fixed;top:0;right:0;bottom:0;width:420px" in page, "the sheet is a side panel on the right"
    assert "body.tkopen #win{margin-right:420px}" in page, "the sheet pushes the page instead of covering it"
    assert "#content{flex:1;min-width:0;overflow:auto}" in page, "the content pane takes what the sheet leaves (#889: the board is a full-width pane, so the push narrows it)"
    assert "scrollIntoView" in page, "the selected card is scrolled into view"


def test_the_sheet_shows_every_field_the_card_carries(hub):
    page = hub[0].PAGE
    for field in SHEET_FIELDS:
        assert f">{field}</b>" in page, f"the sheet shows no {field!r} row"


def test_the_sheet_renders_the_served_html_and_never_frames_github(hub):
    page = hub[0].PAGE
    line = next(ln for ln in page.splitlines() if "<div id=tk " in ln)
    assert "<iframe" not in line, "the sheet holds no frame"
    assert "d.html" in page, "the body is the rendered HTML the route serves"
    assert "d.last" in page, "the last comment is rendered from the same read"


def test_the_todo_button_is_drawn_only_for_a_served_promotable_card(hub):
    page = hub[0].PAGE
    assert "$(\"#tktodo\").hidden" in page, "the → Todo button reads the served promotable fact"
    assert "c.pm" in page, "the button is shown for a card the board marks promotable, never by a column rule of its own"


def test_the_sheet_steps_a_selection_with_buttons_chips_and_the_keys(hub):
    page = hub[0].PAGE
    assert 'e.key==="j"' in page and 'e.key==="k"' in page, "j and k step the selection"
    assert "ArrowRight" in page and "ArrowLeft" in page, "the arrow keys step the selection"
    assert " of ${nav.length}" in page, "the sheet names where the card stands in its selection"
    assert "data-o" in page and "tkset" in page, "the number chips pick a card"


def test_escape_and_the_close_button_close_the_sheet_and_clear_the_selection(hub):
    page = hub[0].PAGE
    assert "function closeSheet(" in page, "one close rule"
    assert 'e.key==="Escape"' in page and "closeSheet()" in page, "Escape closes the sheet"
    body = page[page.index("function closeSheet("):]
    body = body[:body.index("\n")]
    for clear in ("tkopen", "nav=[]", "hit", '$("#tkset").innerHTML=""'):
        assert clear in body, f"closing the sheet does not clear {clear!r}"


def test_a_blocker_link_opens_that_ticket_in_the_same_sheet(hub):
    page = hub[0].PAGE
    assert 'a[data-o]' in page, "a blocker link opens the card it names"
    assert "openCard(" in page, "the link opens the same sheet, never a new tab"
