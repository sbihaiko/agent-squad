---
name: agent-to-tickets
description: "Agent-invocable door to the global /to-tickets skill, in this repo only. Break a plan, spec, or the current conversation into a set of tracer-bullet tickets, each declaring its blocking edges, published to the configured tracker (edges as text in one file per ticket locally, or native blocking links on a real tracker)."
---

# agent-to-tickets

The Owner lets agents run `/to-tickets` in this repo. The global skill sets `disable-model-invocation`, and a personal skill wins over a project skill of the same name, so this door has its own name.

1. Find the skill by name: run `python3 -m squad.skillpath to-tickets` from the repository root. It prints the path of `SKILL.md`: the personal copy first, else the trusted plugin's current install. Read that file. Its base directory is the folder it sits in; read its other files from there when it points at them.
   - Exit 1 means the skill was removed or renamed upstream. Do not improvise its steps. Stop that skill, open an issue that names it, and carry on with other work.
2. Follow it exactly, as if the Owner had typed `/to-tickets`, with the arguments you were given.
3. Where it would interview the Owner and the Owner is away, use the panel skill (`autonomy:panel`, from the `autonomy` plugin), as `/autonomy` says.

## Repository rules for a spec parent

These rules run after step 1 of the global skill (gather context) and before its step 3 (draft vertical slices). They add to the global skill; they never replace one of its steps.

1. **Read the parent's check comment before you draft.** When the parent is a spec, find its premise-check comment: the one whose body starts with `<!-- premise-check: spec=<n> -->`. List the spec's comments with `gh api repos/<owner>/<repo>/issues/<n>/comments`. Read its table: each premise, its state, its proof or answer, and who answered.
2. **Refuse a spec with no check comment.** Stop and name the reason: `no premise-check comment on spec <n>`. Draft nothing. Never improvise a breakdown on an unchecked spec.
3. **Refuse a check with an open question.** A row whose state is `open` is a question the owner has not answered. Stop and name each open question with what would settle it: the recommended answer or the proof the row asks for. Draft nothing. A row whose state is `refuted` is a premise a probe disproved, and the check is open again: stop, name each refuted premise, and draft nothing. Its tickets stay held until the premise is settled.
4. **Make exactly one probe ticket for each premise that needs a probe.** Each row whose state is `needs a probe` becomes one probe ticket in the proposed breakdown. The probe ticket carries the label `probe`, has no blockers, names the spec under `## Parent`, and carries one line `Premise: <premise>` that copies the row's premise exactly. The loop's close path reads that line: a probe whose acceptance command ends unmet refutes its premise with `squad premise-check <spec> --refute <premise>` and stays open, frozen to the owner. Every ticket whose work rests on that premise gets a native blocking edge from it to its probe ticket. Write each edge with `python3 contrib/squad-goal/scripts/edge_write.py <owner>/<repo> <dependent>:<probe>`: the writer calls the deadlock validator first, refuses an invalid or unknown edge with its path, and reads the accepted edge back. The check comment does not carry the `depends_on` field, so the breakdown lists each of these edges and the owner checks them at approval.
5. **List the proxy answers before the owner approves.** A row whose state is `decided` and whose "Answered by" is not the owner was answered by the panel or the `/autonomy` proxy. Before you present the breakdown for approval, list every such row, so the owner can overturn it before anything is published.
6. **The standing rules still hold.** Only the owner approves the breakdown. Nothing is published before that approval. A spec is never work (ADR-0142).
