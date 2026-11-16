# A proof is bounded at fifteen minutes and holds no dispatch

A local promotion proof never runs past fifteen minutes, and it never holds the loop's dispatch while it runs.

A proof that runs for hours costs more than it buys. It saturates the machine it shares with dispatch, so its own reds are
the load's and not the code's, and it delays every card behind the tip. The owner named the fifteen-minute bound and the
trade: a tip the bound leaves unproven waits for a retry, and the CI tiers and the nightly carry the coverage the local
proof does not reach.

The bound covers the whole attempt, never one step of it. The fast battery, the suite and its retry all spend from the same
fifteen minutes, and the clock never restarts. A bound on one step alone would let an attempt run past the rule it was
written for.

- The bound is `SG_PROMOTE_DEADLINE_SECONDS`, default 900. Past it the suite and every child it started are stopped, TERM
  then KILL, and the tip waits for a retry.
- A defect a check reported before the deadline is a fail. The bound never turns it into a pass.
- A proof holds no dispatch by default: it writes no reserve, and the loop takes its run slots beside the proof.
- An owner who wants the measurement shielded names a reserve in `SG_PROMOTE_RESERVE_SECONDS`. Past it the reserve lifts and
  the proof keeps running beside the loop; the reserve never cancels the proof.
- The bound is the local proof's. A proof read from CI runs no suite here: the fast tier answers for `stg`, and the complete
  tier for `main`, on the runner ([ADR-0173](0173-the-complete-tier-runs-on-the-promotion-request.md)).
- A local proof reads `stg` before a battery starts and when it ends. A tip `stg` already holds runs no battery and writes no
  verdict.
- The nightly keeps the full suite in both modes, heavy included, off the delivery path
  ([ADR-0153](0153-the-full-suite-runs-nightly-in-both-modes.md)).

Guard: `contrib/squad-goal/scripts/test-promote-pass.sh`, `tests/test_adr_index.py`, `tests/test_promote_dead_code.py`.
