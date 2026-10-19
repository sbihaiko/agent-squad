"""The hub draws a proved move at once, marked pending, until the board read agrees (#650).

The board door records each move it proved in `$SG_STATE/board-moves.json`, keyed by the item, with the column it
set and the instant (#649). The board the hub serves applies that record on top of the list read: a card the list
read contradicts is drawn in the recorded column and wears a pending mark, and the column counts are taken after the
overlay. When a pass whose read postdates the move carries the recorded column for that item, the hub drops the entry
and the mark goes with no flicker. A read taken before the move says nothing about it, so the mark stays until a read
that postdates the move agrees. The hub writes the record only to drop an entry the read agrees with: a disagreeing
entry and every other entry stay, and a failed drop never breaks the served board. A record naming no column of the
shipped vocabulary is dropped, never drawn. The page draws the mark from the row, so a reload keeps it. Every column
of the shipped vocabulary takes the same path.

The suite serves the board over HTTP from a temporary state directory, against a lagging board read.
"""
import http.client
import importlib.util
import json
import pathlib
import shutil
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TABLE = _module("board_columns_pending", SCRIPTS / "board_columns.py")
SHIPPED = [column["name"] for column in TABLE.shipped_columns()]
NODE = shutil.which("node")


@pytest.fixture
def hub(monkeypatch, tmp_path):
    """The hub loaded for a throwaway profile, with its own state directory: the record is the test's, never the loop's."""
    state = tmp_path / "state"
    state.mkdir()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path / "hub")), ("SG_STATE", str(state))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    return _module("hub_board_pending", HUB_PY), state


@pytest.fixture
def served(hub):
    """The hub's own HTTP handler on a loopback port: the board is read through the route the page reads."""
    mod, state = hub
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, state, srv.server_port
    srv.shutdown()
    srv.server_close()


def now():
    return time.time()


def at(epoch):
    """The instant a record entry carries, in the door's own form: whole seconds, UTC."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def card(n, status="Todo", **extra):
    """One row as the board pass stores it: the item id (`i`) is what the record is keyed by."""
    return {"n": n, "t": f"card {n}", "s": "OPEN", "st": status, "l": [], "i": f"ITEM{n}", **extra}


def lagging(mod, items, read_at):
    """The rows the last pass read: a list read that has not carried the door's proved move yet."""
    mod.board_cache = {"t": read_at, "err": None, "errs": [], "items": items, "prs": [], "lc": {}, "cases": []}


def record(state, moves):
    (state / "board-moves.json").write_text(json.dumps(moves, sort_keys=True) + "\n")


def recorded(state):
    return json.loads((state / "board-moves.json").read_text())


def get_board(port):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/api/board", headers={"Host": f"127.0.0.1:{port}"})
    answer = conn.getresponse()
    body = json.loads(answer.read())
    conn.close()
    return body


def where(board, n):
    """Every (column, row) the served board draws card `n` in."""
    return [(g["name"], x) for g in board["groups"] for x in g["items"] if x["n"] == n]


