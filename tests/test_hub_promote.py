"""The owner's one move: a card in the hub's Backlog column carries an arrow that moves it to Todo (#560).

ADR-0142 makes Todo the owner's go signal, and ADR-0143 says the loop never moves a card into Backlog or Todo: this
control is the owner's, and it shows on Backlog cards only. ADR-0144 says a writer sends a board write only when the
value differs, and the hub's cache follows the write so the card moves at the next refresh without a GitHub read
right after it.

`POST /api/promote` carries the body `{"issue": N}` and the same guard `/api/models` and `/api/plans` carry: a
loopback Host, the hub's own Origin and a JSON body. The route accepts one open issue whose card is in Backlog now,
and it writes Status=Todo through the board seam, `board.sh`.

A spec card moves with its open Backlog sub-issues in one write (ADR-0142, #838): the owner starts a spec by moving its
card, and its tickets follow. A ticket the board holds in another column, or closed, keeps its column (ADR-0143).
"""
import http.client
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
HUB = {}   # the hub under test, while its fixture runs: `post` reads the board only after a background write has answered (#1300)
GHREAD_PY = ROOT / "contrib/squad-goal/scripts/ghread.py"
NODE = shutil.which("node")

BACKLOG = {"n": 7, "i": 101, "t": "a ticket", "s": "OPEN", "st": "Backlog", "l": ["enhancement"]}
TODO = {"n": 8, "i": 102, "t": "already started", "s": "OPEN", "st": "Todo", "l": []}
SPEC = {"n": 9, "i": 103, "t": "a spec", "s": "OPEN", "st": "Backlog", "l": ["spec"]}


@pytest.fixture
def hub(monkeypatch, tmp_path):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE", "SG_STATE_BASE", "SG_SHARED", "SG_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", GHREAD_PY)   # the one GitHub door (ADR-0277), the object the hub imports
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)   # registered before it runs: its dataclasses look the module up by name
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_promote", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.board_cache = {"t": 1, "items": [dict(BACKLOG), dict(TODO), dict(SPEC)], "prs": [], "err": None,
                       "lc": {}, "errs": [], "cases": []}
    HUB["mod"] = mod
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, srv.server_port
    HUB.pop("mod", None)
    mod.wait_for_moves()
    srv.shutdown()
    srv.server_close()


def post(port, body, *, host=None, origin=None, content_type="application/json", path="/api/promote"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{port}", "Origin": origin if origin is not None else f"http://127.0.0.1:{port}"}
    if content_type:
        headers["Content-Type"] = content_type
    c.request("POST", path, body=json.dumps(body), headers=headers)
    r = c.getresponse()
    data = json.loads(r.read() or b"{}")
    c.close()
    if HUB.get("mod") is not None:   # the writer answers in the background: the board is read after it
        HUB["mod"].wait_for_moves()
    return r.status, data


def writer(calls, rc=0, err=""):
    """The board seam as the route calls it: the issue and the `--add` flag are recorded, and the answer is canned."""
    def run(issue, add=False):
        calls.append((issue, add))
        return subprocess.CompletedProcess(["board.sh"], rc, f"#{issue} -> Todo\n", err)
    return run


def test_the_writer_call_is_the_board_seam(hub, monkeypatch):
    mod, port = hub
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "#7 -> Todo\n", "")

    monkeypatch.setattr(mod.subprocess, "run", fake)
    assert mod.promote_write(7).returncode == 0
    assert calls and calls[0][1].endswith("board.sh") and calls[0][2:] == ["x", "4", "Todo", "7"], calls
    assert mod.promote_write([9, 20, 21], add=True).returncode == 0
    assert calls[1][1].endswith("board.sh") and calls[1][2:] == ["--add", "x", "4", "Todo", "9", "20", "21"], calls


def test_the_writer_call_carries_the_state_directory_the_hub_resolved(hub, monkeypatch):
    """A hub started with no SG_STATE (hubctl starts it with none) still resolves its state directory by the seam, and the
    board seam it starts reads $SG_STATE: the writer call carries the one the hub resolved, never the empty environment.
    Without it board.py dies on the key and the card shows the traceback (#1750)."""
    mod, _ = hub
    kw = {}

    def fake(cmd, **options):
        kw.update(options)
        return subprocess.CompletedProcess(cmd, 0, "#7 -> Todo\n", "")

    monkeypatch.setattr(mod.subprocess, "run", fake)
    mod.promote_write(7)
    assert (kw.get("env") or {}).get("SG_STATE") == str(mod.STATE), kw


def test_a_backlog_card_moves_to_todo_and_the_cache_follows(hub):
    mod, port = hub
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 7})
    assert status == 200 and out.get("ok") is True, out
    assert calls == [([7], False)], calls        # a row with a Project item moves with the plain write
    board = mod.board()
    groups = {g["name"]: [i["n"] for i in g["items"]] for g in board["groups"]}
    assert 7 in groups["Todo"] and 7 not in groups["Backlog"], groups
    assert next(i for i in mod.board_cache["items"] if i["n"] == 7)["st"] == "Todo"


def test_a_card_the_board_read_never_carried_is_added_and_moves(hub):
    """#815: `board_pass` draws an open issue with no Project item as a Backlog row (`st` null, no item id). The route asks
    the seam for `--add` there, because a plain move is answered `#<n> not on the board` and writes nothing."""
    mod, port = hub
    calls = []
    mod.promote_write = writer(calls)
    mod.board_cache["items"].append({"n": 12, "t": "filed by hand", "s": "OPEN", "st": None, "l": []})
    status, out = post(port, {"issue": 12})
    assert status == 200 and out.get("ok") is True, out
    assert calls == [([12], True)], calls


def test_a_foreign_origin_is_refused(hub):
    mod, port = hub
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 7}, origin="https://evil.example")
    assert status == 400 and out == {"err": "forbidden"}, (status, out)
    assert calls == []


def test_a_non_loopback_host_is_refused(hub):
    mod, port = hub
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 7}, host=f"evil.example:{port}")
    assert status == 400 and out == {"err": "forbidden"}, (status, out)
    assert calls == []


@pytest.mark.parametrize("route", ["/api/promote", "/api/models", "/api/plans"])
def test_a_loopback_write_needs_the_hubs_own_origin_on_its_own_port(hub, monkeypatch, route):
    """#824: a loopback write passes only with the hub's own exact Origin, on either loopback name and the hub's port.
    Another local port is refused on every write route, and the refusal writes nothing."""
    mod, port = hub
    calls = []
    mod.promote_write = writer(calls)
    monkeypatch.setattr(mod, "models_set", lambda body: calls.append("models") or {"saved": True})
    monkeypatch.setattr(mod, "plans_set", lambda body: calls.append("plans") or {"saved": True})
    body = {"issue": 7} if route == "/api/promote" else {}
    for origin in (f"http://127.0.0.1:{port + 1}", f"http://localhost:{port + 1}"):
        assert post(port, body, origin=origin, path=route) == (400, {"err": "forbidden"}), origin
    assert calls == []
    for origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
        status, out = post(port, body, origin=origin, path=route)
        assert out != {"err": "forbidden"}, (origin, status, out)
    assert calls


