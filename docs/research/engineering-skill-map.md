# `/engineering:*` skill map

This map lists the ten skills of the `engineering` plugin, what each one does, and where each one fits.
Source: each skill's `SKILL.md` in plugin `engineering` 1.2.0 (Anthropic, `knowledge-work-plugins` marketplace). Re-check when the plugin version changes.

The plugin is generic. It has no agent-squad context. Several skills overlap the repo's own skills (section 4).

## 1. How to call them

Call `/engineering:<name> <arguments>`. The prefix is the plugin name.
Only `architecture`, `code-review`, `debug`, `deploy-checklist`, `incident-response`, and `standup` take an argument hint.
`documentation`, `system-design`, `tech-debt`, and `testing-strategy` are framework skills. They fire on trigger phrases.

## 2. Skill table

| Skill | Job | Argument | Output |
| --- | --- | --- | --- |
| `architecture` | Write or evaluate an ADR. Compare options. | decision or system | ADR: context, decision, options, trade-offs, consequences, action items |
| `system-design` | Design a system, API, data model, or service boundary. | none | Design doc with diagrams, assumptions, trade-offs |
| `code-review` | Review a change for security, performance, correctness, maintainability. | PR URL, diff, or path | Issue table, suggestion table, verdict (Approve, Request Changes, Needs Discussion) |
| `debug` | Reproduce, isolate, diagnose, fix. | error or symptom | Root cause, fix, regression test suggestion |
| `testing-strategy` | Design a test plan and coverage targets. | none | Test plan: what to test, test type, targets, example cases |
| `tech-debt` | Find, group, and rank technical debt. | none | Ranked list, effort estimate, phased plan |
| `documentation` | Write a README, API doc, runbook, architecture doc, or onboarding guide. | none | Document for the named type |
| `deploy-checklist` | Check readiness before a release. | service or release | Checklist: pre-deploy, deploy, post-deploy, rollback triggers |
| `incident-response` | Run an incident from triage to postmortem. | `new`, `update`, or `postmortem` | Status update or blameless postmortem |
| `standup` | Build a daily update. | `yesterday`, `today`, or `blockers` | Yesterday, today, blockers |

## 3. Details per skill

### `architecture`

- Three modes: create an ADR, evaluate a design, design a system.
- ADR status values: Proposed, Accepted, Deprecated, Superseded.
- It points to `system-design` for requirements and scale frameworks.
- Connectors: knowledge base (find prior ADRs), project tracker (link epics, create tasks).

### `system-design`

Five steps: requirements, high-level design, deep dive, scale and reliability, trade-off analysis.
The output names what to revisit as the system grows.

### `code-review`

- Four dimensions: security, performance, correctness, maintainability.
- Security covers injection, auth flaws, secrets, deserialization, path traversal, and SSRF.
- Performance covers N+1 queries, complexity, missing indexes, unbounded loops, and leaks.
- Severity marks: critical issue or suggestion.
- Connectors: source control (pull the diff, read CI), project tracker (check the stated requirements), knowledge base (team standards).

### `debug`

Four steps: reproduce, isolate, diagnose, fix.
The skill targets root cause, not symptoms.

### `testing-strategy`

- It uses the test pyramid: many unit tests, some integration tests, few end-to-end tests.
- It sets a strategy per component type: API, data pipeline, frontend, infrastructure.
- Skip list: trivial getters and setters, framework code, one-off scripts.

### `tech-debt`

Six categories: code, architecture, test, dependency, documentation, infrastructure.
Priority = (Impact + Risk) x (6 - Effort). Each score runs 1 to 5.

### `documentation`

Five document types: README, API doc, runbook, architecture doc, onboarding guide.
Five principles: write for the reader, lead with the most useful fact, show, keep current, link and do not copy.

### `deploy-checklist`

- The checklist adapts to feature flags, database migrations, and breaking API changes.
- Connectors: source control (release diff, approvals), CI/CD (build status), monitoring (rollback thresholds).

### `incident-response`

- Four phases: triage, communicate, mitigate, postmortem.
- Severity: SEV1 (service down, immediate) to SEV4 (cosmetic, next business day).
- The postmortem is blameless. It has a timeline, five whys, and action items with owners.
- Connectors: PagerDuty, Datadog, Slack.

### `standup`

- Standalone: you state your work and it formats the update.
- With connectors: it reads commits, PRs, ticket moves, and chat.

## 4. Overlap with the repo's skills

| `/engineering:*` skill | Nearest repo or user skill | Which one to use here |
| --- | --- | --- |
| `architecture` | `/domain-modeling` (ADRs) | `/domain-modeling`. It follows `docs/adr/` and ADR-0000. |
| `system-design` | `/codebase-design`, `/grill-with-docs` | `/grill-with-docs` first. `/codebase-design` for module shape. |
| `code-review` | `/code-review` (Standards plus Spec) | `/code-review`. It checks the repo's standards and the spec. |
| `debug` | `/diagnosing-bugs`, `/systematic-debugging` | `/diagnosing-bugs`. |
| `testing-strategy` | `/tdd` | `/tdd`. |
| `documentation` | `/writing-for-agents`, ADR-0001 | Repo rules win: US English, Concise STE100. |
| `tech-debt` | `/improve-codebase-architecture` | `/improve-codebase-architecture`. |
| `deploy-checklist`, `incident-response`, `standup` | none | Use the engineering skill. |

Rule of thumb: a repo skill knows this repo's ADRs and gates. An engineering skill does not.
Output from an engineering skill must still follow ADR-0001 in this repo.

## 5. Connectors

The plugin ships an `.mcp.json` with HTTP servers. Skills use `~~category` placeholders.

| Category | Default | Alternatives |
| --- | --- | --- |
| Chat | Slack | Microsoft Teams |
| Source control | GitHub | GitLab, Bitbucket |
| Project tracker | Linear, Asana, Atlassian | Shortcut, ClickUp |
| Knowledge base | Notion | Confluence, Guru, Coda |
| Monitoring | Datadog | New Relic, Grafana, Splunk |
| Incident management | PagerDuty | Opsgenie, Incident.io, FireHydrant |
| CI/CD | none | CircleCI, GitHub Actions, Jenkins, BuildKite |

In this session each server needs OAuth first (`authenticate` tools, names like `mcp__plugin_engineering_github__authenticate`). Until then the skills run standalone.
Repo rule: connecting a service is an access change, so it is an owner-only action.

## 6. Guards

- This map lists only skills in plugin `engineering` 1.2.0. When the plugin adds, renames, or removes a skill, update this map in the same change.
- Verify with `ls ~/.claude/plugins/marketplaces/knowledge-work-plugins/engineering/skills`. The list must show ten entries.