def _decl(page, name):
    """One declaration of the page, whole: from `const <name>=` to its own `;`, however many lines it takes."""
    i = page.index(f"const {name}=")
    j, depth = i + len(f"const {name}="), 0
    while j < len(page):
        c = page[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ";" and depth == 0:
            break
        j += 1
    return page[i:j + 1]


def render_cards(mod, rows):
    """The page's own `cards` renderer, run by node over the rows the hub serves: the mark is proved by rendering."""
    if NODE is None:
        pytest.skip("node is required")
    stubs = ("const esc=s=>String(s==null?'':s).replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));"
             "const colOf=()=>'';const CN={};const tint=()=>'';const gstrip=()=>'';const age=()=>'1d';"
             "const ageC=()=>'';const lbc=()=>'';const bo={labels:[]};")
    script = (f"{stubs}\n{_decl(mod.PAGE, 'cards')}\n"
              f"console.log(JSON.stringify(cards({{name:'Doing'}}, {json.dumps(rows)}, {json.dumps(rows)})))")
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return p.stdout


def test_a_lagging_read_draws_the_recorded_column_marked_pending(served):
    mod, state, port = served
    read_at = now() - 10
    lagging(mod, [card(7)], read_at)
    record(state, {"ITEM7": {"column": "Doing", "at": at(read_at + 5)}})
    board = get_board(port)
    placed = where(board, 7)
    assert [p[0] for p in placed] == ["Doing"], f"the lagging read kept the card in its old column: {placed}"
    assert placed[0][1].get("pd") is True, "the card the overlay moved wears no pending mark"
    assert {g["name"]: g["n"] for g in board["groups"]}["Doing"] == 1


def test_a_read_that_agrees_drops_the_entry_and_the_mark(served):
    mod, state, port = served
    read_at = now() - 10
    lagging(mod, [card(7, "Doing")], read_at)
    record(state, {"ITEM7": {"column": "Doing", "at": at(read_at - 5)},
                   "ITEM8": {"column": "Doing", "at": at(read_at - 5)}})
    board = get_board(port)
    placed = where(board, 7)
    assert [p[0] for p in placed] == ["Doing"] and "pd" not in placed[0][1], f"the agreeing read left a mark: {placed}"
    moves = recorded(state)
    assert "ITEM7" not in moves, "the entry the read agreed with was not dropped"
    assert "ITEM8" in moves, "the hub dropped an entry the read said nothing about"


def test_a_move_recorded_after_the_read_keeps_its_mark_though_the_column_agrees(served):
    """The read carries the same column by coincidence: it cannot have carried a move recorded after its snapshot."""
    mod, state, port = served
    read_at = now() - 10
    lagging(mod, [card(7, "Doing")], read_at)
    record(state, {"ITEM7": {"column": "Doing", "at": at(read_at + 5)}})
    board = get_board(port)
    placed = where(board, 7)
    assert [p[0] for p in placed] == ["Doing"], f"the card left its column: {placed}"
    assert placed[0][1].get("pd") is True, "a move newer than the read lost its pending mark"
    assert "ITEM7" in recorded(state), "a move newer than the read was dropped"


def test_a_move_the_door_writes_during_the_pass_is_never_lost(served, monkeypatch):
    """The door may write the record while the board pass runs: that write is the door's, and this pass drops nothing."""
    mod, state, port = served
    read_at = now() - 10
    lagging(mod, [card(7, "Doing")], read_at)
    old_body = json.dumps({"ITEM7": {"column": "Doing", "at": at(read_at - 5)}}, sort_keys=True) + "\n"
    new_body = json.dumps({"ITEM7": {"column": "Doing", "at": at(read_at - 5)},
                           "ITEM8": {"column": "Doing", "at": at(read_at - 4)}}, sort_keys=True) + "\n"
    (state / "board-moves.json").write_text(old_body)

    class DoorWritesMidPass:
        """The record as the board sees it while the door writes: the first reads see the old body, then the door's."""
        name = "board-moves.json"

        def __init__(self):
            self.calls = 0

        def read_text(self):
            self.calls += 1
            return old_body if self.calls <= 2 else new_body

        def with_name(self, name):
            return (state / "board-moves.json").with_name(name)

    monkeypatch.setattr(mod, "MOVES", DoorWritesMidPass())
    get_board(port)
    moves = recorded(state)
    assert "ITEM7" in moves, "the pass dropped an entry while the door was writing the record"
    assert "ITEM8" in moves, "the door's write during the pass was lost"


def test_a_failing_record_write_never_breaks_the_served_board(served, monkeypatch):
    """Dropping an agree entry is bookkeeping: a state directory that refuses the write never costs the board."""
    mod, state, port = served
    read_at = now() - 10
    lagging(mod, [card(7, "Doing")], read_at)
    record(state, {"ITEM7": {"column": "Doing", "at": at(read_at - 5)}})

    def refuse(*a, **kw):
        raise OSError("read-only state directory")

    monkeypatch.setattr(mod.os, "replace", refuse)
    board = get_board(port)
    assert [p[0] for p in where(board, 7)] == ["Doing"], "the board did not answer after the record write failed"
    assert "ITEM7" in recorded(state), "the record changed though the write failed"
    assert not list(state.glob("board-moves.json.*.tmp")), "the temp file of the failed write stayed behind"


def test_a_record_outside_the_shipped_vocabulary_is_never_drawn_nor_dropped(served):
    """A renamed or corrupt record value draws no column the board does not ship, and the hub never rewrites it."""
    mod, state, port = served
    read_at = now() - 10
    lagging(mod, [card(7, "Todo")], read_at)
    record(state, {"ITEM7": {"column": "Nowhere", "at": at(read_at + 5)}})
    board = get_board(port)
    placed = where(board, 7)
    assert [p[0] for p in placed] == ["Todo"], f"the card left its read column: {placed}"
    assert "pd" not in placed[0][1], "the card wears a mark for a column no board ships"
    assert "Nowhere" not in {g["name"] for g in board["groups"]}, "the board served a column it does not ship"
    assert "ITEM7" in recorded(state), "the hub rewrote a record entry it does not use"


def test_a_pending_card_takes_the_freeze_of_the_column_it_is_drawn_in(served):
    """The freeze is the drawn column's: the read's freeze never travels with a card the record moved."""
    mod, state, port = served
    read_at = now() - 10
    lagging(mod, [card(7, "Blocked")], read_at)
    record(state, {"ITEM7": {"column": "Doing", "at": at(read_at + 5)}})
    placed = where(get_board(port), 7)
    assert [p[0] for p in placed] == ["Doing"], f"the recorded column is not the one drawn: {placed}"
    assert placed[0][1].get("pd") is True
    assert "fz" not in placed[0][1], f"the read's freeze travelled with the moved card: {placed}"


def test_the_served_card_shape_is_the_old_one_plus_the_mark(served):
    """The wire shape the page reads does not change: a plain card keeps its keys, and only a pending card adds `pd`."""
    mod, state, port = served
    read_at = now() - 10
    lagging(mod, [card(7, "Todo"), card(8, "Todo")], read_at)
    record(state, {"ITEM7": {"column": "Doing", "at": at(read_at + 5)}})
    board = get_board(port)
    pending = where(board, 7)[0][1]
    plain = where(board, 8)[0][1]
    assert set(pending) == {"n", "t", "c", "l", "pd"}, f"the pending card's shape changed: {sorted(pending)}"
    assert set(plain) == {"n", "t", "c", "l"}, f"the plain card's shape changed: {sorted(plain)}"


def test_column_counts_are_taken_after_the_overlay(served):
    mod, state, port = served
    read_at = now() - 10
    lagging(mod, [card(1, "Todo"), card(2, "Todo"), card(3, "Doing")], read_at)
    record(state, {"ITEM2": {"column": "Doing", "at": at(read_at + 5)}})
    board = get_board(port)
    counts = {g["name"]: g["n"] for g in board["groups"]}
    assert counts["Doing"] == 2 and counts["Todo"] == 1, f"the counts are not taken after the overlay: {counts}"
    assert all(g["n"] == len(g["items"]) for g in board["groups"]), "a count disagrees with the column it names"


@pytest.mark.parametrize("column", SHIPPED)
def test_every_column_of_the_shipped_vocabulary_takes_the_same_path(served, column):
    """No column is a special case: the recorded column is drawn, marked, whatever column it is."""
    mod, state, port = served
    read_at = now() - 10
    read = "Todo" if column != "Todo" else "Backlog"
    lagging(mod, [card(7, read)], read_at)
    record(state, {"ITEM7": {"column": column, "at": at(read_at + 5)}})
    board = get_board(port)
    placed = where(board, 7)
    assert [p[0] for p in placed] == [column], f"the recorded column {column!r} is not the one drawn: {placed}"
    assert placed[0][1].get("pd") is True, f"the card moved to {column!r} wears no pending mark"


def test_a_page_reload_keeps_the_pending_mark(served):
    """The page renders the row it is served: a fresh pass over the same lagging read keeps the mark."""
    mod, state, port = served
    read_at = now() - 10
    lagging(mod, [card(7, "Todo")], read_at)
    record(state, {"ITEM7": {"column": "Doing", "at": at(read_at + 5)}})
    first = where(get_board(port), 7)
    assert first and first[0][1].get("pd") is True, f"the first serve lost the mark: {first}"
    lagging(mod, [card(7, "Todo")], now())   # a new pass over the same lagging list read: the page reloads
    again = where(get_board(port), 7)
    assert again and again[0][1].get("pd") is True, f"a reload lost the pending mark: {again}"
    assert "ITEM7" in recorded(state), "a disagreeing entry was dropped, so the mark would vanish on a reload"


def test_the_hub_writes_the_record_only_to_drop_an_agreed_entry(served):
    mod, state, port = served
    read_at = now() - 10
    lagging(mod, [card(7, "Todo")], read_at)
    record(state, {"ITEM7": {"column": "Doing", "at": at(read_at + 5)}})
    before = (state / "board-moves.json").read_bytes()
    get_board(port)
    assert (state / "board-moves.json").read_bytes() == before, \
        "the hub rewrote the record while the read still disagreed with it"


def test_the_page_draws_the_pending_mark_only_on_a_pending_row(hub):
    """The rule lives in the hub's row (`pd`); the page's own renderer draws the mark on that row and on no other."""
    mod, _ = hub
    plain = {"n": 8, "t": "card 8", "l": []}
    marked = {"n": 7, "t": "card 7", "l": [], "pd": True}
    assert "class=pnd" not in render_cards(mod, [plain]), "a plain card wears the pending mark"
    drawn = render_cards(mod, [marked])
    assert "class=pnd" in drawn and ">pending</span>" in drawn, f"the page does not draw the pending mark: {drawn}"
    mark = next(line for line in mod.PAGE.splitlines() if line.startswith("#bbody .it .pnd"))
    assert "background:var(--warn)" in mark and "font-weight:700" in mark, f"the mark does not read at a glance: {mark}"


def test_the_board_pass_reads_the_item_id_the_record_is_keyed_by(hub, monkeypatch):
    """The door keys each entry by the Project item id, so the pass must carry it on the row it stores."""
    mod, _ = hub
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        s = " ".join(cmd)
        if "/fields?" in s:
            out = "PVTSSF_status\n"
        elif "projectsV2" in s and "/items" in s:
            out = json.dumps({"n": 11, "i": "ITEM11", "t": "a card", "s": "OPEN", "st": "Todo", "l": [],
                              "si": {"total": 0, "completed": 0}}) + "\n"
        elif "blockedBy" in s:
            out = json.dumps({"data": {"repository": {"issues": {"nodes": []}}}})
        elif "pullRequests(first:100" in s:
            out = json.dumps({"data": {"repository": {"pullRequests": {"nodes": []}}}})
        else:
            out = ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(mod.subprocess, "run", fake)
    mod.board_cache = {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None}
    mod.board_pass()
    row = next(c for c in calls if "projectsV2" in " ".join(c) and "/items" in " ".join(c))
    assert ".id" in row[-1], f"the items read does not ask for the item id: {row[-1]}"
    assert [i.get("i") for i in mod.board_cache["items"]] == ["ITEM11"], mod.board_cache["items"]
