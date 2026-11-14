# A proof is bounded at fifteen minutes, and the delta answers past it

A promotion proof never runs past fifteen minutes. When the full suite would take longer, the delta proof answers instead:
it names the change the tip carries and proves that, and it is sized to fit the same bound. What the delta does not cover
is proven hot — by the nightly and by the running system — rather than held back by a proof nobody finishes.

A proof that runs for hours costs more than it buys. It saturates the machine it shares with dispatch, so its own reds are
the load's and not the code's; it delays every card behind the tip; and when it is killed it leaves the tip untested, which
is exactly the state the rule was written to avoid. The owner named the fifteen-minute bound, and named the trade: be
optimistic about the part no test reached, and find it in production.

Only a tip the delta cannot carry at all stays indeterminate: it is promoted with a marker naming what no test reached, and a later pass proves it ([ADR-0164](0164-delivery-outranks-the-proof.md)).

The bound covers the whole attempt, never one step of it. The delta proof, the fallback and the promotion all spend from
the same fifteen minutes: the suite is bounded at what the attempt has left once the fallback keeps its share, and the
fallback at what is left then. A bound on the suite alone would let an attempt run the suite to its bound and only then
start the fallback, so the attempt could run past the rule it was written for.

- The bound is `SG_PROMOTE_BOUND_SECONDS`, default 900; the fallback's share of it is `SG_PROMOTE_FALLBACK_SECONDS`, default 240.
- Past its share the suite and every child it started are killed, and `delta-pass.sh` answers for the tip under what is left of the bound.
- A delta pass promotes with a marker that says the full suite was killed by the bound, never that it was green.
- The nightly keeps the full suite in both modes, heavy included, off the delivery path (ADR-0153).
- The bound is the local proof's. A proof read from CI runs no suite here: the fast tier answers for `stg`, and the complete tier for `main`, on the runner ([ADR-0173](0173-the-complete-tier-runs-on-the-promotion-request.md)).
- A local proof reads `stg` before a battery starts and when it ends. A tip `stg` already holds runs no battery and writes no verdict.

Guard: `contrib/squad-goal/scripts/test-promote-pass.sh`, `tests/test_adr_index.py`.
