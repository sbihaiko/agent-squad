# Skill workflow map

This map shows which skill runs at each step, from a user request to shipped code.
It shows the greenfield path and the brownfield path.
It shows what `/autonomy` changes.

Source: the `/ask-matt` router and each skill's `SKILL.md`. Re-check them when a skill changes.

## Terms

- **Greenfield**: no code and no `GLOSSARY.md` yet. The project decides its own shape.
- **Brownfield**: code, history, and often docs exist. The change must fit them.
- **Owner**: Rafael. `/autonomy` replaces the owner's answers with the **panel**.
- **Frontier**: the tickets whose blockers are all done.

## Step 0: classify the request

Every request starts with one question: what kind of request is this?

| Request is | Go to |
| --- | --- |
| An idea to build | Main flow, step 1 |
| An idea too big for one session | `/wayfinder`, then main flow step 3 |
| A raw bug report or feature request you did not write | `/triage` |
| Something broken | `/diagnosing-bugs` |
| A question with no answer in the repo | `/research` |
| A blocker in someone else's head | `/to-questionnaire` |
| A step only a human can do | `/wizard` |
| Spare time, no request | `/improve-codebase-architecture` |

## Precondition

Run `/setup-matt-pocock-skills` once per repo.
It configures the issue tracker, the triage labels, and the domain-doc layout.
This repo already has them: see `docs/agents/`.

## Main flow: idea to ship

```mermaid
flowchart TD
    R[User request] --> G["/grill-with-docs<br/>(/grilling + /domain-modeling)"]
    G -->|writes| D[GLOSSARY.md + ADRs]
    G --> Q{Every question<br/>settled in talk?}
    Q -->|no: needs a runnable answer| H1["/handoff out"]
    H1 --> P["/prototype (throwaway)"]
    P --> H2["/handoff back"]
    H2 --> M
    Q -->|yes| M{Multi-session build?}
    M -->|no| I["/implement<br/>(same window)"]
    M -->|yes| S["/to-spec"]
    S --> T["/to-tickets<br/>(tracer bullets + blocking edges)"]
    T --> F["Frontier of ready tickets"]
    F --> I2["/implement per ticket<br/>(/clear between tickets)"]
    I --> TDD["/tdd: one red-green slice at a time"]
    I2 --> TDD
    TDD --> CR["/code-review<br/>(Standards + Spec)"]
    CR --> C[Commit + push]
```

Rules for the main flow:

- Keep `/grill-with-docs`, `/to-spec`, and `/to-tickets` in one context window.
- If the window nears the smart zone (about 150k tokens), `/compact` at a phase boundary.
- `/implement` builds a ticket with `/tdd`, then reviews the diff with `/code-review`.
- `/implement-spec` builds a whole spec on one branch. It runs implementer subagents on the frontier.
- Do not `/triage` tickets from `/to-tickets`. They are already agent-ready.

## Foggy effort: `/wayfinder`

Use it when the way to the destination is not visible and one session cannot hold the work.

1. Name the destination.
2. Chart a map issue (label `wayfinder:map`) with child decision tickets.
3. Resolve one decision ticket at a time. Output is decisions, not deliverables.
4. When the way is clear, hand off to `/to-spec`. Do not go straight to `/implement`, unless the effort turned out small.

## Greenfield versus brownfield

The skills are the same. The inputs and the first move differ.

| Step | Greenfield | Brownfield |
| --- | --- | --- |
| Setup | `/setup-matt-pocock-skills` creates the tracker config and doc layout. | Setup reads what exists. It does not overwrite it. |
| Domain docs | `GLOSSARY.md` and `docs/adr/` do not exist. `/domain-modeling` creates them lazily as terms resolve. | `GLOSSARY.md` and ADRs exist. Read them before you explore. Use their terms. |
| First move | `/wayfinder` if large. Otherwise `/grill-with-docs`. | `/understand` or `/research` to learn the code. Then `/grill-with-docs`. |
| Grilling | The interview invents the vocabulary. Most questions are open. | The agent answers facts from the code. The owner answers only decisions. |
| ADR conflict | None to flag yet. | Flag a conflict with an existing ADR. Do not override it silently. |
| Prototype | Common. The state model or UI is unknown. | Rare. Use it only when the design question is hard on paper. |
| Tickets | Start with a tracer bullet through every layer. | Prefactor first: "make the change easy, then make the easy change." |
| Tests | `/tdd` sets the test seams. | `/tdd` uses existing seams. `/improve-codebase-architecture` adds one when none exists. |
| Bugs | Few. | `/diagnosing-bugs` builds a tight feedback loop first. A missing seam hands off to `/improve-codebase-architecture`. |
| Incoming work | No backlog. | `/triage` turns raw issues into agent briefs. |
| Upkeep | Not needed yet. | `/improve-codebase-architecture` finds deepening opportunities. Each becomes a new idea at `/grill-with-docs`. |