def test_a_plain_text_body_is_refused(hub):
    mod, port = hub
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 7}, content_type="text/plain")
    assert status == 400 and out == {"err": "forbidden"}, (status, out)
    assert calls == []


def test_a_card_not_in_backlog_is_refused(hub):
    mod, port = hub
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 8})
    assert status == 400 and "Backlog" in out.get("err", ""), out
    assert calls == []


def test_a_closed_issue_is_refused(hub):
    mod, port = hub
    mod.board_cache["items"][0]["s"] = "CLOSED"
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 7})
    assert status == 400 and "closed" in out.get("err", ""), out
    assert calls == []


def sub_issues(monkeypatch, answer=None, failure=None):
    """The sub-issue read as the hub makes it through ghread (ADR-0277): one answer, or a refusal or failure the hub must
    not act on. Returns the routes it was asked for."""
    gh = sys.modules["ghread"]
    routes = []

    def read(route, transport=None):
        routes.append(route)
        if failure is not None:
            return failure
        return gh.Read(kind=gh.ANSWER, answer=answer)

    monkeypatch.setattr(gh, "read", read)
    return routes


def test_a_spec_with_no_ticket_moves_alone(hub, monkeypatch):
    mod, port = hub
    routes = sub_issues(monkeypatch, answer=[])
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 9})
    assert status == 200 and out == {"ok": True, "issue": 9, "to": "Todo", "pending": [9]}, out
    assert calls == [([9], False)], calls
    assert routes and routes[0].startswith("repos/x/y/issues/9/sub_issues"), routes
    assert next(i for i in mod.board_cache["items"] if i["n"] == 9)["st"] == "Todo"


def test_a_spec_moves_with_its_backlog_tickets_in_one_write(hub, monkeypatch):
    mod, port = hub
    mod.board_cache["items"] += [{"n": 20, "i": 120, "t": "ticket a", "s": "OPEN", "st": "Backlog", "l": []},
                                 {"n": 21, "i": 121, "t": "ticket b", "s": "OPEN", "st": "Backlog", "l": ["enhancement"]}]
    sub_issues(monkeypatch, answer=[{"number": 20, "state": "open", "labels": []},
                                         {"number": 21, "state": "open", "labels": [{"name": "enhancement"}]}])
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 9})
    assert status == 200 and out == {"ok": True, "issue": 9, "to": "Todo", "pending": [9, 20, 21]}, out
    assert calls == [([9, 20, 21], False)], calls
    groups = {g["name"]: [i["n"] for i in g["items"]] for g in mod.board()["groups"]}
    assert {9, 20, 21} <= set(groups["Todo"]) and not {9, 20, 21} & set(groups["Backlog"]), groups


def project_item(n, reason, labels=()):
    """One project item as the REST items read answers it: an open issue of this repository, with a close reason."""
    return {"content_type": "Issue", "id": f"PVT_{n}",
            "fields": [{"name": "Status", "value": {"name": {"raw": "Backlog"}}}],
            "content": {"number": n, "repository_url": "https://api.github.com/repos/x/y", "title": f"t{n}",
                        "state": "OPEN", "state_reason": reason, "closed_at": None,
                        "labels": [{"name": x} for x in labels],
                        "sub_issues_summary": {"total": 0, "completed": 0},
                        "created_at": "2026-10-01T00:00:00Z"}}


def test_a_reopened_card_keeps_its_repository_so_its_spec_moves_its_tickets(hub, monkeypatch):
    """#921: REST answers state_reason "reopened" for an issue closed and reopened (#817). The close reason rides its own
    key, never the row's repository: the spec's sub-issue read goes to x/y, and a reopened Backlog ticket moves with its
    spec (ADR-0142)."""
    mod, port = hub
    jq = shutil.which("jq")
    if jq is None:
        pytest.skip("jq is not installed")
    read = subprocess.run([jq, "-c", mod.board_items_jq("x/y")],
                          input=json.dumps([project_item(9, "reopened", ["spec"]), project_item(20, "reopened")]),
                          capture_output=True, text=True, check=True).stdout
    mod.board_cache["items"] = [row for row in (json.loads(line) for line in read.splitlines()) if "_page" not in row]
    routes = sub_issues(monkeypatch, answer=[{"number": 20, "state": "open"}])
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 9})
    assert routes and routes[0].startswith("repos/x/y/issues/9/sub_issues"), routes
    assert status == 200 and out.get("pending") == [9, 20], out


def test_a_ticket_without_an_item_id_is_added_with_the_spec(hub, monkeypatch):
    mod, port = hub
    mod.board_cache["items"].append({"n": 22, "t": "filed by hand", "s": "OPEN", "st": None, "l": []})
    sub_issues(monkeypatch, answer=[{"number": 22, "state": "open"}])
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 9})
    assert status == 200 and out.get("pending") == [9, 22], out
    assert calls == [([9, 22], True)], calls


def test_a_closed_ticket_or_one_outside_backlog_keeps_its_column(hub, monkeypatch):
    mod, port = hub
    mod.board_cache["items"] += [{"n": 20, "i": 120, "t": "closed", "s": "CLOSED", "st": "Backlog", "l": []},
                                 {"n": 21, "i": 121, "t": "in progress", "s": "OPEN", "st": "Doing", "l": []}]
    sub_issues(monkeypatch, answer=[{"number": 20, "state": "closed"}, {"number": 21, "state": "open"},
                                         {"number": 23, "state": "open"}])   # 23 is on no board the hub holds
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 9})
    assert status == 200 and out.get("pending") == [9], out
    assert calls == [([9], False)], calls
    items = {i["n"]: i["st"] for i in mod.board_cache["items"]}
    assert items[20] == "Backlog" and items[21] == "Doing", items


def test_a_ticket_that_is_itself_a_spec_keeps_its_column(hub, monkeypatch):
    """A spec is never work (ADR-0142): a spec under a spec is named by the read and left where it is."""
    mod, port = hub
    mod.board_cache["items"].append({"n": 22, "i": 122, "t": "a sub-spec", "s": "OPEN", "st": "Backlog", "l": ["spec"]})
    sub_issues(monkeypatch, answer=[{"number": 22, "state": "open"}])
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 9})
    assert status == 200 and out.get("pending") == [9], out
    assert calls == [([9], False)], calls


def test_a_ticket_with_an_owner_label_keeps_its_column(hub, monkeypatch):
    """The janitor's rule (janitor.py, #589): a sub-issue labelled needs-owner or needs-ratification is the owner's decision,
    and one labelled wontfix is abandoned. The spec's move never puts such a ticket in Todo, where a dispatcher would take it
    as work (ADR-0142 sections 2 and 6)."""
    mod, port = hub
    mod.board_cache["items"] += [{"n": 20, "i": 120, "t": "owner decides", "s": "OPEN", "st": "Backlog", "l": ["needs-owner"]},
                                 {"n": 21, "i": 121, "t": "ratify", "s": "OPEN", "st": "Backlog", "l": ["needs-ratification"]},
                                 {"n": 22, "i": 122, "t": "dropped", "s": "OPEN", "st": "Backlog", "l": ["wontfix"]}]
    sub_issues(monkeypatch, answer=[{"number": 20, "state": "open"}, {"number": 21, "state": "open"}, {"number": 22, "state": "open"}])
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 9})
    assert status == 200 and out.get("pending") == [9], out
    assert calls == [([9], False)], calls
    items = {i["n"]: i["st"] for i in mod.board_cache["items"]}
    assert items[20] == items[21] == items[22] == "Backlog", items


