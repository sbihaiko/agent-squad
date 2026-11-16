"""One board snapshot per window (#1796).

The full board read is the loop's most expensive call: the REST items route answers 100 cards a page, so a board of
more than a thousand cards is a dozen requests that take eighteen seconds. Every pass that only reports (the health
check, the board read behind a listing) asks for the same answer again seconds later.

The seam keeps one snapshot per window: the first reader inside the window reads the carrier and stores the answer,
a second reader inside the window is served from it and sends no request, and the window is a setting
(`SG_BOARD_SNAPSHOT_S`). A reader that gates a write asks for a live read, because a card it is about to move must not
be answered from an old snapshot, and a window of zero keeps every read live.
"""
import importlib.util
import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts" / "issue_state.py"
SPEC = importlib.util.spec_from_file_location("issue_state_snapshot", SCRIPT)
issue_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_state)

FIELDS = [
    {"id": "F_STATUS", "name": "Status", "options": [{"id": "O_TODO", "name": {"raw": "Todo"}}]},
    {"id": "F_RUN", "name": "Run"},
    {"id": "F_START", "name": "Run started"},
]


class Board:
    """A stub gh that counts the items reads, one card in Todo."""

    def __init__(self):
        self.reads = 0

    def __call__(self, cmd, input=None, **kw):
        s = " ".join(cmd)
        out = ""
        if "/fields?" in s:
            out = json.dumps(FIELDS)
        elif "/items?" in s:
            self.reads += 1
            item = {"id": "ITEM1", "content": {"number": 241, "repository_url": "https://api.github.com/repos/x/y"},
                    "fields": [{"id": "F_STATUS", "name": "Status", "value": {"name": "Todo"}}]}
            jq = cmd[cmd.index("--jq") + 1]
            out = subprocess.run(["jq", "-r", jq], input=json.dumps([item]), capture_output=True, text=True, check=True).stdout
        return subprocess.CompletedProcess(cmd, 0, out, "")


def seam(tmp_path, board, **env):
    base = {"SG_BOARD_READ_BACKOFF": "0", "SG_BOARD_SNAPSHOT_S": "120"}
    base.update(env)
    return issue_state.board_state("o", "4", "x/y", kind="users", state_dir=tmp_path, run=board, env=base)


def test_two_readers_in_one_window_send_one_request(tmp_path):
    board = Board()
    state = seam(tmp_path, board)

    first, _ = state.board()
    second, _ = state.board()

    assert board.reads == 1, "the second reader inside the window is served from the snapshot"
    assert second == first == {"241": "Todo"}


def test_a_reader_that_gates_a_write_asks_the_carrier_every_time(tmp_path):
    board = Board()
    state = seam(tmp_path, board)

    state.board(fresh=True)
    state.board(fresh=True)

    assert board.reads == 2, "a live read is never answered from the snapshot"


def test_a_live_read_refreshes_the_snapshot_for_the_next_reader(tmp_path):
    board = Board()
    state = seam(tmp_path, board)

    state.board()
    state.board(fresh=True)
    state.board()

    assert board.reads == 2, "the live read leaves the newest answer in the window"


def test_a_window_of_zero_keeps_every_read_live(tmp_path):
    board = Board()
    state = seam(tmp_path, board, SG_BOARD_SNAPSHOT_S="0")

    state.board()
    state.board()

    assert board.reads == 2


def test_a_window_past_its_bound_reads_again(tmp_path):
    import time
    board = Board()
    state = seam(tmp_path, board, SG_BOARD_SNAPSHOT_S="1")

    state.board()
    for f in tmp_path.glob("board-snapshot-*"):
        old = time.time() - 5
        import os
        os.utime(f, (old, old))
    state.board()

    assert board.reads == 2, "a snapshot older than its window is no answer"


def test_the_snapshot_is_per_repository(tmp_path):
    """Two repositories on one board never read each other's snapshot (#770)."""
    one, two = Board(), Board()
    a = seam(tmp_path, one)
    b = issue_state.board_state("o", "4", "other/repo", kind="users", state_dir=tmp_path, run=two,
                                env={"SG_BOARD_READ_BACKOFF": "0", "SG_BOARD_SNAPSHOT_S": "120"})

    a.board()
    b.board()

    assert one.reads == 1 and two.reads == 1
    files = sorted(p.name for p in tmp_path.glob("board-snapshot-*"))
    assert len(files) == 2, f"one snapshot per repository, got {files}"


def test_the_state_read_keeps_its_own_snapshot(tmp_path):
    """The state read stays the Status-only answer, and two reporting passes inside the window send one request (#1796).

    It is its own snapshot on purpose: a carrier that names no Run field still answers the state read, and a caller
    that decides a write passes fresh."""
    board = Board()
    state = seam(tmp_path, board)

    first = state.read_all()
    second = state.read_all()

    assert first == second == {"241": "Todo"}
    assert board.reads == 1, "the second state read is served from the window's snapshot"


def test_a_live_state_read_asks_the_carrier(tmp_path):
    board = Board()
    state = seam(tmp_path, board)

    state.read_all(fresh=True)
    state.read_all(fresh=True)

    assert board.reads == 2
