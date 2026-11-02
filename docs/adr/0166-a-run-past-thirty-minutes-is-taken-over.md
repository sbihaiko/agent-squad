# A run past thirty minutes is taken over, never requeued

A run that passes thirty minutes with no verdict is ended by the loop and handed to a person or an agent. Its issues stay
claimed. The loop never puts them back in the queue, so no fresh child starts the same work again.

A requeued run throws away its context. The child that ran for half an hour read the issue, the code and its own failed
attempts; a fresh child starts from none of that, and often runs into the same wall. Each requeue spends a run slot and
model spend on work the loop already did once, and the loop goes in circles while the card shows activity. Delivery is the
goal, and a run that cannot finish inside the bound is a delivery problem that a takeover solves faster than a restart.

The cut is hard. A run at thirty-one minutes is ended even when its session is alive, and even when its wave entry gate is
running and would pass in five minutes. The bound has no grace window, because a condition on the run's state is a way to
keep a run past the bound for ever.

The claim must be visible. A run that left active-runs with its dispatch marker still set looks like a stale claim, and
the cleanup rules release a stale claim. The takeover marker names the run and its issues, so the owner sees which issues
a hand fix owes, and the loop holds them while the marker stands.

## Decision

1. A run with no verdict whose start is more than `SG_TAKEOVER_SECONDS` ago (default 1800) is taken over by the janitor (rule T). No other condition applies: not the session, not the idle time, not a running gate.
2. The janitor writes the killed verdict into the run's record, kills every process inside the run folder and the run's dashboard, and drops the run from active-runs.
3. The janitor never frees `auto/disp-<n>` or `auto/bug-<n>` of a taken-over run, so its card stays where the run left it and its work branch stays as the child left it.
4. The janitor writes `auto/taken-<run>`, which names the run id and its issues, and appends one `TAKEN-OVER` line to `alerts.log`.
5. While `auto/taken-<run>` stands, its issues are held: no cleanup rule puts their cards back in Todo or drops their dispatch markers.
6. Whoever takes over finishes the work with a multi-agent workflow, and clears `auto/taken-<run>` when the last issue of that run is closed.

## Consequences

- A run that would have finished in thirty-five minutes is ended. The owner accepts that cost; the takeover starts from the run's branch, so the work is not lost.
- Rule C still reaps a zombie run that dies inside the bound, and frees its markers: a run that dies early has little context to lose.
- A taken-over issue does not move until the marker is cleared. A marker nobody clears holds its issues for good, and `alerts.log` is where the owner finds it.
- A test fixture that builds a live run with an old start stamp is a taken-over run. A suite that tests another rule sets `SG_TAKEOVER_SECONDS` high, or starts its run now.

Guard: `tests/test_janitor_takeover.py`, `tests/test_adr_index.py`.