def test_a_spec_reads_every_page_of_its_sub_issues(hub, monkeypatch):
    """The sub-issue read follows pages while a page comes back full: a ticket on the second page moves with the spec."""
    mod, port = hub
    gh = sys.modules["ghread"]
    mod.board_cache["items"].append({"n": 20, "i": 120, "t": "ticket on page two", "s": "OPEN", "st": "Backlog", "l": []})
    pages = {"page=1": [{"number": 1000 + k, "state": "open"} for k in range(100)], "page=2": [{"number": 20, "state": "open"}]}
    routes = []

    def read(route, transport=None):
        routes.append(route)
        return gh.Read(kind=gh.ANSWER, answer=pages["page=2" if "page=2" in route else "page=1"])

    monkeypatch.setattr(gh, "read", read)
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 9})
    assert status == 200 and out.get("pending") == [9, 20], out
    assert calls == [([9, 20], False)], calls
    assert len(routes) == 2 and "page=2" in routes[1], routes


def test_a_sub_issue_of_another_repository_is_not_a_card_of_this_board(hub, monkeypatch):
    """A sub-issue is matched to a card by repository as well as number. Sub-issue 20 of another repository is not card 20 of
    this board, and it is skipped; sub-issue 21 of this repository (its URL in any letter case) moves with the spec."""
    mod, port = hub
    mod.board_cache["items"] += [{"n": 20, "i": 120, "t": "this repo's card 20", "s": "OPEN", "st": "Backlog", "l": []},
                                 {"n": 21, "i": 121, "t": "this repo's card 21", "s": "OPEN", "st": "Backlog", "l": []}]
    sub_issues(monkeypatch, answer=[{"number": 20, "state": "open", "repository_url": "https://api.github.com/repos/x/other"},
                                         {"number": 21, "state": "open", "repository_url": "https://api.github.com/repos/X/Y"}])
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 9})
    assert status == 200 and out.get("pending") == [9, 21], out
    assert calls == [([9, 21], False)], calls
    items = {i["n"]: i["st"] for i in mod.board_cache["items"]}
    assert items[20] == "Backlog" and items[21] == "Todo", items


def test_the_writer_timeout_scales_with_the_cards(hub, monkeypatch):
    """The writer's base is 120 s, and each card after the first adds SG_BOARD_SETTLE_SECONDS (default 60), since board.py
    settles every card it moves."""
    mod, _ = hub
    seen = []

    def fake(cmd, **kw):
        seen.append(kw.get("timeout"))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(mod.subprocess, "run", fake)
    monkeypatch.delenv("SG_BOARD_SETTLE_SECONDS", raising=False)
    mod.promote_write(7)
    mod.promote_write([9, 20, 21])
    monkeypatch.setenv("SG_BOARD_SETTLE_SECONDS", "5")
    mod.promote_write([9, 20, 21])
    assert seen == [120, 240, 130], seen


def test_a_writer_timeout_names_a_partial_move_and_asks_for_a_read(hub, monkeypatch, tmp_path):
    """A timed-out writer may have moved some cards. The answer says the move may be partial, the cache is not written over
    cards the hub cannot prove, and the board-dirty flag asks the next pass to read the real columns."""
    mod, port = hub
    monkeypatch.setattr(mod, "STATE", tmp_path)
    mod.board_cache["items"].append({"n": 20, "i": 120, "t": "ticket", "s": "OPEN", "st": "Backlog", "l": []})
    sub_issues(monkeypatch, answer=[{"number": 20, "state": "open"}])

    def timed_out(issues, add=False):
        raise subprocess.TimeoutExpired(["board.sh"], 240)

    mod.promote_write = timed_out
    status, out = post(port, {"issue": 9})
    assert status == 200 and out.get("ok") is True and out.get("pending") == [9, 20], out   # the page is not held for the writer
    assert "may be partial" in rows_by_number(mod)[9].get("mr", ""), rows_by_number(mod)[9]
    assert (tmp_path / "board-dirty").exists(), "the next board pass is not asked to read the moved cards"
    assert next(i for i in mod.board_cache["items"] if i["n"] == 20)["st"] == "Backlog"


def test_a_failed_ticket_read_moves_nothing(hub, monkeypatch):
    mod, port = hub
    gh = sys.modules["ghread"]
    for failure in (gh.Read(kind=gh.FAILED, detail="gh timed out"),
                    gh.Read(kind=gh.REFUSED, message="API rate limit exceeded", request_id="AB12:3C")):
        sub_issues(monkeypatch, failure=failure)
        calls = []
        mod.promote_write = writer(calls)
        status, out = post(port, {"issue": 9})
        assert status == 400 and out.get("err"), out
        assert calls == [], calls
        assert next(i for i in mod.board_cache["items"] if i["n"] == 9)["st"] == "Backlog"


def test_a_refused_write_is_the_reason_the_card_shows(hub):
    mod, port = hub
    calls = []
    mod.promote_write = writer(calls, rc=1, err="#7: the board carries no 'Todo' on it after 3 reads; a read failure\n")
    status, out = post(port, {"issue": 7})
    assert status == 200 and out.get("ok") is True, out   # the answer is at once; the refusal lands on the card (#1300)
    assert "read failure" in rows_by_number(mod)[7].get("mr", ""), rows_by_number(mod)[7]
    assert next(i for i in mod.board_cache["items"] if i["n"] == 7)["st"] == "Backlog"


def test_the_arrow_is_on_backlog_cards_only(hub):
    """The arrow shows on every Backlog card, a spec included: the owner's move starts the spec (ADR-0142, section 6)."""
    mod, port = hub
    board = mod.board()
    groups = {g["name"]: {i["n"]: i for i in g["items"]} for g in board["groups"]}
    assert groups["Backlog"][7].get("pm") is True
    assert not groups["Todo"][8].get("pm")
    assert groups["Backlog"][9].get("pm") is True, "a spec card carries the arrow: the owner's move starts it (ADR-0142 section 6)"


def test_the_page_draws_the_arrow_and_delegates_the_move(hub):
    mod, port = hub
    page = mod.PAGE
    assert "class=pmp" in page, "the card carries no promote arrow"
    assert "x.pm&&!pr" in page, "the arrow is not bound to the row's own promotable fact"
    assert 'fetch("/api/promote"' in page and "JSON.stringify({issue:n})" in page, "the click does not delegate the move"
    assert 'e.target.closest("button")' in page, "a click on the arrow also opens the card's GitHub page"


def ticket(n, **extra):
    """A Backlog card the board read recorded as a sub-issue of spec #9: `pa` is that parent (#829)."""
    return {"n": n, "i": 100 + n, "t": f"ticket {n}", "s": "OPEN", "st": "Backlog", "l": [], "pa": 9, **extra}


