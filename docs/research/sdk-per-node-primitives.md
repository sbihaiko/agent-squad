# What the Agent SDK can express per graph node

Research for wayfinder ticket #3. Pinned to `claude-agent-sdk` **0.2.161**, bundled CLI **2.1.284**. Researched on 2026-09-29.
Language: US English, Concise STE100 (ADR-0001).

Builds on [claude-agent-sdk.md](claude-agent-sdk.md) (called NOTE below) and [agent-sdk-command-map.md](agent-sdk-command-map.md). This file re-checks the claims a graph decision will rest on. It adds new facts. It runs no new probes and spends no money.

## Source tags

| Tag | Meaning |
|---|---|
| `[S:file:line]` | SDK source. Root: `~/.cache/uv/environments-v2/probe-slash-63cf4056329d3438/lib/python3.14/site-packages/claude_agent_sdk/` |
| `[D:page]` | `https://code.claude.com/docs/en/agent-sdk/<page>`, fetched 2026-09-29 |
| `[N:section]` | NOTE (`claude-agent-sdk.md`), section number. Not re-verified here. |
| `[P:name]` | Earlier probe recorded in NOTE. Not re-run. |
| UNVERIFIED | Not confirmed by source, docs, or probe. |

## 1. Answer in brief

A graph node maps to **one `query()` call**. The call is one CLI subprocess and one session. It carries: model, effort, prompt, tool surface, permission mode, hooks, custom tools, output schema, turn cap, and spend cap. It does not carry a wall-clock deadline. Python must add that.

| Node setting | SDK primitive | Hard limit or gap |
|---|---|---|
| Model, effort | `model`, `effort` | None found. |
| Tool surface | `tools`, `allowed_tools`, `disallowed_tools`, `permission_mode` | `allowed_tools` does not restrict. See 3. |
| Output contract | `output_format` | Validate again in Python. [N:11] |
| Turn cap | `max_turns` | Counts tool-use turns. Ends with an error result. |
| Spend cap | `max_budget_usd` | Checked after a response. Spend can pass it. |
| Wall-clock deadline | None | Must be built. See 7. |
| Human steer or stop | `ClaudeSDKClient.interrupt()` | Not on `query()`. See 2. |
| Sub-role (critic) | `AgentDefinition` or a second `query()` | Subagent is background by default. See 4. |
| Resume, checkpoint | `resume`, `session_id`, `session_store` | Transcript only. Not files. See 6. |
| Live view | `include_partial_messages`, `include_hook_events`, `forward_subagent_text`, task messages | See 8. |

## 2. `query()` versus `ClaudeSDKClient`

- The SDK always talks to the CLI in streaming mode internally, even for `query()`. [S:_internal/client.py:138-143]
- `query()` has no interrupt. The docs list "real-time interruption" as unsupported in single message mode. [D:streaming-vs-single-mode, "Limitations"]
- `ClaudeSDKClient.interrupt()` sends an `interrupt` control request. [S:client.py:308-312] [S:_internal/query.py:792-794] Its docstring says it works only in streaming mode.
- An interrupted turn ends with `ResultMessage.terminal_reason` of `aborted_streaming` or `aborted_tools`. [S:types.py:1363-1371]
- `set_model`, `set_permission_mode`, `stop_task`, `get_context_usage`, and `rewind_files` are client-only. [S:client.py:314-504]
- `query()` accepts an optional `transport`. With a custom transport, CLI-flag options (model, cwd, permission mode, tools) are **not applied**, and store-backed resume is skipped. [S:query.py docstring, `transport` argument]
- An error result behaves differently by entry point. `query()` yields the result, then raises. The client stays alive, except after a session crash. [D:agent-loop, "Handle the result"]

**Consequence.** Use `query()` for every node that runs unattended. Use the client only for a node that a human or the playground may steer. Both take the same `ClaudeAgentOptions`, so the node schema does not change.

