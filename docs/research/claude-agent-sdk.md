# Claude Agent SDK (Python) research note

Pinned to `claude-agent-sdk` **0.2.161**. It bundles Claude Code CLI **2.1.284** (`_version.py`, `_cli_version.py`).

Install it with the `sdk` extra (`uv sync --extra sdk`). The engine and its tests run without it; `squad/sdk_adapter.py` imports the SDK late.
Researched on 2026-09-29. Language: US English, Concise STE100.

## How to read this note

Each claim carries a source tag.

| Tag | Meaning |
|---|---|
| `[S:file:line]` | SDK source. Root: `~/.cache/uv/environments-v2/probe-slash-63cf4056329d3438/lib/python3.14/site-packages/claude_agent_sdk/` |
| `[D:page]` | Official page `https://code.claude.com/docs/en/agent-sdk/<page>`. The old `docs.claude.com` and `platform.claude.com` URLs redirect there. |
| `[D:hooks-ref]`, `[D:skills-ref]` | `https://code.claude.com/docs/en/hooks` and `https://code.claude.com/docs/en/skills` |
| `[P:name]` | Empirical probe run on 0.2.161 with model `haiku`. Scripts were in `/tmp/sdkprobe/` and are not saved. |
| UNVERIFIED | Not confirmed by source, docs, or probe. |

Probes cost about US$0.60 in total. Probe cost figures are client-side estimates.

## 1. Mental model

- The SDK is a thin wrapper. It starts the Claude Code CLI as a subprocess and talks to it over a control protocol. [D:overview] [S:query.py:1-20]
- The agent loop lives in the CLI, not in Python. Your code consumes a message stream. [D:agent-loop]
- Python code runs only in callbacks: hooks, `can_use_tool`, and in-process MCP tools. [D:hooks] [D:custom-tools]
- A "turn" is one tool-use round trip. `max_turns` counts tool-use turns only. [D:agent-loop, "Turns and messages"]
- The SDK is not a message-API client. There is no `messages.create` loop to write.

## 2. `query()` versus `ClaudeAgentOptions` clients

| Feature | `query()` | `ClaudeAgentOptions` + `ClaudeSDKClient` |
|---|---|---|
| Session | New session per call | One session per client |
| Follow-up prompts | Manual: `resume=` or `continue_conversation=True` | Automatic |
| Interrupt | No | `client.interrupt()` |
| Change mode or model mid-run | No | `set_permission_mode()`, `set_model()` |
| Hooks, tools, subagents | Yes | Yes |
| Error result | Yields the result, then raises `ResultError` | Yields the result, stays alive |

Sources: [D:python] comparison table; [S:client.py:26-64,272-340]; [S:query.py].

Rules for this project:

- Use `query()` for one-shot phase runs. Each pipeline phase is one call. Persist `session_id` when a resume is needed.
- Use `ClaudeSDKClient` when a human or the playground must interrupt or steer a run.
- Never share one client across async contexts (trio nurseries, task groups). Connect and disconnect in the same task. [S:client.py:57-64]
- A `break` inside `async for` over `query()` can cause asyncio cleanup errors. Let the loop end. [D:python, "Key gotchas"] [D:agent-loop]
- A few trailing system events can arrive after `ResultMessage`. Iterate to the end. [D:agent-loop, "Message types"]
- `receive_response()` stops after the first `ResultMessage`. It hangs if none arrives. [S:client.py:566-606]

Minimal, tested helper that returns the last result and absorbs the follow-up `ResultError`:

```python
from claude_agent_sdk import ClaudeAgentOptions, ResultError, ResultMessage, query

async def run_phase(prompt: str, options: ClaudeAgentOptions) -> ResultMessage:
    """Return the last ResultMessage. Swallow the ResultError that follows an error result."""
    last: ResultMessage | None = None
    try:
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, ResultMessage):
                last = msg
    except ResultError:
        if last is None:
            raise
    assert last is not None
    return last
```

[P:final4] ran this helper. One `query()` call can yield more than one `ResultMessage`; see Gotcha G3.

## 3. `ClaudeAgentOptions` fields that matter

Source for all rows: [S:types.py:1971-2440] unless noted.

| Field | Use in agent-squad |
|---|---|
| `model`, `fallback_model` | Pick per phase. Alias (`"haiku"`, `"sonnet"`) or full ID. |
| `effort`, `thinking` | `effort`: `low` to `max`. `thinking` supersedes `max_thinking_tokens`. |
| `system_prompt` | `str`, `{"type":"preset","preset":"claude_code","append":...}`, or `{"type":"file","path":...}`. |
| `setting_sources` | `None` loads user, project, and local. `[]` loads none. See G1. |
| `cwd`, `add_dirs` | Project root for CLAUDE.md, skills, and project settings. |
| `tools` | Base built-in set. `[]` removes all built-ins. See G6. |
| `allowed_tools` | Auto-approve list. It does not restrict tools. |
| `disallowed_tools` | Bare name removes the tool from context. Scoped rule (`Bash(rm *)`) denies matches. |
| `permission_mode` | `default`, `acceptEdits`, `plan`, `dontAsk`, `auto`, `bypassPermissions`. |
| `can_use_tool` | Async permission callback. Runs only when a call would prompt. |
| `hooks` | `dict[HookEvent, list[HookMatcher]]`. |
| `agents` | `dict[str, AgentDefinition]`. |
| `mcp_servers`, `strict_mcp_config` | In-process and external MCP. `strict_mcp_config=True` ignores `.mcp.json` and plugin servers. |
| `skills` | `"all"`, a list, or `[]`. It also adds the `Skill` tool and the setting sources. |
| `plugins` | `[{"type":"local","path":...}]`. |
| `output_format` | `{"type":"json_schema","schema":...}`. |
| `max_turns`, `max_budget_usd`, `task_budget` | Loop caps. `task_budget` is an API-side token budget. |
| `resume`, `continue_conversation`, `session_id`, `fork_session`, `resume_session_at` | Session control. |
| `session_store`, `session_store_flush` | Mirror transcripts to your store. |
| `enable_file_checkpointing` | Enables `client.rewind_files()`. |
| `include_partial_messages`, `include_hook_events`, `forward_subagent_text` | Extra stream content for the playground. |
| `verbatim_prompts` | Disables `@path` and slash-command expansion. See section 8. |
| `env`, `extra_args`, `stderr`, `cli_path`, `sandbox`, `settings` | Process control. `env` merges over the parent environment. |

