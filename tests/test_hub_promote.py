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
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
GHREAD_PY = ROOT / "contrib/squad-goal/scripts/ghread.py"

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
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, srv.server_port
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
    assert status == 200 and out == {"ok": True, "issue": 9, "to": "Todo", "moved": [9]}, out
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
    assert status == 200 and out == {"ok": True, "issue": 9, "to": "Todo", "moved": [9, 20, 21]}, out
    assert calls == [([9, 20, 21], False)], calls
    groups = {g["name"]: [i["n"] for i in g["items"]] for g in mod.board()["groups"]}
    assert {9, 20, 21} <= set(groups["Todo"]) and not {9, 20, 21} & set(groups["Backlog"]), groups


def test_a_ticket_without_an_item_id_is_added_with_the_spec(hub, monkeypatch):
    mod, port = hub
    mod.board_cache["items"].append({"n": 22, "t": "filed by hand", "s": "OPEN", "st": None, "l": []})
    sub_issues(monkeypatch, answer=[{"number": 22, "state": "open"}])
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 9})
    assert status == 200 and out.get("moved") == [9, 22], out
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
    assert status == 200 and out.get("moved") == [9], out
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
    assert status == 200 and out.get("moved") == [9], out
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
    assert status == 200 and out.get("moved") == [9], out
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
    assert status == 200 and out.get("moved") == [9, 20], out
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
    assert status == 200 and out.get("moved") == [9, 21], out
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
    assert status == 400 and "may be partial" in out.get("err", ""), out
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
    assert status == 400 and "read failure" in out.get("err", ""), out
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
