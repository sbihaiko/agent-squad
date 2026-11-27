"""A board move to Todo answers at once and confirms in the background (#1300).

The owner's arrow, the drag and the batch move all send one write to the writer (`board.sh`). The writer reads the board and
proves the write, which can take up to two minutes. The page must not wait for it. The route answers at once, the card
draws in Todo carrying a `pending` mark while the write is in flight, and the writer's answer ends the mark: a proved move
clears it, and a refusal clears it and names the reason on the card. No request holds the page for the writer's timeout.

The writer is a fake here, held on an event, so each state of the card is observed before and after the answer. The
suite never reaches GitHub.
"""
import http.client
import importlib.util
import json
import pathlib
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
GHREAD_PY = ROOT / "contrib/squad-goal/scripts/ghread.py"

BACKLOG = {"n": 7, "i": 101, "t": "a ticket", "s": "OPEN", "st": "Backlog", "l": ["enhancement"]}


@pytest.fixture
def hub(monkeypatch, tmp_path):
    """The hub loaded for a throwaway profile: its state and cache files live under tmp_path, never the loop's."""
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path)), ("SG_STATE", str(tmp_path))):
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SG_SHARED", raising=False)
    monkeypatch.delenv("SG_PROFILE", raising=False)
    gspec = importlib.util.spec_from_file_location("ghread", GHREAD_PY)
    gh = importlib.util.module_from_spec(gspec)
    monkeypatch.setitem(sys.modules, "ghread", gh)
    gspec.loader.exec_module(gh)
    spec = importlib.util.spec_from_file_location("hub_move_async", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.board_cache = {"t": 1, "items": [dict(BACKLOG)], "prs": [], "err": None, "lc": {}, "errs": [], "cases": []}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, srv.server_port
    srv.shutdown()
    srv.server_close()


def post(port, body, path="/api/promote"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    headers = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"}
    c.request("POST", path, body=json.dumps(body), headers=headers)
    r = c.getresponse()
    data = json.loads(r.read() or b"{}")
    c.close()
    return r.status, data


def card(mod, n=7):
    """The row the served board draws for card `n`, and the name of the column that holds it."""
    for g in mod.board()["groups"]:
        for i in g["items"]:
            if i["n"] == n:
                return g["name"], i
    raise AssertionError(f"card #{n} is in no column")


def held_writer(release, rc=0, err="", out=None):
    """A writer that answers only when the test lets it: the write is in flight until `release` is set."""
    def run(issues, add=False):
        release.wait(10)
        text = out if out is not None else "".join(f"#{n} -> Todo\n" for n in issues)
        return subprocess.CompletedProcess(["board.sh"], rc, text, err)
    return run


def test_the_route_answers_before_the_writer_does(hub):
    mod, port = hub
    release = threading.Event()
    mod.promote_write = held_writer(release)
    started = time.monotonic()
    status, out = post(port, {"issue": 7})
    elapsed = time.monotonic() - started
    try:
        assert status == 200 and out.get("ok") is True and out.get("pending") == [7], (status, out)
        assert elapsed < 3, f"the route held the page for {elapsed:.1f} s while the writer was out"
    finally:
        release.set()
        mod.wait_for_moves()


def test_the_card_draws_in_todo_with_a_pending_mark_while_the_writer_is_out(hub):
    mod, port = hub
    release = threading.Event()
    mod.promote_write = held_writer(release)
    post(port, {"issue": 7})
    try:
        column, row = card(mod, 7)
        assert column == "Todo", column
        assert row.get("pd") is True, "the card carries no pending mark while its write is in flight"
        assert not row.get("pm"), "a card in flight is offered the arrow again"
    finally:
        release.set()
        mod.wait_for_moves()


def test_a_proved_write_clears_the_pending_mark_and_keeps_the_card_in_todo(hub):
    mod, port = hub
    release = threading.Event()
    mod.promote_write = held_writer(release)
    post(port, {"issue": 7})
    release.set()
    mod.wait_for_moves()
    column, row = card(mod, 7)
    assert column == "Todo" and not row.get("pd") and not row.get("mr"), (column, row)
    assert next(i for i in mod.board_cache["items"] if i["n"] == 7)["st"] == "Todo"


def test_a_refusal_clears_the_pending_mark_and_names_the_reason_on_the_card(hub):
    mod, port = hub
    release = threading.Event()
    release.set()
    mod.promote_write = held_writer(release, rc=1, err="#7: the board carries no 'Todo' on it after 3 reads; a read failure\n",
                                    out="")
    status, out = post(port, {"issue": 7})
    assert status == 200 and out.get("ok") is True, (status, out)
    mod.wait_for_moves()
    column, row = card(mod, 7)
    assert column == "Backlog", column
    assert not row.get("pd"), "a refused move keeps its pending mark"
    assert "read failure" in row.get("mr", ""), row
    assert "did not answer in 120 s" not in json.dumps(mod.board()), "the 120 s wait text reached the board"


def test_a_writer_timeout_clears_the_mark_and_names_a_partial_move(hub, monkeypatch, tmp_path):
    mod, port = hub

    def timed_out(issues, add=False):
        raise subprocess.TimeoutExpired(["board.sh"], 120)

    mod.promote_write = timed_out
    status, out = post(port, {"issue": 7})
    assert status == 200 and out.get("ok") is True, (status, out)
    mod.wait_for_moves()
    column, row = card(mod, 7)
    assert column == "Backlog" and not row.get("pd"), (column, row)
    assert "may be partial" in row.get("mr", ""), row
    assert "did not answer in 120 s" not in row.get("mr", ""), row
    assert (tmp_path / "board-dirty").exists(), "the next board pass is not asked to read the card"


def test_the_board_serves_a_new_body_when_a_write_starts_and_when_it_ends(hub):
    mod, port = hub
    release = threading.Event()
    mod.promote_write = held_writer(release)
    before, _ = mod.board_served()
    post(port, {"issue": 7})
    try:
        during, _ = mod.board_served()
        assert during != before, "the served board kept the body from before the write started"
    finally:
        release.set()
        mod.wait_for_moves()
    after, _ = mod.board_served()
    assert after != during, "the served board kept the pending body after the writer answered"


def test_the_writer_is_handed_the_item_ids_the_hub_already_holds(hub, monkeypatch):
    """The writer reads the item id from the hub's cache, so one move reads no whole-project list for the id (#1300)."""
    mod, port = hub
    seen = {}

    def fake(cmd, **kw):
        seen.update(kw.get("env") or {})
        return subprocess.CompletedProcess(cmd, 0, "#7 -> Todo\n", "")

    monkeypatch.setattr(mod.subprocess, "run", fake)
    post(port, {"issue": 7})
    mod.wait_for_moves()
    assert json.loads(seen.get("SG_BOARD_ITEM_HINTS", "{}")) == {"7": 101}, seen


def test_the_page_draws_the_refusal_the_board_row_carries(hub):
    mod, _ = hub
    page = mod.PAGE
    assert "x.mr" in page, "the card does not draw the refusal its row carries"
    assert "pending" in page, "the card does not draw the pending mark"


def test_the_board_serves_a_new_chip_when_the_loop_writes_its_own_reason(hub):
    """The chip is the loop's own file, so a served body built before it changed is a reason the loop already replaced (#1771).
    The key that keeps the body reads auto/off, auto/.pass and auto/gate-<n>, so the card follows the file (SPEC 3049)."""
    mod, port = hub
    mod.board_cache["prs"] = [{"n": 9, "r": "x/y", "t": "a pull request", "d": False, "ci": [], "ms": "clean",
                               "u": "2026-01-01T00:00:00Z", "h": "abcdef0123456789ab"}]
    st = pathlib.Path(mod.STATE) / "auto"
    st.mkdir(exist_ok=True)
    (pathlib.Path(mod.STATE) / "revci-9.out").write_text("#9 abcdef012 clean CI=success:2 VERDICT: APPROVE\n")
    (st / "off").write_text("")
    kill_switch, _ = mod.board_served()
    mod.board_served()   # a build marks its own rows, so the second serve settles the key: only the loop's file changes below
    (st / "off").unlink()
    (st / ".pass").write_text(str(time.time()))
    (st / "gate-9").write_text(f"abcdef012\t{int(time.time())}\tmerged\tmerged as abc1234: APPROVE + CI green\n")
    merged, _ = mod.board_served()
    assert json.loads(kill_switch)["prs"]["items"][0]["ps"][1] == "loop off"
    assert json.loads(merged)["prs"]["items"][0]["ps"][1] == "ready to merge"
