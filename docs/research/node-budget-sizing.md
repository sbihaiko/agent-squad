# Node budget sizing: assume an uncached prefix

Ticket: #150. Source: a reporter's run through a gateway (plugin 0.1.3).

## Question

Does the gateway return `cache_read` for a plugin-qualified model such as `claude-deepseek-v4-flash`?

## Answer

**Unknown for the reporter's gateway (`claude-deepseek-v4-flash`). The local DeepSeek proxy caches in a real run; the probe disagrees.**

Probe run 2026-10-06 against the local proxy (`~/.deepseek-in-claude`, `http://127.0.0.1:8016`), through the SDK adapter,
same 12 000-token prompt sent twice per model: `claude-deepseek-v4-pro`, `claude-deepseek-flash` and
`claude-deepseek-v4-pro[1m]` each returned `cache_read: 0` (verdict `no_cache`). The probe cannot be trusted alone.

A real router run on the same proxy (run `run-20261006-120327-e97213`, dynamic seed with the router on `claude-deepseek-v4-pro`)
returned `cache_read: 17 408` for the router (2 turns, $0.25, artifact `router.1.json`) and `cache_read: 8 064` for its
`claude-deepseek-flash` child. So the proxy returns cache reads for plugin-qualified DeepSeek ids. The probe's fixed prompt,
sent as a user message in a fresh session, does not hit the cache. The reporter's `claude-deepseek-v4-flash` has not been probed.

The reporter measured `cache_read: 0` on `claude-grok-4.6` and
`cache_read: 593 581` on `opus` through the same SDK and call shape. That shows the gateway returned no cache read for
that model. It does not show whether the gateway cannot cache or the SDK sent no breakpoints.

## Rule until the probe answers

- Every node budget assumes an **uncached prefix**. A node re-sends its whole system prompt on every turn
  (about 16 000 tokens for the router) and pays full price for each turn.
- Size the node's `spend.max` as turns times the full prefix price, not as turns times a cache-read price.
- Size the run's `spend_cap` as the sum of those node caps. A cached model then comes in under it.
- A swap of model does not cut the cost. The reporter measured three models within 11% of each other.
- The router carries a stopping rule (`prompts/router.md`), because on an uncached model a long survey costs the whole run.

## How to answer it

Run `ANTHROPIC_BASE_URL=<gateway> uv run scripts/probe_gateway_cache.py <model> [model ...]`. A `caches` verdict means the
second call reported `cache_read` above zero. Then replace the answer above and relax the rule for that model.
Guard: `tests/test_probe_gateway_cache.py` (the verdict rule) and `tests/test_dispatch.py` (the router stopping rule).

## Not met

Acceptance 1 of #150 (a completed router run on the reporter's setup) and the probe of `claude-deepseek-v4-flash` need the reporter's gateway.
A completed router run on the local DeepSeek proxy (above) is not the reporter's setup, so it does not close acceptance 1.
