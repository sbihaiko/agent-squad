# Triage Labels

The triage vocabulary is three label strings on this repository's issue tracker. `contrib/squad-goal/scripts/triage-labels.sh` creates and refreshes them.

| Label | Kind | Meaning |
| --- | --- | --- |
| `ready-for-agent` | Permission | The agent may take the card once it is in Todo. |
| `ready-for-human` | Holder | A person implements the card. No dispatch pass takes it. |
| `ready-for-HAL9K` | Holder | The HAL9K agent implements the card. No dispatch pass takes it. |

A permission authorizes when present. A holder holds when present, whatever else the card carries, even beside `ready-for-agent`. An absent label authorizes: a card that carries no holder and no hold is the agent's to take in Todo.

`needs-triage` holds an issue that nobody has triaged. It is a hold on its own, so an untriaged issue is never read as an unlabelled one. Removing it is the triage.

Every issue an agent creates also carries one product type label: `bug` for a defect, `enhancement` for a non-defect change, or `spec` for a specification. The triage vocabulary is the three labels in the first table; do not invent a fourth.

The holds are a floor of both dispatch lanes, the frontier pass and the bug pass. A card that carries a floor label is never a candidate, with or without `ready-for-agent`. A profile's own skip list adds labels and never removes one. The floor and lane tables below name the labels the dispatch passes read: apply a floor label to hold a card, and remove it to release the card.

| Floor label | Meaning |
| --- | --- |
| `ready-for-human` | A holder: a person implements the card. |
| `ready-for-HAL9K` | A holder: the HAL9K agent implements the card. |
| `needs-triage` | A hold: nobody has triaged the issue. |
| `frozen:owner` | The owner freeze. The card keeps the column the work reached; only the owner removes the label. |
| `frozen:dependency` | The card waits on a dependency outside the loop. |

Each lane skips more labels of its own:

| Lane | Label | Meaning |
| --- | --- | --- |
| frontier | `spec` | A specification is a document, never work (ADR-0142). |
| frontier | `needs-ratification` | The card waits on the owner's ratification. |
| frontier | `needs-owner` | The owner acts next. Janitor rule R adds `frozen:owner` over its column. |
| frontier | `needs-info` | The card waits on an answer. |
| frontier | `wontfix` | Will not be actioned. |
| frontier | `bug`, `debt`, `run-parent` | Units that belong to their own lanes. |
| bug | `no-auto` | The bug pass does not take the issue. |
| bug | `question` | The issue asks a question. The bug pass does not take it. |
| bug | `needs-owner`, `needs-info`, `wontfix` | As in the frontier. |

The loop also reads these labels; no triage step adds them:

| Label | Meaning |
| --- | --- |
| `loop-bug` | A defect in the loop itself (ADR-0146). Only a person adds it. While one is open, bare or prefixed (`loop-bug:dispatch`, `loop-bug:landing`, `loop-bug:infra`), the loop drains and stays off; it starts again when none is open. |