## 3. Tool surface and permissions per node

Facts re-checked against docs:

- `allowed_tools` auto-approves. Tools not listed stay available. [D:agent-loop, "Tool permissions"]
- `disallowed_tools` blocks "regardless of other settings". [D:agent-loop, "Tool permissions"]
- `dontAsk` denies every call that would prompt. It gives a fixed tool surface for a headless node. [D:agent-loop, "Permission mode"]
- `bypassPermissions` cannot run as root on Unix. [D:agent-loop, "Permission mode"]
- A programmatic `AgentDefinition.tools` list removes other tools from that subagent completely. No prompt, no error. [D:subagents, "Tool restrictions"]

For the schema: a node needs `permission_mode`, `allowed_tools`, and `disallowed_tools` as separate fields. A decision must not treat `allowed_tools` as a limit.

## 4. Subagents

### 4.1 What an `AgentDefinition` can set

Fields in 0.2.161: `description`, `prompt`, `tools`, `disallowedTools`, `model`, `skills`, `memory`, `mcpServers`, `initialPrompt`, `maxTurns`, `background`, `effort`, `permissionMode`. [S:types.py:108-124] [D:subagents, "AgentDefinition configuration"]

- No per-subagent budget field and no per-subagent deadline field exist. Docs state: "No per-subagent wall-clock deadline. Cap each subagent with `maxTurns`." [D:hosting, "Known limitations"]
- `CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS` is a stall watchdog. It fires when a subagent stops producing output. It is not a total-runtime deadline. [D:hosting, "Known limitations"]
- At `maxTurns`, the subagent result is marked partial. The parent can resume it. The marking needs CLI 2.1.246 or later. [D:subagents, "AgentDefinition configuration"]
- `initialPrompt` is ignored when the agent runs as a subagent. [D:subagents, "AgentDefinition configuration"]
- The Python type has no `omitClaudeMd`. [D:subagents] [S:types.py:108-124]
- A subagent gets its own prompt and the Agent-tool prompt string. It does not get the parent history, the parent system prompt, or unlisted skill content. [D:subagents, "What subagents inherit"]
- A subagent inherits the main session's thinking configuration. [D:subagents, "What subagents inherit"]

### 4.2 Run mode: background by default

- Docs: subagents run in the background by default. The model sets `run_in_background: false` when it needs the result. `background: true` on the definition forces background. [D:subagents, "AgentDefinition configuration"]
- **No documented field forces foreground.** Only the model's tool input does. This answers open question 1 in NOTE in part: `background=False` is not documented as a force. UNVERIFIED by probe.
- New source fact. After a `ResultMessage`, the SDK keeps the call open while a background `local_agent` or `local_workflow` task runs. It waits for the CLI to report `idle`. [S:_internal/query.py:36-40, 246-262, 455-475]
- The wait has a ceiling: `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`, default 600000 ms (10 minutes). `0` means no limit. Set it through `env`. [S:_internal/query.py:47-70]
- This explains gotcha G3 in NOTE (a second `ResultMessage` for the notification turn). It is design, not a bug. [N:16 G3] [P:p3]
- `ResultMessage.origin` tells the two apart. `{"kind": "task-notification"}` marks the result of an injected turn. [S:types.py:1372-1377]

**Consequence.** A node verdict must not blindly take "the last `ResultMessage`" (NOTE rule). Select by `origin`: ignore `task-notification` results, or read the first. UNVERIFIED which rule is right; no probe checked that `origin` is set on the second result.

### 4.3 Limits on fan-out and spend

- Depth: `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`, default 3. Concurrency: `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`, default 20. Both go in `env`. [D:subagents, "Cap subagent depth, concurrency, and spend"]
- Spend: `max_budget_usd` counts subagent spend. At the cap, new subagents fail with `Budget limit reached` and running background subagents stop. Needs CLI 2.1.217 or later. [D:agent-loop, "Turns and budget"] [D:subagents]
- In Python, `env` merges over the parent environment. [D:subagents, "Cap subagent depth, concurrency, and spend"]

