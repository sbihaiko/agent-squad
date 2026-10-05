# ADR-0123 — One test call over 20 seconds fails the suite, unless it carries a reason

## Status

Accepted, 2026-10-02. Ticket [#133](https://github.com/mmctech-ai/agent-squad/issues/133).

## Context

Nothing watched suite time. It grew to 141 s serial (39 s with `-n 8`) unnoticed. The slowest single test measured 16.4 s.

## Decision

1. **The slowest 15 tests print on every run** (`--durations=15` in `addopts`).
2. **A test call over `slow_cap_seconds` (20 s, `pyproject.toml`) fails** and the failure names the test (`tests/slowcap.py`).
3. **A test that needs more carries `@pytest.mark.slow(reason="...")`.** A `slow` marker with no reason refuses the run.
4. **The cap is a stop condition, never a target** ([ADR-0012](0012-a-gate-is-a-stop-condition-never-an-optimization-target-verify-the-instrument-first.md)). Nothing tunes a test to sit just under it. A breach is investigated, not re-anchored ([ADR-0018](0018-a-failing-gate-is-a-defect-to-investigate-expectations-are-re-anchored-never-retuned.md)).

## Consequences

Growth in one test shows at once. Growth spread over many tests still shows only in the printed durations and the total.

## Guard

- `tests/test_slow_cap.py::test_a_test_over_the_cap_fails_and_is_named` (the red case: a test that sleeps over the cap)
- `tests/test_slow_cap.py::test_a_test_under_the_cap_passes`
- `tests/test_slow_cap.py::test_a_slow_marker_with_a_reason_lets_a_test_pass`
- `tests/test_slow_cap.py::test_a_slow_marker_with_no_reason_refuses_the_run`
- `tests/test_slow_cap.py::test_the_repo_default_cap_is_20_seconds_and_durations_print`
