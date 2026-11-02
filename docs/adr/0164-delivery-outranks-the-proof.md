# Delivery outranks the proof

A proof exists to make delivery safe, never to replace it. When a proof cannot finish inside its bound, the work still
ships, and the record says what was proven and what was not. The owner names this rule: the delivery is more important
than the proof.

A stopped pipe costs more than an unproven tip. Every hour stg sits behind dev, a landing waits for a promotion that is
measuring a tree nobody ships; the cards pile up, the loop dispatches more work on top of unproven work, and the
difference between "not proven yet" and "not shipped" grows into a backlog that is the real risk. A proof that holds
delivery is a gate read as a target (ADR-0012), which is the defect this decision removes.

## Three outcomes, told apart by their evidence

A gate attempt ends in one of three outcomes, and the evidence is what tells them apart — never the exit code alone:

- **pass**: the suite ran to its summary and named no failing test.
- **fail**: the suite named a failing test, or a check measured a defect.
- **unproven**: the attempt ended without a summary — the bound ended it, a signal stopped it, or the suite never ran.

## Precedence

- **A measured red wins.** A `fail` on a tree holds that tree until the defect is fixed: a bound, a retry, a fallback,
  a later unproven attempt and a pass of another scope never release it. Only a pass that covers the red's scope and reran
  the checks that reported it releases it, and the record names which of the two released it — the green run on the same
  tree, or the change on a descendant that fixed it (ADR-0018).
- **An unfinished proof never holds delivery.** Past its bound (ADR-0163) the delta proof answers for the tip; past what
  the delta can carry, the tip is promoted with a marker that names the unproven part.
- **A failed proof still holds delivery.** This decision never promotes a tip a test named.
- **An `unproven` entry is a job, not a verdict.** It carries an obligation that settles it: the nightly runs the check
  the obligation names, over the tree it names, and writes `settled-green` or `settled-red`. A green settlement credits
  the tree and scope it measured; a red one holds the tip like any other red; an obligation past its deadline is
  reported by name, never dropped.
- **One deadline covers the whole attempt.** The gate, its retry and the delta answer inside one bound, and the delta
  gets a reserved slice of it, so a suite that overruns cannot leave the delta with nothing to prove.

The same reading applies to a landing gate: a gate that cannot finish holds nothing that a bounded proof could answer,
and a gate that measured a red holds everything until the red is fixed.

The ground truth stays off the delivery path. The nightly keeps the full suite in both modes, heavy included
(ADR-0153); it is what turns a marker or an obligation into a fact, and it never gates a landing.

Guard: `contrib/squad-goal/scripts/test-promote-pass.sh`, `tests/test_adr_index.py`.