Defaults that surprise:

- The SDK default system prompt is minimal. It omits the `claude_code` preset and its safety text. Set the preset on purpose. [D:modifying-system-prompts, "How system prompts work"]
- `max_turns`, `max_budget_usd`, and all time limits default to unlimited. [D:agent-loop, "Turns and budget"]
- The SDK does not read `.env` files. [D:quickstart, "Set your API key"]

## 4. Message and block types

Stream union: `UserMessage | AssistantMessage | SystemMessage | ResultMessage | StreamEvent | RateLimitEvent | ConversationResetMessage`. [D:python, "Message Types Summary"]

| Type | Key fields | Source |
|---|---|---|
| `AssistantMessage` | `content: list[ContentBlock]`, `model`, `parent_tool_use_id`, `error`, `usage`, `message_id`, `uuid` | [S:types.py:1139] |
| `UserMessage` | `content`, `tool_use_result`, `parent_tool_use_id`, `origin` | [S:types.py:1124] |
| `SystemMessage` | `subtype`, `data: dict` | [S:types.py:1154] |
| `ResultMessage` | See below | [S:types.py:1340] |
| `StreamEvent` | Raw API event. Needs `include_partial_messages=True`. | [S:types.py:1381] |

Content blocks: `TextBlock`, `ThinkingBlock`, `ToolUseBlock(id, name, input)`, `ToolResultBlock(tool_use_id, content, is_error)`, `ServerToolUseBlock`, `ServerToolResultBlock`. [S:types.py:955-1030]

`AssistantMessage.error` values: `authentication_failed`, `billing_error`, `rate_limit`, `invalid_request`, `server_error`, `unknown`. [S:types.py:1040-1050]

`ResultMessage` fields (dataclass, so use attributes): `subtype`, `is_error`, `num_turns`, `session_id`, `total_cost_usd`, `usage`, `model_usage`, `result`, `structured_output`, `stop_reason`, `terminal_reason`, `errors`, `permission_denials`, `deferred_tool_use`, `api_error_status`, `duration_ms`, `duration_api_ms`. [S:types.py:1340-1380]

`ResultMessage.subtype` values: `success`, `error_max_turns`, `error_max_budget_usd`, `error_during_execution`, `error_max_structured_output_retries`. [D:agent-loop, "Handle the result"] [D:python]

`SystemMessage` subtypes seen in probes: `init`, `hook_started`, `hook_response`, `commands_changed`, `thinking_tokens`, `permission_denied`. [P:p2]

The `init` message `data` keys (observed): `agents`, `apiKeySource`, `capabilities`, `claude_code_version`, `cwd`, `mcp_servers`, `memory_paths`, `model`, `output_style`, `permissionMode`, `plugins`, `session_id`, `skills`, `slash_commands`, `tools`, `terminal_slash_commands`. [P:p1]

Subagent messages carry `parent_tool_use_id`. The tool name is `Agent` in `tool_use` blocks and `Task` in the `init` tools list and in old versions. Match both. [D:subagents, "Detect subagent invocation"]

## 5. Permission modes and `can_use_tool`

### 5.1 Evaluation order

The CLI checks in this order. [D:permissions, "How permissions are evaluated"]

1. Hooks. A hook `deny` blocks even in `bypassPermissions`.
2. Deny rules (`disallowed_tools`, settings). Blocks even in `bypassPermissions`.
3. Ask rules (settings). They go to `can_use_tool`.
4. Permission mode.
5. Allow rules (`allowed_tools`, settings).
6. `can_use_tool`. In `dontAsk` mode this step becomes a deny.

### 5.2 Modes

| Mode | Behavior |
|---|---|
| `default` | No auto-approve by mode. Prompting calls go to `can_use_tool`. With no callback they are denied. |
| `acceptEdits` | Auto-approves edits and `mkdir`, `touch`, `rm`, `rmdir`, `mv`, `cp`, `sed` inside the working directory. |
| `plan` | No source edits. Edits go to `can_use_tool` even with allow rules. |
| `dontAsk` | Every prompt becomes a deny. `can_use_tool` never runs. Best for a fixed tool surface. |
| `auto` | A model classifier approves or denies. |
| `bypassPermissions` | Approves everything not blocked earlier. `allowed_tools` does not limit it. Refused as root. |

Source: [D:permissions, "Permission modes"]; [S:types.py:23-26].

