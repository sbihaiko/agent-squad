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

**When the parent is a spec issue, check its `spec` label before drafting.** Read it back (`gh issue view <n> --json labels`). If the label is missing, stop and report it: the squad hub's Specs filter only shows issues that carry it, and an unlabelled spec can be dispatched as work. Add it (creating the label if it does not exist) only with the owner's go-ahead; this is the one change to a parent the skill may make.

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

Give each ticket its **wave**: `1` when it has no blockers, otherwise one more than the highest wave among its blockers (the longest blocking path). Every ticket in a wave can run in parallel once the previous waves are done, and a ticket never shares a wave with one of its blockers. Write it with two digits (`01`, `02`, …) so it sorts.

Give each ticket its **labels**: the triage label (`ready-for-agent` unless instructed otherwise), its wave as `wave:NN`, and any type label the tracker's vocabulary defines that fits (for example `bug` for a defect). Reuse existing labels; never invent a near-duplicate of one that exists.

**Wide refactors are the exception to vertical slicing.** A **wide refactor** is one mechanical change (rename a column, retype a shared symbol) whose **blast radius** fans across the whole codebase, so a single edit breaks thousands of call sites at once and no vertical slice can land green. Don't force it into a tracer bullet; sequence it as **expand–contract**. First expand: add the new form beside the old so nothing breaks. Then migrate the call sites over in batches sized by blast radius (per package, per directory), each batch its own ticket blocked by the expand, keeping CI green batch to batch because the old form still exists. Finally contract: delete the old form once no caller remains, in a ticket blocked by every migrate batch. When even the batches can't stay green alone, keep the sequence but let them share an integration branch that all block a final integrate-and-verify ticket; green is promised only there.

### 4. Quiz the user

Present the proposed breakdown as a numbered list. For each ticket, show:

- **Title**: short descriptive name. A child of a spec carries the spec's number as the first words of its title, written `[SPEC <n>] <title>` (for example `[SPEC 117] Every refusal has one shape: a closed set in code`). The prefix is a reading aid: it never replaces the parent relation, and the sub-issue link stays the fact. A child whose parent is not a spec (a plan, a conversation) carries no prefix, and a prefix names the spec's number only, never a wave, a kind or a column.
- **Blocked by**: which other tickets (if any) must complete first
- **Wave** and **labels**: `wave:NN` and the labels it will be published with
- **What it delivers**: the end-to-end behaviour this ticket makes work

Ask the user:

- Does the granularity feel right? (too coarse / too fine)
- Are the blocking edges correct: does each ticket only depend on tickets that genuinely gate it?
- Do the waves match the order you expect (they follow from the blocking edges; fix an edge, not a wave)?
- Should any tickets be merged or split further?

Iterate until the user approves the breakdown.

Only the owner approves a breakdown. The autonomy panel never approves one unless the owner asked for this breakdown in the session. With the owner away, the breakdown waits; do not publish it.

### 5. Publish the tickets to the configured tracker

Publish the approved tickets. **How** depends on the tracker `/setup-matt-pocock-skills` configured; the tickets are the same either way, only the shape of the blocking edges changes:

