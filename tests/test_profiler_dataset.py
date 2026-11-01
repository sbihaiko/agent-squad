"""SPEC 1035 (#1038): the profile dataset, one append-only file per ticket.

A test states what a reader of a ticket's file must conclude. The rows are the profile row contract's rows
(`squad/profiler.py`), built with its own key; the file is read through the dataset's public door.
"""
import json
import os

import pytest

from squad import profile_dataset, profiler

ISSUE = "sbihaiko/agent-squad#7"
OTHER_ISSUE = "sbihaiko/agent-squad#8"
EMPTY_ISSUE = "sbihaiko/agent-squad#9"
SUBSCRIPTION_MODEL = "claude:claude-opus-5-5"
API_KEY_MODEL = "deepseek-flash"


def make_row(run_id="run-1", phase="code", review_round=0, entry_index=1, attempt=1, issue=ISSUE,
             outcome="accepted", tokens_in=10, tokens_out=5, tokens_cache=None, tokens_src="record",
             chain_id="chain-a", model_id="model-a"):
    row = dict(schema=profiler.SCHEMA, issue=issue, run_id=run_id, phase=phase, review_round=review_round,
               chain_id=chain_id, entry_index=entry_index, model_id=model_id, provider="provider-a",
               attempt=attempt, outcome=outcome, tokens_in=tokens_in, tokens_out=tokens_out,
               tokens_cache=tokens_cache, tokens_src=tokens_src, turns=2)
    row["key"] = profiler.key(row)
    return row


def test_first_append_creates_the_ticket_file_with_exactly_that_line(tmp_path):
    row = make_row()

    assert profile_dataset.append_row(tmp_path, row) is True

    lines = profile_dataset.path(tmp_path, ISSUE).read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == row


def test_appending_the_same_row_twice_keeps_one_line_and_changes_nothing(tmp_path):
    row = make_row()
    profile_dataset.append_row(tmp_path, row)
    before = profile_dataset.path(tmp_path, ISSUE).read_bytes()

    assert profile_dataset.append_row(tmp_path, row) is False

    assert profile_dataset.path(tmp_path, ISSUE).read_bytes() == before
    assert profile_dataset.read(tmp_path, ISSUE) == [row]


def test_a_second_row_with_the_same_key_is_a_duplicate_even_when_its_counts_differ(tmp_path):
    first = make_row(tokens_in=10)
    again = make_row(tokens_in=999)
    profile_dataset.append_row(tmp_path, first)
    before = profile_dataset.path(tmp_path, ISSUE).read_bytes()

    assert profile_dataset.append_row(tmp_path, again) is False

    assert profile_dataset.path(tmp_path, ISSUE).read_bytes() == before
    assert profile_dataset.read(tmp_path, ISSUE) == [first]


@pytest.mark.parametrize("module_call", ["fsync", "replace"])
def test_an_interrupted_write_leaves_the_file_whole_lines_only(tmp_path, monkeypatch, module_call):
    profile_dataset.append_row(tmp_path, make_row(entry_index=1))
    before = profile_dataset.path(tmp_path, ISSUE).read_bytes()

    def killed(*args, **kwargs):
        raise RuntimeError("killed mid-write")

    monkeypatch.setattr(profile_dataset.os, module_call, killed)
    with pytest.raises(RuntimeError):
        profile_dataset.append_row(tmp_path, make_row(entry_index=2))
    monkeypatch.undo()

    after = profile_dataset.path(tmp_path, ISSUE).read_bytes()
    assert after == before
    assert after.endswith(b"\n")
    assert [json.loads(line) for line in after.splitlines()] == profile_dataset.read(tmp_path, ISSUE)
    assert sorted(p.name for p in tmp_path.iterdir()) == [profile_dataset.path(tmp_path, ISSUE).name]


def test_two_runs_on_one_ticket_keep_both_runs_rows_in_write_order(tmp_path):
    run_one = [make_row(run_id="run-1", entry_index=1), make_row(run_id="run-1", phase="review", entry_index=1)]
    run_two = [make_row(run_id="run-2", entry_index=1), make_row(run_id="run-2", phase="gate", entry_index=2)]
    for row in run_one:
        profile_dataset.append_row(tmp_path, row)
    first_run_bytes = profile_dataset.path(tmp_path, ISSUE).read_bytes()

    for row in run_two:
        profile_dataset.append_row(tmp_path, row)

    assert profile_dataset.read(tmp_path, ISSUE) == run_one + run_two
    assert profile_dataset.path(tmp_path, ISSUE).read_bytes().startswith(first_run_bytes)


