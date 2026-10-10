# ADR-0018: A failing gate is a defect to investigate; expectations are re-anchored, never retuned

## Status

Accepted.

## Context

A rule with no guard becomes fiction within days. A red guard also tempts two fixes that look the same from outside: one repairs the defect, the other edits the expectation until the guard goes green. Only investigation tells them apart.

## Decision

1. **Every rule names its guard.** A guard is a test, a script check, or a named follow-up with an owner. A rule with no named guard is a draft, not a decision.
2. **A fix counts only after a real run exercised it.** A tested correction that no real run has used is unproven. Close the follow-up with evidence from a real run, never with the fix's own test suite.
3. **No red guard is "not mine", skipped, or made green by editing the expectation.** Investigate each one to a named root cause first.
4. **Edit an expectation only when all four conditions hold.**
   - (a) The referent is named: the artifact the values are true about.
   - (b) The delta traces to identified committed changes to that referent.
   - (c) A generator re-derives the values, and the generator is independently known not to have changed.
   - (d) Every anchor that stays true about a frozen artifact is preserved.

   Overwriting a true anchor so today's input passes is gaming the guard.
5. **A pinned expectation names its referent's mutability.** A pin against a frozen artifact never drifts. A pin against generated state moves with its source and is re-verified in the same commit. Prefer two anchors, one frozen and one current.
6. **A missing fixture is a failure, not a skip.**
7. **Never change the producing code to emit the expected value.** That fabricates the defect the guard should catch.

A delta with no committed change behind it is a defect, not a pin refresh.

## Guard

The full suite, run in both modes.
