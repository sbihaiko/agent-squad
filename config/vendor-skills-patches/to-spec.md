## This repository's rules for a spec

This section is appended by `scripts/build_vendor_skills.py` from `config/vendor-skills-patches/to-spec.md`.
Edit the patch file, never the vendored copy: a rebuild writes it again.

**Publishing ends at the kanban.** Creating the issue is half the publish. Before you report the spec published,
its card stands on the Squad Board in **Backlog**, and one read of the issue names both the label and the column:
`gh issue view <n> --json labels,projectItems`. A card a later read does not carry is read again — a new card reads
late — and a spec whose project is not `Squad Board` is reported to the owner, never guessed onto another project.
A spec with sub-issues takes its column from them as a fold over the children; a spec with none stays where the
publish put it.
