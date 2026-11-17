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
