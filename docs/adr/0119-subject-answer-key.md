# ADR-0119: The subject's answer key asserts what its issue text states, and the 24-issue set can pass together

## Status

Accepted.

## Context

The held-out tests in `bench/subject/acceptance/` were authored one issue at a time. Their assertions contradict each other: one test pins a field list that a later issue extends, another requires an exception that a later issue replaces, a third places an order that a later issue refuses. A router run over all 24 issues could then never end green, because the final gate re-runs every landed child's command on the final tree.

In each case the issue text states a property, and the held-out assertion demands more: a literal count, a row shape, a construction, an exception class or an attribute that no text names.

One conflict is between texts. Issue 16 states a tuple arity as a criterion. Issues 02 and 07, read literally, ask for extra tuple slots. Issue 16 wins: `inclusive` and `rounding` travel by the mapping row shape only, and the two issue texts say so.

## Decision

1. **Every held-out test asserts the property its issue text states, and no more.** An over-specified assertion is re-anchored to that property. A value derived from the record type's own declared fields is a re-anchor. A literal moved to whatever a run printed is not.
2. **The 24-issue set can pass together.** A `failed` verdict from the router run on this set is a defect to investigate (ADR-0018).
3. **Per-issue grading stays the instrument for tuning.** The 24-issue run measures delivery into one tree.
4. **Issue texts and the fixture project do not change,** except where two texts conflict. A re-anchored test cannot settle a conflict between texts, so the text changes. Issue 16's arity criterion governs.
5. **The key stays pinned.** Any change to a held-out file is a deliberate act, made in the same commit as its pin.
6. **Ordering cannot fix a conflict.** A `blocked_by` edge does not stop a later land from turning an earlier test red.

Rejected: a fixed key with no pass bar (a green run could not tell a good flow from a bad one). Rejected: partitioning the set into groups (it keeps the over-specified assertions). Rejected: re-cutting every issue (it changes the benchmark's input).

## Guard

`tests/test_subject_key.py`.
