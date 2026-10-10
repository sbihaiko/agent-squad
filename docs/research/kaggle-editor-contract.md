# Kaggle workflow editor contract

This note records what `../kaggle/playground/workflow_editor.html` loads, saves, and edits. It also lists what agent-squad's graph schema needs so an editor of this kind can load it.
Answers wayfinder ticket #6.

Sources (read-only, `../kaggle`):
- `playground/workflow_editor.html` (674 lines). Cited as `E:<line>`.
- `scripts/arm_from_spec.py`, the consumer of the exported file. Cited as `S:<line>`.
- `playground/specs/` exists and is empty. No sample spec file exists. The editor code is the only definition of the shape.

## 1. Summary

- The editor is one static HTML file with no server. State lives in one JS object and in `localStorage`.
- The exported file is one JSON spec per "arm". A separate script validates it and writes the arm files.
- The graph is a tree of named agents. Four classes: one leaf class (`LlmAgent`) and three workflow classes.
- The editor is a design-time tool. It reads no run record. It only estimates cost from the spec.
- Fixed limits of the host framework are read-only constants in the editor. The user cannot edit them.

## 2. Data shape

### 2.1 Spec JSON (saved and loaded)

The export builds this object in `spec()` (E:603-612):

| Field | Type | Notes |
| --- | --- | --- |
| `spec_version` | int | Constant `1` (E:138). Import does not read it (E:669). |
| `name` | string | Arm name. Also the download file name (E:666). |
| `description` | string | Free text. |
| `root` | string | Name of the entry agent. |
| `eval` | object | Four ints: `max_tool_calls`, `max_turns`, `max_time_minutes`, `timeout_seconds` (E:233). |
| `sampling` | object | `temperature`, `top_p`, `max_output_tokens`, `thinking` (`on` or `off`), `thinking_budget` (E:234). |
| `agents` | array | Flat list. Order matters for display only. |

Each agent (E:605-611):

- Always: `name`, `class`. `description` only when non-empty.
- `LlmAgent` adds: `tools[]`, `skills[]`, `agent_tools[]`, `call_budget`, `include_contents` (`default` or `none`), `instruction`. Optional: `output_key`, `max_output_tokens`, `thinking` (only when not `inherit`).
- Workflow classes (`SequentialAgent`, `LoopAgent`, `ParallelAgent`) add: `children[]` (ordered agent names). `LoopAgent` also adds `max_iterations`.

Edges are not stored as edge objects. They are name references:
- `children[]` gives the parent-child edge. Order in a Sequential agent gives the run order.
- `agent_tools[]` gives an LLM agent calling another agent as a tool.
- State edges are implicit: `output_key` of one agent, and `{key}` or `{key?}` inside another agent's `instruction` (E:288, E:336-350).

The `instruction` is text, or `@include <repo path>` to copy a file (E:570, S:65-73).

### 2.2 Internal state versus file

The in-memory state uses camelCase (`cls`, `agentTools`, `outputKey`, `budget`) and adds two fields. The export renames them and drops both (E:604, E:231-232):
- `layout`: node positions `{name: {x, y}}` from dragging. Not exported. Lost on import (E:670).
- `_preset`: which preset started the session. Not exported.

### 2.3 Load paths

- **Boot.** Reads `localStorage['armEditor']`. Accepts it if it has `agents`; otherwise loads the `pipeline_v4` preset (E:277-280).
- **Presets.** Five factory functions: `base_v3`, `base_v3_compact`, `pipeline_v4`, `locator_tool`, `blank` (E:236-272). A preset also becomes the `baseline` for the diff in the prompt output (E:282, E:618-631).
- **Import.** A file picker for `.json` (E:667-670). It maps snake_case back to camelCase, fills defaults for missing fields, and runs no schema check. A bad file throws in `JSON.parse` or in `.map`. After import the analysis panel reports problems (E:651).

### 2.4 Save paths

- **Autosave.** `save()` writes the whole state, with `layout`, to `localStorage` on every edit (E:281, E:652).
- **Download spec.** Blob of `JSON.stringify(spec(), null, 2)` named `<name>.json` (E:666).
- **Copy.** Copies the visible bottom pane text (E:665).
- **Prompt tab.** A text brief for an agent. It has the intent, a diff against the preset, the shape, the analysis errors, next steps, and the spec in a fenced JSON block (E:613-639).

## 3. Controls

Left panel (E:63-91):
- Preset buttons.
- Arm: `name`, `description`.
- Harness budget: four number inputs with min and max.
- Sampling: four number inputs and a `reasoning` select.
- Framework block: read-only text.
- Agent list with `+ agent`.

Agent card (E:547-600). Header buttons: move up, move down, make root, delete (delete refuses the last agent; references to a deleted or renamed agent are rewritten, E:578, E:594). Clicking the header selects the card. Body fields:
- Shared: `name`, `class` select, `description`.
- `LlmAgent`: checkboxes for `tools`, `skills`, `agent_tools` (any other agent); numbers for `call_budget` and `max_output_tokens`; text `output_key`; selects `include_contents` and `thinking`; instruction textarea.
- Workflow classes: a comma-separated `children` text field; `max_iterations` for `LoopAgent`.

