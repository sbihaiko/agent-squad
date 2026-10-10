# SDK live smoke test

Run on 2026-09-30, `claude-agent-sdk` 0.2.161, model `claude-sonnet-5-5`, effort `medium`, CLI login (OAuth). No API key was set.
The owner granted the run in chat: OAuth runs use usage limits, so a full model is fine. A cap of $2 per call was a guard only. Script: `scripts/archive/probe_live_smoke.py`.

## Result 1: spend is measured on the OAuth login

One call, prompt "Reply with exactly: ok", one turn, finished in 1.9 s.

| Field | Value |
|---|---|
| `total_cost_usd` | 0.087272 |
| `num_turns` | 1 |
| Cache creation tokens | 21807 |
| Output tokens | 4 |

- The cost comes from the `ResultMessage`. It is a measured number under OAuth, not a billed amount.
- A fixed prompt prefix of about 22 000 tokens dominates a tiny call. A node's spend minimum must allow for it. The spend rules need a run cap above the sum of those minimums.

## Result 2: a deadline-killed call shows no cost

One call asked for a 90 s foreground sleep. `anyio.fail_after` fired at 12 s. Control returned at 17.1 s, so teardown took about 5 s.

- Six messages arrived and no `ResultMessage` did. No spend figure exists for a killed call.
- The CLI child and the command it started were gone 5 s after. The only live descendant was the probe's own `ps`.
- This confirms the open question in the spend rules: the spend of a deadline-killed call is unknown. The engine must reserve the node cap for it and never count it as zero.

## Not tested

A run past the turn cap, a deadline inside a subagent, and a hard kill of the Python parent.
