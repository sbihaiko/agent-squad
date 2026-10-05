# Role to command map

This map names the tools and controls each agent role in `prompts/` should reach. It is a proposal. `config/agent-defaults.yaml` does not use it yet (ticket #46).
Source: [agent-sdk-command-map.md](agent-sdk-command-map.md) sections 2 and 3, and [claude-agent-sdk.md](claude-agent-sdk.md) sections 9 and 16. Skills per role are in [role-skill-map.md](role-skill-map.md).
Permission mode is `bypassPermissions`, so `allowed_tools` restricts nothing. A role gets a tool by omission from `disallowed_tools`.

## Tool table

`R` read, `E` edit and write, `B` Bash, `L` LSP, `S` Skill. `-` denied.

| Role | Read, Grep, Glob | Edit, Write | Bash | LSP | Skill | Why |
|---|---|---|---|---|---|---|
| `scout` | yes | - | yes | yes | - | Reads code and runs read-only commands (`git log`, test lists). |
| `spec_draft` | yes | - | yes | yes | - | Each acceptance command must run, so Bash is needed. It must not edit. |
| `spec_proxy` | yes | - | yes | - | - | Check 1 runs each command to see it fail before the change. |
| `decompose` | yes | - | - | yes | - | Reads files to assign them to items. No run needed. |
| `plan_proxy` | yes | - | - | - | - | Four mechanical checks on given text. |
| `actor` | yes | yes | yes | yes | `tdd` | Implements and runs tests. |
| `critic` | yes | - | yes | yes | none | Runs the tests near the diff. |
| `visual_actor` | yes | - | yes | - | `ego-browser` | Starts the app and saves a capture through Bash. |
| `visual_critic` | Read only | - | - | - | - | Opens capture files. No Grep needed. |
| `audit` | yes | - | - | yes | `codebase-design` | Reads only. |
| `router` | yes | - | - | - | the given skill | Runs one skill, then returns `spawns`. |

## Denied for every role

| Tool | Reason |
|---|---|
| `WebFetch`, `WebSearch` | Fetched text is untrusted. Acting on it is owner-only. |
| `Task` | A subagent is never a node (GLOSSARY.md). A background subagent can end the call early (G3). Its spend is hard to cap. |
| `SendMessage`, `ListAgents`, `TaskStop` | Agent-to-agent control belongs to the engine. |
| `CronCreate`, `CronDelete`, `CronList`, `ScheduleWakeup`, `RemoteTrigger` | Scheduling and remote triggers leave the run. No call may outlive its deadline. |
| `EnterWorktree`, `ExitWorktree` | The workspace is declared on the node and created by an action node. |
| `Workflow`, `ReportFindings`, `NotebookEdit` | Not part of any role. |
| `ToolSearch` | It loads deferred tools. No MCP server is configured for runs. |

## Decided: `critic` and `Task` (panel 2026-09-30, AGREED)

Deny `Task` and `Agent` for every role. Drop `code-review` from `critic` and keep its prompt. The text below is the question as asked.

The `code-review` skill runs two sub-agents, so it needs `Task`. Denying `Task` breaks that skill. Allowing it for `critic` accepts the G3 risk and adds subagent spend to the cap. Options: allow `Task` on `critic` only, or drop `code-review` from `critic` and keep its prompt.

## Engine-side controls

These are client methods the engine uses. An agent never calls them.

| Method | Use |
|---|---|
| `get_context_usage` | Record context use at each call end. It gives data for the compaction log. |
| `interrupt`, `stop_task` | Enforce the per-call deadline without waiting for the turn. |
| `rewind_files` | Revert an actor's files when a scope or security gate fails. Needs `enable_file_checkpointing: true` on `actor`. |
| `set_model` | Not needed. A model change applies at the next entry as a new call (ADR-0103 rule 6). |

## Built-in commands

The 30 CLI built-ins ([agent-sdk-command-map.md](agent-sdk-command-map.md) section 1.1) are not skills. The `Skill` tool cannot run them. Only the engine runs one, by starting a prompt with `/name`. So this table lists what the engine may send to a role, not what the role may call.

| Role | Built-in | Use | Status |
|---|---|---|---|
| `actor` | `goal` | Sets the done condition for the call. It ran in the probe. It loops until the goal holds, so it burns turns when unmet (G5). The turn and spend caps stay on. | Ran in a probe; effect on a real item untested. |
| `critic` | `security-review` | Reviews the pending change for security. It ran in the probe. It overlaps `code-review`. | Ran in a probe; output shape unread. |
| all | `context` | Reads context use. The client method `get_context_usage` does the same in code. Use the method. | Not needed. |
| all | `model`, `effort` | Change settings. The engine sets both per call in options. | Not needed. |
| all | `reload-skills`, `reload-plugins` | Refresh skills after the vendored plugin changes. Needed only in a long-lived session. | Not needed: each call is fresh. |
| all | `compact`, `autocompact`, `clear` | `compact` needs prior history. A node has none. Auto-compact is on already. | Not useful. |

No other built-in fits a role. `init` writes a `CLAUDE.md`, `team-onboarding` and `insights` serve people, and `usage`, `usage-credits` and `extra-usage` read account data. Internal and terminal commands (`heapdump`, `color`, `focus`) do nothing for a run.

`verbatim_prompts: false` must stay on for any prompt that starts with `/name`. With it on, the dispatch is off. Keep it off only for prompts that hold no untrusted text.

## Bundled skills worth a look

`verify`, `simplify`, `debug` and `run` are bundled skills. Their bodies were not read. By name, `verify` fits `actor` and `critic`, and `simplify` fits `critic`. Unverified; do not pin them before reading them.

## Not verified

- `Grep` and `Glob` are absent from the observed list of 23 tools, yet the research baseline and the live subject run use them. The probe may list them under another name. Re-run `scripts/archive/probe_commands.py`.
- No tool list was run. Nothing here was tested with a model.
- Bash inside a read-only role can still write files. Only `disallowed_tools` with a scoped rule such as `Bash(rm *)` or a `PreToolUse` hook narrows it.

## Guard

This map lists roles in `prompts/`. When a role file is added or removed, update this map in the same change. Verify with `ls prompts`.
