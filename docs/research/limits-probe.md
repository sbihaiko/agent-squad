# Probe of the agent-defaults limits

Run 2026-09-30 with `uv run scripts/archive/probe_limits.py`. Raw data: `limits-probe.json`. Model `claude-sonnet-5-5`, CLI 2.1.286 (the SDK that `uv` fetched), OAuth login, scratch directory, `setting_sources=[]`.
Spend was granted for probes (CLAUDE.md). The total was about 5.5 USD by `total_cost_usd`, a client estimate. One run each: these are facts about behavior, not measurements of a rate.

## Findings

| Probe | Result |
|---|---|
| Context window | `CLAUDE_CODE_AUTO_COMPACT_WINDOW` works. Default for Sonnet 5.5: window 1 000 000, auto-compact at 967 000. With 100000: window 100000, auto-compact at 67000. The threshold is the window minus 33 000. |
| Window floor | With 50000 the CLI reported 100000. It clamps up. `context_tokens` below 100000 is refused by `squad.defaults.check`. |
| Output cap | `CLAUDE_CODE_MAX_OUTPUT_TOKENS=300` ends the call. A `ResultMessage` arrives first (success, cost 0.022), then the SDK raises `API Error: Claude's response exceeded the 300 output token maximum`. A cap is a hard stop, not a truncation. |
| Output cap, handled | The adapter keeps the measured spend when that error follows a result and reports `output_cap`. The engine records a fail with reason `node_output_cap`. Any other error after a result stays an adapter error with unknown spend. |
| Grep and Glob | With `tools` unset, the init list has no `Grep` or `Glob` (it has `Bash`, `Read`, `Edit`, `Write`, `Task`, and others). With `tools=["Read","Grep","Glob"]`, or with them in `allowed_tools`, both appear. A node must name them. `squad new-node` does. |
| `dontAsk` | Viable for the actor. With `allowed_tools` of Read, Edit, Write, Grep, Glob: the edit worked, no denial. `echo hi` through Bash ran too, although Bash was not listed. Unverified: whether a write-type Bash command is denied. |
| Real compaction | Window 100000, five 14 000-word files read one at a time. Auto-compact fired twice: two `compact_boundary` system messages and two PreCompact hook calls, both with trigger `auto`. The call then hit its 2.0 USD budget (`error_max_budget_usd`, cost 2.0017, 6 turns). Compaction is costly: each one re-reads the window. |
| Subagent spend | One foreground `Agent` request made two `Agent` calls. The single `ResultMessage` held `total_cost_usd` 2.196 against a `max_budget_usd` of 1.0, and `model_usage` held one model row with that total. So subagent spend is inside the total, and the budget check passed it by 2.2 times. |

## Rulings that follow

- **The subagent ban stands.** The panel said to reopen if a probe forces foreground subagents and measures their spend inside `total_cost_usd`. It is inside, but a trivial task cost 2.2 times its cap. No reopening.
- **`context_tokens` 200000 is valid.** Auto-compact would fire near 167 000 (window minus 33 000, by the 100000 case). Confirmed by the compaction probe: the hook fires on each auto-compact.
- **`max_output_tokens` 32000 is valid.** One essay (4 171 output tokens) passed under it.

## Not verified

- The hook's record event in a full engine run. The probe used a bare hook; the adapter's hook is tested with a stub.
- `Bash` write commands under `dontAsk`.
- Whether the CLI default of 1 000 000 windows applies to other models.