- **Local files** → write one file per ticket under `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01` in dependency order (blockers first). Each file's "Blocked by" lists the numbers/titles it depends on. Use the per-ticket file template below: one ticket per file, never a single combined file.
- **A real issue tracker (GitHub, Linear, …)** → publish one issue per ticket in dependency order (blockers first) so each ticket's blocking edges can reference real identifiers. **Attach every ticket to the parent issue as the platform's own relationship**, so the parent shows its tasks without anyone reading a body. On GitHub that is the sub-issue relation, and it is the last step of each ticket's publish (see the publish order below).
  ```sh
  id=$(gh api "repos/$OWNER/$REPO/issues/$TICKET" --jq .id)   # the numeric database id, NOT `gh issue view --json id`, which answers the GraphQL node id and the endpoint rejects it
  gh api "repos/$OWNER/$REPO/issues/$PARENT/sub_issues" -F sub_issue_id="$id"   # -F, so the value is sent as an integer
  ```
  Verify the relation landed (read the parent's sub-issue list back) before moving on. Where the tracker has no such relation, set each ticket's "Blocked by" to the blocking issues and name the parent in the body.
  Write every blocking edge as the platform's own dependency too, so the relation is visible where a reader looks for it and not only in the body: on GitHub, `POST /repos/$OWNER/$REPO/issues/$TICKET/dependencies/blocked_by` with `-F issue_id=<the blocker's numeric id>` says "this ticket is blocked by that one", and the blocker's own panel then answers it as blocking. Read every edge back before moving on. Both relations carry their own weight: the parent link puts the tasks under the parent issue, and the dependencies are what the issue's Relationships panel shows.
  Publish no `relates_to` edge of the parent to its own tickets, and none between tickets on a guess. The parent link already lists them under the parent, with progress; a second copy can only disagree with it. A `relates_to` edge (on GitHub: `POST /repos/$OWNER/$REPO/issues/$TICKET/relates_to` with `-F issue_id=<the numeric id>`) is earned when two tickets touch the same place and neither blocks the other — and only once the reason is known and written in a comment, because the edge itself carries no direction and no proof. Where the repository already defers a startable ticket whose file another holds, that guard is the operational half and no edge replaces it.
  Apply every ticket's `wave:NN` label and any type label when it is created. The triage label comes later, in the publish order below. Before the first issue, make sure each `wave:NN` label you need exists (on GitHub: `gh label create "wave:NN" --color <hex> --description "Wave NN: startable once waves before it are done" --force`, one color per wave or one shared color). If a later edge change moves a ticket to another wave, move its label too: a ticket carries exactly one `wave:` label.
  Every ticket gets the triage label (`ready-for-agent`) in the publish order below, blocked or not. The repository's own guard (for example its `stack.sh`) holds a ticket whose blocker is open; the skill does not withhold the label. The wave labels stay on all of them, so the order is readable and the edges are the same thing the dispatch sorts by.
  Where the tracker has a board, create each ticket's card in **Backlog** (a bug's card in **Todo**).
  **Set the project to "Squad Board" when you create the issue.** On GitHub the card is the issue's project item, and the board is the project titled `Squad Board` in the repository's owner (the wellzesta org for wz-zesty-agent), never a project looked up by number or by any other title:
  ```sh
  OWNER=${SG_OWNER:-$(gh repo view --json owner --jq .owner.login)}
  NUM=$(gh project list --owner "$OWNER" --format json --jq '.projects[]|select(.title=="Squad Board").number')   # exactly one number, or stop
  gh project item-add "$NUM" --owner "$OWNER" --url "$ISSUE_URL" --format json --jq .id   # the item id; then set its Status to Backlog (a bug's to Todo) with gh project item-edit
  ```
  Read the project back (`gh issue view <n> --json projectItems`): it must list `Squad Board`. Add the ticket to the board when you create it, before the next ticket, so a refused add is seen once. Where the repository has no `Squad Board` project, say so to the owner instead of guessing another project.
  **Publish order, per ticket.** A new card is Todo the moment it joins the board, and Todo plus `ready-for-agent` is the dispatcher's go signal. So for each ticket: create the issue without `ready-for-agent`; add it to the board and set its Status (Backlog; a bug's Todo). Read the Status back; a new card can read late, so read again before you move on. Then add `ready-for-agent` only after the Status reads back, and then link the ticket to its parent. Where the tracker has no board, skip the board steps and add the label after the create.
  The loop never moves a card into Backlog or Todo; only the owner does. Todo plus `ready-for-agent` is the dispatcher's go signal, so the owner starts a ticket by moving its card to Todo.

Work the **frontier**: any ticket whose blockers are all done. For a purely linear chain that means top to bottom.

Do NOT close or modify any parent issue, except adding a missing `spec` label as step 1 allows.

Before reporting the tickets published, read the parent's labels back once more: a spec parent must still carry `spec` and no `ready-for-agent`, and no ticket may carry `spec`.

<local-ticket-template>

# <NN>: <Ticket title>

A child of a spec issue starts its title with the spec's number, `[SPEC <n>] <title>`; a child of anything else carries no prefix.

**What to build:** the end-to-end behaviour this ticket makes work, from the user's perspective, not a layer-by-layer implementation list.

**Blocked by:** the numbers/titles of the tickets that gate this one, or "None (can start immediately)".

**Status:** ready-for-agent

**Wave:** NN

**Labels:** ready-for-agent, wave:NN

- [ ] Acceptance criterion 1
- [ ] Acceptance criterion 2

</local-ticket-template>

<issue-template>

**Title:** a child of a spec issue carries the spec's number first, `[SPEC <n>] <title>`; any other ticket carries the plain title, with no prefix.

## Parent

A reference to the parent issue on the tracker (if the source was an existing issue, otherwise omit this section).

## What to build

The end-to-end behaviour this ticket makes work, from the user's perspective, not layer-by-layer implementation.

## Acceptance criteria

- [ ] Criterion 1
- [ ] Criterion 2

## Blocked by

- A reference to each blocking ticket, or "None (can start immediately)".

(The wave and the other labels are applied as tracker labels, `wave:NN`, not written in the body.)

</issue-template>

In either form, avoid specific file paths or code snippets: they go stale fast. Exception: if a prototype produced a snippet that encodes a decision more precisely than prose can (state machine, reducer, schema, type shape), inline it and note briefly that it came from a prototype. Trim to the decision-rich parts, not a working demo, just the important bits.