### 4.4 Subagent versus separate `query()` for one role

| Need | Subagent | Separate `query()` |
|---|---|---|
| A gate that must run | No. The actor may skip it. [D:subagents, "Claude not delegating"] | Yes. |
| Clean single result | No. Background and summary risk. [D:subagents] | Yes. |
| Per-role model and tools | Yes | Yes |
| Per-role deadline | No | Yes, from Python (see 7) |
| Per-role budget | No, shared with parent | Yes, own `max_budget_usd` |
| Shared context | Parent prompt string only | Only what Python passes |

Decision input: a graph node is a `query()`. A subagent is a detail inside a node. It is never a graph edge. This follows [N:8.3] and is now backed by the missing budget and deadline fields.

## 5. Hooks per node

- Python hook events: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `UserPromptSubmit`, `Stop`, `SubagentStop`, `PreCompact`, `Notification`, `SubagentStart`, `PermissionRequest`. [S:types.py:283-296] [N:6.1]
- `hooks` is a field of `ClaudeAgentOptions`, so each node carries its own hook set. `HookMatcher.timeout` is per matcher. [S:types.py:606-620]
- `SubagentStopHookInput` has `stop_hook_active`, `agent_id`, `agent_transcript_path`, `agent_type`. [S:types.py:377-384]
- `StopHookInput.stop_hook_active` guards a block loop. [S:types.py:370-374] [N:6.3]
- A hook runs in the caller's process, not in the agent context. It costs no context. [D:agent-loop, "Hooks"]
- A hook `deny` blocks even in `bypassPermissions`. [N:5.1] [D:permissions via N]
- Hooks may not fire once `max_turns` is reached. [N:6.3] UNVERIFIED here.

**Rule for the graph (ADR-0012).** Hooks enforce tool-level guards and cheap self-checks. The node gate runs in Python after the real `ResultMessage`. A `Stop` hook is not a gate. [N:6.3]

## 6. Sessions, resume, and checkpoints per node

- `ResultMessage.session_id` is set on every result, including errors. [D:agent-loop, "Handle the result"]
- `resume=<id>` continues. `fork_session=True` copies history to a new ID. `session_id=<uuid>` sets your own ID and needs `fork_session` when combined with `resume`. [S:types.py:2040-2049] [N:10]
- `resume_session_at` plus `resume_drops_turn` truncates history. [S:types.py:2249-2290]
- A cap error is recoverable. Resume the session with a higher limit. [D:agent-loop, "Put it all together" example]
- `SessionStore` needs two methods: `append(key, entries)` and `load(key)`. Four more are optional: `list_sessions`, `list_session_summaries`, `delete`, `list_subkeys`. [D:session-storage, "The SessionStore interface"] [S:types.py:1631-1653]
- The store is a **mirror**. The CLI writes to local disk first. A run resumed from the store deletes its local copy at the end, so the store then holds the only copy. [D:session-storage, "Dual-write architecture"]
- The store holds transcripts only. Not `CLAUDE.md` memory, not working-directory files. [D:hosting, "Session and state persistence"]
- `session_store` **cannot** be combined with `enable_file_checkpointing`. The SDK raises at startup. [S:_internal/session_store_validation.py:40-44] [D:session-storage, "Dual-write architecture"]
- A failed store write is dropped after three tries. The run continues. The stream shows a `mirror_error` system message. [D:session-storage, "Mirror writes are best-effort"] [S:types.py:1283-1293]
- `projectKey` encodes the working directory. Resume from the store must use a matching `cwd`. [D:session-storage, "The SessionStore interface"]
- Subagent transcripts mirror under `subpath` `subagents/agent-<id>`. Resume restores them only if the store implements `list_subkeys`. [D:session-storage, "Subagent transcripts"]
- Session state does not include files. To restore files, use `rewind_files` (client only, local disk) or git. [N:10]

