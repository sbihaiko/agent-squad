# `/claude-api` feature map

This map lists what the bundled `/claude-api` skill covers, how to call it, and where each feature is documented.
Source: the skill body and its file tree, Claude Code 2.1.285. Model data is cached 2026-09-25 in the skill.
Paths such as `shared/...` are relative to the skill's base directory. The base directory changes per Claude Code version.

The skill builds apps on the Claude API. It does not build Claude Agent SDK code.
For the Agent SDK, see [claude-agent-sdk.md](claude-agent-sdk.md) and [agent-sdk-command-map.md](agent-sdk-command-map.md).

## 1. How to call it

Call `/claude-api <prose>` for a normal request, or `/claude-api <subcommand>` for a fixed flow.
A bare subcommand string runs its flow. Any prose is a normal request.

| Subcommand | Job | Reads first |
| --- | --- | --- |
| `migrate` | Move code to a newer model. Confirm scope before any edit. | `shared/model-migration.md` |
| `prompt-audit` | Find dated prompt patterns in prompts, tool text, skills, `CLAUDE.md`. Report plus diff, no edits unless asked. | `shared/prompt-audit.md` |
| `upgrade` | Move the Python SDK from 0.x to 1.x. Python only. | `python/claude-api/sdk-upgrade.md` |
| `cost-optimize` | Cut API spend. Profile first, free wins before tradeoffs. Model runs need approval. | `shared/cost-optimization.md` |
| `build-eval` | Interview, then build an eval set with a runnable script and measured cost. | `shared/evals/build-eval.md` |
| `hillclimb` | Improve an app against an eval, with a train, validation, and test split. | `shared/evals/eval-hillclimb.md` |
| `preserved-thinking-migration` | Make a harness pass the thinking-history check. Replays spend money. | `shared/preserved-thinking-migration.md` |
| `managed-agents-onboard` | Interview to set up a Managed Agent. | `shared/managed-agents-onboarding.md` |

## 2. Guards the skill applies to every request

- Stop if the file uses another provider (`openai`, `langchain_openai`, `gpt-*`). Ask before switching to Claude. `prompt-audit` records it and never switches.
- Use the official SDK. Use raw HTTP only on request, in a shell project, or where no SDK exists. Never mix the two.
- Never guess an SDK name. Read `{lang}/` files or fetch the SDK repo from `shared/live-sources.md`.
- Default model: `claude-opus-5-5`. Change it only when the user names another. Never add date suffixes.
- Default thinking: adaptive. Default to streaming for long input, long output, or high `max_tokens`.
- Default `max_tokens`: about 16000 non-streaming, about 64000 streaming.
- On Fable 5.1, Opus 5.5, Opus 5, and Sonnet 5.5, include the server-side `fallbacks` parameter and tell the user.

## 3. Language routing

| Project signal | Docs folder |
| --- | --- |
| `.py`, `pyproject.toml` | `python/` |
| `.ts`, `.js`, `package.json` | `typescript/` |
| `.java`, `.kt`, `.scala` | `java/` |
| `.go` | `go/` |
| `.rb` | `ruby/` |
| `.cs` | `csharp/` |
| `.php` | `php/` |
| Shell, or an unsupported language | `curl/` |

Each `{lang}/claude-api/` folder holds `README.md`, `tool-use.md`, `streaming.md`, and `files-api.md`.
`batches.md` exists for Python, TypeScript, C#, and PHP. Go, Java, and Ruby do not have it.
`{lang}/managed-agents/README.md` exists for Python, TypeScript, Go, Java, Ruby, and PHP. C# uses `csharp/claude-api/README.md`.

## 4. Which surface to use

| Need | Surface |
| --- | --- |
| One call: classify, summarize, extract, Q&A | Claude API |
| Bulk work at 50% cost, no latency need | Message Batches |
| Custom tools, you own the loop | Claude API with tool use (manual loop or Tool Runner) |
| Anthropic runs the loop and a sandbox | Managed Agents (beta) |
| Scheduled agent | Managed Agents, scheduled deployments |
| Work that must pass a quality bar | Managed Agents, outcomes |
| Coding agent with built-in file tools, on your host | Claude Agent SDK (not covered by this skill) |