def test_a_ticket_of_a_spec_is_never_promotable(hub):
    """ADR-0142: a ticket moves with its spec, never alone. The board does not mark the ticket, and the route refuses it
    before any write, so a drag or an arrow on it can never move the ticket by itself."""
    mod, port = hub
    mod.board_cache["items"].append(ticket(30))
    groups = {g["name"]: {i["n"]: i for i in g["items"]} for g in mod.board()["groups"]}
    assert not groups["Backlog"][30].get("pm"), "a ticket of a spec was marked promotable"
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 30})
    assert status == 400 and "spec" in out.get("err", ""), out
    assert calls == [], calls


def test_the_board_route_and_the_promote_route_ask_one_rule(hub, monkeypatch):
    """The served `pm` and the route's refusal are the same decision: a card the board marks not promotable is refused,
    and a card it marks promotable is written. The test compares every card against the one rule."""
    mod, port = hub
    sub_issues(monkeypatch, answer=[])   # a spec reads its sub-issues; the read is the test's, never GitHub's
    mod.board_cache["items"] += [ticket(30), {**ticket(31), "l": ["spec"]},
                                 {"n": 32, "i": 132, "t": "no parent", "s": "OPEN", "st": "Backlog", "l": []}]
    rows = {i["n"]: i for g in mod.board()["groups"] for i in g["items"]}
    for item in mod.board_cache["items"]:
        assert bool(rows[item["n"]].get("pm")) == mod.promotable(item), (item, rows[item["n"]])
    for item, want in ((ticket(30), False), ({**ticket(31), "l": ["spec"]}, True)):
        mod.board_cache["items"] = [dict(item)]
        calls = []
        mod.promote_write = writer(calls)
        status, out = post(port, {"issue": item["n"]})
        assert (status == 200) is want, (item, status, out)
        assert bool(calls) is want, (item, calls)


def test_a_spec_that_is_itself_a_ticket_moves(hub, monkeypatch):
    """A Backlog spec is promotable whether or not a spec holds it: the first clause of the rule is the spec label."""
    mod, port = hub
    sub_issues(monkeypatch, answer=[])
    mod.board_cache["items"].append({**ticket(31), "l": ["spec"]})
    calls = []
    mod.promote_write = writer(calls)
    assert post(port, {"issue": 31})[0] == 200
    assert calls == [([31], False)], calls


def test_the_row_names_the_column_the_move_goes_to(hub):
    """The page names no column of its own: the destination is the row's `pmt`, the same column the route writes."""
    mod, _ = hub
    rows = {i["n"]: i for g in mod.board()["groups"] for i in g["items"]}
    assert rows[7]["pm"] is True and rows[7]["pmt"] == mod.PROMOTE_TO
    assert rows[9]["pmt"] == mod.PROMOTE_TO
    assert "pmt" not in rows[8]


def test_the_board_pass_records_the_spec_a_ticket_belongs_to(hub):
    """The parent read: a card whose parent carries the spec label is a ticket, recorded as `pa`. A parent that is not a
    spec is no spec, and a card with no parent is an ordinary issue."""
    mod, _ = hub
    items = [{"n": 30}, {"n": 31}, {"n": 32}]
    nodes = [{"number": 30, "parent": {"number": 9, "labels": {"nodes": [{"name": "spec"}]}}},
             {"number": 31, "parent": {"number": 5, "labels": {"nodes": [{"name": "enhancement"}]}}},
             {"number": 32, "parent": None}]
    mod.apply_parent_specs(items, nodes)
    assert {i["n"]: i.get("pa") for i in items} == {30: 9, 31: None, 32: None}


def test_the_board_pass_asks_for_each_issues_parent(hub):
    """One GraphQL page carries the parent, so the board needs no read per card."""
    mod, _ = hub
    assert "parent{number labels(first:20){nodes{name}}}" in mod.OPEN_RELATIONS_QUERY


def test_the_parent_read_pages_every_open_issue(hub, monkeypatch):
    """The relation read pages with pageInfo/endCursor. One page is not the repository: a ticket whose spec sits past the
    first page is a ticket all the same, and a read that stops early is named incomplete (#829)."""
    mod, _ = hub
    pages = [
        {"data": {"repository": {"issues": {"pageInfo": {"hasNextPage": True, "endCursor": "c1"}, "nodes": [{"number": 30}]}}}},
        {"data": {"repository": {"issues": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [{"number": 31}]}}}},
    ]
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(pages[len(calls) - 1]), "")

    monkeypatch.setattr(mod.subprocess, "run", fake)
    nodes, complete = mod.open_relations()
    assert [n["number"] for n in nodes] == [30, 31], nodes
    assert complete is True
    assert "cursor=c1" in calls[1], calls


def test_a_parent_read_that_fails_is_incomplete(hub, monkeypatch):
    """A failed read never reads as "no parent": it is incomplete, and the rule fails closed on what it did not read."""
    mod, _ = hub
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "gh: HTTP 502"))
    nodes, complete = mod.open_relations()
    assert nodes == [] and complete is False


RATE_LIMIT_BODY = json.dumps({"errors": [{"type": "RATE_LIMIT", "code": "graphql_rate_limit",
                                          "message": "API rate limit already exceeded for user ID 1."}]})
# The body `gh` really printed when the quota ran out on 2026-11-27: a bare message, no `errors`, no `data`.
REFUSAL_BODY = json.dumps({"message": "API rate limit already exceeded for user ID 122891372."})


@pytest.mark.parametrize("body", [REFUSAL_BODY, RATE_LIMIT_BODY], ids=["observed-refusal", "typed-rate-limit"])
def test_a_rate_limited_parent_read_is_incomplete(hub, monkeypatch, body):
    """#3044: `gh api graphql` prints the refusal body on stdout and exits 1. That answer is no empty last page: the
    read is incomplete, so no card loses the parent spec the last good pass recorded."""
    mod, _ = hub
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, body, "gh: rate limited"))
    nodes, complete = mod.open_relations()
    assert nodes == [] and complete is False
    items = [{"n": 2717}, {"n": 2722}]
    mod.apply_parent_specs(items, nodes, complete, previous=[{"n": 2717, "pa": 2682}, {"n": 2722, "pa": 2682}])
    assert {i["n"]: i.get("pa") for i in items} == {2717: 2682, 2722: 2682}


def test_a_page_that_carries_errors_is_incomplete_and_names_no_node(hub, monkeypatch):
    """#3044: a page with `data` and `errors` together is a partial answer. Its nodes are not trusted (a node whose
    parent came back null would drop a real parent), and the read is incomplete."""
    mod, _ = hub
    body = json.dumps({"data": {"repository": {"issues": {"nodes": [{"number": 2717, "parent": None}],
                                                          "pageInfo": {"hasNextPage": False, "endCursor": None}}}},
                       "errors": [{"type": "FORBIDDEN", "message": "one node could not be read"}]})
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, body, ""))
    nodes, complete = mod.open_relations()
    assert nodes == [] and complete is False
    items = [{"n": 2717}]
    mod.apply_parent_specs(items, nodes, complete, previous=[{"n": 2717, "pa": 2682}])
    assert items[0].get("pa") == 2682