**Consequence for a mid-graph checkpoint.** A node checkpoint is `(session_id, ResultMessage fields, output)` plus a git commit or worktree state. Do not rely on the SDK for file state when a store is on. Prefer git.

## 7. Budgets, turn caps, and deadlines per node

### 7.1 What exists

| Cap | Where | Ends with |
|---|---|---|
| `max_turns` | Session | `error_max_turns` |
| `max_budget_usd` | Session, includes subagents | `error_max_budget_usd` |
| `AgentDefinition.maxTurns` | One subagent | Partial result to the parent |
| `task_budget` | Session | Soft. Tells the model its token budget. [S:types.py:2430-2440] |
| `HookMatcher.timeout` | One hook | No decision |
| `load_timeout_ms` | Store `load` on resume, default 60000 | A clear error instead of a hang. [S:types.py:2421-2428] |
| `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS` | Wait for background tasks after a result, default 10 minutes | The run ends. [S:_internal/query.py:47-70] |

- Defaults for `max_turns` and `max_budget_usd`: no limit. [D:agent-loop, "Turns and budget"]
- The SDK docstring calls a turn "a user message and assistant response". The docs say `max_turns` counts tool-use round trips. Use the docs. [S:types.py:2050-2054] [D:agent-loop, "Turns and messages"]
- Budget is a client-side estimate. It can pass the cap. [N:12]
- With streaming input, the turn counter restarts for each queued message. The budget total keeps growing until a `/clear`. [D:agent-loop, "Turns and budget"]

### 7.2 What does not exist: a wall-clock deadline

- Docs: "No top-level session timeout." A session does not time out on its own. They advise `max_turns`. [D:hosting, "Known limitations"]
- No option in `ClaudeAgentOptions` sets a deadline. A search of `types.py` for `timeout` found only `HookMatcher.timeout`, `load_timeout_ms`, and store timeouts. [S:types.py grep]

### 7.3 How to build one (from source, not probed)

- `SubprocessCLITransport.close()` runs inside a shielded cancel scope. It closes stdin, waits, then terminates and kills the CLI. Worst case about 20 s. [S:_internal/transport/subprocess_cli.py:962-1030]
- **Caveat in the source.** A raw asyncio cancellation (`asyncio.wait_for`, `asyncio.timeout`, `task.cancel()`) is delivered inside `close()`. The terminate and kill step can be skipped. The child then stays in `_ACTIVE_CHILDREN` until process exit. [S:_internal/transport/subprocess_cli.py:962-990, docstring "Caveat"]
- An anyio cancel scope (`anyio.fail_after`, `anyio.move_on_after`) does not have this gap. The shield defers anyio cancels. [S:_internal/transport/subprocess_cli.py:962-990]
- At interpreter exit, the SDK sends SIGTERM to every child it still tracks. [S:_internal/transport/subprocess_cli.py:54-68]
- `ClaudeSDKClient.disconnect()` cancels running in-process MCP tools first. A tool blocked in a thread is abandoned after a few seconds. [S:client.py:607-613]

**Recommended deadline design (UNVERIFIED by probe).** Wrap the node's `query()` in `anyio.fail_after(deadline)`. Keep the `session_id` from any message already seen. On timeout, record `deadline_exceeded` as a node outcome. Resume with `resume=session_id` if the graph allows. For a steerable node, call `client.interrupt()` first, then `disconnect()`. This answers open question 5 in NOTE only in part: no probe confirmed that the CLI process dies. Test it before an ADR rests on it.

## 8. Forwarding events to a playground

Stream union: `UserMessage | AssistantMessage | SystemMessage | ResultMessage | StreamEvent | RateLimitEvent | ConversationResetMessage`. [S:types.py:1499-1507] `SystemMessage` has subclasses. [S:types.py:1194-1293, 1466]

