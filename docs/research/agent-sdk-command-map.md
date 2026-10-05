# Agent SDK command map

This map lists every command the Claude Agent SDK can call on this host.
Source: `scripts/archive/probe_commands.py`, run with `claude_code_version` 2.1.285, `setting_sources=["user","project"]`.
The lists come from the `init` message, so they are observed, not documented. Details: [claude-agent-sdk.md](claude-agent-sdk.md) section 9.

The SDK has four command surfaces:

1. Slash commands in a prompt (130 observed).
2. Tools the model calls (23 observed).
3. Client control methods (15).
4. Module functions (session helpers and MCP builders).

Rerun the probe after you add a skill, a plugin, or a CLI update. The lists change with the host.

## 1. Slash commands in a prompt

A prompt that starts with `/name` dispatches the command. An unknown name does not fail: the model answers that it does not exist.
`init.slash_commands` holds all 130 names. `init.skills` holds the 98 that are skills. The other 32 are not skills.
`verbatim_prompts=True` turns dispatch off.

### 1.1 CLI built-ins (30, not skills)

| Group | Commands |
| --- | --- |
| Session | `clear`, `compact`, `autocompact`, `rename`, `recap`, `context`, `goal` |
| Model and mode | `model`, `effort`, `fast`, `output-style`, `auto-mode-setup`, `config` |
| Extensions | `mcp`, `agents`, `list-agents`, `reload-plugins`, `reload-skills` |
| Project | `init`, `security-review`, `team-onboarding` |
| Usage | `usage`, `usage-credits`, `extra-usage`, `insights` |
| Terminal look | `color`, `focus` |
| Internal | `heapdump`, `__remote-workflow`, `workflow-launch-exec` |

`doctor` is a bundled skill that the init message also lists under `terminal_slash_commands`, with `color`, `focus`, and `reload-plugins`.
Those four are terminal-oriented. Whether they do anything in a headless run is unverified.

Two more non-skill commands come from the Codex plugin: `codex:rescue` and `codex:setup` (see 1.5). Together with the 30 above they make the 32.

### 1.2 Bundled skills (15)

`deep-research`, `dataviz`, `update-config`, `verify`, `debug`, `simplify`, `batch`, `fewer-permission-prompts`, `doctor`, `loop`, `schedule`, `claude-api`, `workflow-authoring`, `run`, `run-skill-generator`.

### 1.3 User skills, `~/.claude/skills` (49)

- Flow: `ask-matt`, `grill-with-docs`, `grill-me`, `grilling`, `domain-modeling`, `wayfinder`, `to-spec`, `to-tickets`, `triage`, `implement`, `implement-spec`, `tdd`, `code-review`, `prototype`, `research`, `handoff`, `claude-handoff`, `wait-what`, `to-questionnaire`, `wizard`.
- Codebase: `codebase-design`, `improve-codebase-architecture`, `understand`, `diagnosing-bugs`, `systematic-debugging`, `resolving-merge-conflicts`, `setup-matt-pocock-skills`, `setup-pre-commit`, `setup-ts-deep-modules`, `git-guardrails-claude-code`, `migrate-to-shoehorn`, `scaffold-exercises`.
- Writing: `writing-for-agents`, `writing-beats`, `writing-fragments`, `writing-shape`, `write-like-me`, `copywriting`, `ux-writing`, `proposal-writer`, `pricing`.
- Other: `design-doc-mermaid`, `frontend-design`, `ego-browser`, `find-skills`, `loop-me`, `retro`, `scikit-learn`, `teach`.

### 1.4 Project skills, `.claude/skills` (2)

`autonomy`, `panel`.

### 1.5 Plugin skills (34, namespaced `plugin:skill`)

