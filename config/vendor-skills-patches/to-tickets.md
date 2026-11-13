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