def test_an_answer_with_no_issues_field_is_incomplete(hub, monkeypatch):
    """#3044: an answer that exits 0 but holds no `data.repository.issues` is no empty last page."""
    mod, _ = hub
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, json.dumps({"data": {"repository": None}}), ""))
    assert mod.open_relations() == ([], False)


@pytest.mark.parametrize("issues", [{}, {"nodes": [], "pageInfo": {}}, {"nodes": [], "pageInfo": {"hasNextPage": None}}],
                         ids=["no-pages", "no-flag", "flag-not-a-bool"])
def test_a_page_that_names_no_last_page_is_incomplete(hub, monkeypatch, issues):
    """#3044: a page with no `pageInfo`, or one whose `hasNextPage` is not a bool, says nothing about a last page, so
    the read is incomplete. An empty `{}` for `issues` is the shape a refusal parses into."""
    mod, _ = hub
    body = json.dumps({"data": {"repository": {"issues": issues}}})
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, body, ""))
    assert mod.open_relations() == ([], False)


def test_an_incomplete_parent_read_keeps_the_last_good_parent(hub):
    """A card the incomplete read never reached keeps the `pa` the last good pass recorded, so it stays a ticket."""
    mod, _ = hub
    items = [{"n": 30}, {"n": 31}]
    nodes = [{"number": 30, "parent": {"number": 8, "labels": {"nodes": [{"name": "spec"}]}}}]
    mod.apply_parent_specs(items, nodes, complete=False, previous=[{"n": 31, "pa": 9}])
    assert {i["n"]: i.get("pa") for i in items} == {30: 8, 31: 9}


def test_a_card_the_parent_read_never_named_is_not_promotable(hub):
    """A Backlog card whose parent no read named, and no last good read held, is not promotable: an unknown parent is
    never "no spec parent" (ADR-0142)."""
    mod, _ = hub
    items = [{"n": 33}]
    mod.apply_parent_specs(items, [], complete=False, previous=[])
    assert items[0].get("pau") is True and items[0].get("pa") is None
    assert mod.promotable({**items[0], "s": "OPEN", "st": "Backlog", "l": []}) is False


def test_a_backlog_card_with_an_unread_parent_is_refused_by_both_routes(hub):
    """The board does not mark it, and the route refuses it before any write: the same rule on the same input."""
    mod, port = hub
    mod.board_cache["items"].append({"n": 33, "i": 133, "t": "parent not read", "s": "OPEN", "st": "Backlog",
                                     "l": [], "pau": True})
    rows = {g["name"]: {i["n"]: i for i in g["items"]} for g in mod.board()["groups"]}
    assert not rows["Backlog"][33].get("pm")
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 33})
    assert status == 400 and calls == [], (status, out, calls)


def test_a_proved_move_is_the_same_column_in_both_routes(hub, monkeypatch, tmp_path):
    """The board draws a proved move the read has not carried yet, and the route reads the column the board draws. While
    the move stands pending, the card is not promotable in either route: one rule over one input (#829, AC3)."""
    mod, port = hub
    moves = tmp_path / "board-moves.json"
    moves.write_text(json.dumps({"101": {"column": mod.PROMOTE_TO, "at": "2026-10-29T12:00:00Z", "by": "owner"}}))
    monkeypatch.setattr(mod, "MOVES", moves)
    monkeypatch.setenv("SG_PENDING_OVERLAY", "on")
    observed = int(mod._utc("2026-10-29T09:00:00Z").timestamp())   # the list read began before the move
    mod.board_cache["items"] = [dict(i, ot=observed) for i in mod.board_cache["items"]]
    rows = {g["name"]: {i["n"]: i for i in g["items"]} for g in mod.board()["groups"]}
    assert 7 in rows[mod.PROMOTE_TO], rows          # the board draws the proved move
    assert not rows[mod.PROMOTE_TO][7].get("pm"), "the board marks a pending-move card promotable"
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 7})
    assert status == 400 and calls == [], (status, out, calls)
    assert "not Backlog" in out.get("err", ""), out


def batch_fixture(mod, monkeypatch, tmp_path):
    """A Backlog spec (#9) with one open ticket (#20), a standalone Backlog issue (#7) and a Todo card (#8) the batch leaves."""
    mod.BATCH_AUDIT = tmp_path / "batch.jsonl"
    mod.board_cache["items"].append({"n": 20, "i": 120, "t": "ticket a", "s": "OPEN", "st": "Backlog", "l": [], "pa": 9})
    sub_issues(monkeypatch, answer=[{"number": 20, "state": "open", "labels": []}])


def test_the_batch_names_its_count_and_writes_nothing_until_confirmed(hub, monkeypatch, tmp_path):
    mod, port = hub
    batch_fixture(mod, monkeypatch, tmp_path)
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"batch": True}, path="/api/promote")
    assert status == 200 and out == {"count": 3, "issues": [9, 20, 7]}, out
    assert calls == [] and not mod.BATCH_AUDIT.exists()


def test_the_batch_moves_every_backlog_card_in_one_write_with_the_spec_first(hub, monkeypatch, tmp_path):
    mod, port = hub
    batch_fixture(mod, monkeypatch, tmp_path)
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"batch": True, "confirm": 3}, path="/api/promote")
    assert status == 200 and out == {"ok": True, "count": 3, "pending": [9, 20, 7]}, out
    assert calls == [([9, 20, 7], False)], calls
    groups = {g["name"]: [i["n"] for i in g["items"]] for g in mod.board()["groups"]}
    assert {7, 9, 20} <= set(groups["Todo"]) and not {7, 9, 20} & set(groups["Backlog"]), groups


def test_the_batch_never_moves_a_card_outside_backlog(hub, monkeypatch, tmp_path):
    mod, port = hub
    batch_fixture(mod, monkeypatch, tmp_path)
    calls = []
    mod.promote_write = writer(calls)
    post(port, {"batch": True, "confirm": 3}, path="/api/promote")
    assert all(8 not in issues for issues, _ in calls), calls
    assert next(i for i in mod.board_cache["items"] if i["n"] == 8)["st"] == "Todo"


def test_the_batch_refuses_a_count_that_is_not_the_one_named(hub, monkeypatch, tmp_path):
    mod, port = hub
    batch_fixture(mod, monkeypatch, tmp_path)
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"batch": True, "confirm": 2}, path="/api/promote")
    assert status == 400 and "err" in out, (status, out)
    assert calls == [] and not mod.BATCH_AUDIT.exists()
    assert all(i["st"] == "Backlog" for i in mod.board_cache["items"] if i["n"] in (7, 9, 20))


def test_an_empty_backlog_moves_nothing(hub, monkeypatch, tmp_path):
    mod, port = hub
    mod.BATCH_AUDIT = tmp_path / "batch.jsonl"
    mod.board_cache["items"] = [dict(TODO)]
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"batch": True, "confirm": 0}, path="/api/promote")
    assert status == 400 and "err" in out, (status, out)
    assert calls == []


