# Prompt cache reuse: what the two legs give us today

Date: 2026-10-02. Instrument: `claude-agent-sdk` 0.2.161 through `squad.sdk_adapter`, one call per case.
Probe scripts: `/tmp/probe_cache.py` (one turn, two calls) and `/tmp/probe_cache2.py` (several turns, two calls).
Owner granted probe spend, 2026-09-30. Ticket: #102.

## Question

Does a run reuse the provider's prompt (KV) cache? The cache sets speed and cost, because a cache read costs a
fraction of a cache miss. The question has two legs: Anthropic, reached by the graph's own model, and DeepSeek,
reached through the proxy at `127.0.0.1:8016`.

## How a run drives the model (code facts)

- `call()` opens one `query()` per node and closes it in `finally` (`squad/sdk_adapter.py:117-142`). No
  `ClaudeSDKClient`, no `resume`, no `continue_conversation`, anywhere in `squad/`.
- Every call gets a new session id: "always new: a repeat never resumes a session" (`squad/adapter.py:27`).
- A node's `instructions` join the **system prompt** after the shared contract (`squad/sdk_adapter.py:107-111`).
  The user message holds only a small JSON object (`squad/engine.py:782-785`).
- A child that returns `next: "repeat"` re-enters the node with a new entry and a new session (`engine.py:242-252`).
  The retry pays for the whole prefix again.
- `usage_of` **sums** `cache_creation_input_tokens` and `cache_read_input_tokens` into `tokens_in`
  (`squad/sdk_adapter.py:20-25`). The record keeps no split, so no run can show its own reuse.
- `call_env` sets three variables and no cache control (`squad/defaults.py:43-49`): the spawn depth, the output
  token cap, and the auto-compact window. The default cache TTL applies unchanged (5 minutes).
- The CLI child inherits `ANTHROPIC_BASE_URL` from the shell (`squad/subject.py:21-22`), so the DeepSeek proxy
  serves every subject run.

## Measured

**One-turn probe.** Two calls, identical 12,793-token prefix, one turn each: `cache_read_input_tokens` and
`cache_creation_input_tokens` were **0** on both legs. A single turn has no earlier turn to reuse, so this probe
measures nothing about caching. Kept here as a caution: a one-turn probe cannot answer this question.

**Agentic probe.** Two calls, each several turns, reading three files in this repository. The figure is the
`ResultMessage` aggregate over the call's turns.

| leg | call | turns | `input_tokens` | `cache_read` | `cache_write` | read share |
|---|---|---|---|---|---|---|
| Anthropic, `claude-sonnet-5-5` | 1 | 7 | 271,448 | 510,592 | 0 | 65% |
| Anthropic, `claude-sonnet-5-5` | 2 | 6 | 248,409 | 325,504 | 0 | 57% |
| DeepSeek, `deepseek-flash` | 1 | 4 | 226,224 | 178,816 | 0 | 44% |
| DeepSeek, `deepseek-flash` | 2 | 5 | 394,905 | 174,208 | 0 | 31% |

Read share is `cache_read / (input_tokens + cache_read)`, by the API identity
`total_input = input + cache_creation + cache_read`.

**A real run.** Child `route.router.1` of
`runs/router-01-02-...-24-20261002-124655` recorded `tokens_in 1,180,598`, `tokens_out 31,228`, `turns 32`,
`spend $0.643`. One million input tokens for one child, and the record cannot say how much of it was reuse.

## What this settles

- **Reuse works on both legs.** DeepSeek's automatic disk cache reaches us through the proxy's Anthropic-shaped
  leg. The proxy neither enables nor breaks it: it forwards an unchanged body byte-identical, adds no text, and
  reorders nothing. It has no `cache_control` handling at all.
- **Most input is still paid at full rate.** The DeepSeek leg reused 31% to 44% of its input; the Anthropic leg
  57% to 65%. Each call restarts the conversation, so the first turns of every call build the prefix again.
- **Nothing crosses a node boundary.** Two nodes never share a session, and a node's role text changes the system
  prompt, so the cached prefix of one node is not the cached prefix of the next.
- **The split is invisible in our own record.** `tokens_in` mixes three differently priced buckets.

## External facts that set the levers

Anthropic, from `platform.claude.com/docs/en/build-with-claude/prompt-caching` and `/about-claude/pricing`:

- A 5-minute cache write costs 1.25x base input, a 1-hour write 2x, and a read 0.1x. Caching pays off after one
  read at 5 minutes, or two reads at 1 hour.
- The minimum cacheable prefix for Sonnet 5.5 is 512 tokens. A shorter prompt is not cached and returns no error;
  `cache_creation_input_tokens` and `cache_read_input_tokens` both read 0.
- A change to `tools` invalidates the whole cache. A change to `system` invalidates the system prompt and the
  messages. A change of model or of effort level invalidates it too.

Claude Code, from `code.claude.com/docs/en/prompt-caching` and `/env-vars`:

- The CLI places the breakpoints itself. The caller does nothing.
- TTL is controlled through the environment: `CLAUDE_CODE_PROMPT_CACHE_TTL` (`5m` or `1h`),
  `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`, `ENABLE_PROMPT_CACHING_1H`, and `FORCE_PROMPT_CACHING_5M` to pin 5
  minutes. `DISABLE_PROMPT_CACHING` turns it off. A Claude subscription gives the main conversation a 1-hour TTL;
  an API key gives 5 minutes.
- `ClaudeAgentOptions` has **no** cache field in 0.2.161. `env` is the only lever.
- `system_prompt={"type": "preset", "preset": "claude_code", "exclude_dynamic_sections": True}` strips the
  per-machine dynamic sections (working directory, memory, git status) so identical configurations share one cache
  entry. We do not set it.

DeepSeek, from `api-docs.deepseek.com/guides/kv_cache`, `/quick_start/pricing` and `/guides/anthropic_api`:

- The disk cache is automatic. No flag and no opt-in.
- `cache_control` blocks are listed as **Ignored** on the Anthropic-compatible endpoint. Caching happens anyway.
- For `deepseek-flash`: a cache hit costs $0.006 per 1M input tokens at peak, a miss $0.30. That is a **50x**
  spread, so hit rate is most of the bill on this leg.
- Usage comes back as `prompt_cache_hit_tokens` and `prompt_cache_miss_tokens` on the OpenAI-shaped endpoint, and
  in Anthropic shape on the Anthropic-shaped one.

## Unverified

- Whether a resumed session reads the same cache prefix here. The documentation says it does while the entry lives.
  We have not measured a resume.
- The exact TTL DeepSeek applies. The documentation says "a few hours to a few days" and gives no number.
- Whether our nodes would share a prefix if the role text moved out of the system prompt. Not measured. The CLI
  places the breakpoint, so the answer depends on where it places it.
