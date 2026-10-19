"""The hub draws a proved move at once, marked pending, until the board read agrees (#650).

The board door records each move it proved in `$SG_STATE/board-moves.json`, keyed by the item, with the column it
set and the instant (#649). The board the hub serves applies that record on top of the list read: a card the list
read contradicts is drawn in the recorded column and wears a pending mark, and the column counts are taken after the
overlay. A move is agreed only by a list read that began in a later whole second than the move and carries the
recorded column for that item; then the hub drops the entry and the mark goes with no flicker. A read that began
before or in the move's own second says nothing about it, so the mark stays until a later read agrees. The hub
writes the record only to drop an entry the read agrees with: a disagreeing entry and every other entry stay, a
record naming no column of the shipped vocabulary is never drawn and stays, and a failed drop is counted and retried
by the next serve. The page draws the mark from the row, so a reload keeps it. Every column of the shipped
vocabulary takes the same path.

An entry no list read agrees with expires after its window (#651): the served board returns the card to the column
the list read holds, the entry is dropped, and the loop's log names the item, the recorded column and the read
column once. The window is a setting (`SG_PENDING_WINDOW_SECONDS`, 120 s by default). One setting
(`SG_PENDING_OVERLAY`) turns the overlay off, and the served board is then the list read alone.

The suite serves the board over HTTP from a temporary state directory, against a lagging board read.
"""
import http.client
import importlib.util
import json
import pathlib
import re
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


def lagging(mod, items, read_at, completed=None):
    """The rows the last pass read: a list read that has not carried the door's proved move yet. `rt` is the instant
    the fetch began, the only stamp the overlay may compare a move against; `t` is when the pass finished."""
    mod.board_cache = {"t": completed if completed is not None else read_at, "rt": int(read_at),
                       "err": None, "errs": [], "items": items, "prs": [], "lc": {}, "cases": []}


def lagging_fetch(mod, monkeypatch, items, on_read=None):
    """Point a real board pass at a fetch that returns `items`: the list read its rows come from, with the door's other
    reads answered empty. `on_read` runs while that fetch is in flight, as a writer on the hub would."""
    class Seam:
        def rows(self, *a, **kw):
            if on_read:
                on_read()
            return "".join(json.dumps(i) + "\n" for i in items)

        def holders(self):
            return {}

    monkeypatch.setattr(mod, "board_seam", lambda: Seam())
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))


def record(state, moves):
    (state / "board-moves.json").write_text(json.dumps(moves, sort_keys=True) + "\n")


def recorded(state):
    return json.loads((state / "board-moves.json").read_text())


def get_board(port):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/api/board", headers={"Host": f"127.0.0.1:{port}"})
    answer = conn.getresponse()
    status, ctype, raw = answer.status, answer.getheader("Content-Type") or "", answer.read()
    conn.close()
    assert status == 200, f"served board missing: /api/board answered {status}: {raw[:200]!r}"
    assert "application/json" in ctype.lower(), \
        f"served board missing: /api/board answered {ctype!r}, not a JSON board: {raw[:200]!r}"
    return json.loads(raw)


def where(board, n):
    """Every (column, row) the served board draws card `n` in."""
    return [(g["name"], x) for g in board["groups"] for x in g["items"] if x["n"] == n]


def _lum(hexcolor):
    """WCAG relative luminance of a `#rrggbb` colour."""
    v = [int(hexcolor.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in v]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a, b):
    """The WCAG contrast ratio of two `#rrggbb` colours, from 1:1 to 21:1."""
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


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
    """The page's own `cards` renderer, run by node over the rows the hub serves: the mark is proved by rendering.
    With no node on this runner it returns None, and the test reads the renderer's own branch as a fragment: no skip."""
    if NODE is None:
        return None
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


def test_a_move_proved_during_the_list_read_is_not_agreed_by_that_read(served, monkeypatch):
    """The overlay compares a move with the instant the list read began. A door that proves the move while the fetch is
    in flight cannot have had it carried, even when the page it returns ends up naming the recorded column."""
    mod, state, port = served

    def proved_mid_read():
        record(state, {"ITEM7": {"column": "Doing", "at": at(now())}})

    lagging_fetch(mod, monkeypatch, [card(7, "Doing")], on_read=proved_mid_read)
    mod.board_pass(repos=[])
    placed = where(get_board(port), 7)
    assert [p[0] for p in placed] == ["Doing"], f"the recorded column is not the one drawn: {placed}"
    assert placed[0][1].get("pd") is True, "a move proved after the list read began was taken as carried by that read"
    assert "ITEM7" in recorded(state), "a move the read cannot have carried was dropped"


