"""SPEC 1035 (#1036): the profile row contract.

The reader takes a run record that already exists and answers one row per phase per model attempt. A test
states what a reader of the profile must conclude, never how a row is assembled: the fixtures are records on
disk, at the shape `squad/record.py` writes, and the reader is asked through its public door.
"""
import pytest

from squad import profiler, record

START = dict(run_id="run-1", graph_name="g", graph_hash="h", engine_options={}, root="main", plan=["main"])


def write_record(tmp_path, events):
    """One run record on disk, at the shape `record.Record` writes: it opens with its run_start."""
    r = record.Record(tmp_path / "record.jsonl")
    for kind, fields in [("run_start", START), *events]:
        r.append(kind, **fields)
    return r.path


def call(session, node="code", entry=1):
    return ("call_start", dict(node=node, entry=entry, item=None, session=session, pid=1, limit=None, started_at=1.0))


def refused(model, node="code", entry=1):
    return ("model_refused", dict(node=node, entry=entry, model=model, error="session limit"))


def skipped(model, node="code", entry=1):
    return ("model_skipped", dict(node=node, entry=entry, model=model, reason="outside its hours window"))


def outcome(outcome, model=None, node="code", entry=1, **extra):
    fields = dict(node=node, entry=entry, outcome=outcome, reasons=[], artifact=None, spend=None, turns=None,
                  cap=None, elapsed=None)
    fields.update(extra)
    if model is not None:
        fields["model"] = model
    return ("outcome", fields)


def review_round(node, round_, verdict="fix"):
    return ("review_round", dict(node=node, round=round_, head="abc123", verdict=verdict, offered=[], review={}))


def test_two_attempts_of_one_phase_on_two_models_answer_two_rows(tmp_path):
    path = write_record(tmp_path, [call("s1"), refused("claude-sonnet-5-5"), call("s2"),
                                   outcome("pass", model="claude-opus-5-5", tokens_in=100, tokens_out=20, turns=4)])
    rows = profiler.from_file(path)
    assert [(r["phase"], r["model_id"], r["attempt"], r["outcome"]) for r in rows] == [
        ("code", "claude-sonnet-5-5", 1, "refused"),
        ("code", "claude-opus-5-5", 2, "accepted"),
    ]
    assert (rows[1]["tokens_in"], rows[1]["tokens_out"], rows[1]["turns"]) == (100, 20, 4)
    assert (rows[0]["tokens_in"], rows[0]["tokens_out"]) == (None, None)


def test_a_refused_entry_and_a_skipped_entry_answer_their_own_outcome(tmp_path):
    path = write_record(tmp_path, [skipped("claude-haiku-5-5"), call("s1"), refused("claude-sonnet-5-5"),
                                   outcome("fail", model="claude-opus-5-5", reasons=["adapter_error"])])
    rows = profiler.from_file(path)
    assert [r["outcome"] for r in rows] == ["skipped", "refused", "failed"]
    assert "failed" not in (rows[0]["outcome"], rows[1]["outcome"])


def test_a_node_that_ran_no_model_answers_its_own_phase_and_no_model(tmp_path):
    path = write_record(tmp_path, [call("s1", node="review_gate"), outcome("pass", node="review_gate")])
    (row,) = profiler.from_file(path)
    assert (row["phase"], row["model_id"], row["outcome"]) == ("gate", "none", "accepted")


def test_every_row_carries_the_idempotency_key_and_the_same_record_read_twice_answers_the_same_rows(tmp_path):
    path = write_record(tmp_path, [review_round("fix", 1), call("s1", node="fix", entry=1),
                                   outcome("pass", model="claude-opus-5-5", node="fix", entry=1),
                                   call("s2", node="fix", entry=2),
                                   outcome("pass", model="claude-sonnet-5-5", node="fix", entry=2)])
    once, twice = profiler.from_file(path), profiler.from_file(path)
    assert once == twice
    assert [r["key"] for r in once] == ["run-1:fix:1:1:1", "run-1:fix:1:2:1"]
    assert len({r["key"] for r in once}) == len(once)
    assert all(r["schema"] == profiler.SCHEMA for r in once)


def test_a_record_with_no_chain_answers_unknown_and_is_accepted(tmp_path):
    path = write_record(tmp_path, [call("s1"), outcome("pass", model="claude-opus-5-5")])
    (row,) = profiler.from_file(path)
    assert row["chain_id"] == "unknown"
    profiler.validate(row)


def test_a_row_missing_a_required_column_is_refused_and_the_refusal_names_the_field(tmp_path):
    path = write_record(tmp_path, [call("s1"), outcome("pass", model="claude-opus-5-5")])
    (row,) = profiler.from_file(path)
    for field in ("chain_id", "key", "schema"):
        with pytest.raises(profiler.RowError, match=field):
            profiler.validate({k: v for k, v in row.items() if k != field})


def test_the_winning_model_of_a_phase_is_its_accepted_entry_highest_on_the_record(tmp_path):
    path = write_record(tmp_path, [call("s1"), refused("claude-haiku-5-5"), call("s2"),
                                   outcome("pass", model="claude-sonnet-5-5", entry=1),
                                   call("s3", entry=2), outcome("pass", model="claude-opus-5-5", entry=2),
                                   call("s4", node="code", entry=3), outcome("fail", model="gpt-6-luna", entry=3)])
    rows = profiler.from_file(path)
    win = profiler.winner(rows, "code")
    assert (win["entry_index"], win["model_id"]) == (2, "claude-opus-5-5")
    assert profiler.winner(rows, "fix") is None