Four agent approaches:

| # | Approach | Harness | Deployment |
| --- | --- | --- | --- |
| 1 | Manual loop | You | You |
| 2 | Tool Runner (`client.beta.messages.tool_runner`) | SDK | You |
| 3 | Managed Agents | Anthropic | Anthropic |
| 4 | Claude Agent SDK | SDK | You |

Check four things before you build an agent: complexity, value, viability, and cost of error.

## 5. Model features

Current models (cached): Fable 5.2, Fable 5.1, Mythos 5.1, Fable 5, Opus 5.5, Opus 5, Opus 4.8, 4.7, 4.6, Sonnet 5.5, Sonnet 5, Sonnet 4.6, Haiku 4.5.
Live lookup: `client.models.list()` and `client.models.retrieve(id)`. Field reference: `shared/models.md`.

| Feature | Note | Detail file |
| --- | --- | --- |
| Adaptive thinking | Default on 4.6 and later. `budget_tokens` is removed on the newest models (400). | `shared/model-migration.md` |
| Thinking display | `omitted` by default. Set `summarized` to show reasoning. `updates` shows progress notes. | `shared/model-migration.md` |
| Effort | `output_config.effort`: `low` to `max`. Opus 5.5 defaults to `medium`. | `shared/cost-optimization.md` |
| Per-message effort | Beta `mid-conversation-output-config-2026-07-01`. | `shared/model-migration.md` |
| Fast mode | Beta `fast-mode-2026-02-01`, `speed: "fast"`. Opus 5, 5.5, 4.8. Claude API only. | skill body |
| Task budgets | Beta `task-budgets-2026-03-13`. Token ceiling for an agent loop. | `shared/model-migration.md` |
| Refusal handling | `stop_reason: "refusal"` and `stop_details`. Server-side `fallbacks`. | `shared/model-migration.md` |
| Compaction | Beta `compact-2026-01-12`. Summarizes old context. | `{lang}/claude-api/README.md` |
| Context editing | Beta `context-management-2025-06-27`. Clears old tool results or thinking. | skill body |
| Prompt caching | Prefix match. Verify with `usage.cache_read_input_tokens`. | `shared/prompt-caching.md` |
| Mid-conversation system messages | `{"role":"system"}` in `messages`. Keeps the cache. | `shared/prompt-caching.md` |
| Structured outputs | `output_config.format`, `messages.parse()`. `output_format` is deprecated. | `{lang}/claude-api/tool-use.md` |
| Token counting | `messages.count_tokens`. Do not use `tiktoken`. | `shared/token-counting.md` |
| Preserved thinking | Thinking blocks bind to a model and a conversation. Edited history breaks them. | `shared/preserved-thinking-migration.md` |
| No prefill | A last-assistant prefill returns 400 on the 4.6+ family. | skill body |
| Forced tool use removed | `tool_choice` `any` or `tool` returns 400 on Fable 5.1, Opus 5.5, Sonnet 5.5. | skill body |

## 6. Tools

| Group | Items | Detail file |
| --- | --- | --- |
| Client tools | Custom tools, manual loop, Tool Runner, strict tool use, parallel tool calls, eager input streaming | `shared/tool-use-concepts.md`, `{lang}/claude-api/tool-use.md` |
| Server tools | Web search, web fetch, code execution, tool search (regex, BM25) | skill body, `shared/tool-use-concepts.md` |
| Anthropic-defined | Bash, text editor, memory (`memory_20250818`), computer use, advisor | `shared/tool-use-concepts.md` |
| Programmatic tool calling | Claude calls your tool from inside code execution | `shared/tool-use-concepts.md` |
| MCP connector | `mcp_servers` plus `mcp_toolset`, beta `mcp-client-2025-11-20` | skill body |
| Agent Skills | `container={"skills": [...]}` with code execution | skill body |
| Agent design | Bash versus dedicated tools, tool search, caching principles | `shared/agent-design.md` |

Server-tool errors return HTTP 200 with an error object. They do not raise.

## 7. Other API endpoints