def test_a_failed_ticket_read_moves_no_batch(hub, monkeypatch, tmp_path):
    mod, port = hub
    gh = sys.modules["ghread"]
    sub_issues(monkeypatch, failure=gh.Read(kind=gh.FAILED, detail="gh timed out"))
    mod.BATCH_AUDIT = tmp_path / "batch.jsonl"
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"batch": True}, path="/api/promote")
    assert status == 400 and out.get("err"), out
    assert calls == [] and not mod.BATCH_AUDIT.exists()


def test_the_batch_is_recorded_with_its_count_and_who_asked(hub, monkeypatch, tmp_path):
    mod, port = hub
    batch_fixture(mod, monkeypatch, tmp_path)
    mod.promote_write = writer([])
    status, _ = post(port, {"batch": True, "confirm": 3}, path="/api/promote")
    assert status == 200
    lines = [json.loads(x) for x in mod.BATCH_AUDIT.read_text().splitlines()]
    assert len(lines) == 1, lines
    assert lines[0]["action"] == "promote-batch" and lines[0]["count"] == 3 and lines[0]["issues"] == [9, 20, 7], lines
    assert lines[0]["who"] == "owner" and lines[0]["ok"] is True and lines[0]["at"], lines


def test_the_batch_route_keeps_the_write_guard(hub, monkeypatch, tmp_path):
    mod, port = hub
    batch_fixture(mod, monkeypatch, tmp_path)
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"batch": True, "confirm": 3}, path="/api/promote", origin="https://evil.example")
    assert status == 400 and out == {"err": "forbidden"}, (status, out)
    assert calls == []


def test_the_desktop_page_puts_the_batch_control_on_the_backlog_column(hub):
    mod, _ = hub
    page = mod.PAGE
    assert 'fetch("/api/promote",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({batch:true' in page, "the batch control does not send the batch request"
    rule = page.split("#bbody .bmv{", 1)[1].split("}", 1)[0]
    assert "min-height:32px" in rule and "var(--acc)" in rule, rule


def spec_plan_fixture(mod, monkeypatch, tmp_path, subs):
    """A Backlog spec (#9) with two Backlog tickets (#20, #21) on the board in that order; the sub-issue read answers `subs`."""
    mod.BATCH_AUDIT = tmp_path / "batch.jsonl"
    mod.board_cache["items"] += [{"n": 20, "i": 120, "t": "ticket a", "s": "OPEN", "st": "Backlog", "l": [], "pa": 9},
                                 {"n": 21, "i": 121, "t": "ticket b", "s": "OPEN", "st": "Backlog", "l": [], "pa": 9}]
    sub_issues(monkeypatch, answer=subs)


def sub(number, state="open", labels=()):
    return {"number": number, "state": state, "labels": [{"name": x} for x in labels]}


def test_a_spec_plan_names_its_count_and_order_and_writes_nothing(hub, monkeypatch, tmp_path):
    mod, port = hub
    spec_plan_fixture(mod, monkeypatch, tmp_path, [sub(21), sub(20)])
    calls = []
    mod.promote_write = writer(calls)
    before = json.dumps(mod.board_cache, sort_keys=True)
    status, out = post(port, {"batch": True, "spec": 9}, path="/api/promote")
    assert status == 200 and out == {"count": 3, "issues": [9, 20, 21], "skipped": []}, out
    assert calls == [] and not mod.BATCH_AUDIT.exists()
    assert json.dumps(mod.board_cache, sort_keys=True) == before


def test_a_spec_plan_names_a_ticket_past_backlog_as_skipped_and_never_plans_it(hub, monkeypatch, tmp_path):
    mod, port = hub
    spec_plan_fixture(mod, monkeypatch, tmp_path, [sub(21), sub(22), sub(20)])
    mod.board_cache["items"].append({"n": 22, "i": 122, "t": "started", "s": "OPEN", "st": "Todo", "l": [], "pa": 9})
    status, out = post(port, {"batch": True, "spec": 9}, path="/api/promote")
    assert status == 200 and out == {"count": 3, "issues": [9, 20, 21], "skipped": [22]}, out


def test_a_spec_outside_backlog_is_refused_in_one_line(hub, monkeypatch, tmp_path):
    mod, port = hub
    spec_plan_fixture(mod, monkeypatch, tmp_path, [sub(20)])
    next(i for i in mod.board_cache["items"] if i["n"] == 9)["st"] = "Doing"
    status, out = post(port, {"batch": True, "spec": 9}, path="/api/promote")
    assert status == 400 and "Backlog" in out.get("err", "") and "\n" not in out["err"], out


def test_a_spec_with_no_open_ticket_is_refused_in_one_line(hub, monkeypatch, tmp_path):
    mod, port = hub
    spec_plan_fixture(mod, monkeypatch, tmp_path, [sub(20, state="closed"), sub(21, state="closed")])
    status, out = post(port, {"batch": True, "spec": 9}, path="/api/promote")
    assert status == 400 and "no open ticket" in out.get("err", "") and "\n" not in out["err"], out


@pytest.mark.parametrize("kind", ["failed", "refused"])
def test_a_failed_or_refused_sub_issue_read_refuses_the_plan(hub, monkeypatch, tmp_path, kind):
    mod, port = hub
    spec_plan_fixture(mod, monkeypatch, tmp_path, [])
    gh = sys.modules["ghread"]
    sub_issues(monkeypatch, failure=gh.Read(kind=gh.FAILED if kind == "failed" else gh.REFUSED, detail="gh said no"))
    status, out = post(port, {"batch": True, "spec": 9}, path="/api/promote")
    assert status == 400 and out.get("err") and "count" not in out, out


def test_a_spec_plan_keeps_closed_spec_and_owner_held_tickets_out(hub, monkeypatch, tmp_path):
    mod, port = hub
    spec_plan_fixture(mod, monkeypatch, tmp_path, [sub(20), sub(21), sub(23, state="closed"), sub(24), sub(25)])
    mod.board_cache["items"] += [{"n": 23, "i": 123, "t": "closed", "s": "OPEN", "st": "Backlog", "l": [], "pa": 9},
                                 {"n": 24, "i": 124, "t": "a spec", "s": "OPEN", "st": "Backlog", "l": ["spec"], "pa": 9},
                                 {"n": 25, "i": 125, "t": "held", "s": "OPEN", "st": "Backlog", "l": ["needs-owner"], "pa": 9}]
    status, out = post(port, {"batch": True, "spec": 9}, path="/api/promote")
    assert status == 200 and out == {"count": 3, "issues": [9, 20, 21], "skipped": []}, out


def test_a_ticket_reached_twice_enters_the_spec_plan_once(hub, monkeypatch, tmp_path):
    mod, port = hub
    spec_plan_fixture(mod, monkeypatch, tmp_path, [sub(20), sub(21), sub(20)])
    status, out = post(port, {"batch": True, "spec": 9}, path="/api/promote")
    assert status == 200 and out == {"count": 3, "issues": [9, 20, 21], "skipped": []}, out


def partial_writer(calls, moved, err):
    """The board seam as it answers a batch that fails part way: each card it proved prints its move, and the call fails."""
    def run(issues, add=False):
        calls.append((issues, add))
        return subprocess.CompletedProcess(["board.sh"], 1, "".join(f"#{n} -> Todo\n" for n in issues if n in moved), err)
    return run


