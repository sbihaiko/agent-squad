"""SPEC 1035 (#1040): the close path's profile comment.

Closing a ticket reads a run record, appends its rows to the ticket's dataset, then projects the dataset into one
marked comment. A test reads the comment through the module's public door and drives the close path through a fake
transport: no network, no token.
"""
import pytest

from squad import profile_comment, profile_dataset, profiler, record

ISSUE = "sbihaiko/agent-squad#1040"
MARKER = "<!-- agent-squad:profile -->"


def write_record(tmp_path, name, run_id, model="claude-opus-5-5"):
    r = record.Record(tmp_path / f"{name}.jsonl")
    r.append("run_start", run_id=run_id, issue=ISSUE, graph_name="g", graph_hash="h", engine_options={},
             root="main", plan=["main"])
    r.append("call_start", node="code", entry=1, item=None, session="s1", pid=1, limit=None, started_at=1.0)
    r.append("outcome", node="code", entry=1, model=model, outcome="pass", reasons=[], artifact=None, spend=None,
             turns=4, cap=None, elapsed=None, tokens_in=100, tokens_out=20)
    return r.path


def add_outcome(path, model="claude-sonnet-5-5"):
    record.Record(path).append("outcome", node="code", entry=1, model=model, outcome="pass", reasons=[],
                               artifact=None, spend=None, turns=2, cap=None, elapsed=None, tokens_in=7,
                               tokens_out=3)


class FakeTransport:
    def __init__(self, fail_post=False):
        self.stored = []
        self.fail_post = fail_post
        self.writes = 0

    def comments(self, issue):
        return [dict(c) for c in self.stored]

    def post(self, issue, body):
        if self.fail_post:
            raise OSError("comment write refused")
        self.writes += 1
        self.stored.append({"id": len(self.stored) + 1, "body": body})

    def patch(self, comment_id, body):
        self.writes += 1
        next(c for c in self.stored if c["id"] == comment_id)["body"] = body


def test_closing_a_ticket_writes_one_comment_marked_for_a_later_pass(tmp_path):
    transport = FakeTransport()

    profile_comment.close(tmp_path / "profiles", ISSUE, write_record(tmp_path, "r1", "run-1"), transport)

    assert len(transport.stored) == 1
    assert transport.stored[0]["body"].startswith(MARKER + "\n")


def test_regenerating_the_comment_from_the_dataset_gives_the_same_bytes(tmp_path):
    root = tmp_path / "profiles"
    transport = FakeTransport()
    profile_comment.close(root, ISSUE, write_record(tmp_path, "r1", "run-1"), transport)
    rows = profile_dataset.read(root, ISSUE)

    body = transport.stored[0]["body"]
    assert profile_comment.render(rows) == body
    assert profile_comment.render(list(reversed(rows))) == body


def test_a_failed_comment_write_leaves_the_dataset_whole_and_the_comment_regenerable(tmp_path):
    root = tmp_path / "profiles"
    record_path = write_record(tmp_path, "r1", "run-1")
    transport = FakeTransport(fail_post=True)
    with pytest.raises(OSError):
        profile_comment.close(root, ISSUE, record_path, transport)

    rows_after_failure = profile_dataset.read(root, ISSUE)
    assert rows_after_failure == profiler.from_file(record_path)
    expected = profile_comment.render(rows_after_failure)

    transport.fail_post = False
    profile_comment.close(root, ISSUE, record_path, transport)

    assert len(transport.stored) == 1
    assert transport.stored[0]["body"] == expected
    assert profile_dataset.read(root, ISSUE) == rows_after_failure


def test_two_runs_on_one_ticket_show_as_two_blocks_in_one_comment(tmp_path):
    root = tmp_path / "profiles"
    transport = FakeTransport()
    profile_comment.close(root, ISSUE, write_record(tmp_path, "r1", "run-1"), transport)
    profile_comment.close(root, ISSUE, write_record(tmp_path, "r2", "run-2"), transport)

    assert len(transport.stored) == 1
    parsed = profile_comment.parse(transport.stored[0]["body"])
    assert set(parsed) == {"run-1", "run-2"}
    assert parsed["run-1"] == [r for r in profile_dataset.read(root, ISSUE) if r["run_id"] == "run-1"]
    assert parsed["run-2"] == [r for r in profile_dataset.read(root, ISSUE) if r["run_id"] == "run-2"]


def test_closing_the_same_record_twice_keeps_one_row_and_writes_the_comment_once(tmp_path):
    root = tmp_path / "profiles"
    record_path = write_record(tmp_path, "r1", "run-1")
    transport = FakeTransport()
    profile_comment.close(root, ISSUE, record_path, transport)
    body = transport.stored[0]["body"]

    profile_comment.close(root, ISSUE, record_path, transport)

    assert len(profile_dataset.read(root, ISSUE)) == 1
    assert transport.stored[0]["body"] == body
    assert transport.writes == 1


def test_a_resumed_run_replaces_its_own_block_and_never_appends_a_second(tmp_path):
    root = tmp_path / "profiles"
    record_path = write_record(tmp_path, "r1", "run-1")
    transport = FakeTransport()
    profile_comment.close(root, ISSUE, record_path, transport)
    add_outcome(record_path)

    profile_comment.close(root, ISSUE, record_path, transport)

    body = transport.stored[0]["body"]
    assert len(transport.stored) == 1
    assert body.count("<!-- agent-squad:profile-run run-1 begin -->") == 1
    assert [r["attempt"] for r in profile_comment.parse(body)["run-1"]] == [1, 2]
    assert body == profile_comment.render(profile_dataset.read(root, ISSUE))