def test_a_move_in_the_reads_own_whole_second_is_not_agreed(served):
    """The record's stamps and the read's start are whole epoch seconds. In the same second nothing tells which came
    first, so the mark stays rather than lose a proved move; the read must begin in a later second."""
    mod, state, port = served
    second = int(now()) - 10
    lagging(mod, [card(7, "Doing")], second)
    record(state, {"ITEM7": {"column": "Doing", "at": at(second)}})
    placed = where(get_board(port), 7)
    assert [p[0] for p in placed] == ["Doing"], f"the card left its column: {placed}"
    assert placed[0][1].get("pd") is True, "a move in the read's own second was taken as carried by that read"
    assert "ITEM7" in recorded(state), "a move in the read's own second was dropped"


def test_a_read_that_began_a_later_whole_second_does_agree(served):
    """One whole second is the step: the same columns one second apart are the read's proof of the move."""
    mod, state, port = served
    second = int(now()) - 10
    lagging(mod, [card(7, "Doing")], second + 1)
    record(state, {"ITEM7": {"column": "Doing", "at": at(second)}})
    placed = where(get_board(port), 7)
    assert [p[0] for p in placed] == ["Doing"] and "pd" not in placed[0][1], f"the agreeing read left a mark: {placed}"
    assert "ITEM7" not in recorded(state), "a read that began a later second than the move did not drop it"


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
            if self.calls >= 3:   # between the pass's own read and its guard read, the door replaces the record
                (state / "board-moves.json").write_text(new_body)
                return new_body
            return old_body

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


def test_a_failed_drop_is_counted_and_retried_by_the_next_serve(served, monkeypatch):
    """A drop the state directory refuses is bookkeeping that reports itself: the entry stays, the failure is counted,
    the temp is removed, and the next serve tries again."""
    mod, state, port = served
    read_at = now() - 10
    lagging(mod, [card(7, "Doing")], read_at)
    record(state, {"ITEM7": {"column": "Doing", "at": at(read_at - 5)}})
    real_replace, tries = mod.os.replace, []

    def fail_once(src, dst):
        tries.append(dst)
        if len(tries) == 1:
            raise OSError("the state directory refused the replace")
        return real_replace(src, dst)

    monkeypatch.setattr(mod.os, "replace", fail_once)
    first = get_board(port)
    assert [p[0] for p in where(first, 7)] == ["Doing"], "the agreeing read drew the wrong column"
    assert "ITEM7" in recorded(state), "the failed drop changed the record"
    assert mod.DROP_FAILURES == 1, "the failed drop was not counted"
    assert not list(state.glob("board-moves.json.*.tmp")), "the failed drop left its temp behind"
    get_board(port)
    assert "ITEM7" not in recorded(state), "the next serve did not retry the failed drop"
    assert mod.DROP_FAILURES == 1, "the successful retry was counted as another failure"


def test_a_record_outside_the_shipped_vocabulary_is_never_drawn_and_is_kept(served):
    """A renamed or corrupt record value draws no column the board does not ship. The read says nothing about it, so it
    is no agreement: the hub never writes the record except to drop an entry the read agrees with."""
    mod, state, port = served
    read_at = now() - 10
    lagging(mod, [card(7, "Todo")], read_at)
    record(state, {"ITEM7": {"column": "Nowhere", "at": at(read_at + 5)}})
    board = get_board(port)
    placed = where(board, 7)
    assert [p[0] for p in placed] == ["Todo"], f"the card left its read column: {placed}"
    assert "pd" not in placed[0][1], "the card wears a mark for a column no board ships"
    assert "Nowhere" not in {g["name"] for g in board["groups"]}, "the board served a column it does not ship"
    assert "ITEM7" in recorded(state), "the hub dropped a record entry its read never agreed with"


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


