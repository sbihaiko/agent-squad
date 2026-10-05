# Role to skill map

This map names the best skill for each agent role in `prompts/`. It covers roles only. It does not wire anything (ticket #45).
Source of the skill list: [agent-sdk-command-map.md](agent-sdk-command-map.md), the observed list of 98 skills on this host. Skill descriptions were read from each `SKILL.md` on 2026-09-30. Bodies were not read, so fit is judged from descriptions.

## Table

| Role | Best skill | Fit | Runner-up | Note |
| --- | --- | --- | --- | --- |
| `scout` | none | - | `understand` | `understand` builds an interactive knowledge graph. The scout writes nothing. |
| `spec_draft` | `to-spec` | good | none | Synthesis with no interview. It publishes to the tracker, so publication must be an action node, not the agent. |
| `spec_proxy` | `panel` | fair | none | The proxy stands in for the owner. The panel does that for a blocked decision. |
| `decompose` | `to-tickets` | good | none | Tracer-bullet slices with blocking edges match `work_items` and `depends_on`. |
| `plan_proxy` | `panel` | weak | none | The four checks are mechanical. Code may do them better than a skill. |
| `actor` | `tdd` | good | `implement` | The actor prompt already says test first. `implement` adds `code-review` and a commit, which the critic node owns. |
| `critic` | none (prompt only) | - | `code-review` | `code-review` runs two sub-agents, and no role may start one (panel 2026-09-30). Two reviewers would be two graph nodes. |
| `visual_actor` | `ego-browser` | good | `anthropic-skills:built-in-browser`, `chrome-browser` | Runner-up descriptions were not read: unverified. |
| `visual_critic` | none | - | `frontend-design` | `frontend-design` guides building UI, not judging a capture against a spec. |
| `audit_a` | none | - | none | No skill reads run learnings. |
| `audit_b` | `codebase-design` | fair | `improve-codebase-architecture` | `codebase-design` is model-invocable. The runner-up is not, and it opens an interview. |
| `audit_c` | none | - | none | No skill checks fit across work items. |
| `router` | `ask-matt` | good | none | It routes a request to skills. The Jev map names it the skill router. |

## Extra skill for one work type

`diagnosing-bugs` fits an `actor` whose work item is a bug fix. It forces a feedback loop before a fix. Load it on that item only.

## Who may run which skill

- `implement-spec`, `to-tickets`, `improve-codebase-architecture`, `ask-matt` and `grill-with-docs` set `disable-model-invocation`. A model cannot start them. In this repo the door skills `agent-to-tickets`, `agent-ask-matt` and `agent-improve-codebase-architecture` exist for this.
- `to-spec` and `tdd` did not show that flag.
- Whether a headless SDK node can load each skill depends on `setting_sources`. Unverified per skill.

## Rules for the graph

- A node gets one pinned skill at most. Pin it in the dispatch envelope, not in the prompt (ADR-0113).
- A skill with a side effect (`to-spec` publishes, `implement` commits) needs an `action` node or a declared workspace. A read-only role must not load it.
- A role with `none` keeps its prompt alone. Do not invent a skill to fill the column.

## Not verified

- No skill was run. Fit comes from descriptions.
- `understand`, `panel` and `ego-browser` bodies were not read.
- The Jev map reports no gain over the agent for skill choice on this roster ([jev-in-the-skill-workflow.md](jev-in-the-skill-workflow.md) section 8.1). A pinned skill may not beat the prompt. The subject run must compare both.

## Guard

This map lists roles in `prompts/`. When a role file is added or removed, update this map in the same change. Verify with `ls prompts`. Every role file except `_contract.md` and `README.md` needs a row.