Rules to remember:

- **`allowed_tools` is not a restriction.** Unlisted tools stay available. With `bypassPermissions` they all run. [D:permissions, warning box]
- **Auto-approved calls never reach `can_use_tool`.** The SDK warns with `CanUseToolShadowedWarning` when an allow entry shadows the callback. [S:types.py:1835-1925] [P:p3 saw the warning]
- **Subagents inherit the parent mode.** `AgentDefinition.permissionMode` is never applied as `bypassPermissions`. [D:permissions, "Subagent inheritance"]
- **Use a `PreToolUse` hook for gates that must see every call.** [S:types.py:2050-2060 docstring]
- **Without `can_use_tool`, a prompting call is denied.** The stream shows a `permission_denied` `SystemMessage` and an error `tool_result`. [P:p2]

### 5.3 `can_use_tool`

Signature: `async (tool_name, tool_input, ToolPermissionContext) -> PermissionResultAllow | PermissionResultDeny`. [S:types.py:223-296]

- `PermissionResultAllow(updated_input=..., updated_permissions=[PermissionUpdate...])`.
- `PermissionResultDeny(message=..., interrupt=False)`. `interrupt=True` stops the run.
- Context fields: `tool_use_id`, `agent_id`, `suggestions`, `blocked_path`, `decision_reason`, `title`, `display_name`, `description`.

[P:cut] verified the callback: with `default` mode and no allow rules, `Write` and `Bash` reached the callback. Both were denied. The file was not created. The `ResultMessage.permission_denials` list held both calls, and the run ended `success`.

```python
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

async def can_use_tool(name, tool_input, ctx):
    if name.startswith("mcp__phase__"):
        return PermissionResultAllow()
    return PermissionResultDeny(message=f"{name} is not allowed in this phase")
```

## 6. Hooks (Stop and gates)

### 6.1 Events in Python

`PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `UserPromptSubmit`, `Stop`, `SubagentStop`, `PreCompact`, `Notification`, `SubagentStart`, `PermissionRequest`. [S:types.py:283-296] [D:hooks, "Available hooks"]

`SessionStart`, `SessionEnd`, `PostToolBatch`, `StopFailure`, `TaskCompleted`, and others are TypeScript-only. In Python, `SessionStart` works only as a shell hook from settings files. [D:hooks, "Session hooks not available in Python"]

### 6.2 Callback contract

- Signature: `async (input_data: dict, tool_use_id: str | None, context) -> dict`. `context` is a placeholder. [S:types.py:584-620]
- Register with `HookMatcher(matcher=..., hooks=[...], timeout=...)`. [S:types.py:606]
- The matcher tests tool names only. It is an exact list (`Write|Edit`) or an unanchored regex. `Stop` ignores matchers. [D:hooks] [D:hooks-ref, "Matcher patterns"]
- Matching hooks run **in parallel**. Do not depend on order. For permission decisions, `deny` beats `defer`, then `ask`, then `allow`. [S:types.py:2050-2065] [D:hooks]
- Python output keys `continue_` and `async_` convert to `continue` and `async`. [S:types.py:526-580]
- Catch errors inside the hook. Use `asyncio.to_thread` for blocking work. [D:hooks, "Make HTTP requests from hooks"]

### 6.3 `Stop` as a gate

A `Stop` hook fires when the agent is about to finish.

- Return `{"decision": "block", "reason": "..."}` to keep the agent working. The reason goes to the model. [D:hooks-ref, "Stop decision control"]
- Return `{"continue_": False}` to stop the whole run. [D:hooks-ref]
- `StopHookInput` has `stop_hook_active`. It is `True` when the agent already continues because of a Stop hook. [S:types.py:370-374]
- The CLI hook input also has `last_assistant_message`. It is not declared in the Python `StopHookInput` type. Read it with `.get()`. [D:hooks-ref] [S:types.py:370-374]
- Observed: the first call had `stop_hook_active=False`. After a block, the second call had `True`. [P:p1] [P:final4]
- A hook that always blocks loops until `max_turns` or `max_budget_usd`. Always test `stop_hook_active` or keep a counter.
- Timeout: the hook counts as "no decision" and the agent stops. [D:hooks, "Hook timeout"]
- Hooks may not fire once `max_turns` is reached. [D:hooks, "Hook not firing"]

Tested gate:

```python
from claude_agent_sdk import HookMatcher

async def stop_gate(input_data, tool_use_id, context):
    if input_data["stop_hook_active"]:      # second pass: let the agent stop
        return {}
    return {"decision": "block", "reason": "Gate: re-check that reasons is non-empty."}

hooks = {"Stop": [HookMatcher(hooks=[stop_gate])]}
```

**Design rule.** A Stop hook is a stop condition for the agent. It is not the pipeline gate. Run the real gate in Python after `ResultMessage`. Use Stop only for cheap self-checks.

### 6.4 `PreToolUse`

Return `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny"|"allow"|"ask"|"defer", "permissionDecisionReason": ..., "updatedInput": {...}}}`. [D:hooks, "Outputs"]

- `defer` ends the run with `ResultMessage.deferred_tool_use`. Resume later. [S:types.py:1301-1312]
- `updatedInput` must sit inside `hookSpecificOutput`. Do not pair it with `defer`. [D:hooks, "Modified input not applied"]
- Inside a subagent, the input has `agent_id` and `agent_type`. [S:types.py:314-345]
- `PreToolUse` fired for both the custom MCP tool and the internal `StructuredOutput` tool. [P:p1]