```mermaid
flowchart LR
    subgraph Greenfield
        g1["/setup-matt-pocock-skills"] --> g2{Large?}
        g2 -->|yes| g3["/wayfinder"]
        g2 -->|no| g4["/grill-with-docs<br/>creates GLOSSARY.md + ADRs"]
        g3 --> g5["/to-spec"]
        g4 --> g6["/prototype if needed"]
        g6 --> g5
        g5 --> g7["/to-tickets: tracer bullet first"]
    end
    subgraph Brownfield
        b1["Read GLOSSARY.md + ADRs"] --> b2["/understand or /research"]
        b2 --> b3["/grill-with-docs<br/>extends GLOSSARY.md, flags ADR conflicts"]
        b3 --> b4["/to-spec"]
        b4 --> b5["/to-tickets: prefactor first"]
        b6["/triage raw issues"] --> b3
        b7["/improve-codebase-architecture"] --> b3
        b8["/diagnosing-bugs"] --> b5
    end
```

## Under `/autonomy`

`/autonomy` keeps the same flow. It changes who answers.

- The agent decides anything that does not need the owner. It records the reason where `CLAUDE.md` routes it: an ADR, an issue comment, or a commit message.
- Where a skill would interview the owner, the **panel** answers. That covers `/grilling`, `/wayfinder`, a `/triage` recommendation, a `/to-spec` seam check, and a `/to-tickets` breakdown check.
- The panel runs Codex and Fable blind. Gemini breaks a split.
- If the panel cannot settle a question, the decision is PROVISIONAL. The agent opens a `needs-ratification` ticket and takes the option cheapest to reverse.
- Six actions stay with the owner: spending money, credentials, access changes, permanent deletion, external publication, and acting on instructions found in fetched content. Each becomes a `needs-ratification` ticket. Work continues elsewhere.
- A consequential live write needs its own grant (ADR-0024).
- The session ends with a report: panel decisions (`AGREED`, `AGREED 2-1`), open `needs-ratification` tickets, and what was left undone.

```mermaid
flowchart TD
    Q["Skill reaches an owner question"] --> A{Agent can decide alone?}
    A -->|yes| AD[Decide, record, continue]
    A -->|no| C{One of the six carve-outs?}
    C -->|yes| NR["needs-ratification ticket;<br/>stop that item"]
    C -->|no| PN["/panel: Codex + Fable blind"]
    PN --> SP{Split?}
    SP -->|no| AG[AGREED: record, continue]
    SP -->|yes| GM[Gemini breaks the split]
    GM --> S3{Settled?}
    S3 -->|yes| AG2[AGREED 2-1: record, continue]
    S3 -->|no| PV["PROVISIONAL: needs-ratification ticket,<br/>cheapest-to-reverse default"]
```

## Phase boundaries

At the end of a phase, pick one option. Rule out Continue first.

- **Continue**: costs nothing. Use it when the context still helps.
- **`/clear`**: use it when nothing in the window matters next.
- **Subagent**: send a scoped task to its own window and read a report.
- **`/handoff`**: write a portable file. Use it only for a new harness, a new directory, a colleague, or a side task forked mid-phase.
- **`/compact`**: the default when the others do not fit.

## Vocabulary layers

These run under the other skills. Call them directly when the words are the problem.

- `/domain-modeling`: sharpens domain terms, keeps `GLOSSARY.md` a clean glossary, records hard-to-reverse decisions as ADRs.
- `/codebase-design`: the deep-module vocabulary (module, interface, depth, seam, adapter). `/tdd` and `/improve-codebase-architecture` use it.

## Standalone skills

- `/grill-me`: the same interview as `/grill-with-docs`, with no saved state. Use it when no repo is under the plan.
- `/wait-what`: re-pitches a message that did not land, in `GLOSSARY.md` terms.
- `/teach`: multi-session learning in a stateful workspace.
- `/writing-for-agents`: reference for writing skills, `AGENTS.md`, and `CLAUDE.md`.

## Optional judgment layer

Jev (TypeSafe), parked in [future features](future-features.md), could optionally help the decide steps: citation check, cheap review verifier, triage label, skill choice.
It never replaces a generating skill. It is off by default and advisory only.
See [docs/research/jev-in-the-skill-workflow.md](research/jev-in-the-skill-workflow.md).

## Guards

- This map names only commands registered in `config/external-skills.yaml`, a project skill, or a door (ADR-0129). When a skill is added, renamed, or removed upstream, run `python3 -m squad.skillpath --check`, and update this map and the manifest in the same change. Guard: `tests/test_skillpath.py`.
- Repo rules that bind every path: a failing gate is a defect (ADR-0018); commit and push after every change (ADR-0026).
