---
name: to-tickets
description: Break a plan, spec, or the current conversation into a set of tracer-bullet tickets, each declaring its blocking edges, published to the configured tracker (edges as text in one file per ticket locally, or native blocking links on a real tracker).
disable-model-invocation: true
---

# To Tickets

Break a plan, spec, or conversation into a set of **tickets**: tracer-bullet vertical slices, each declaring the tickets that **block** it.

The issue tracker and triage label vocabulary should have been provided to you. If not, tell the user to run `/setup-matt-pocock-skills`.

## Process

### 1. Gather context

Work from whatever is already in the conversation context. If the user passes a reference (a spec path, an issue number or URL) as an argument, fetch it and read its full body and comments.

### 2. Explore the codebase (optional)

If you have not already explored the codebase, do so to understand the current state of the code. Ticket titles and descriptions should use the project's domain glossary vocabulary, and respect ADRs in the area you're touching.

Look for opportunities to prefactor the code to make the implementation easier. "Make the change easy, then make the easy change."

### 3. Draft vertical slices

Break the work into **tracer bullet** tickets.

<vertical-slice-rules>

- Each slice cuts a narrow but COMPLETE path through every layer (schema, API, UI, tests): vertical, NOT a horizontal slice of one layer
- A completed slice is demoable or verifiable on its own
- Each slice is sized to fit in a single fresh context window
- Any prefactoring should be done first

</vertical-slice-rules>

Give each ticket its **blocking edges**: the other tickets that must complete before it can start. A ticket with no blockers can start immediately.

**Wide refactors are the exception to vertical slicing.** A **wide refactor** is one mechanical change (rename a column, retype a shared symbol) whose **blast radius** fans across the whole codebase, so a single edit breaks thousands of call sites at once and no vertical slice can land green. Don't force it into a tracer bullet; sequence it as **expand–contract**. First expand: add the new form beside the old so nothing breaks. Then migrate the call sites over in batches sized by blast radius (per package, per directory), each batch its own ticket blocked by the expand, keeping CI green batch to batch because the old form still exists. Finally contract: delete the old form once no caller remains, in a ticket blocked by every migrate batch. When even the batches can't stay green alone, keep the sequence but let them share an integration branch that all block a final integrate-and-verify ticket; green is promised only there.

### 4. Quiz the user

Present the proposed breakdown as a numbered list. For each ticket, show:

- **Title**: short descriptive name
- **Blocked by**: which other tickets (if any) must complete first
- **What it delivers**: the end-to-end behaviour this ticket makes work

Ask the user:

- Does the granularity feel right? (too coarse / too fine)
- Are the blocking edges correct: does each ticket only depend on tickets that genuinely gate it?
- Should any tickets be merged or split further?

Iterate until the user approves the breakdown.

### 5. Publish the tickets to the configured tracker

Publish the approved tickets. **How** depends on the tracker `/setup-matt-pocock-skills` configured; the tickets are the same either way, only the shape of the blocking edges changes:

- **Local files** → write one file per ticket under `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01` in dependency order (blockers first). Each file's "Blocked by" lists the numbers/titles it depends on. Use the per-ticket file template below: one ticket per file, never a single combined file.
- **A real issue tracker (GitHub, Linear, …)** → publish one issue per ticket in dependency order (blockers first) so each ticket's blocking edges can reference real identifiers. Use the platform's native blocking relationship where it has one; otherwise set each ticket's "Blocked by" to the blocking issues. If the source was an existing issue, make each ticket its sub-issue (tracker doc's operation). Apply the `ready-for-agent` triage label unless instructed otherwise; the tickets are agent-grabbable by construction.

Work the **frontier**: any ticket whose blockers are all done. For a purely linear chain that means top to bottom.

Do NOT close or modify any parent issue.

<local-ticket-template>

# <NN>: <Ticket title>

**What to build:** the end-to-end behaviour this ticket makes work, from the user's perspective, not a layer-by-layer implementation list.

**Blocked by:** the numbers/titles of the tickets that gate this one, or "None (can start immediately)".

**Status:** ready-for-agent

- [ ] Acceptance criterion 1
- [ ] Acceptance criterion 2

</local-ticket-template>

<issue-template>

## Parent

A reference to the parent issue on the tracker (if the source was an existing issue, otherwise omit this section).

## What to build

The end-to-end behaviour this ticket makes work, from the user's perspective, not layer-by-layer implementation.

## Acceptance criteria

- [ ] Criterion 1
- [ ] Criterion 2

## Blocked by

- A reference to each blocking ticket, or "None (can start immediately)". Omit this section when blockers were set as native edges.

</issue-template>

In either form, avoid specific file paths or code snippets: they go stale fast. Exception: if a prototype produced a snippet that encodes a decision more precisely than prose can (state machine, reducer, schema, type shape), inline it and note briefly that it came from a prototype. Trim to the decision-rich parts, not a working demo, just the important bits.

## This repository's rules for tickets

This section is appended by `scripts/build_vendor_skills.py` from `config/vendor-skills-patches/to-tickets.md`.
Edit the patch file, never the vendored copy: a rebuild writes it again.