| Endpoint | Use | Detail file |
| --- | --- | --- |
| `POST /v1/messages` | Core call. Tools and output limits are features of it. | `{lang}/claude-api/README.md` |
| Batches | Async at 50% cost. Key results by `custom_id`. | `{lang}/claude-api/batches.md` |
| Files | Upload once, reference by `file_id`. Out of beta. | `{lang}/claude-api/files-api.md` |
| Token counting | `POST /v1/messages/count_tokens` | `shared/token-counting.md` |
| Models | `GET /v1/models` | `shared/models.md` |
| Streaming | Events, `finalMessage()`, `get_final_message()` | `{lang}/claude-api/streaming.md` |
| Documents and citations | PDF base64 or file, `citations: {enabled: true}` | skill body |
| Admin API (beta) | Members, workspaces, API keys, service accounts, CMEK | `shared/admin-api.md` |

## 8. Managed Agents (beta)

Flow: create an Agent once, then start a Session for every run. `model`, `system`, and `tools` live on the agent.

| Topic | Detail file |
| --- | --- |
| Overview and reading guide | `shared/managed-agents-overview.md` |
| Core concepts, session budgets | `shared/managed-agents-core.md` |
| Environments, self-hosted sandboxes | `shared/managed-agents-environments.md`, `shared/managed-agents-self-hosted-sandboxes.md` |
| Tools, permission policies, vaults | `shared/managed-agents-tools.md` |
| Events | `shared/managed-agents-events.md` |
| Outcomes (rubric grader) | `shared/managed-agents-outcomes.md` |
| Multiagent | `shared/managed-agents-multiagent.md` |
| Memory stores | `shared/managed-agents-memory.md` |
| Scheduled deployments | `shared/managed-agents-scheduled-deployments.md` |
| Webhooks | `shared/managed-agents-webhooks.md` |
| Client patterns | `shared/managed-agents-client-patterns.md` |
| API reference | `shared/managed-agents-api-reference.md` |

Beta header: `managed-agents-2026-04-01`. The SDK sets it. Memory stores use `agent-memory-2026-07-22` instead.
Manage agent configs as files with `ant apply` (`shared/anthropic-cli.md`).

## 9. Platforms and auth

| Platform | Client | Note |
| --- | --- | --- |
| Claude API | `Anthropic()` | Default. |
| Amazon Bedrock | Mantle client, for example `AnthropicBedrockMantle` | Model IDs take an `anthropic.` prefix. Managed Agents unsupported. |
| Google Vertex AI | `AnthropicVertex` | Needs `project_id` and `region`. |
| Microsoft Foundry | `AnthropicFoundry` | No Go or Ruby client. |
| Claude Platform on AWS | See `shared/claude-platform-on-aws.md` | Anthropic-operated. |

Feature availability per platform: `shared/platform-availability.md`.

Auth order: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, an OAuth profile from `ant auth login`, workload identity federation variables, the default profile.
An unset `ANTHROPIC_API_KEY` does not mean no credentials. Run `ant auth status` before you ask for a key.

## 10. Evals and cost

| Job | File |
| --- | --- |
| Build an eval | `shared/evals/build-eval.md` |
| Audit an eval | `shared/evals/eval-audit.md` |
| Hill-climb an app | `shared/evals/eval-hillclimb.md` |
| Hill-climb on cost | `shared/evals/cost-hillclimb.md` |
| HTML report | `shared/evals/report/build-report-lite.mjs` |
| Cost levers | `shared/cost-optimization.md` |
| Error classes | `shared/error-codes.md` |
| Live docs URLs | `shared/live-sources.md` |

## 11. Relevance to agent-squad

- The pipeline runs on the Agent SDK, so approach 4 applies. This skill does not generate that code.
- Direct API features still matter for phase gates: structured outputs, `count_tokens`, prompt caching, and effort.
- Use `/claude-api migrate` when a model ID changes. Use `/claude-api prompt-audit` on skills and `CLAUDE.md`.

## Guards

- Rerun `/claude-api` after a Claude Code update and compare it with this map. The skill text is versioned with the CLI.
- Unverified: I read the skill body and file names only. I did not open the `shared/` and `{lang}/` files, so their contents are not checked here.
