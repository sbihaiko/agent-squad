"""The board's reads merge into one cache per item, each item carrying the instant its read began (#974, ADR-0149).

A full read writes a card's structure always, and its column inputs (Status, issue state, labels, close reason) only when
its observation is newer than the one the cache holds. The pending-move overlay compares a proved move with each item's
own observation instant, the board route and the promote route alike. A pending move expires only on a read that saw its
card. The cache keeps a timestamp and an error per part (status, structure, pull requests): a failed read keeps the last
good part, and an empty successful answer is an empty board.
"""
import http.client
import importlib.util
import json
import pathlib
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"


@pytest.fixture
def hub(monkeypatch, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path / "hub")), ("SG_STATE", str(state))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("hub_board_observation", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, state


@pytest.fixture
def served(hub):
    mod, state = hub
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, state, srv.server_port
    srv.shutdown()
    srv.server_close()


def at(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def card(n, status="Todo", observed=None, **extra):
    row = {"n": n, "t": f"card {n}", "s": "OPEN", "st": status, "l": [], "i": f"ITEM{n}", **extra}
    if observed is not None:
        row["ot"] = int(observed)
    return row


def install(mod, items, completed):
    mod.board_cache = {"t": completed, "err": None, "errs": [], "items": items, "prs": [], "lc": {}, "cases": []}


def record(state, moves):
    (state / "board-moves.json").write_text(json.dumps(moves, sort_keys=True) + "\n")


def recorded(state):
    return json.loads((state / "board-moves.json").read_text())


def get_board(port):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/api/board", headers={"Host": f"127.0.0.1:{port}"})
    answer = conn.getresponse()
    raw = answer.read()
    conn.close()
    assert answer.status == 200, raw[:200]
    return json.loads(raw)


def where(board, n):
    return [(g["name"], x) for g in board["groups"] for x in g["items"] if x["n"] == n]


def fetch_rows(mod, monkeypatch, rows):
    class Seam:
        def rows(self, *a, **kw):
            return "".join(json.dumps(i) + "\n" for i in rows) + json.dumps({"_page": len(rows)}) + "\n"

        def holders(self):
            return {}

    monkeypatch.setattr(mod, "board_seam", lambda: Seam())
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))


def test_a_full_read_writes_structure_always_and_column_inputs_only_when_newer(hub):
    mod, _ = hub
    held = [card(7, "Todo", observed=100, l=["bug"], title_old=True)]
    newer = [{"n": 7, "t": "renamed card", "s": "OPEN", "st": "Doing", "l": ["loop-bug"], "i": "ITEM7"}]
    merged = mod.merge_items(held, newer, 200)
    assert merged[0]["t"] == "renamed card", "structure is written by every read"
    assert merged[0]["st"] == "Doing" and merged[0]["l"] == ["loop-bug"], "a newer observation writes the column inputs"
    assert merged[0]["ot"] == 200
    older = mod.merge_items(merged, [{"n": 7, "t": "later title", "s": "CLOSED", "st": "Todo", "l": [], "i": "ITEM7"}], 150)
    assert older[0]["t"] == "later title", "structure is written even by an older observation"
    assert older[0]["st"] == "Doing" and older[0]["l"] == ["loop-bug"] and older[0]["s"] == "OPEN", \
        "an older observation never overwrites the column inputs the cache holds"
    assert older[0]["ot"] == 200, "the cache keeps the newer instant of the inputs it holds"


def test_the_overlay_reads_each_items_own_observation_instant(served, monkeypatch):
    mod, state, port = served
    move = 1_000_000
    install(mod, [card(7, "Doing", observed=move + 5), card(8, "Todo", observed=move - 5)], completed=move + 10)
    record(state, {"ITEM7": {"column": "Doing", "at": at(move)}, "ITEM8": {"column": "Doing", "at": at(move)}})
    board = get_board(port)
    seen, unseen = where(board, 7), where(board, 8)
    assert seen and seen[0][0] == "Doing" and seen[0][1].get("pd") is None, \
        f"a card observed after the move agrees and draws no mark: {seen}"
    assert unseen and unseen[0][0] == "Doing" and unseen[0][1].get("pd") is True, \
        f"a card observed before the move still wears the pending mark: {unseen}"


