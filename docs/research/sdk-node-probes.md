# SDK node probes

Two open facts from the per-node primitives research are now probed.
Run on 2026-09-29, `claude-agent-sdk` 0.2.161, model `haiku`, CLI login (OAuth). No API key was set.
The owner approved the run in chat and on the ratification issue. Script: `scripts/archive/probe_node_deadline.py`.
Cost was not measured. The runs used subscription quota, with `max_budget_usd=0.5` and `max_turns=6` as guards.

## Result 1: a deadline around `query()` stops the CLI child

| Variant | Deadline | Deadline fired | Elapsed | Live descendants 5 s after | Orphan `sleep` after |
|---|---|---|---|---|---|
| `asyncio.wait_for` | 15 s | yes | 20.1 s | none | none |
| `anyio.fail_after` | 15 s | yes | 20.1 s | none | none |

- The turn ran a foreground command that sleeps 90 s. The deadline fired in both variants.
- The CLI child and the command it started were gone in both cases. The plain asyncio timeout did not leave the child behind.
- The deadline fired at 15 s, but control returned at 20.1 s. The teardown took about 5 s. Budget for it.
- `asyncio.wait_for` was run twice with the same outcome. `anyio.fail_after` was run once.

Not tested: a hard kill of the Python parent, a deadline inside a subagent, and other SDK versions.

## Result 2: a background subagent yields a second `ResultMessage` with `origin`

The main turn ended with `main done` and `origin` of `None`. The subagent then produced a second result, `Background agent completed with result: **bg done**`.
The second result carried `origin` with `kind` set to `task-notification` and `producer` set to `session-task`.
A node that spawns a background subagent must tell the two results apart by `origin`. Keep the result whose `origin` is `None` as the node result.

Not tested: several background subagents in one turn, and a subagent that fails.

## Probe pitfall

A first attempt ran `sleep 90` as the command. The turn ended in 9.6 s and no deadline fired. That run proved nothing. The second attempt wrapped the sleep in `python3 -c`, and the deadline fired.
Cause is unverified. A probe must check that its deadline fired before it counts.
