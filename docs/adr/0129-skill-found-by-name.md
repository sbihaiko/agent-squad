# ADR-0129: A borrowed skill is found by name, and a run records the hash of every skill it can load

## Status

Accepted.

## Context

This repo borrows skills it does not own. The `agent-*` doors open a global skill, and `config/vendor-skills.yaml` copies skills into `vendor/skills`. Update channels (`npx skills update`, `claude plugin update`) rewrite, move or remove the files a path would name. A path then fails with a vague "file not found", or runs a stale copy.

A run loads skills only from the committed `vendor/skills`, never from `~`. Without a record, a past run cannot show which skill text it used, and a resume would run its later nodes on new text.

## Decision

### Finding a skill

1. **Find by name.** `python3 -m squad.skillpath NAME` (`squad/skillpath.py`) prints the `SKILL.md` path. First hit: the personal copy in `~/.claude/skills`. Next: the skills that the newest install of a trusted plugin lists in its `plugin.json`. Claude Code ranks a personal skill above a plugin skill, so the owner and a door read the same file. `~/.agents/skills` is not a source.
2. **Not a hit:** a dangling link or empty `SKILL.md`; a skill the plugin does not list (`deprecated/`, `in-progress/`, examples); a skill only an older plugin install has; a project-scope install outside its project; a plugin the manifest does not trust. A door runs skill text with the door's authority.
3. **A missing skill stops that skill.** Exit 1 with a message: removed or renamed upstream. The door does not improvise the steps. It opens an issue that names the skill, and the agent carries on with other work.
4. **A manifest registers every borrowed name** (`config/external-skills.yaml`: `plugins`, `referenced`, `harness`). Door targets and `skill:NAME` vendor sources count without a listing. `docs/skill-workflow-map.md` names only registered commands.
5. **The vendor builder uses the same lookup,** and shares the resolver's list of files left out of a copy and of its hash.
6. **Drift is reported, never repaired.** `python3 -m squad.skillpath --check` exits 1 when a skill that a door opens or the builder copies resolves nowhere. It warns for a missing reference and for a personal copy that differs from the plugin copy it shadows. `python3 scripts/build_vendor_skills.py --check` reports a vendored copy that differs from its source. Neither edits or deletes a file under `~`. Deletion is the owner's action.
7. **Personal wins.** The report names both copies with the day each was written. Rejected: plugin first (the owner's typed command would differ from a door). Rejected: a red check until the owner deletes a copy (a gate used as a target, ADR-0012).
8. **Tests may move the home** with `SQUAD_SKILL_HOME`.

### Recording skills in a run

9. **A skill's version is its tree hash** (`vendor/skills/VERSIONS.json`, `squad.skillpath.tree_hash`). The source is provenance only.
10. **`run_start` records them.** The field `skills` maps each skill the graph can load (every node `skills` entry and dispatch `allow.skills` entry) to `{hash, source}`, plus the git commit of `vendor/skills`. A run refuses to start with an uncommitted change in `vendor/skills` or with vendored bytes that differ from `VERSIONS.json`. A skill the graph names but `vendor/skills` lacks is recorded as absent.
11. **Graphs pin or float.** A dispatch `allow.skills` `version` is a hash, and the envelope rejects a mismatch. A graph with no pin takes the current hash at start.
12. **A resume refuses on drift.** It compares the recorded hashes with `VERSIONS.json` now. A difference refuses the resume, names each skill, both hashes and the recorded commit, and writes no event. There is no silent fallback to new text.
13. **No snapshot in the run folder.** Git history supplies the old text. The dashboard shows each run's hashes; an older record reads "unrecorded".

## Guard

`tests/test_skillpath.py`, `tests/test_agent_doors.py`, `tests/test_skills.py`, `tests/test_envelope.py`, `tests/test_dashboard_skills.py` (ADR-0018). The live checks go red after a skill update until the owner reinstalls the skill or rebuilds the vendored copy.