### 6.5 Slash command `/goal`

`/goal <condition>` installs a session-scoped Stop hook. A model judges the condition after each stop attempt. [P:p2]

Observed on `/goal the file /tmp/zz_never_exists_42.txt exists` with `max_turns=3`:

1. The CLI replied "Goal set: ...".
2. When the agent tried to stop, a `UserMessage` arrived with text `Stop hook feedback: [<condition>]: <reason>`. The agent then continued.
3. The run ended `error_max_turns`, `is_error=True`, `num_turns=4`, `terminal_reason="max_turns"`, `result=""`, `errors=["Reached maximum number of turns (3)"]`.
4. `query()` then raised `ResultError` with `exit_code=1`.

## 7. Custom tools and in-process MCP

- `@tool(name, description, input_schema, annotations=None)` wraps an `async def handler(args) -> {"content": [...], "is_error": bool}`. [S:__init__.py:251-336]
- `input_schema` is a `{name: type}` dict, a TypedDict, or full JSON Schema. Dict-style makes every key required. Use JSON Schema for optional fields and enums. [D:custom-tools, "Create a custom tool"]
- `create_sdk_mcp_server(name, version, tools)` returns a config. Put it in `mcp_servers={"key": cfg}`. [S:__init__.py:491]
- The tool's full name is `mcp__<key>__<tool>`. Use that name in `allowed_tools`. Wildcard: `mcp__<key>__*`. [D:custom-tools, "Call a custom tool"]
- An uncaught exception becomes an error result. The loop continues. Return `is_error: True` yourself to control the message. [D:custom-tools, "Handle errors"]
- Set `ToolAnnotations(readOnlyHint=True)` to allow parallel calls. Custom tools run sequentially by default. [D:agent-loop, "Parallel tool execution"]
- The Python decorator forwards only `content` and `is_error`. `structuredContent` needs a standalone MCP server. [D:custom-tools, "Return structured data"]
- Tool search is on by default. It defers MCP tool schemas. [D:custom-tools, "Add more tools"]
- Tools run in your process. They may touch application state directly. [S:__init__.py:491-540]

```python
from claude_agent_sdk import create_sdk_mcp_server, tool

@tool("word_count", "Count words in text", {"text": str})
async def word_count(args):
    return {"content": [{"type": "text", "text": str(len(args["text"].split()))}]}

server = create_sdk_mcp_server("phase", tools=[word_count])
# options: mcp_servers={"phase": server}, allowed_tools=["mcp__phase__word_count"]
```

[P:p1] and [P:final4] ran this pattern. With `tools=[]` the `init` tool list held only `mcp__calc__add` and `StructuredOutput`. [P:p1]

## 8. Subagents (`AgentDefinition`) for actor-critic roles

### 8.1 Definition

Fields in 0.2.161: `description`, `prompt`, `tools`, `disallowedTools`, `model`, `skills`, `memory`, `mcpServers`, `initialPrompt`, `maxTurns`, `background`, `effort`, `permissionMode`. Multi-word names keep camelCase. [S:types.py:108-124] [D:subagents, "AgentDefinition configuration"]

- The Python type has no `omitClaudeMd`. Subagents load project CLAUDE.md when `"project"` is a setting source. [D:subagents]
- `model`: alias, full ID, or `"inherit"`.
- `tools` omitted means all subagent-eligible tools. `tools=[]` means none.
- Programmatic agents override filesystem agents of the same name. [D:subagents]

### 8.2 Behavior

- The parent invokes a subagent through the `Agent` tool. Put `"Agent"` in `allowed_tools`. Name the agent in the prompt to force it. [D:subagents, "Explicit invocation"]
- Context isolation: the subagent gets only its `prompt` and the parent's Agent-tool prompt string. Only its final message returns. [D:subagents, "What subagents inherit"]
- The parent may summarize the subagent result. To keep it verbatim, say so in the prompt. [D:subagents]
- Subagents can spawn subagents. Depth default 3 (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`). Concurrency default 20 (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`). Set both through `env`. [D:subagents, "Cap subagent depth, concurrency, and spend"]
- **Subagents run in the background by default.** The Agent call returns at once unless the model sets `run_in_background: false`. [D:subagents, "AgentDefinition configuration"] See G3.
- Subagent spend counts in `total_cost_usd` and `model_usage`, not in `usage`. [D:cost-tracking]
- Resume a subagent by resuming its session and giving the `agentId` from the Agent tool result. Explore and Plan built-ins cannot resume. [D:subagents, "Resume subagents"]
- `SubagentStart` and `SubagentStop` hooks give `agent_id` and `agent_transcript_path`. [S:types.py:377-410]

### 8.3 Two ways to build actor-critic

| Pattern | How | Fit |
|---|---|---|
| Critic as subagent | Actor session has `agents={"critic": ...}`. The actor calls it. | Cheap. The actor sees the critic text. The critic cannot be forced to run. |
| Critic as separate `query()` | Python calls the critic with the actor output as its prompt. Python owns the loop and the verdict. | Deterministic. Recommended for gates. Costs one more session. |

Tested critic-as-subagent run: [P:final4]. The actor asked the `critic` agent, then returned a Pydantic-valid verdict in `structured_output`. The Stop gate ran twice (`[False, True]`). Cost US$0.054, 5 turns.