def column_of(mod, n):
    return next(i for i in mod.board_cache["items"] if i["n"] == n)["st"]


def test_a_confirmed_spec_batch_moves_its_cards_in_one_write_with_the_spec_first(hub, monkeypatch, tmp_path):
    mod, port = hub
    spec_plan_fixture(mod, monkeypatch, tmp_path, [sub(21), sub(20)])
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"batch": True, "spec": 9, "confirm": 3}, path="/api/promote")
    assert status == 200 and out == {"ok": True, "count": 3, "pending": [9, 20, 21]}, out
    assert calls == [([9, 20, 21], False)], calls
    assert [column_of(mod, n) for n in (9, 20, 21)] == ["Todo", "Todo", "Todo"]


def test_a_spec_batch_with_a_changed_count_is_refused_and_moves_nothing(hub, monkeypatch, tmp_path):
    mod, port = hub
    spec_plan_fixture(mod, monkeypatch, tmp_path, [sub(21), sub(20)])
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"batch": True, "spec": 9, "confirm": 2}, path="/api/promote")
    assert status == 400 and out.get("count") == 3 and "not 2" in out.get("err", ""), out
    assert calls == [] and [column_of(mod, n) for n in (9, 20, 21)] == ["Backlog"] * 3


def test_a_spec_batch_that_fails_part_way_names_the_cards_that_moved_and_those_that_did_not(hub, monkeypatch, tmp_path):
    mod, port = hub
    spec_plan_fixture(mod, monkeypatch, tmp_path, [sub(21), sub(20)])
    calls = []
    mod.promote_write = partial_writer(calls, moved={9, 20}, err="#21: the board carries no 'Todo' on it after 3 reads\n")
    status, out = post(port, {"batch": True, "spec": 9, "confirm": 3}, path="/api/promote")
    assert status == 200 and out.get("pending") == [9, 20, 21], out
    assert [column_of(mod, n) for n in (9, 20, 21)] == ["Todo", "Todo", "Backlog"]
    assert "read" in rows_by_number(mod)[21].get("mr", ""), rows_by_number(mod)[21]
    assert [json.loads(x)["outcome"] for x in mod.BATCH_AUDIT.read_text().splitlines()] == ["partial"]


def test_a_second_spec_batch_moves_only_what_is_still_in_backlog(hub, monkeypatch, tmp_path):
    mod, port = hub
    spec_plan_fixture(mod, monkeypatch, tmp_path, [sub(21), sub(20)])
    calls = []
    mod.promote_write = writer(calls)
    assert post(port, {"batch": True, "spec": 9, "confirm": 3}, path="/api/promote")[0] == 200
    status, out = post(port, {"batch": True, "spec": 9}, path="/api/promote")
    assert status == 200 and out["count"] == 0 and out["issues"] == [], out
    status, out = post(port, {"batch": True, "spec": 9, "confirm": 0}, path="/api/promote")
    assert status == 400 and out.get("count") == 0 and len(calls) == 1, (out, calls)
    mod.board_cache["items"].append({"n": 22, "i": 122, "t": "ticket c", "s": "OPEN", "st": "Backlog", "l": [], "pa": 9})
    sub_issues(monkeypatch, answer=[sub(20), sub(21), sub(22)])
    status, out = post(port, {"batch": True, "spec": 9}, path="/api/promote")
    assert status == 200 and out["count"] == 1 and out["issues"] == [22], out


def test_a_spec_batch_writes_one_audit_line_per_confirmed_request(hub, monkeypatch, tmp_path):
    mod, port = hub
    spec_plan_fixture(mod, monkeypatch, tmp_path, [sub(21), sub(20)])
    mod.promote_write = writer([])
    post(port, {"batch": True, "spec": 9}, path="/api/promote")
    post(port, {"batch": True, "spec": 9, "confirm": 2}, path="/api/promote")
    post(port, {"batch": True, "spec": 9, "confirm": 3}, path="/api/promote")
    lines = [json.loads(x) for x in mod.BATCH_AUDIT.read_text().splitlines()]
    assert [x["outcome"] for x in lines] == ["refused", "moved"], lines
    moved = lines[1]
    assert moved["action"] == "promote-spec-batch" and moved["scope"] == 9 and moved["count"] == 3, moved
    assert moved["issues"] == [9, 20, 21] and moved["moved"] == [9, 20, 21] and moved["who"] == "owner" and moved["at"], moved
    assert lines[0]["moved"] == [] and lines[0]["count"] == 3, lines[0]


def rows_by_number(mod):
    return {i["n"]: i for g in mod.board()["groups"] for i in g["items"]}


def test_a_spec_card_in_backlog_with_a_plan_carries_the_start_control(hub, monkeypatch, tmp_path):
    """#1128: the control is drawn on the spec's card in Backlog, and on no ticket or other card."""
    mod, _ = hub
    spec_plan_fixture(mod, monkeypatch, tmp_path, [sub(20), sub(21)])
    rows = rows_by_number(mod)
    assert rows[9].get("sb") is True, "a Backlog spec with open tickets carries no start control"
    assert not rows[20].get("sb") and not rows[21].get("sb") and not rows[7].get("sb"), "a non-spec card carries the start control"


@pytest.mark.parametrize("ticket_extra", [
    None,
    {"s": "closed"},
    {"l": ["needs-owner"]},
    {"l": ["spec"]},
])
def test_a_spec_card_with_an_empty_plan_carries_no_start_control(hub, ticket_extra):
    """#1128: a spec whose plan holds no open ticket the batch would move carries no control."""
    mod, _ = hub
    if ticket_extra is not None:
        mod.board_cache["items"].append({"n": 22, "i": 122, "t": "ticket c", "s": "OPEN", "st": "Backlog", "l": [], "pa": 9,
                                         **ticket_extra})
    assert not rows_by_number(mod)[9].get("sb"), ticket_extra


def test_a_started_spec_carries_no_start_control(hub, monkeypatch, tmp_path):
    mod, _ = hub
    spec_plan_fixture(mod, monkeypatch, tmp_path, [sub(20), sub(21)])
    next(i for i in mod.board_cache["items"] if i["n"] == 9)["st"] = "Todo"
    assert not rows_by_number(mod)[9].get("sb"), "a spec already in Todo carries the start control"


def test_a_spec_whose_only_open_ticket_is_past_backlog_still_carries_the_control(hub, monkeypatch, tmp_path):
    """The plan is refused only when it holds no ticket and no skipped ticket, so a skipped ticket keeps the control."""
    mod, _ = hub
    spec_plan_fixture(mod, monkeypatch, tmp_path, [sub(22)])
    mod.board_cache["items"].append({"n": 22, "i": 122, "t": "started", "s": "OPEN", "st": "Todo", "l": [], "pa": 9})
    assert rows_by_number(mod)[9].get("sb") is True


def test_a_render_makes_no_batch_read(hub, monkeypatch, tmp_path):
    """#1128: the count is asked for on the press, never on a render: the board reads the cache only."""
    mod, _ = hub
    spec_plan_fixture(mod, monkeypatch, tmp_path, [sub(20), sub(21)])
    routes = sub_issues(monkeypatch, answer=[sub(20), sub(21)])
    monkeypatch.setattr(mod, "spec_batch_cards", lambda number: pytest.fail("a render read the spec's batch"))
    rows_by_number(mod)
    assert routes == [], routes


