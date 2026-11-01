# Delivery outranks the proof

A proof exists to make delivery safe, never to replace it. When a proof cannot finish inside its bound, the work still
ships, and the record says what was proven and what was not. The owner named the rule (2026-11-01): the delivery is more
important than the proof.

A stopped pipe costs more than an unproven tip. Every hour stg sits behind dev, a landing waits for a promotion that is
measuring a tree nobody ships; the cards pile up, the loop dispatches more work on top of unproven work, and the
difference between "not proven yet" and "not shipped" grows into a backlog that is the real risk. A proof that holds
delivery is a gate read as a target (ADR-0012), which is the defect this decision removes.

The line is between an unfinished proof and a failed one:

- **An unfinished proof never holds delivery.** Past its bound (ADR-0163) the delta proof answers for the tip; past what
  the delta can carry, the tip is promoted with a marker that names the unproven part, and a later pass proves it.
- **A failed proof still holds delivery.** A red the proof actually measured is a defect (ADR-0018) and is fixed; this
  decision never promotes a tip a test named.
- **The record carries the difference.** A promotion writes whether the full suite passed, whether the delta carried it,
  or whether the bound ended the attempt. A reader never has to guess which of the three they are looking at.
- **The ground truth stays off the delivery path.** The nightly keeps the full suite in both modes, heavy included
  (ADR-0153); it is what turns a marker into a fact, and it never gates a landing.

The same reading applies to a landing gate: a gate that cannot finish holds nothing that a bounded proof could answer,
and a gate that measured a red holds everything until the red is fixed.

Guard: `contrib/squad-goal/scripts/test-promote-pass.sh`, `tests/test_adr_index.py`.