```python
import asyncio
from pydantic import BaseModel
from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions

class Verdict(BaseModel):
    approved: bool
    reasons: list[str]

options = ClaudeAgentOptions(
    model="haiku",
    setting_sources=[],
    allowed_tools=["Agent"],
    disallowed_tools=["Bash", "Write", "Edit", "WebFetch", "WebSearch"],
    agents={"critic": AgentDefinition(
        description="Strict critic. Call it to judge a claim.",
        prompt="Judge the claim. Reply APPROVED or REJECTED plus one reason.",
        tools=[], model="haiku", maxTurns=2)},
    output_format={"type": "json_schema", "schema": Verdict.model_json_schema()},
    max_turns=8, max_budget_usd=0.30,
)
# res = await run_phase("Ask the critic agent to judge: '...'. Wait for its answer, then return the verdict.", options)
# verdict = Verdict.model_validate(res.structured_output)
```

## 9. Skills and slash commands

### 9.1 Loading

- Skills are files: `.claude/skills/<name>/SKILL.md`. There is no Python API to register a skill. [D:skills]
- Discovery uses `setting_sources`. `None` (default) loads user, project, and local. `[]` loads none. [S:types.py:2196-2206] [D:claude-code-features]
- `skills="all"` or a list also adds the `Skill` tool and the sources. `[]` hides every skill. It is a context filter, not a sandbox. [S:types.py:2213-2233]
- `plugins=[{"type":"local","path":...}]` adds namespaced skills, `plugin:skill`. [D:plugins]
- Bundled skills always appear, for example `code-review`, `verify`, `loop`, `run`. [P:p1]

### 9.2 Slash commands

- A prompt that starts with `/name` dispatches a built-in, a bundled skill, a user skill, or a `.claude/commands/` file. It does not need the `skills` option. [D:skills, "Dispatch commands by name"]
- The `init` message lists them: `data["slash_commands"]` and `data["skills"]`. [D:skills, "Discover available commands"] [P:p1,p2]
- Skills with `user-invocable: false` do not appear in either list. [D:skills]
- An unknown `/name` does not fail. The CLI sends the prompt to the model with a note. Observed: `success`, 1 turn, and a model reply that the command does not exist. [D:skills] [P:p3]
- `verbatim_prompts=True` turns off `@path` expansion and slash dispatch. Use it for prompts built from untrusted text. [S:types.py:2106-2130]
- `/compact` needs prior history. `/clear` matters only in a streaming session. [D:skills]

### 9.3 Frontmatter that affects the SDK

- `disable-model-invocation: true` removes the skill from the model context. The model cannot call it through the `Skill` tool. Only `/name` runs it. [D:skills-ref, "Frontmatter reference" and "Control who invokes a skill"]
- `user-invocable: false` blocks `/name` but not the model. [D:skills-ref]
- `allowed-tools` frontmatter applies to project and personal skills in SDK sessions. [D:skills, "Pre-approve tools for skills"]

## 10. Sessions, resume, and fork

- Every `ResultMessage` has `session_id`. It is also in `init.data["session_id"]`. [D:sessions, "Capture the session ID"] [P:p1]
- Transcripts are files: `~/.claude/projects/<encoded-cwd>/<id>.jsonl`. Files stay local to the machine. [D:sessions]
- `resume=<id>` continues. `continue_conversation=True` takes the latest session for the `cwd`. They are mutually exclusive. [S:types.py:2042-2054]
- `fork_session=True` with `resume` makes a new session ID and leaves the original untouched. Forking does not fork files. [D:sessions, "Fork to explore alternatives"]
- `resume_session_at=<uuid>` truncates history. Pair it with `resume_drops_turn` to validate the cut. [S:types.py:2108-2140]
- `session_id=<uuid>` sets your own ID. With `resume` it needs `fork_session=True`. [S:types.py:2050-2060]
- Resume works from another `cwd` on the same machine. Across hosts, use `session_store` or copy the file. [D:sessions, "Resume across hosts"]
- Helpers: `list_sessions`, `get_session_info`, `get_session_messages`, `rename_session`, `tag_session`, `delete_session`, `fork_session`, `list_subagents`, `get_subagent_messages`. [S:__init__.py:714-745]
- `enable_file_checkpointing=True` plus `client.rewind_files(user_message_id)` reverts files. Sessions store conversation only. [D:sessions] [S:client.py:364]
- Recovery from a cap: resume the session with a higher limit. [D:sessions, "Resume by ID"]

Observed: [P:p3]
- A client turn 2 remembered a code word from turn 1 in the same session.
- `query(resume=s1, fork_session=True)` gave a different `session_id` and still knew the word.

```python
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

async with ClaudeSDKClient(ClaudeAgentOptions(model="haiku", max_turns=2)) as client:
    for prompt in ["Remember the code word PELICAN. Reply OK.", "What was the code word?"]:
        await client.query(prompt)
        async for m in client.receive_response():
            if isinstance(m, ResultMessage):
                print(m.session_id, m.is_error, m.result)
```

[P:p6] ran this with default tools. See G6 for a failure with `tools=[]`.

## 11. Structured output

