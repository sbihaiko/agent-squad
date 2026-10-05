# Subject runs: issues 02 to 24 (ticket #52)

Run on 2026-09-30 with `uv run scripts/run_subject.py N`. Every node: `claude-sonnet-5-5`, effort `medium`, OAuth login. The runner loads `config/agent-defaults.yaml` and records its hash in `run_start` (commit `d7f7042`). The subject fixture was not written. Held-out acceptance tests decide pass or fail.

Jev is not wired. No Jev score exists. No threshold is set. Ticket #13 gets outcome data only.

## Results

| Issue | Runs | Verdicts | Spend per run (USD) | Note |
|---|---|---|---|---|
| 02 | 1 | completed | 0.31 | |
| 03, 04, 05, 06 | 1 each | completed | 0.28-0.35 | |
| 07 | 2 | completed, completed | 0.28, 0.30 | |
| 08 | 2 | completed, **failed** | 0.20, 0.24 | Failure 1: spec output has no `ac_commands`. |
| 09 | 2 | completed, completed | 0.35, 0.27 | |
| 10 | 2 | completed, completed | 0.26, 0.32 | |
| 11 | 2 | completed, completed | 0.30, 0.30 | |
| 12 | 2 | completed, **failed** | 0.15, 0.17 | Failure 2: held-out test fails. |
| 13 | 2 | **failed**, **failed** | 0.20, 0.21 | Failure 3 (run 1: `ac_commands` misplaced) and failure 2 (run 2). |
| 14 | 1 | completed | 0.30 | Chained run (#54): blocker 8 applied. |
| 15 | 2 | **failed**, completed | 0.04, 0.23 | Failure 4: spec node left no artifact, `loop_max_exhausted`. |
| 16 | 2 | **failed**, completed | 0.25, 0.28 | Failure 2: held-out test fails. |
| 17, 18, 19, 22 | 2 each | completed | 0.18-0.35 | |
| 20 | 1 | completed | 0.30 | Chained run: blocker 19 applied. |
| 21 | 1 | completed | 0.28 | Chained run: blockers 19, 20 applied. |
| 23 | 1 | completed | 0.40 | Chained run: blocker 22 applied. |
| 24 | 1 | **failed** | 0.35 | Chained run: blockers 22, 23 applied. Failure 2: held-out test fails (10 of 19 tests). |

- Measured spend of all runs: 7.96 USD (`total_cost_usd` sums). Under OAuth it counts against usage limits. It is not a billed amount.
- No run logged a compaction event. No run came near a cap.
- Most issues ran twice. A launch error of mine started two batches at once. The two batches ran in separate run directories and did not share state. The second batch's runs of issues 03 to 06 crashed, because I deleted their directories. Those four crashes are harness noise, not model results, and I removed them.
- n is 1 or 2 per issue. This is not a pass rate (ADR-0012).

## Findings (ADR-0018: each is a defect to investigate)

1. **A malformed spec passes the spec node.** In run `subject-08-...191302`, the spec held no `ac_commands`. Only `ac-verify` caught it, as `no_ac_commands`, and it failed the run. The spec node has no output shape check, so its `next: fail: repeat` path never fires.
2. **Held-out tests catch real edge-case misses.** Runs `12-...191401`, `13-...191405` and `16-...191333` passed their own tests and failed the held-out test. Two expected a `TypeError` that the actor did not raise. One expected an exact error message. These are model results.
3. **The spec output nests `ac_commands` two ways.** In run `13-...191312` the model put `ac_commands` beside `spec`, not inside it. The artifact store exposes `spec` as the inner object, so the gate saw none. The instructions ask for the inner form, and the store reads the inner form.
4. **A spec call can end with no artifact.** Run `15-...191327` spent 0.04 USD in 3 turns and left `artifact_missing`. The loop repeated and exhausted its maximum. Cause unverified: the spec reply probably held no JSON fence.

Findings 1, 3 and 4 have one shared fix: check the spec node's output shape in code and repeat the spec on a bad shape. No ticket exists yet for it. I did not change the engine in this ticket.

## Chained runs (ticket #54)

A blocked issue runs in a clone that holds each blocker's accepted merge. `squad/subject.prepare` reads the merge commit from the blocker's own run record (a `completed` verdict and a passing `merge` outcome), applies it in dependency order, and refuses a blocker with no completed run. Runs 14, 20, 23 started first. Runs 21 and 24 started after 20 and 23 completed. Each run wrote `chain.json` with its applied blockers. Total spend 1.63 USD, inside the defaults caps. One run each is no pass-rate claim.

## Not done

- Jev is not wired. A Jev call needs a TypeSafe key (ticket #13).
- The gate list, the diff artifact and the actor sandbox gaps from `subject-run-01.md` still hold.