def test_a_pending_closed_card_carries_no_open_freeze(served):
    """The drawn column's freeze is computed with the row's own closedness: a closed card never wears the open card's
    owner freeze from its labels."""
    mod, state, port = served
    read_at = now() - 10
    lagging(mod, [card(7, "Closed", s="CLOSED", l=["frozen:owner"])], read_at)
    record(state, {"ITEM7": {"column": "Doing", "at": at(read_at + 5)}})
    placed = where(get_board(port), 7)
    assert [p[0] for p in placed] == ["Doing"], f"the recorded column is not the one drawn: {placed}"
    assert placed[0][1].get("pd") is True
    assert "fz" not in placed[0][1], f"a closed card wore an open card's freeze: {placed}"


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


def test_a_page_reload_keeps_the_pending_mark(served, monkeypatch):
    """The page renders the row it is served: two real passes over a lagging fetch both keep the mark, and the second
    pass's snapshot is its own -- the reload never reuses the first pass's list read."""
    mod, state, port = served
    lagging_fetch(mod, monkeypatch, [card(7, "Todo")])
    record(state, {"ITEM7": {"column": "Doing", "at": at(now())}})
    mod.board_pass(repos=[])
    first_board = get_board(port)
    first = where(first_board, 7)
    assert first and first[0][1].get("pd") is True, f"the first pass lost the mark: {first}"
    mod.board_pass(repos=[])   # the page reloads: the hub runs another pass over the same lagging fetch
    again_board = get_board(port)
    again = where(again_board, 7)
    assert again and again[0][1].get("pd") is True, f"a reload lost the pending mark: {again}"
    assert again_board["t"] != first_board["t"], "the second serve reused the first pass's snapshot: no fresh pass ran"
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
    plain_drawn, drawn = render_cards(mod, [plain]), render_cards(mod, [marked])
    if drawn is None:   # no node here: the renderer's own branch is the fragment, asserted and never skipped
        assert "x.pd?`<span class=pnd" in mod.PAGE, "the page's renderer has no branch for the pending row"
    else:
        assert "class=pnd" not in plain_drawn, f"a plain card wears the pending mark: {plain_drawn}"
        assert "class=pnd" in drawn and ">pending</span>" in drawn, f"the page does not draw the pending mark: {drawn}"
    root = next(line for line in mod.PAGE.splitlines() if line.startswith(":root{"))
    warn = re.search(r"--warn:(#[0-9a-fA-F]{6})", root)
    assert warn, f"the page defines no --warn, so the pending mark has no colour: {root}"
    mark = next(line for line in mod.PAGE.splitlines() if line.startswith("#bbody .it .pnd"))
    ink = re.search(r"color:(#[0-9a-fA-F]{6})", mark)
    assert ink, f"the mark names no ink colour: {mark}"
    assert "background:var(--warn)" in mark and "font-weight:700" in mark, f"the mark does not read at a glance: {mark}"
    ratio = _contrast(ink[1], warn[1])
    assert ratio >= 4.5, f"the pending mark's {ink[1]} on {warn[1]} reads {ratio:.1f}:1, under the AA floor for its small text"


def test_the_board_pass_reads_the_item_id_the_record_is_keyed_by(hub, monkeypatch):
    """The door keys each entry by the Project item id, so the pass must carry it on the row it stores, and the overlay
    must find the entry by it: a record for that id draws the pending mark."""
    mod, state = hub
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
    assert [i.get("i") for i in mod.board_cache["items"]] == ["ITEM11"], \
        f"the pass stored no item id, so no record can be keyed to the row: {mod.board_cache['items']}"
    record(state, {"ITEM11": {"column": "Doing", "at": at(now())}})
    placed = where(mod.board(), 11)
    assert placed and placed[0][1].get("pd") is True, \
        f"a record keyed by the stored item id draws no pending mark: {placed}"


def test_an_entry_past_the_window_returns_the_card_to_the_reads_column(served):
    """No read will ever agree with a lost board write, so the entry expires: the served board returns the card to
    the column the list read holds, with no pending mark, and the entry is dropped."""
    mod, state, port = served
    lagging(mod, [card(7, "Todo")], now() - 10)
    record(state, {"ITEM7": {"column": "Doing", "at": at(now() - 600)}})
    board = get_board(port)
    placed = where(board, 7)
    assert [p[0] for p in placed] == ["Todo"], f"an expired entry still drew its column: {placed}"
    assert "pd" not in placed[0][1], "the card whose entry expired still wears the pending mark"
    assert "ITEM7" not in recorded(state), "the expired entry stayed in the record"