- Set `output_format={"type": "json_schema", "schema": <JSON Schema draft-07>}`. Pydantic: `Model.model_json_schema()`. [D:structured-outputs]
- The agent may use tools first. The result carries `ResultMessage.structured_output` (a parsed value). `result` holds the JSON text. [P:p1: `{'sum': 5}` and `'{"sum":5}'`]
- The CLI injects a `StructuredOutput` tool. It shows in the `init` tool list and in `PreToolUse`. [P:p1]
- On repeated validation failure: `subtype="error_max_structured_output_retries"`. [D:structured-outputs, "Error handling"]
- A `success` result may still have no `structured_output`. Treat that as a failure. [D:structured-outputs]
- The final `stop_reason` was `tool_use`, with `terminal_reason="completed"`. Do not treat `tool_use` as an error. [P:p1]
- An invalid schema fails at startup. `format` is not enforced. [D:structured-outputs, "Output format configuration"]
- Always validate again with Pydantic. It gives typed objects and catches schema drift.

## 12. Budgets and cost tracking

### 12.1 Caps

| Cap | Effect at limit |
|---|---|
| `max_turns` | `error_max_turns`. Counts tool-use turns. Observed `num_turns=4` with `max_turns=3`. [P:p2] |
| `max_budget_usd` | `error_max_budget_usd`. Includes subagent spend. Blocks new subagents. [D:agent-loop] [P:dbg2 hit it] |
| `task_budget` | Tells the model its token budget. Soft. [S:types.py:88-96] |
| Hook `timeout` | See section 6. |
| Wall clock | No SDK option. UNVERIFIED how to bound it. See open question 5. |

After a cap, the result has `is_error=True`, empty `result`, and `errors`. Then `query()` raises `ResultError`. [P:p2]

`max_budget_usd` checks after a model response, so spend can pass the cap. `usage` may omit the crossing response. [D:cost-tracking, "Track costs on failed conversations"]

### 12.2 Reading cost

- `total_cost_usd` is a client-side estimate. It is not billing data. Do not bill from it. [D:cost-tracking, warning box]
- It may be `None`. Guard it.
- `usage` covers the main loop only. `model_usage` (per model, camelCase keys) and `total_cost_usd` include subagents. [D:cost-tracking]
- In a streaming client, cost fields are cumulative for the call. Read the last result. Do not sum. [D:cost-tracking, "Track costs in streaming input mode"]
- A resumed session's result includes earlier spend. Do not sum across resumes (CLI 2.1.277 and later). [D:cost-tracking, "Accumulate costs"]
- Per-message `output_tokens` is a placeholder. Read output tokens from the result. [D:cost-tracking]
- After a crash, the final `error_during_execution` result may carry zero cost. [D:cost-tracking]
- Observed costs with `haiku`: about US$0.005 for a short tool run, US$0.054 for the actor plus critic run. [P:p1] [P:final4]

## 13. Errors

Hierarchy: `ClaudeSDKError` > `CLIConnectionError` > `CLINotFoundError`; `ClaudeSDKError` > `ProcessError` > `ResultError`; plus `CLIJSONDecodeError`, `MessageParseError`. [S:_errors.py]

| Situation | What you see |
|---|---|
| Error result (`error_max_turns`, `error_max_budget_usd`, `error_during_execution`, API failure) | A `ResultMessage` with `is_error=True`, then `ResultError` from `query()`. `ResultError` has `subtype`, `errors`, `result`, `terminal_reason`, `api_error_status`, `session_id`, `data`. [S:_errors.py:56-105] |
| `ResultError.subtype == "success"` | The loop ended but the last turn was an API error. Read `api_error_status`. [S:_errors.py:56-75] |
| CLI missing | `CLINotFoundError`. |
| CLI crash | `ProcessError` with `exit_code`, `stderr`. |
| Streaming client | The client stays alive after an error result. [D:agent-loop, "Handle the result"] |
| Bad `skills` entry | `ValueError` before the process starts. [D:skills, "Invalid skill name error"] |

Rules:

- Test `is_error`, not only `subtype`. [P:p4] saw `subtype="success"` with `is_error=True` and `terminal_reason="prompt_too_long"`.
- `terminal_reason` values seen: `completed`, `max_turns`, `budget_exhausted`, `prompt_too_long`. Docs also list `api_error`, `aborted_streaming`, `aborted_tools`. [P:p1,p2,p4,dbg2] [D:python]
- Map each outcome to one verdict in one module. Map needs-human separately.

## 14. Concurrency

- One `query()` call is one CLI subprocess. UNVERIFIED by probe; inferred from [D:overview] ("a library that runs the Claude Code binary"). Parallel calls use `asyncio.gather` on separate calls.
- Separate `query()` calls do not share state. Sessions share files under `~/.claude/projects/`.
- Never let two runs share one `cwd` with `continue_conversation=True`. They would pick the same "latest" session. Use explicit `resume=<id>`.
- Concurrent file edits by parallel agents in one `cwd` are your problem. Fork copies history, not files. [D:sessions]
- Hook callbacks for one event run in parallel. [S:types.py:2050-2060]
- Do not move a `ClaudeSDKClient` between task groups. [S:client.py:57-64]
- Async callbacks run on your event loop. Block it and the agent waits. Use `asyncio.to_thread`. [D:hooks]
- Subagent fan-out limits: depth 3, concurrency 20 by default. [D:subagents]

## 15. Authentication

