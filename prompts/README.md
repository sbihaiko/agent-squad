# Agent system prompts

One system prompt per agent node of `docs/design/five-phases.graph.json` and `router.graph.json`. The engine sends `_contract.md` first, then the node's file. `audit.md` serves `audit_a`, `audit_b`, and `audit_c`. The user message is the JSON the engine builds (`node`, `entry`, `inputs`).

## Rules taken from `docs/research/claude-api-skill-map.md`

| Rule | Why | Map section |
|---|---|---|
| The output contract asks for one JSON object as the final message. No prefill, no forced tool choice. | A last-assistant prefill and forced `tool_choice` return 400 on the newest models. Code validates the JSON (ADR-0102 rule 8). | 5 |
| Fixed text comes first, per-call data comes last. | Caching matches a prefix. The engine puts the variable JSON in the user message. | 5 |
| Prompts stay short. | A fixed prefix of about 22 000 tokens already dominated a one-turn call (`sdk-live-smoke.md`). | 5 |
| No "think step by step" line. | Adaptive thinking is the default. Effort is a node setting (`effort`), not prose. | 5 |
| Each role states what it may not change. | Tool surface and workspace enforce it. The prompt says the same, so the model does not plan around a denied call. | 6 |

## Not verified

- `/claude-api prompt-audit` ran once on 2026-09-30, target Sonnet 5.5. Two medium findings are proposed, not applied (see the ticket #45 comment).
- The Agent SDK has its own `system_prompt` option. I did not check how it combines with a skill or `CLAUDE.md`.
- No prompt has run against a model. Quality is unmeasured.

## How a prompt reaches a call

- `squad new-node ROLE` copies the role file into a node's `instructions` and the node-level defaults beside it (`squad/scaffold.py`). The graph hash covers the text (ADR-0114 rule 1).
- The SDK adapter appends `_contract.md` (from `config/agent-defaults.yaml`) and then the node's `instructions` to the `claude_code` preset system prompt. The user message is only the JSON.
- The prompt audit findings 1 and 2 are applied. Finding 3 stays until a run shows the SDK exposes structured output. Finding 4 stays until a run shows the model skips tests.
- Not applied: a read-only acceptance path that code enforces (ticket #45 comment, finding 2).
