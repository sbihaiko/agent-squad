# Triage Labels

The repository's triage labels are the label strings that exist in this issue tracker.

| Label | Meaning |
| --- | --- |
| `ready-for-agent` | Fully specified and ready for an agent. |
| `ready-for-human` | Requires human implementation. |
| `wontfix` | Will not be actioned. |

Every issue an agent creates also carries one product type label: `bug` for a defect, `enhancement` for a non-defect change, or `spec` for a specification. Do not add a triage label outside this table.

The loop also reads these labels; no triage step adds them:

| Label | Meaning |
| --- | --- |
| `needs-owner` | The owner acts next. No dispatch pass takes the issue; janitor rule R adds `frozen:owner` over its column. |
| `frozen:owner` | The owner freeze. The card keeps the column the work reached; only the owner removes the label. |
| `loop-bug` | A defect in the loop itself (ADR-0146). Only a person adds it. A bare `loop-bug` halts nothing; the defect that stops dispatch carries `loop-bug:dispatch`, `loop-bug:landing` or `loop-bug:infra`, and while one of those is open every dispatch pass dispatches nothing. |