- Set `ANTHROPIC_API_KEY` in the process environment. The SDK does not load `.env`. [D:quickstart]
- Cloud providers: `CLAUDE_CODE_USE_BEDROCK=1`, `CLAUDE_CODE_USE_VERTEX=1`, `CLAUDE_CODE_USE_FOUNDRY=1`, `CLAUDE_CODE_USE_ANTHROPIC_AWS=1` with their credentials. [D:quickstart]
- Pass variables per run through `ClaudeAgentOptions.env`. It merges over the parent environment and drops `CLAUDECODE`. [S:types.py:2131-2149]
- Anthropic does not allow third parties to offer claude.ai login or its rate limits in their products. Use API keys. [D:overview] [D:quickstart]
- Credentials are an owner-only action per project CLAUDE.md. Do not create or change them without a `needs-ratification` issue.
- Our probes used the local login of the machine. `init.data["apiKeySource"]` reports the source. Its value was not recorded. UNVERIFIED.
- Multi-tenant isolation needs `setting_sources=[]`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, and a separate filesystem. Managed policy and `~/.claude.json` load anyway. [D:claude-code-features, "What settingSources does not control"]

## 16. Known gotchas

**G1. `setting_sources` default loads everything.** The probe script sets `["user","project"]`, but `None` already loads user, project, and local. `[]` loads none of your skills: with `[]` the `init` skill list held only 16 bundled skills. With `["user","project"]` it held 98, including `autonomy` and `panel`. [S:types.py:2196-2206] [P:p1,p2] Finding 2 from the task is refined: "no skills load" is true only for `[]`.

**G2. User-level setup leaks into runs.** With user sources, 98 skills and user `SessionStart` hooks loaded. [P:p2] Pipeline runs should set `setting_sources=["project"]` or `[]` and list skills with `skills=[...]`.

**G3. Background subagents can end the call early.** With a critic subagent, the parent got the Agent call back at once and produced a `ResultMessage` before the critic finished. A second `ResultMessage` followed for the injected notification turn. [P:p3] Observed once: the second result had `is_error=True` and text "Prompt is too long". Keep the last `ResultMessage`. Prefer critic-as-separate-`query()` for gates. UNVERIFIED: a reliable way to force foreground subagents from Python.

**G4. `ResultError` after the result.** Wrap `query()` in `try/except ResultError`. Read `session_id` from the yielded result first. [D:sessions, "Capture the session ID"]

**G5. `/goal` can burn turns.** A goal the agent cannot meet loops until `max_turns`. Always set `max_turns` and `max_budget_usd`. [P:p2]

**G6. `haiku` with a restricted `tools` list returned "Prompt is too long".** [P:p4,dbg] failed with `tools=["Agent"]`, `tools=["Agent","Read"]`, and (on turn 2) `tools=[]`. The same setup passed with default tools, and with model `sonnet`. Cause UNKNOWN. The error is `AssistantMessage.error="invalid_request"` and `terminal_reason="prompt_too_long"`, with a tiny prompt. Workaround: use `disallowed_tools` instead of `tools`. [P:final4 passed]

**G7. `allowed_tools` does not restrict.** See section 5.

**G8. The `Stop` input lacks `last_assistant_message` in the Python type.** Use `.get()`. [S:types.py:370-374] [D:hooks-ref]

**G9. Streaming generator errors.** In Python, an exception in a prompt generator is logged at debug level and the session stalls. [D:streaming-vs-single-mode]

**G10. Big system prompts.** Above about 128 KB on Linux, use `{"type":"file","path":...}`. [D:python, "Key gotchas"]

**G11. Prompt cache.** Changing `append` on a resumed session has no effect unless `snapshot=False`. [S:types.py:40-70]

**G12. Hook timeout docs disagree.** `HookMatcher.timeout` comment says default 60 s. Docs say 600 s for most events. [S:types.py:606-620] [D:hooks, "Hook timeout"] Set `timeout` explicitly.

**G13. Tool name.** `Agent` in blocks, `Task` in older versions and in the `init` list. Match both. [D:subagents]

**G14. Version drift.** The docs describe newer CLI versions than some installs. Pin the SDK. Record `init.data["claude_code_version"]` in every run log.

## 17. Verification of the six probe findings

| # | Finding | Status |
|---|---|---|
| 1a | A prompt starting `/name` runs a command or skill | **Confirmed.** `/goal` and `/security-review` ran. [P:p2] [D:skills] |
| 1b | A slash command inside another command's argument is not expanded | **UNVERIFIED.** `/security-review /autonomy` ran the outer command only. The output did not show whether the inner text was expanded. No doc page states the rule. |
| 2 | `setting_sources=["user","project"]` is needed to load skills, else none | **Corrected.** The default `None` loads them. Only `[]` (or a list without user and project) hides them. [P:p1,p2] [D:skills, "Skills not found"] |
| 3 | `init` has `slash_commands` and `skills` | **Confirmed.** [P:p1] [D:skills] |
| 4 | `/goal` sets a Stop hook, can hit `max_turns`, raises `ResultError` | **Confirmed.** [P:p2] |
| 5 | `disable-model-invocation: true` blocks the Skill tool | **Confirmed by docs only.** Not re-probed. [D:skills-ref] |
| 6 | `ResultMessage` carries `is_error`, `num_turns`, `total_cost_usd`, `result` | **Confirmed.** Also `structured_output`, `terminal_reason`, `errors`, `permission_denials`. [P:p1,p2] |

## 18. Implications for agent-squad

Design stance: Python owns the pipeline, the loops, and every gate. The SDK runs one agent session per unit of work.

### 18.1 Phase mapping

