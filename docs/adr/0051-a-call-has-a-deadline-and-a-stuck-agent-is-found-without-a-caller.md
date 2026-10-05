# ADR-0051 — A call has a deadline, and a stuck agent is found without a caller

## Status

Accepted, 2026-09-29. Rule 2 rewritten 2026-09-30 by [ADR-0105](0105-a-loop-s-outcome-follows-control-flow-the-verdict-module-folds-the-record-and-a-deadline-adds-no-exit-code.md): a deadline adds no exit code.

## Context

A workflow makes many agent calls. Some calls are SDK runs. Some are scripts. A call can hang. A hung call holds its lock or its slot for as long as it hangs. Later calls to the same agent are then refused or queued behind it.

A caller that waits on its own call knows about that call only. It cannot see another caller's call. If the only test for a stuck agent is to call it, the test fails on the one agent that refuses calls.

A lock handles a dead caller. A lock whose process is gone is stale, and the next call takes it over. A lock does nothing about a caller that is alive and going nowhere.

The critical path does not live in a conversation. Neither does the watching of it (ADR-0011).

## Decision

1. **Every call carries a deadline.** This applies to any agent call in the workflow, including SDK runs. Set a default. Make it a setting. If the host cannot enforce a deadline, say so out loud (ADR-0003). Do not assume it away.
2. **A deadline has its own outcome, distinct from failure.** It is not a refusal and not a crash. It adds no exit code: the run verdict folds a deadline into failed, and the reason `deadline` in the record tells a stopped call from a failed one (ADR-0105). Record the outcome in the run record. A reader can then tell a stopped call from a failed one (ADR-0028, ADR-0029).
3. **Find a stuck agent without a caller.** A monitor reads the run records and reports a call that runs past the limit. It does not call the agent first.
4. **Stuck and abandoned are different defects.** A live process past the limit is stuck. The call will not return on its own. A record that says running while its process is dead is abandoned. The transport died, and the last turn was never recorded.
5. **The monitor runs on a timer and writes to a log.** It does not write to Rafael's chat. Rafael asks for status. The log holds it.
6. **A finding past the deadline is a second defect.** Give the monitor the same limit as the deadline. A call found running longer than that means the deadline did not fire. Investigate it. Do not retune the limit (ADR-0018).

## Consequences

A long call now stops at its deadline. The default number is a guess. It is a setting. A route that needs more time sets its own value. Do not remove the deadline.

The deadline outcome adds one code that other scripts read. A caller that treats any non-zero code as failure still works. It says less.

Rule 3 answers the question "is an agent stuck?" without calling the agent.

The monitor reads run records, not processes. It sees only what the call path writes. An agent started by another route leaves no record. The monitor cannot see it. Name this limit. Do not hide it.

## Guard

Tests that guard this rule:

- `tests/test_deadlines.py::test_a_deadline_gives_the_outcome_deadline_with_no_extra_exit_code`
- `tests/test_deadlines.py::test_a_repeat_after_a_deadline_starts_a_fresh_session`
- `tests/test_deadlines.py::test_the_run_default_deadline_applies_where_a_node_declares_none`
- `tests/test_deadlines.py::test_a_fake_child_is_stopped_at_the_deadline_and_leaves_no_orphan`
- `tests/test_monitor.py::test_a_live_call_past_its_limit_is_one_stuck_finding`
- `tests/test_monitor.py::test_a_dead_process_with_no_outcome_is_abandoned`
- `tests/test_monitor.py::test_a_live_call_inside_its_limit_and_a_finished_call_give_no_finding`
- `tests/test_monitor.py::test_a_sweep_writes_findings_to_a_log_and_nothing_else`
- `tests/test_monitor.py::test_the_engine_writes_a_call_start_before_each_call`

Open: the SDK child pid is not visible, so `pid` is the engine process; a timer runs the monitor from outside (`python -m squad.monitor RUNS LOG 60`) and no scheduler is installed.