| Event | Switch | Key fields |
|---|---|---|
| Text and tool blocks | Default | `AssistantMessage.content`, `parent_tool_use_id` |
| Token deltas | `include_partial_messages=True` | `StreamEvent` [S:types.py:2192-2197] |
| Hook lifecycle | `include_hook_events=True` | `HookEventMessage`: `hook_event_name`, subtype `hook_started` or `hook_response`, `data` [S:types.py:1466-1497, 2199-2205] |
| Subagent text | `forward_subagent_text=True` | Same message types with `parent_tool_use_id`. Default gives tool blocks only. [S:types.py:2207-2220] |
| Task lifecycle | Default | `TaskStartedMessage`, `TaskProgressMessage` (usage, `last_tool_name`), `TaskNotificationMessage`, `TaskUpdatedMessage` [S:types.py:1194-1282] |
| Rate limit | Default | `RateLimitEvent.rate_limit_info` [S:types.py:1425-1436] |
| Store failure | Default with a store | `MirrorErrorMessage`, subtype `mirror_error` [S:types.py:1283-1293] |
| Conversation reset | Default | `ConversationResetMessage`. Cost totals restart. [S:types.py:1439-1461] |
| Final | Default | `ResultMessage` [S:types.py:1340-1377] |

Rules:

- A task terminal status can arrive only as `TaskUpdatedMessage`. Clear active task IDs on a terminal status from either message. [S:types.py:1229-1282]
- A few system events can arrive after `ResultMessage`. Read the stream to its end. [D:agent-loop, "Message types"]
- To replay a finished node, use `get_session_messages`, `list_subagents`, and `get_subagent_messages`. [S:__init__.py:714-745, via N:10] With a store, use the `*_from_store` forms. [D:session-storage, "Supported on"]
- Run-record events can be a thin JSON form of these messages plus Python-side node events (start, gate result, deadline). UNVERIFIED: whether every field serializes to JSON without loss.

## 9. Facts the next decisions need

1. A node is one `query()`. It carries model, effort, tools, mode, hooks, schema, `max_turns`, and `max_budget_usd`.
2. No wall-clock deadline exists in the SDK. Python builds it with an anyio scope. A raw asyncio cancel can leave the CLI child alive.
3. A subagent has `maxTurns` only. It has no budget, no deadline, and no forced foreground mode. A background subagent yields a second `ResultMessage`. Use `origin` to tell them apart.
4. `session_store` mirrors transcripts only and cannot combine with file checkpointing. Node checkpoints need git for file state.
5. Interrupt and live steering need `ClaudeSDKClient`. Event forwarding needs three switches and covers hooks, subagents, and task lifecycle.

## 10. Open items

| # | Item | Status |
|---|---|---|
| 1 | Does `anyio.fail_after` around `query()` end the CLI child? | UNVERIFIED. Needs a probe. |
| 2 | Is `origin` set to `task-notification` on the second result of a background critic? | UNVERIFIED. Needs a probe. |
| 3 | Does `AgentDefinition.background=False` force foreground? | Docs silent. UNVERIFIED. |
| 4 | Does a `Stop` block loop stay inside one `ResultMessage`? | Not re-checked. [N:19 Q12] |
| 5 | Hook firing at `max_turns`. | UNVERIFIED. |
| 6 | Full JSON round trip of all message types for the playground. | UNVERIFIED. |
| 7 | Does `interrupt()` on a client stop a background subagent? | UNVERIFIED. `stop_task` exists for that. [S:client.py:443-455] |

Probes cost real money (about US$0.60 for NOTE). A new probe is a spending decision that stays with the owner under CLAUDE.md. Items 1 and 2 need only the smallest model and a tiny prompt.

## Guards

- A decision that cites a fact here keeps its tag. An UNVERIFIED tag blocks an ADR until a probe clears it.
- Re-check this file after an SDK or CLI bump. `claude_code_version` is in the `init` message. [N:16 G14]