| Phase | Session shape | SDK features |
|---|---|---|
| Scout | Read-only, one `query()`. | `permission_mode="dontAsk"`, `allowed_tools=["Read","Grep","Glob"]`, `output_format` for a findings schema, `effort="low"`, small `max_budget_usd`. Use subagents for wide reads. |
| Spec | Actor writes the spec. Critic reviews. | `output_format` (spec schema). Critic as separate `query()`. `resume=` to revise in the same context. |
| Decompose | One structured call. | `output_format` (task list schema). Validate with Pydantic. Python checks the graph. |
| Execute | One session per task, in a worktree `cwd`. | `permission_mode="acceptEdits"`, scoped `allowed_tools` (`Bash(uv run pytest *)`), `disallowed_tools`, `PreToolUse` hook for path guards, `enable_file_checkpointing`, `max_turns`, `max_budget_usd`. |
| Verify | Read-only critic plus real test runs. | Python runs tests and reads the exit code. A verify agent reviews the diff with `output_format`. The gate is Python logic, not model text. |

### 18.2 Actor-critic loops

- Actor: `query()` or `ClaudeSDKClient` with the phase system prompt, tools, and `output_format`.
- Critic: separate `query()`, read-only tools, `output_format` `{approved, reasons}`. Fresh context avoids anchoring on the actor's reasoning.
- Loop control lives in Python: iteration count, budget sum, and stop rule. Feed critic reasons back with `resume=<actor session_id>`.
- Use a critic subagent (`AgentDefinition`) only for cheap in-session self-review. It is not authoritative (G3).
- Log per iteration: `session_id`, `subtype`, `is_error`, `num_turns`, `total_cost_usd`, `terminal_reason`.

### 18.3 Gates

| Gate type | Where |
|---|---|
| Pipeline gate (pass or fail a phase) | Python code after the last `ResultMessage`. Inputs: `structured_output`, real command exit codes, `is_error`. |
| Self-check before the agent stops | `Stop` hook with `stop_hook_active` guard. |
| Tool-level guard (paths, commands) | `PreToolUse` hook (runs first, cannot be bypassed by modes) and `disallowed_tools`. |
| Human approval (needs-human) | `can_use_tool` or `PreToolUse` returning `defer`. Then return the needs-human exit code. |
| Spend limit | `max_budget_usd` per session plus a Python sum per run. |
| Live-write approval | `PreToolUse` deny by default. Allow only with a recorded grant. |

Tag a red gate as a defect, not a retry target. Retry only on `ResultError` with a transient `api_error_status`.

### 18.4 Playground

The configuration playground can edit these fields, all plain data:

- Per phase: `model`, `effort`, `system_prompt`, `allowed_tools`, `disallowed_tools`, `permission_mode`, `max_turns`, `max_budget_usd`, `setting_sources`, `skills`.
- Per role: `AgentDefinition` fields and `output_format` schema.
- Show the `init` message (tools, skills, slash commands, model) as a preview of the resolved run. [P:p1]
- Stream `include_partial_messages`, `include_hook_events`, and `forward_subagent_text` for a live view. [S:types.py:2080-2105]
- Use `list_sessions` and `get_session_messages` for replay. [S:__init__.py:714-720]
- Dry-run with `permission_mode="plan"` or `dontAsk`.

### 18.5 Baseline options

```python
ClaudeAgentOptions(
    model="sonnet",                       # pin per phase; record init.data["model"]
    setting_sources=["project"],          # CLAUDE.md and project skills only
    system_prompt={"type": "preset", "preset": "claude_code", "append": "..."},
    permission_mode="dontAsk",            # deny-by-default; widen per phase
    allowed_tools=["Read", "Grep", "Glob"],
    disallowed_tools=["WebFetch", "WebSearch"],
    max_turns=30, max_budget_usd=2.0,     # every session gets both caps
    env={"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1"},
)
```

This block is a starting point. It was not run as one unit.

## 19. Open questions

1. Force foreground subagents. Does `AgentDefinition(background=False)` or an env variable force it? G3 shows the background default breaks single-result assumptions.
2. Root cause of "Prompt is too long" (G6) with `haiku` and a restricted `tools` list. Is it a CLI bug, a tool-search interaction, or the alias? Test with pinned full model IDs.
3. Nested slash commands (finding 1b). Test with a project skill that echoes `$ARGUMENTS`.
4. `disable-model-invocation` with the Skill tool. Re-probe on CLI 2.1.284 to confirm the docs.
5. Wall-clock deadlines. Is `asyncio.timeout` plus `client.interrupt()` enough? Does cancelling `query()` kill the subprocess cleanly?
6. Are parallel `query()` calls safe at scale? Check process count, file locks on `~/.claude`, and session file races.
7. Which `HookMatcher.timeout` default is right: 60 s or 600 s (G12)?
8. Does the Python `StopHookInput` payload include `last_assistant_message` at runtime? Log one payload.
9. Can `/goal` be set from Python without a slash prompt? Or should we write our own Stop gate? Cost and reliability of the `/goal` evaluator model are unknown.
10. Credentials: which auth source will pipeline runs use (API key or a cloud provider)? Owner decision. Record as `needs-ratification`.
11. Does `dontAsk` with scoped `Bash(...)` allow rules block compound commands (`a && b`)? See [D:permissions] "Bash rule limits". Not probed.
12. Is `structured_output` reliable together with a `Stop` block loop? [P:final4] passed once. Test more.
13. Should the pipeline use `session_store` for resume across CI workers? Depends on the run environment.
