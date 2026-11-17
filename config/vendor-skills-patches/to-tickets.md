## This repository's rules for tickets

This section is appended by `scripts/build_vendor_skills.py` from `config/vendor-skills-patches/to-tickets.md`.
Edit the patch file, never the vendored copy: a rebuild writes it again.

**The title of a spec's child.** A child of a spec carries the spec's number as the first words of its title, written
`[SPEC <n>] <title>` (for example `[SPEC 117] Every refusal has one shape: a closed set in code`). The prefix is a
reading aid: it never replaces the parent relation, and the sub-issue link stays the fact. A child whose parent is not
a spec (a plan, a conversation) carries no prefix, and a prefix names the spec's number only, never a wave, a kind or a
column.

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
