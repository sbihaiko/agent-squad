"""SPEC 1035 (#1036): the profile row contract.

The reader takes a run record that already exists and answers one row per phase per model attempt. A test
states what a reader of the profile must conclude, never how a row is assembled: the fixtures are records on
disk, at the shape `squad/record.py` writes, and the reader is asked through its public door.
"""
import json

import pytest

from squad import profiler, record

START = dict(run_id="run-1", graph_name="g", graph_hash="h", engine_options={}, root="main", plan=["main"])
USAGE = dict(input_tokens=100, output_tokens=20, cache_read_input_tokens=50, cache_creation_input_tokens=5)


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
    for field in ("chain_id", "key", "schema", "tokens_src"):
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


def assistant_line(mid, model="claude-opus-5-5", usage=USAGE):
    return json.dumps({"type": "assistant", "message": {"id": mid, "model": model, "usage": usage}})


def transcript_file(tmp_path, lines, name="session.jsonl"):
    path = tmp_path / name
    path.write_text("".join(f"{line}\n" for line in lines))
    return path


def transcripts_of(*files):
    return lambda node, entry: list(files)


def test_a_phase_with_no_recorded_count_takes_its_tokens_from_the_transcript(tmp_path):
    path = write_record(tmp_path, [call("s1"), outcome("pass", model="claude-opus-5-5")])
    (row,) = profiler.from_file(path, transcripts=transcripts_of(transcript_file(tmp_path, [assistant_line("m1")])))
    assert (row["tokens_in"], row["tokens_out"], row["tokens_cache"]) == (100, 20, 55)
    assert row["tokens_src"] == "transcript"


def test_a_recorded_count_is_kept_and_marked_record_not_transcript(tmp_path):
    path = write_record(tmp_path, [call("s1"), outcome("pass", model="claude-opus-5-5", tokens_in=7, tokens_out=3)])
    (row,) = profiler.from_file(path, transcripts=transcripts_of(transcript_file(tmp_path, [assistant_line("m1")])))
    assert (row["tokens_in"], row["tokens_out"], row["tokens_cache"]) == (7, 3, None)
    assert row["tokens_src"] == "record"


@pytest.mark.parametrize("transcripts", [None, lambda node, entry: None, lambda node, entry: []],
                         ids=["no-adapter", "answers-none", "answers-no-files"])
def test_a_phase_with_no_transcript_answers_missing_and_no_invented_zero(tmp_path, transcripts):
    path = write_record(tmp_path, [call("s1"), outcome("pass", model="claude-opus-5-5")])
    (row,) = profiler.from_file(path, transcripts=transcripts)
    assert row["outcome"] == "accepted"
    assert row["tokens_src"] == "missing"
    assert (row["tokens_in"], row["tokens_out"], row["tokens_cache"]) == (None, None, None)


def test_a_transcript_of_another_model_answers_missing_for_this_row(tmp_path):
    path = write_record(tmp_path, [call("s1"), outcome("pass", model="claude-opus-5-5")])
    f = transcript_file(tmp_path, [assistant_line("m1", model="claude-sonnet-5-5")])
    (row,) = profiler.from_file(path, transcripts=transcripts_of(f))
    assert (row["tokens_src"], row["tokens_in"]) == ("missing", None)


PARTIAL = {
    "truncated-line": lambda tmp: [transcript_file(tmp, [assistant_line("m1"), '{"type":"assistant","message":{"id":"m2","mod'])],
    "unparseable-line": lambda tmp: [transcript_file(tmp, [assistant_line("m1"), "not json at all"])],
    "usage-missing-input": lambda tmp: [transcript_file(tmp, [assistant_line(
        "m1", usage={k: v for k, v in USAGE.items() if k != "input_tokens"})])],
    "usage-missing-output": lambda tmp: [transcript_file(tmp, [assistant_line(
        "m1", usage={k: v for k, v in USAGE.items() if k != "output_tokens"})])],
    "absent-file": lambda tmp: [tmp / "gone.jsonl"],
    "unreadable-file": lambda tmp: [tmp],
}


@pytest.mark.parametrize("kind", sorted(PARTIAL))
def test_a_partial_or_unreadable_transcript_answers_missing_and_never_a_half_count(tmp_path, kind):
    path = write_record(tmp_path, [call("s1"), outcome("pass", model="claude-opus-5-5")])
    (row,) = profiler.from_file(path, transcripts=transcripts_of(*PARTIAL[kind](tmp_path)))
    assert row["tokens_src"] == "missing"
    assert (row["tokens_in"], row["tokens_out"], row["tokens_cache"]) == (None, None, None)


def test_a_message_id_on_two_lines_counts_once_and_a_synthetic_model_counts_nothing(tmp_path):
    path = write_record(tmp_path, [call("s1"), outcome("pass", model="claude-opus-5-5")])
    synthetic = dict(input_tokens=999, output_tokens=999, cache_read_input_tokens=9, cache_creation_input_tokens=9)
    f = transcript_file(tmp_path, [assistant_line("m1"), assistant_line("m1"),
                                   assistant_line("m2", model="<synthetic>", usage=synthetic)])
    (row,) = profiler.from_file(path, transcripts=transcripts_of(f))
    assert (row["tokens_in"], row["tokens_out"], row["tokens_cache"]) == (100, 20, 55)
    assert row["tokens_src"] == "transcript"


def test_a_transcript_of_only_synthetic_lines_answers_missing(tmp_path):
    path = write_record(tmp_path, [call("s1"), outcome("pass", model="claude-opus-5-5")])
    f = transcript_file(tmp_path, [assistant_line("m1", model="<synthetic>")])
    (row,) = profiler.from_file(path, transcripts=transcripts_of(f))
    assert (row["tokens_src"], row["tokens_in"]) == ("missing", None)


def test_a_row_with_an_unknown_token_source_is_refused(tmp_path):
    path = write_record(tmp_path, [call("s1"), outcome("pass", model="claude-opus-5-5")])
    (row,) = profiler.from_file(path)
    with pytest.raises(profiler.RowError, match="tokens_src"):
        profiler.validate({**row, "tokens_src": "guess"})
