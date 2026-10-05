# Subject: the test target for agent-squad

The subject is a plain project at `~/sources/subject`: a billing engine with its own git repository, `CLAUDE.md`, ADRs, and 24 open issues in `issues/`. It has no remote. It carries no test-bed rules, so the squad treats it as any repository.

This directory holds what stays outside the project:

- `acceptance/test_issue_NN.py`: one held-out test file per issue. The harness copies one into the project's `tests/api/` when it runs that issue. The project never holds them ahead of time.
- `issues.json`: issue number, title, blockers, and the acceptance file. `original_id` is the old corpus id, kept for traceability.
- `make_issues.py`: the one-off script that cut the issue files from the old catalog. It drops the answer-key fields (touches, units, independence).

## Provenance

Copied from `../llm-driven-development` at commit `6965a7c`, FS variant only, then flattened, renamed, and stripped of benchmark wiring on 2026-09-30. The code is a fixed fixture. Do not edit it to help the squad pass.

## Consistency of the answer key

The 24 held-out tests were authored one issue at a time against the base tree. Ten assertions demanded more than their own issue text states, in five groups: the fourth element of a tax rate row (16 against 07 and 02); the refusal message's field list (16 against 02 and 07); issue 11's pinned field lists (11 against 02, 03, 05 and 16); the declaring rule's missing-key exception (13 against 06); and placing an over-limit order (24 against 04). Each is re-anchored to the property its issue text states, so the 24 files can pass together on one tree. See [ADR-0119](../../docs/adr/0119-the-subject-answer-key-asserts-what-its-issue-text-states.md).

One clause is read narrowly. Issue 16's criterion fixes the tuple rate row at 2 or 3 elements, and issue 02's and issue 07's Description clauses would add a fourth and a fifth. The arity criterion wins, and `inclusive` and `rounding` travel by the mapping row. ADR-0119 names that choice and its reversal.

A 24-issue run therefore carries an all-pass expectation. A green final gate is the target, and a red one is a defect to investigate. Per-issue grading remains the instrument for tuning. The key is pinned: `tests/test_subject_key.py` holds the hash of every file, so an edit must be a deliberate act in the same commit.

## Baseline

`cd ~/sources/subject && python3 -m pytest -q` passes 323 tests.