def test_the_expiry_is_reported_once_in_the_loop_log_naming_the_item_and_both_columns(served):
    """The disagreement is visible in the loop's log: one line names the item, the recorded column and the column
    the list read holds. A second serve of the same record writes no second line."""
    mod, state, port = served
    lagging(mod, [card(7, "Todo")], now() - 10)
    record(state, {"ITEM7": {"column": "Doing", "at": at(now() - 600)}})
    get_board(port)
    get_board(port)
    lines = (state / "cycle.log").read_text().splitlines() if (state / "cycle.log").exists() else []
    assert len(lines) == 1, f"the expiry was not reported once: {lines}"
    line = lines[0]
    assert "#7" in line, f"the expiry report does not name the item: {line}"
    assert "Doing" in line and "Todo" in line, f"the expiry report does not name both columns: {line}"


def test_a_card_another_writer_moved_follows_the_read_once_its_entry_expires(served):
    """Another writer moved the card while the door's entry slept: once the entry expires the hub follows the carrier,
    never the stale record."""
    mod, state, port = served
    lagging(mod, [card(9, "Doing")], now() - 10)
    record(state, {"ITEM9": {"column": "Backlog", "at": at(now() - 600)}})
    placed = where(get_board(port), 9)
    assert [p[0] for p in placed] == ["Doing"], f"the hub argued with the carrier after the expiry: {placed}"
    assert "pd" not in placed[0][1], "the card another writer moved still wears the pending mark"
    assert "ITEM9" not in recorded(state), "the expired entry of the card another writer moved stayed"


def test_the_window_defaults_to_120_seconds(served):
    """An entry 100 s old is still pending; one 130 s old has expired, with no setting in the environment."""
    mod, state, port = served
    lagging(mod, [card(7, "Todo"), card(8, "Todo")], now() - 10)
    record(state, {"ITEM7": {"column": "Doing", "at": at(now() - 100)},
                   "ITEM8": {"column": "Doing", "at": at(now() - 130)}})
    board = get_board(port)
    inside, past = where(board, 7), where(board, 8)
    assert [p[0] for p in inside] == ["Doing"] and inside[0][1].get("pd") is True, \
        f"an entry inside the 120 s default window was not drawn as pending: {inside}"
    assert [p[0] for p in past] == ["Todo"] and "pd" not in past[0][1], \
        f"an entry past the 120 s default window did not expire: {past}"


def test_the_window_is_a_setting(served, monkeypatch):
    """A window named in the environment replaces the default: the same 100 s old entry expires under 60 s."""
    mod, state, port = served
    monkeypatch.setenv("SG_PENDING_WINDOW_SECONDS", "60")
    lagging(mod, [card(7, "Todo")], now() - 10)
    record(state, {"ITEM7": {"column": "Doing", "at": at(now() - 100)}})
    placed = where(get_board(port), 7)
    assert [p[0] for p in placed] == ["Todo"], f"the 60 s window did not expire a 100 s old entry: {placed}"
    assert "pd" not in placed[0][1], "the card whose entry expired under the setting still wears the mark"


@pytest.mark.parametrize("off", ["off", "0"])
def test_the_off_setting_serves_the_list_read_only(served, monkeypatch, off):
    """One setting turns the overlay off: no pending card, no mark, and the record is left as the door wrote it."""
    mod, state, port = served
    monkeypatch.setenv("SG_PENDING_OVERLAY", off)
    lagging(mod, [card(7, "Todo")], now() - 10)
    record(state, {"ITEM7": {"column": "Doing", "at": at(now() - 600)}})
    board = get_board(port)
    placed = where(board, 7)
    assert [p[0] for p in placed] == ["Todo"], f"the overlay drew a card though it is off: {placed}"
    assert "pd" not in placed[0][1], "the overlay marked a card though it is off"
    assert "ITEM7" in recorded(state), "the hub wrote the record while the overlay was off"
    assert not (state / "cycle.log").exists(), "the hub reported an expiry while the overlay was off"