def test_a_pending_move_expires_only_on_a_read_that_saw_its_card(served):
    mod, state, port = served
    move = 1_000_000
    install(mod, [card(9, "Todo", observed=move - 5000, by="owner")], completed=move + 100_000)
    record(state, {"ITEM9": {"column": "Doing", "at": at(move), "by": "owner"}})
    board = get_board(port)
    placed = where(board, 9)
    assert placed and placed[0][0] == "Doing" and placed[0][1].get("pd") is True, \
        f"a read that never saw the card expired its move: {placed}"
    assert "ITEM9" in recorded(state), "a move was dropped by a read that did not see its card"


def test_a_pending_move_expires_on_a_read_that_saw_its_card_past_the_window(served):
    mod, state, port = served
    move = 1_000_000
    install(mod, [card(9, "Todo", observed=move + 500, by="owner")], completed=move + 500)
    record(state, {"ITEM9": {"column": "Doing", "at": at(move), "by": "owner"}})
    board = get_board(port)
    placed = where(board, 9)
    assert placed and placed[0][0] == "Todo" and placed[0][1].get("pd") is None, \
        f"a read that saw the card past the window did not return it to the read's column: {placed}"
    assert "ITEM9" not in recorded(state), "the expired move stayed in the record"


def test_a_failed_items_read_keeps_the_last_good_rows_and_names_its_error(hub, monkeypatch):
    mod, _ = hub
    install(mod, [card(11, "Todo", observed=100)], completed=100)
    mod.board_cache["parts"] = {"status": {"t": 100.0, "err": None}, "structure": {"t": 100.0, "err": None},
                                "prs": {"t": 100.0, "err": None}}

    def boom(cmd, **kw):
        raise FileNotFoundError("gh is not on PATH")

    monkeypatch.setattr(mod.subprocess, "run", boom)
    mod.board_pass(repos=[])
    assert [i["n"] for i in mod.board_cache["items"]] == [11], "a failed read dropped the last good rows"
    assert mod.board_cache["parts"]["status"]["t"] == 100.0, "a failed read advanced the status part's timestamp"
    assert mod.board_cache["parts"]["status"]["err"], "a failed read left the status part with no error"
    assert mod.board_cache["parts"]["structure"]["err"], "a failed read left the structure part with no error"


def test_an_empty_successful_read_is_an_empty_board(hub, monkeypatch):
    mod, _ = hub
    install(mod, [card(11, "Todo", observed=100)], completed=100)
    fetch_rows(mod, monkeypatch, [])
    mod.board_pass(repos=[])
    assert mod.board_cache["items"] == [], "an empty successful answer kept the previous rows"
    assert mod.board_cache["parts"]["status"]["err"] is None
    assert mod.board()["groups"] and all(g["n"] == 0 for g in mod.board()["groups"] if not g.get("end")), \
        "the served board is not empty after an empty successful read"


def test_the_board_names_each_part_with_its_own_timestamp_and_error(hub, monkeypatch):
    mod, _ = hub
    install(mod, [], completed=100)
    fetch_rows(mod, monkeypatch, [card(11, "Todo")])
    mod.board_pass(repos=[])
    parts = mod.board()["parts"]
    assert set(parts) == {"status", "structure", "prs"}, f"the served board names {sorted(parts)}, not the three parts"
    for name in ("status", "structure", "prs"):
        assert isinstance(parts[name]["t"], (int, float)) and parts[name]["t"] > 0, f"{name} has no timestamp"
        assert "err" in parts[name], f"{name} carries no error field"


def test_the_page_draws_the_age_of_each_part(hub):
    mod, _ = hub
    assert '["status","structure","prs"].map(k=>{const p=(b.parts||{})[k];return k+" "' in mod.PAGE, \
        "the page draws no age for each part of the board"
