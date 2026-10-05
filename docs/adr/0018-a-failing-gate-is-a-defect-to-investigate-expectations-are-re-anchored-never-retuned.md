# ADR-0018 — A failing gate is a defect to investigate; expectations are re-anchored, never retuned

## Status

Accepted, 2026-09-29.

## Context

A rule with no guard becomes fiction within days. A claimed guard may point to a test that does not exist. A promised guard may stay silent against every real input. The honest format lists what is frozen and declares the rest as debt.

Once a guard exists, a new failure mode appears. A red guard tempts two fixes that look identical from outside. One repairs the defect. The other edits the expectation until the guard goes green. Only investigation tells them apart.

## Decision

1. **Every rule names its guard.** A guard is a test, a script check, or a named follow-up with a date and an owner. A rule with no named guard is a draft, not a decision.
2. **A fix counts only after a real run exercised it.** A committed and tested correction that no real run has used is unproven. Each fix carries a follow-up item, "first real run after the fix". Close it with a date and evidence, never with the fix's own test suite.
3. **No red guard is "not mine", skipped, or made green by editing the expectation.** Investigate every red guard to a named root cause before any expectation changes.
4. **Edit an expectation only when all four conditions hold.**
   - (a) The referent is named. State the artifact the values are true about.
   - (b) The delta traces to identified committed changes to that referent.
   - (c) A generator re-derives the values. The generator code is independently known not to have changed.
   - (d) Every anchor that stays true about a frozen artifact is preserved, not overwritten.

   Overwriting a true anchor so today's input passes is gaming the guard.
5. **A pinned expectation names its referent's mutability.** A pin against a frozen artifact never legitimately drifts. A pin against a generated state file moves whenever its source moves. Re-verify it in the same commit that moved it. Prefer two anchors, one frozen and one current, over one ambiguous pin.
6. **A missing fixture is a failure, not a skip.** A guard that passes because its input is absent is correct but unreachable. That is the silent hole the guard exists to close.
7. **Never change the producing code to emit the expected value.** Editing the generator or wrapper so the guard turns green is the worse way to fake a pass. It leaves the expectation untouched and fabricates the defect the guard should catch.

## Consequences

Guards stay trustworthy. Nobody erases evidence to manufacture green. Drift is diagnosable in one read, because each pin declares what it is true about.

Cost: a pin against generated state carries a same-commit obligation. Every new rule costs a guard before it counts. Guard on the guard: a delta with no committed change behind it is a defect, not a pin refresh.

## Guard

Guard: none yet. This rule is a draft until a named guard test exists (ADR-0018).