Graph (E:93-101, E:406-507):
- SVG view with auto layout by tree depth.
- Drag a node to move it. Drag the background to pan. Wheel to zoom. Double-click the background or press Expand to enlarge. Esc collapses. Fit and Reset layout buttons.
- Click a node to select its card. The graph does not create or delete edges. Edges change only through the card fields.
- Edge kinds: `child`, `tool` (dashed green), `seq` (blue, labeled with the previous agent's `output_key`), `loop` (dashed orange) (E:416-418).
- Node text: name, calls and context mode, tool chips, `output_key`. A green border marks a node that holds `submit_patch` (E:454).

Bottom pane (E:103-114): tabs `Prompt` and `Spec JSON`, `Import spec...`, `Download spec`, `Copy`, and a drag splitter (E:474-480).

Right panel: stat counters, per-agent call and prompt-size bars, and the check list (E:640-658).

## 4. Editable versus read-only

Editable (bound to inputs): everything in section 2.1 except `spec_version`, and except the fixed sets below. Also node position, which is view-only state.

Read-only in the editor:
- The `FW` constants (E:127-133): context window, compaction settings, nudges, output and thinking ranges, agent, depth, loop, and instruction limits, tool output caps, time allowance, model name. Shown as text (E:511-532). Used only for checks. The consumer script reads the same limits from the installed harness, not from a copy (S:76-80).
- The tool list (`TOOLS`, E:117-124) and skill list (`SKILLS`, E:125). Closed sets. The user can pick but cannot add.
- The class list (`CLASSES`, E:137).
- `spec_version`.
- Derived numbers: planned calls, turn estimate, prompt estimates, and the check messages.

Soft constraints: the editor allows any value and reports a check error instead of blocking. Examples: a name that is not a Python identifier, a missing child, a cycle, no `submit_patch`, a `{key}` read before write, planned calls over `max_tool_calls` (E:293-384). The editor never blocks a download.

## 5. Validation (design-time)

The `analyse()` function (E:291-404) walks the tree from `root` in run order and reports at four levels: `err`, `warn`, `info`, `ok`. It checks:
- Reference integrity, unique identifier names, and cycles.
- A budget sum with loop multipliers against `max_tool_calls` and `max_turns`.
- A reachable agent that can submit the result.
- State keys: read before write, duplicate writers, unused writers.
- Framework ranges, and the time allowance.
- Prompt size estimates against the context window.

`arm_from_spec.py` repeats the structural checks as hard refusals before it writes files (S:11-21, S:125-198). The editor and the script hold two copies of the same rules. The editor copy is advisory and the script copy is authoritative.

## 6. Export of config

The editor does not write config files. The path is:

1. Download `<name>.json` and save it as `playground/specs/<name>.json` (E:636).
2. `scripts/arm_from_spec.py <spec> [--force] [--build]` validates it and writes `agent.yaml`, `sub_agents/<name>.yaml`, `prompts/<name>.md`, `configs/sampling*.yaml`, `eval_config.yaml`, and copies skills (S:1-6, S:23).
3. `call_budget` is a plan only. The harness has one counter for the whole task (S:8-9).

## 7. What agent-squad's graph schema needs

The editor reads no run record and has no notion of adapting a graph at run time. These points follow from the code above. They are inputs for the schema decision, not decisions.

Needed to load in an editor of this kind:
1. **One JSON document with a version field** and a root pointer. The kaggle editor ignores `spec_version` on import (E:669). A new editor should check it.
2. **A flat list of nodes, each with a unique identifier-safe `name`**, and edges as name references. This matches the fields the editor rewrites on rename and delete (E:578, E:594).
3. **A closed `class` (node kind) enum** that the editor can render as a card form. Each kind needs its own field set, as `LlmAgent` and the workflow classes differ (E:561-574).
4. **Ordered child lists** for sequence, plus a separate reference list for "call as a tool". The editor draws these as different edges (E:416-418).
5. **Named state keys**: a writer field and a reader syntax, so a static check can prove read-after-write (E:336-350).
6. **Closed registries** (tools, skills, gate ids) that the editor can offer as checkboxes. The editor holds them in a constant. agent-squad should serve them from one source, so editor and validator cannot drift.
7. **A separate read-only block for fixed limits**, kept outside the editable fields. For agent-squad, gates never adapt at run time. The schema should mark gate fields read-only so the editor shows them as text, as it shows `FW` (E:511-532).
8. **Layout kept out of the spec.** The editor stores positions apart from the spec and drops them on export (E:604). Keep the same split so a graph diff shows no layout noise.
9. **Numeric budgets with min and max** so the editor can bound inputs and sum them along the run order, including loop multipliers (E:314, E:320-327).
10. **Defaults for every optional field**, because import fills missing fields silently (E:669). The schema should say what each default is.

Gaps to decide (the editor has no answer):
- **Run-record events.** The editor reads none. The map lists "run-record events it reads" as open. The kaggle editor gives no shape to copy. The estimate panel is the closest part: it derives numbers from the spec only.
- **Adapted topology.** The editor edits one static graph. It has no field for "the model proposed this change" or "code validated it". The schema needs a way to mark a graph or a change as proposed, validated, or applied. See the map decision on issue #2.
- **Import validation.** The editor accepts a file with no schema check. A new editor should validate against the machine-readable schema on import and refuse a bad file with a message.
- **Two copies of the rules.** Editor checks (E:291-404) and script checks (S:125-198) duplicate each other. One schema plus one rule set should serve both.
- **Edge editing.** Edges change only through card fields. The graph cannot draw an edge. A later build may want that. The schema does not depend on it.
