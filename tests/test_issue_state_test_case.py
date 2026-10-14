"""A finished test pass becomes one `test case` item on the board, through the seam (#300).

One writer records the pass: the issue it tested, the result, the merge commit it ran on and when it ended
(the age is read from that). The item is a draft item of the board carrying the kind `test case`; its title
holds the four facts, so the next read answers all of them. The write is proven by the read that follows it,
and the items route is eventually consistent, so the proving read is bounded and retried: a write no read
carries is a named `NotCarried`, never reported as written. A pass that ended failed stays failed.
The carrier is a stub gh that keeps a list of items, the way the board does.
"""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts" / "issue_state.py"
SPEC = importlib.util.spec_from_file_location("issue_state_test_case", SCRIPT)
issue_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_state)

SHA = "1641bc94f52afca78acdf6a915a200cccff3e5e9"
SHA2 = "9caabae0000000000000000000000000000000aa"
OPTIONS = {"O_BUG": "Bug", "O_TC": "Test case"}


class Board:
    """A stub gh over a board. `drop` makes a draft POST answer ok and never land; `lag` is how many item reads
    stay behind a write before the item shows (the route is eventually consistent)."""

    def __init__(self, drop=False, lag=0, options=OPTIONS):
        self.items, self.drop, self.lag, self.options = [], drop, lag, options
        self.posts, self.patches, self.reads, self.visible_after = [], [], 0, {}

    def __call__(self, cmd, input=None, **kw):
        s = " ".join(cmd)
        out = ""
        if "-X POST" in s and "/items" in s:
            body = json.loads(input)
            self.posts.append(body)
            iid = f"D{len(self.posts)}"
            if not self.drop:
                self.items.append({"id": iid, "content": {"title": body["title"]}, "fields": []})
                self.visible_after[iid] = self.reads + self.lag
            out = json.dumps({"id": iid})
        elif "-X PATCH" in s and "/items/D" in s:
            body = json.loads(input)
            iid = cmd[cmd.index("-X") + 2].rsplit("/", 1)[1]
            self.patches.append((iid, body))
            item = next((i for i in self.items if i["id"] == iid), None)
            if item is None:   # a draft that never landed has no item to patch
                return subprocess.CompletedProcess(cmd, 1, "", "HTTP 404: Not Found")
            value = body["fields"][0]["value"]
            item["fields"] = [{"id": "F_KIND", "name": "Kind", "value": {"id": value, "name": {"raw": self.options[value]}}}]
            out = json.dumps({"id": item["id"]})
        elif "/fields?" in s:
            out = json.dumps([
                {"id": "F_STATUS", "name": "Status", "options": []},
                {"id": "F_KIND", "name": "Kind", "options": [{"id": i, "name": {"raw": n}} for i, n in self.options.items()]},
            ])
        elif "/items?" in s:
            self.reads += 1
            shown = [i for i in self.items if self.reads > self.visible_after[i["id"]]]
            out = subprocess.run(["jq", "-r", cmd[cmd.index("--jq") + 1]], input=json.dumps(shown), capture_output=True, text=True, check=True).stdout
        return subprocess.CompletedProcess(cmd, 0, out, "")


def seam(tmp_path, board):
    return issue_state.board_state("o", "4", "x/y", kind="users", state_dir=tmp_path, run=board, env={"SG_BOARD_READ_BACKOFF": "0"})


def test_a_finished_pass_writes_one_test_case_item_the_next_read_carries(tmp_path):
    board = Board()
    state = seam(tmp_path, board)
    item = state.record_pass(300, "pass", SHA, ended="2026-10-14T03:00:00Z")
    assert (item["issue"], item["result"], item["commit"]) == (300, "pass", SHA)
    assert len(board.posts) == 1 and board.posts[0]["type"] == "DraftIssue"
    assert [b["fields"][0]["value"] for _, b in board.patches] == ["O_TC"]
    rows = state.test_cases(now="2026-10-14T03:05:00Z")
    assert rows == [{"issue": 300, "result": "pass", "commit": SHA, "ended": "2026-10-14T03:00:00Z", "age": 300}]


def test_the_item_names_the_merge_commit_the_pass_ran_on(tmp_path):
    board = Board()
    seam(tmp_path, board).record_pass(300, "pass", SHA, ended="2026-10-14T03:00:00Z")
    assert SHA in board.posts[0]["title"]


def test_a_pass_with_no_merge_commit_or_no_result_is_refused_before_any_write(tmp_path):
    board = Board()
    state = seam(tmp_path, board)
    with pytest.raises(ValueError):
        state.record_pass(300, "pass", "", ended="2026-10-14T03:00:00Z")
    with pytest.raises(ValueError):
        state.record_pass(300, "green", SHA, ended="2026-10-14T03:00:00Z")
    assert board.posts == []


def test_a_write_no_read_carries_is_a_named_failure_never_a_written_item(tmp_path):
    with pytest.raises(issue_state.NotCarried) as e:
        seam(tmp_path, Board(drop=True)).record_pass(300, "pass", SHA, ended="2026-10-14T03:00:00Z")
    assert "300" in str(e.value) and SHA in str(e.value)


def test_the_proving_read_allows_for_a_read_that_is_behind_the_write(tmp_path):
    board = Board(lag=2)
    item = seam(tmp_path, board).record_pass(300, "pass", SHA, ended="2026-10-14T03:00:00Z")
    assert item["commit"] == SHA
    assert len(board.posts) == 1   # a late read is waited for, never answered by a second write


def test_a_read_behind_past_the_bound_is_a_named_failure(tmp_path):
    with pytest.raises(issue_state.NotCarried):
        seam(tmp_path, Board(lag=9)).record_pass(300, "pass", SHA, ended="2026-10-14T03:00:00Z")


def test_a_pass_that_ended_failed_stays_failed_when_a_green_pass_follows_on_the_same_commit(tmp_path):
    board = Board()
    state = seam(tmp_path, board)
    state.record_pass(300, "fail", SHA, ended="2026-10-14T03:00:00Z")
    kept = state.record_pass(300, "pass", SHA, ended="2026-10-14T04:00:00Z")
    assert kept["result"] == "fail"
    assert [r["result"] for r in state.test_cases()] == ["fail"]
    assert len(board.posts) == 1


def test_a_green_pass_on_a_new_merge_commit_is_its_own_item_and_the_failure_stays(tmp_path):
    state = seam(tmp_path, Board())
    state.record_pass(300, "fail", SHA, ended="2026-10-14T03:00:00Z")
    state.record_pass(300, "pass", SHA2, ended="2026-10-14T04:00:00Z")
    assert sorted((r["commit"], r["result"]) for r in state.test_cases()) == sorted([(SHA, "fail"), (SHA2, "pass")])


def test_the_same_green_pass_recorded_twice_is_one_item(tmp_path):
    board = Board()
    state = seam(tmp_path, board)
    state.record_pass(300, "pass", SHA, ended="2026-10-14T03:00:00Z")
    state.record_pass(300, "pass", SHA, ended="2026-10-14T03:00:00Z")
    assert len(board.posts) == 1


def test_a_binding_that_cannot_carry_the_kind_is_a_named_gap_and_nothing_is_written(tmp_path):
    board = Board(options={"O_BUG": "Bug"})
    with pytest.raises(issue_state.KindGap):
        seam(tmp_path, board).record_pass(300, "pass", SHA, ended="2026-10-14T03:00:00Z")
    assert board.posts == []