def test_rows_of_different_tickets_go_to_different_files(tmp_path):
    mine = make_row()
    other = make_row(issue=OTHER_ISSUE)

    profile_dataset.append_row(tmp_path, mine)
    profile_dataset.append_row(tmp_path, other)

    assert profile_dataset.path(tmp_path, ISSUE) != profile_dataset.path(tmp_path, OTHER_ISSUE)
    assert profile_dataset.read(tmp_path, ISSUE) == [mine]
    assert profile_dataset.read(tmp_path, OTHER_ISSUE) == [other]


def test_a_row_the_contract_refuses_is_not_written(tmp_path):
    bad = make_row()
    bad["key"] = "not-the-key"

    with pytest.raises(profiler.RowError):
        profile_dataset.append_row(tmp_path, bad)

    assert not profile_dataset.path(tmp_path, ISSUE).exists()
    assert not os.listdir(tmp_path)


def test_the_chain_summary_groups_by_chain_and_phase_and_keeps_billing_apart(tmp_path):
    for row in [
        make_row(entry_index=1, model_id=SUBSCRIPTION_MODEL),
        make_row(entry_index=2, model_id=API_KEY_MODEL),
        make_row(entry_index=3, model_id="no-such-model"),
        make_row(entry_index=4, phase="gate", model_id="none"),
        make_row(entry_index=5, phase="review", model_id=SUBSCRIPTION_MODEL),
        make_row(entry_index=6, chain_id="chain-b", model_id=SUBSCRIPTION_MODEL),
    ]:
        profile_dataset.append_row(tmp_path, row)

    summary = profile_dataset.chain_summary(tmp_path, [ISSUE])

    assert [(g["chain_id"], g["phase"], g["billing"], g["attempts"]) for g in summary] == [
        ("chain-a", "code", "api_key", 1),
        ("chain-a", "code", "subscription", 1),
        ("chain-a", "code", "unknown", 1),
        ("chain-a", "gate", "none", 1),
        ("chain-a", "review", "subscription", 1),
        ("chain-b", "code", "subscription", 1),
    ]


def test_a_chain_that_always_needs_a_second_attempt_reads_worse_on_first_try_whatever_its_tokens(tmp_path):
    for entry in (1, 2):
        for row in [
            make_row(run_id="run-slow", entry_index=entry, attempt=1, outcome="refused", tokens_in=None,
                     tokens_out=None, tokens_src="missing", chain_id="chain-slow", model_id=SUBSCRIPTION_MODEL),
            make_row(run_id="run-slow", entry_index=entry, attempt=2, tokens_in=1, tokens_out=1,
                     chain_id="chain-slow", model_id=SUBSCRIPTION_MODEL),
            make_row(run_id="run-fast", entry_index=entry, tokens_in=100000, tokens_out=100000,
                     chain_id="chain-fast", model_id=SUBSCRIPTION_MODEL),
        ]:
            profile_dataset.append_row(tmp_path, row)

    groups = {g["chain_id"]: g for g in profile_dataset.chain_summary(tmp_path, [ISSUE])}

    assert (groups["chain-slow"]["attempts"], groups["chain-slow"]["phase_runs"]) == (4, 2)
    assert groups["chain-slow"]["first_try_rate"] == 0.0
    assert groups["chain-fast"]["first_try_rate"] == 1.0
    assert groups["chain-slow"]["tokens_in"] < groups["chain-fast"]["tokens_in"]


def test_a_ticket_with_no_rows_contributes_nothing_and_is_not_counted_as_a_zero(tmp_path):
    profile_dataset.append_row(tmp_path, make_row(model_id=SUBSCRIPTION_MODEL, tokens_in=10))
    alone = profile_dataset.chain_summary(tmp_path, [ISSUE])

    assert profile_dataset.chain_summary(tmp_path, [EMPTY_ISSUE]) == []
    assert profile_dataset.chain_summary(tmp_path, [ISSUE, EMPTY_ISSUE]) == alone


def test_a_row_with_no_token_count_adds_no_zero_to_the_sums(tmp_path):
    profile_dataset.append_row(tmp_path, make_row(entry_index=1, model_id=SUBSCRIPTION_MODEL, tokens_in=None,
                                                  tokens_out=None, tokens_src="missing"))
    (only,) = profile_dataset.chain_summary(tmp_path, [ISSUE])
    assert (only["tokens_in"], only["tokens_out"], only["tokens_cache"], only["tokens_missing"]) == (None, None, None, 1)

    profile_dataset.append_row(tmp_path, make_row(entry_index=2, model_id=SUBSCRIPTION_MODEL, tokens_in=7,
                                                  tokens_out=3, tokens_src="record"))
    (both,) = profile_dataset.chain_summary(tmp_path, [ISSUE])
    assert (both["attempts"], both["tokens_in"], both["tokens_out"], both["tokens_missing"]) == (2, 7, 3, 1)
