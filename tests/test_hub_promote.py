"""The owner's one move: a card in the hub's Backlog column carries an arrow that moves it to Todo (#560).

ADR-0142 makes Todo the owner's go signal, and ADR-0143 says the loop never moves a card into Backlog or Todo: this
control is the owner's, and it shows on Backlog cards only. ADR-0144 says a writer sends a board write only when the
value differs, and the hub's cache follows the write so the card moves at the next refresh without a GitHub read
right after it.

`POST /api/promote` carries the body `{"issue": N}` and the same guard `/api/models` and `/api/plans` carry: a
loopback Host, the hub's own Origin and a JSON body. The route accepts one open issue whose card is in Backlog now,
and it writes Status=Todo through the board seam, `board.sh`.
"""
import http.client
import importlib.util
import json
import pathlib
import subprocess
import threading
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"

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


def post(port, body, *, host=None, origin=None, content_type="application/json"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{port}", "Origin": origin if origin is not None else f"http://127.0.0.1:{port}"}
    if content_type:
        headers["Content-Type"] = content_type
    c.request("POST", "/api/promote", body=json.dumps(body), headers=headers)
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
    assert calls == [(7, False)], calls          # a row with a Project item moves with the plain write
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
    assert calls == [(12, True)], calls


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


def test_a_spec_card_is_refused(hub):
    mod, port = hub
    calls = []
    mod.promote_write = writer(calls)
    status, out = post(port, {"issue": 9})
    assert status == 400 and out.get("err"), out
    assert calls == []
    assert not any(i.get("pm") for g in mod.board()["groups"] for i in g["items"] if i["n"] == 9)


def test_a_refused_write_is_the_reason_the_card_shows(hub):
    mod, port = hub
    calls = []
    mod.promote_write = writer(calls, rc=1, err="#7: the board carries no 'Todo' on it after 3 reads; a read failure\n")
    status, out = post(port, {"issue": 7})
    assert status == 400 and "read failure" in out.get("err", ""), out
    assert next(i for i in mod.board_cache["items"] if i["n"] == 7)["st"] == "Backlog"


def test_the_arrow_is_on_backlog_cards_only(hub):
    mod, port = hub
    board = mod.board()
    groups = {g["name"]: {i["n"]: i for i in g["items"]} for g in board["groups"]}
    assert groups["Backlog"][7].get("pm") is True
    assert not groups["Todo"][8].get("pm")
    assert not groups["Backlog"][9].get("pm"), "a spec card carries no arrow (ADR-0142)"


def test_the_page_draws_the_arrow_and_delegates_the_move(hub):
    mod, port = hub
    page = mod.PAGE
    assert "class=pmp" in page, "the card carries no promote arrow"
    assert "x.pm&&!pr" in page, "the arrow is not bound to the row's own promotable fact"
    assert 'fetch("/api/promote"' in page and "JSON.stringify({issue:n})" in page, "the click does not delegate the move"
    assert 'e.target.closest("button")' in page, "a click on the arrow also opens the card's GitHub page"
