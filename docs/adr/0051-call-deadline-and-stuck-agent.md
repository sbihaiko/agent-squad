# ADR-0051: A call has a deadline, and a stuck agent is found without a caller

## Status

Accepted.

## Context

A workflow makes many agent calls. A call can hang and hold its lock or slot. A caller that waits on its own call cannot see another caller's call. If the only test for a stuck agent is to call it, the test fails on the one agent that refuses calls. A lock handles a dead caller. It does nothing about a live caller that goes nowhere.

## Decision

1. **Every call carries a deadline.** This covers any agent call, including SDK runs. The default is a setting. If the host cannot enforce a deadline, say so out loud. Do not assume it away.
2. **A deadline is its own outcome, distinct from failure.** It adds no exit code. The run verdict folds it into failed, and the reason `deadline` in the record tells a stopped call from a failed one (ADR-0105).
3. **Find a stuck agent without a caller.** A monitor reads the run records and reports a call that runs past the limit. It does not call the agent first.
4. **Stuck and abandoned are different defects.** A live process past the limit is stuck. A record that says running while its process is dead is abandoned: the transport died and the last turn was never recorded.
5. **A finding past the deadline is a second defect.** The monitor uses the same limit as the deadline. A call found running longer than that means the deadline did not fire. Investigate it. Do not retune the limit (ADR-0018).
6. **A run starts its own monitor** on a timer (`watch.monitor`, default on; `watch.monitor_seconds`, default 30, range 5 to 600). It reads only that run's record. It writes each finding once to `monitor.log` in the run folder and to the command's output. The findings are `stuck`, `abandoned`, `deadline_missed` (a call open more than 60 seconds past its limit), `no_progress` (the record has not grown for 300 seconds, the run is not paused and no call is open), `held` (a pause) and `ended` (the verdict; the monitor stops after it). A healthy run writes nothing until `ended`. The monitor never calls a model, never writes to the record and never pauses a run. A failure to start it is a warning, never a verdict.
7. **A call in flight records where it has got to** (ADR-0105 rule 11), so a reader can tell a live call from an abandoned one.

The monitor reads run records, not processes. An agent started by another route leaves no record, and the monitor cannot see it.

## Guard

`tests/test_deadlines.py`, `tests/test_monitor.py`.