def test_the_page_draws_the_start_control_beside_the_move_arrow(hub):
    mod, _ = hub
    page = mod.PAGE
    assert "Start this spec" in page and "class=spb" in page, "the card carries no start control"
    assert "x.sb" in page, "the control is not bound to the row's own fact"
    assert page.index("class=pmp") < page.index("class=spb"), "the control is not beside the card's move arrow"


def test_the_card_render_asks_for_no_count(hub):
    mod, _ = hub
    cards_line = next(line for line in mod.PAGE.splitlines() if line.startswith("const cards="))
    assert "spec:" not in cards_line and "specPost" not in cards_line and "batchPost" not in cards_line, cards_line[:200]
    assert mod.PAGE.index('closest("#bbody .spb")') < mod.PAGE.index("{spec:n}"), "the count is asked for outside the press"
    assert mod.PAGE.count("{spec:") == 2, "the count is asked for outside the press and the confirm"


def test_a_refused_start_replaces_the_dialog_with_the_reason(hub):
    mod, _ = hub
    page = mod.PAGE
    assert "dlg.innerHTML=`<div class=perr>${esc(plan.err)}</div>" in page, "a refused plan does not replace the dialog"
    assert "dlg.innerHTML=`<div class=perr>${esc(d.err)}</div>" in page, "a refused write does not replace the dialog"


def test_a_changed_count_is_refused_with_the_current_count_and_moves_nothing(hub, monkeypatch, tmp_path):
    mod, port = hub
    spec_plan_fixture(mod, monkeypatch, tmp_path, [sub(21), sub(20)])
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"batch": True, "spec": 9, "confirm": 5}, path="/api/promote")
    assert status == 400 and out["count"] == 3 and "the batch is 3 cards now, not 5" in out["err"], out
    assert calls == []


def test_a_successful_start_repaints_the_board_and_states_the_count(hub):
    mod, _ = hub
    page = mod.PAGE
    confirm = page.split('closest("#spdlg .spgo")', 1)[1].split("document.addEventListener", 1)[0]
    assert "pollBoard()" in confirm and "d.pending.length" in confirm, confirm
    assert "#spnote" in page, "the success count does not survive the repaint"


def test_the_phone_page_gains_no_start_control():
    for name in ("hub-mobile.html", "hub-mobile-run.js", "hub-mobile-gesture.js"):
        text = (ROOT / "contrib/squad-goal/scripts" / name).read_text()
        assert "Start this spec" not in text and "spb" not in text and "{spec:" not in text, name




def test_the_writer_call_carries_the_repository_the_hub_resolved(hub, monkeypatch):
    """#1315: hubctl starts the hub with no SG_REPO, and the add of an issue with no card runs only with a repository, so the
    owner's arrow answered "not on the board" and the card never reached the board. The writer call carries the repository
    the hub resolved, as it carries the state directory (#1750)."""
    mod, _ = hub
    monkeypatch.delenv("SG_REPO", raising=False)
    kw = {}

    def fake(cmd, **options):
        kw.update(options)
        return subprocess.CompletedProcess(cmd, 0, "#7 -> Todo\n", "")

    monkeypatch.setattr(mod.subprocess, "run", fake)
    mod.promote_write(7, add=True)
    assert (kw.get("env") or {}).get("SG_REPO") == mod.REPO, kw


ARROW_END = "b.disabled=false}});"
ARROW_RUNNER = r"""
const SRC = __SRC__;
let handler = null;
const document = { addEventListener: (t, f) => { if (t === "click") handler = f; } };
const log = { posts: 0, polls: 0, inserted: [] };
let reply = null;
const fetch = (url, opts) => { log.posts++; return reply(url, opts); };
const pollBoard = () => { log.polls++; };
const esc = s => String(s);
const card = { insertAdjacentHTML: (pos, html) => { log.inserted.push(html); } };
const btn = { disabled: false, textContent: "→ Todo", dataset: { n: "7" }, closest: sel => (sel === ".it" ? card : null) };
const ev = { target: { closest: sel => (sel === "#bbody .pmp" ? btn : null) }, preventDefault() {}, stopPropagation() {} };
new Function("document", "fetch", "pollBoard", "esc", SRC)(document, fetch, pollBoard, esc);
const json = (status, body) => ({ ok: status < 300, status, headers: { get: () => "application/json" }, json: async () => body });
(async () => {
  const out = {};
__SCENARIO__
  console.log(JSON.stringify(out));
})();
"""
DEFERRED = """
  const releases = [];
  reply = () => new Promise(r => releases.push(r));
  const first = handler(ev);
  out.during = { disabled: btn.disabled, label: btn.textContent };
  handler(ev);
  out.postsDuringSecondTap = log.posts;
  releases.forEach(r => r(json(200, { ok: true })));
  await first;
  out.afterSuccess = { disabled: btn.disabled, label: btn.textContent, polls: log.polls };
"""
REFUSED = """
  reply = async () => json(400, { err: "not in Backlog" });
  await handler(ev);
  out.after = { disabled: btn.disabled, label: btn.textContent, inserted: log.inserted.length };
"""
UNANSWERED = """
  reply = async () => { throw new Error("down"); };
  await handler(ev);
  out.after = { disabled: btn.disabled, label: btn.textContent, inserted: log.inserted.length };
"""


def run_arrow(mod, tmp_path, scenario):
    page = mod.PAGE
    start = page.index('document.addEventListener("click",async e=>{const b=e.target.closest("#bbody .pmp")')
    src = page[start:page.index(ARROW_END, start) + len(ARROW_END)]
    script = tmp_path / "arrow.js"
    script.write_text(ARROW_RUNNER.replace("__SRC__", json.dumps(src)).replace("__SCENARIO__", scenario))
    p = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.mark.node_bound
def test_the_arrow_is_disabled_and_relabelled_while_its_request_is_open(hub, tmp_path):
    mod, _ = hub
    out = run_arrow(mod, tmp_path, DEFERRED)
    assert out["during"]["disabled"] is True
    assert out["during"]["label"] != "→ Todo", "the arrow keeps its label while the write is open"


@pytest.mark.node_bound
def test_a_second_tap_during_the_request_sends_no_second_write(hub, tmp_path):
    mod, _ = hub
    out = run_arrow(mod, tmp_path, DEFERRED)
    assert out["postsDuringSecondTap"] == 1, "a second tap while the write is open sent a second request"


@pytest.mark.node_bound
def test_a_refused_arrow_returns_to_its_label_and_is_enabled(hub, tmp_path):
    mod, _ = hub
    out = run_arrow(mod, tmp_path, REFUSED)
    assert out["after"] == {"disabled": False, "label": "→ Todo", "inserted": 1}


@pytest.mark.node_bound
def test_an_unanswered_arrow_returns_to_its_label_and_is_enabled(hub, tmp_path):
    mod, _ = hub
    out = run_arrow(mod, tmp_path, UNANSWERED)
    assert out["after"] == {"disabled": False, "label": "→ Todo", "inserted": 1}