**The title of a spec's child.** A child of a spec carries the spec's number as the first words of its title, written
`[SPEC <n>] <title>` (for example `[SPEC 117] Every refusal has one shape: a closed set in code`). The prefix is a
reading aid: it never replaces the parent relation, and the sub-issue link stays the fact. A child whose parent is not
a spec (a plan, a conversation) carries no prefix, and a prefix names the spec's number only, never a wave, a kind or a
column.

**Publish order, per ticket.** A new card is Todo the moment it joins the board, and Todo plus `ready-for-agent` is the
dispatcher's go signal. So for each ticket: create the issue without `ready-for-agent`, then add it to the board and set
its Status (Backlog; a bug's Todo). Read the Status back; a new card can read late, so read again before you move on.
Then add `ready-for-agent` only after the Status reads back, and then link the ticket to its parent.

**The kanban is the last step, not a side effect.** Publishing a ticket is not done when the issue exists: it is done
when the card stands on the Squad Board. After the last ticket, read every new card back in one pass
(`gh issue view <n> --json labels,projectItems` for each) and report the tickets by number and column. A card a read
does not carry is read again before you call it missing. The spec's own card follows its children as a fold over them,
so a publish that leaves the spec in Backlog with its children in Backlog is correct, never a spec left behind.

## This repository's rules for tickets

This section is appended by `scripts/build_vendor_skills.py` from `config/vendor-skills-patches/to-tickets.md`.
Edit the patch file, never the vendored copy: a rebuild writes it again.

**The title of a spec's child.** A child of a spec carries the spec's number as the first words of its title, written
`[SPEC <n>] <title>` (for example `[SPEC 117] Every refusal has one shape: a closed set in code`). The prefix is a
reading aid: it never replaces the parent relation, and the sub-issue link stays the fact. A child whose parent is not
a spec (a plan, a conversation) carries no prefix, and a prefix names the spec's number only, never a wave, a kind or a
column.

**A full Backlog refuses the publish.** A Backlog card limit holds new work out of a full Backlog. Before you add a
ticket's card, run `bash contrib/squad-goal/scripts/backlog-gate.sh ticket`. Exit 0 means the card may be added. Exit 3
prints the refusal: add no card, report that line unchanged as the reason the publish stopped, and publish no further
ticket. Any other exit means the board did not answer: add no card, and report its line too.

**Publish order, per ticket.** A card in Todo with no holder is dispatched, so a ticket is held until it is complete.
For each ticket: create the issue with the label `frozen:not-startable` set, which is the publish hold. Then add it to
the board, and set the card to Status Backlog with Priority low. Set the Priority through the board door,
`board.sh --priority <owner> <project> <n> low`, which writes only a changed value and reads it back. Read both back. A
new card can read late, so read again before you move on. Then link the ticket to its parent and read the link back. Release the publish hold only
after the Status, the Priority and the parent link all read back.

**The kanban is the last step, not a side effect.** Publishing a ticket is not done when the issue exists: it is done
when the card stands on the Squad Board. After the last ticket, read every new card back in one pass
(`gh issue view <n> --json labels,projectItems` for each) and report the tickets by number and column. A card a read
does not carry is read again before you call it missing. The spec's own card follows its children as a fold over them,
so a publish that leaves the spec in Backlog with its children in Backlog is correct, never a spec left behind.

**Write it in ASD-STE100, at two levels.** Strict STE100 in acceptance criteria, numbered steps, and a bug's Observed
and Expected sections: one fact or one instruction per sentence, at most 20 words in an instruction and 25 in a
description, active voice, and an uncertainty in its own sentence. Light STE100 in the reasoning (Problem Statement,
Solution, Further Notes, context): one idea per sentence with no word limit, so that a cause and its effect stay
together. At both levels: one term for one concept, no noun stack of more than three words, a title of at most 12
words. Domain terms are technical names, which STE100 allows; the vocabulary is not limited to the STE100 dictionary.

**Run the writing check before the publish.** Write each ticket's body and title to files, then run
`python3 scripts/check_ste.py --title "<title>" <body-file>` from the repository root, once per ticket. It prints one line
per finding (section, kind, detail, sentence) and exits 0: it warns and never blocks the publish. Show the findings with
the breakdown, and let the writer fix or keep each one before the publish.

**Native edges.** This section overrides the upstream sentence "Use the platform's native blocking relationship where it has one; otherwise set each ticket's "Blocked by" to the blocking issues." On this repository's tracker, every blocking edge is a native edge, and a "Blocked by" line in a body is never an edge. Write each edge with `python3 contrib/squad-goal/scripts/edge_write.py <owner>/<repo> <blocked>:<blocker> ...`, blockers first. The writer calls the deadlock validator before each write, refuses an invalid or unknown edge and prints its cycle path, and reads each accepted edge back. A rerun after a failed publish writes each missing edge once. This section adds no `wave:NN` label.

**An edge names its reason.** An edge is real for one of two reasons only: an order of meaning (the blocked ticket uses what the blocker delivers), or a change to the same symbol (the same function, class or rule). A shared file alone is no edge: the file guard orders two tickets that name one file, and two tickets that change different symbols of one file may run at the same time. Write no edge that a chain already gives: when C waits for B and B waits for A, C takes no edge on A. In the breakdown, show the reason of each edge beside it, and remove an edge that has none. The writer refuses an edge a chain of open tickets already gives, with the verdict `redundant` and the chain. A written edge names in `now_redundant` each older direct edge it makes redundant; remove each one. Never write an edge past the writer with a direct API call.
