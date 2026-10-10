# Probe: spend and turns for a model outside the CLI's own catalog

Date: 2026-10-02. Instrument: `claude-agent-sdk` 0.2.161 through `squad.sdk_adapter`, one call per case,
model `deepseek-flash`, `ANTHROPIC_BASE_URL=http://127.0.0.1:8016` (the local DeepSeek proxy).
Probe script: `/tmp/probe_ds.py`. Owner granted probe spend, 2026-09-30.

## Question

The proxy serves an Anthropic-shaped stream for a model the CLI does not know. Does the CLI still report
`total_cost_usd` and `num_turns`, and can `max_budget_usd` still fire? A zero would make the spend cap and the
run's `spend_cap` decorative, because the spend rule only refuses a call that reports nothing at all.

## Result

| field | plain call | call with `max_budget_usd=0.000001` |
|---|---|---|
| `subtype` | `success` | `error_max_budget_usd` |
| `is_error` | `False` | `True` |
| `total_cost_usd` | `0.042688` | `0.04276` |
| `num_turns` | `1` | `1` |
| `usage` | 10,662 in / 2 out | all zeros |
| `model_usage['deepseek-flash'].costBasis` | `unknown` | `unknown` |

The call also logged `[claude-code:unrecognized_model]` once per invocation.

## What this settles

- **Spend is reported.** The cap holds. The adapter already maps `error_max_budget_usd` to `spend_cap`
  (`squad/sdk_adapter.py:14`), so a capped node ends through its artifact with the reason, the spend and the
  turns.
- **The figure is not DeepSeek's real price.** `0.042688` for 10,662 input tokens implies **$4.00 per 1M
  input tokens** — the CLI's fallback rate for a model it does not recognize. DeepSeek Flash costs about
  $0.28 per 1M input. The recorded spend is therefore roughly **14x the real spend**. A `max_budget_usd` or
  `limits.max_budget_usd` calibrated for Sonnet fires long before the real budget is spent. Caps stay
  conservative, and a subject run's cost column cannot be read as money.
- **Token counts vanish on the capped path.** The `ResultMessage` of a budget-stopped call carries
  `total_cost_usd` but an all-zero `usage`. `usage_of()` reads `result.usage`, so the node would record
  `tokens_in: 0, tokens_out: 0` for exactly the nodes that hit the cap. `model_usage` still holds the real
  counts on that path, so a fallback there fixes it. Measured on DeepSeek; the path is the same for any model.

## Unverified

- Whether the CLI's fallback rate tracks the model's real price once the CLI knows the id. It does not know
  `deepseek-flash`, so this probe cannot say.
- Whether a subject run's own caps were calibrated against Sonnet figures. They read `limits.max_budget_usd`
  in `config/agent-defaults.yaml`, which is `2.0`.
