---
name: to-spec
description: "Turn the current conversation into a spec and publish it to the project issue tracker: no interview, just synthesis of what you've already discussed."
disable-model-invocation: true
---

This skill takes the current conversation context and codebase understanding and produces a spec. Do NOT interview the user; just synthesize what you already know.

The issue tracker and triage label vocabulary should have been provided to you. If not, tell the user to run `/setup-matt-pocock-skills`.

## Process

1. Explore the repo to understand the current state of the codebase, if you haven't already. Use the project's domain glossary vocabulary throughout the spec, and respect any ADRs in the area you're touching.

2. Sketch out the seams at which you're going to test the feature. Existing seams should be preferred to new ones. Use the highest seam possible. If new seams are needed, propose them at the highest point you can. The fewer seams across the codebase, the better - the ideal number is one.

Check with the user that these seams match their expectations.

3. Write the spec using the template below, then publish it to the project issue tracker. Apply the `spec` label and no `ready-for-agent` label: a spec is never work, its tickets are, and they carry `ready-for-agent`. The Status-then-label order of the to-tickets publish applies to tickets only: a spec never takes the triage label, so it has no order to keep. Where the tracker has a board, create the spec's card in **Backlog** (see the next paragraph for the project). The owner starts the spec by moving its card to Todo, and its tickets in Backlog follow it there. The loop never moves a card into Backlog or Todo on its own.

   **Set the project to "Squad Board" when you create the issue.** On GitHub the card is the issue's project item, and the board is the project titled `Squad Board` in the repository's owner (the wellzesta org for wz-zesty-agent), never a project looked up by number or by any other title:
   ```sh
   OWNER=${SG_OWNER:-$(gh repo view --json owner --jq .owner.login)}
   NUM=$(gh project list --owner "$OWNER" --format json --jq '.projects[]|select(.title=="Squad Board").number')   # exactly one number, or stop
   gh project item-add "$NUM" --owner "$OWNER" --url "$ISSUE_URL" --format json --jq .id   # the item id; then set its Status to Backlog (a bug's to Todo) with gh project item-edit
   ```
   Read the project back (`gh issue view <n> --json projectItems`): it must list `Squad Board`. Read the Status back too: the card must read Backlog (a bug's Todo); a new card can read late, so read again before you report it. Where the repository has no `Squad Board` project, say so to the owner instead of guessing another project.

   **Verify the spec label before you report the spec published.** A spec without it is invisible to the squad hub's Specs filter and can be dispatched as work.
   - Before creating the issue, make sure the label exists (on GitHub: `gh label create spec --color 5319E7 --description "A spec: never work itself; its sub-issue tickets are" --force`).
   - After creating it, read the labels back (`gh issue view <n> --json labels`). They must include `spec` and must not include `ready-for-agent`. Drop `needs-triage`: a published spec is triaged.
   - A sibling spec (one that another spec depends on) is a spec too and gets the same label.

A spec is a document, never work. Its breakdown into tickets (`/to-tickets`) needs the owner's approval. The autonomy panel never approves a breakdown unless the owner asked for it in the session; with the owner away, the breakdown waits.

<spec-template>

## Problem Statement

The problem that the user is facing, from the user's perspective.

## Solution

The solution to the problem, from the user's perspective.

## User Stories

A LONG, numbered list of user stories. Each user story should be in the format of:

1. As an <actor>, I want a <feature>, so that <benefit>

<user-story-example>
1. As a mobile bank customer, I want to see balance on my accounts, so that I can make better informed decisions about my spending
</user-story-example>

This list of user stories should be extremely extensive and cover all aspects of the feature.

## Implementation Decisions

A list of implementation decisions that were made. This can include:

- The modules that will be built/modified
- The interfaces of those modules that will be modified
- Technical clarifications from the developer
- Architectural decisions
- Schema changes
- API contracts
- Specific interactions

Do NOT include specific file paths or code snippets. They may end up being outdated very quickly.

Exception: if a prototype produced a snippet that encodes a decision more precisely than prose can (state machine, reducer, schema, type shape), inline it within the relevant decision and note briefly that it came from a prototype. Trim to the decision-rich parts, not a working demo, just the important bits.

## Testing Decisions

A list of testing decisions that were made. Include:

- A description of what makes a good test (only test external behavior, not implementation details)
- Which modules will be tested
- Prior art for the tests (i.e. similar types of tests in the codebase)

## Out of Scope

A description of the things that are out of scope for this spec.

## Further Notes

Any further notes about the feature.

</spec-template>

## This repository's rules for a spec

This section is appended by `scripts/build_vendor_skills.py` from `config/vendor-skills-patches/to-spec.md`.
Edit the patch file, never the vendored copy: a rebuild writes it again.

**Publishing ends at the kanban.** Creating the issue is half the publish. Before you report the spec published,
its card stands on the Squad Board in **Backlog**, and one read of the issue names both the label and the column:
`gh issue view <n> --json labels,projectItems`. A card a later read does not carry is read again — a new card reads
late — and a spec whose project is not `Squad Board` is reported to the owner, never guessed onto another project.
A spec with sub-issues takes its column from them as a fold over the children; a spec with none stays where the
publish put it.

## This repository's rules for a spec

This section is appended by `scripts/build_vendor_skills.py` from `config/vendor-skills-patches/to-spec.md`.
Edit the patch file, never the vendored copy: a rebuild writes it again.

**The birth Status and Priority.** A spec is born in Backlog with Priority low. Create the issue with the label
`frozen:not-startable` set, which is the publish hold. Set the card to Status Backlog. Set its Priority through the board
door, `board.sh --priority <owner> <project> <n> low`, which writes only a changed value and reads it back. Release the
publish hold only after the Status and the Priority both read back.

**Publishing ends at the kanban.** Creating the issue is half the publish. Before you report the spec published,
its card stands on the Squad Board in **Backlog**, and one read of the issue names both the label and the column:
`gh issue view <n> --json labels,projectItems`. A card a later read does not carry is read again — a new card reads
late — and a spec whose project is not `Squad Board` is reported to the owner, never guessed onto another project.
A spec with sub-issues takes its column from them as a fold over the children; a spec with none stays where the
publish put it.

**Write it in ASD-STE100, at two levels.** Strict STE100 in acceptance criteria, numbered steps, and a bug's Observed
and Expected sections: one fact or one instruction per sentence, at most 20 words in an instruction and 25 in a
description, active voice, and an uncertainty in its own sentence. Light STE100 in the reasoning (Problem Statement,
Solution, Further Notes, context): one idea per sentence with no word limit, so that a cause and its effect stay
together. At both levels: one term for one concept, no noun stack of more than three words, a title of at most 12
words. Domain terms are technical names, which STE100 allows; the vocabulary is not limited to the STE100 dictionary.

**Run the writing check before the publish.** Write the body and the title to files, then run
`python3 scripts/check_ste.py --title "<title>" <body-file>` from the repository root. It prints one line per finding
(section, kind, detail, sentence) and exits 0: it warns and never blocks the publish. Show the findings with the draft,
and let the writer fix or keep each one before the publish.

**Native edges.** The upstream to-spec text names no blocking edge, so this section overrides nothing. A spec writes no blocking edge: it is a document, never work (ADR-0142), and its sub-issue link is its only relation to its tickets. The tickets that a spec publishes take their native edges through `python3 contrib/squad-goal/scripts/edge_write.py`, as the to-tickets section states.
