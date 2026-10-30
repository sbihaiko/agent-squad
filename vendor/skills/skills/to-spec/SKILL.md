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

3. Write the spec using the template below, then publish it to the project issue tracker. Apply the `spec` label and no `ready-for-agent` label: a spec is never work, its tickets are, and they carry `ready-for-agent`. Where the tracker has a board, create the spec's card in **Backlog** (see the next paragraph for the project). The owner starts the spec by moving its card to Todo, and its tickets in Backlog follow it there. The loop never moves a card into Backlog or Todo on its own.

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