| Plugin | Commands |
| --- | --- |
| `codex` | `codex:adversarial-review`, `codex:cancel`, `codex:rescue`, `codex:result`, `codex:review`, `codex:setup`, `codex:status`, `codex:transfer` |
| `engineering` | `engineering:architecture`, `:code-review`, `:debug`, `:deploy-checklist`, `:documentation`, `:incident-response`, `:standup`, `:system-design`, `:tech-debt`, `:testing-strategy` |
| `anthropic-skills` | `anthropic-skills:built-in-browser`, `:chrome-browser`, `:computer-use`, `:deep-research`, `:docs`, `:docx`, `:google-workspace`, `:import-memory`, `:pdf`, `:pptx`, `:skill-creator`, `:xlsx` |
| Others | `typesafe:typesafe-ai`, `frontend-design:frontend-design`, `superdesign:superdesign`, `playground:playground` |

Installed plugins with no command: `pyright-lsp`, `typescript-lsp`, `cc-plugin-agents-md`. The `design-doc-mermaid` plugin appears as a user skill.

### 1.6 Not in the list

Skills with `user-invocable: false` are absent from both lists.
Commands such as `/help` and `/cost` do not appear. They do not dispatch through the SDK, but no probe ran them: unverified.
Skills with `disable-model-invocation: true` (for example `ask-matt`, `grill-with-docs`) are in the list. Only `/name` runs them; the model cannot.

## 2. Tools the model calls (23)

`Task`, `Bash`, `Read`, `Write`, `Edit`, `NotebookEdit`, `LSP`, `WebFetch`, `WebSearch`, `Skill`, `ToolSearch`, `Workflow`, `ReportFindings`, `SendMessage`, `ListAgents`, `TaskStop`, `CronCreate`, `CronDelete`, `CronList`, `ScheduleWakeup`, `RemoteTrigger`, `EnterWorktree`, `ExitWorktree`.

`Skill` is how the model runs a skill. `Task` starts a subagent. MCP tools add `mcp__<server>__<tool>` names on top.
Restrict them with `allowed_tools`, `disallowed_tools`, and `PreToolUse` hooks.

Subagent types observed (8): `claude`, `codex:codex-rescue`, `Explore`, `general-purpose`, `panel-lens-b`, `Plan`, `statusline-setup`, `write-like-me-drafter`.

## 3. Client control methods

`ClaudeSDKClient` exposes 15 public methods. Each is a command you send to a live session.

| Method | Effect |
| --- | --- |
| `connect`, `disconnect` | Open and close the session. |
| `query` | Send a prompt or slash command. |
| `receive_messages`, `receive_response` | Read all messages, or up to the `ResultMessage`. |
| `interrupt` | Stop the current turn. |
| `stop_task` | Stop one background task. |
| `set_model` | Change the model. |
| `set_permission_mode` | Change the permission mode. |
| `get_context_usage` | Read context use (the `/context` data). |
| `get_server_info` | Read the init data. |
| `get_mcp_status`, `reconnect_mcp_server`, `toggle_mcp_server` | Inspect and control MCP servers. |
| `rewind_files` | Revert files to a message. Needs `enable_file_checkpointing=True`. |

These replace several slash commands in code: `set_model` for `/model`, `get_context_usage` for `/context`, `get_mcp_status` for `/mcp`.

## 4. Module functions

- Run: `query`.
- Sessions: `list_sessions`, `get_session_info`, `get_session_messages`, `rename_session`, `tag_session`, `delete_session`, `fork_session`.
- Subagents: `list_subagents`, `get_subagent_messages`.
- Store variants: each session function has a `*_from_store` or `*_via_store` form. `import_session_to_store` also exists.
- Custom tools: `tool`, `create_sdk_mcp_server`.
- Helper: `project_key_for_directory`.

## Guards

- `uv run scripts/archive/probe_commands.py` regenerates sections 1 to 3. Compare its output with this file after any host change.
- Unverified: whether `doctor`, `color`, `focus`, and `reload-plugins` act in a headless run, and whether `/help` and `/cost` fail.
