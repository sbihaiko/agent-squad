---
name: squad-skill-update
description: "Run after `npx skills update` or `claude plugin update`. Checks that every skill this repo borrows still resolves, rebuilds the vendored copy if it drifted, and reports what the Owner must settle."
---

# squad-skill-update

Run this after a skill update. It runs under `/autonomy`: the Owner is away, and the command makes every repo change the update needs, without asking. It never runs the update itself and never edits or deletes a file under `~`: the Owner runs `npx skills update -g` or `claude plugin update` first.

0. Call the Skill tool with `autonomy`. Its grant covers this run. Work in a worktree and merge back to `main` at the end.
1. From the repository root, run `python3 -m squad.skillpath --check`. Act on each line:
   - `missing:` (exit 1): a skill that a door opens or the vendor builder copies resolves nowhere. First read the plugin `CHANGELOG.md` (newest install under `~/.claude/plugins/cache/`) to see whether upstream removed or renamed it. Removed: retire the door (its `.agents/skills/agent-*` folder, its `.claude/skills` link, its name in `CLAUDE.md` and in `tests/test_agent_doors.py`) and its `config/vendor-skills.yaml` entry. Renamed: point the door and the entry at the new name. Never improvise the skill's steps.
   - `warning: ... not found` for a reference-only skill: if upstream removed it, delete it from `config/external-skills.yaml` and `docs/skill-workflow-map.md`.
   - `warning: ... personal copy differs`: this is the Owner's, because the fix is under `~`. Do not edit `~`. List the skills, and the commands the Owner runs (`npx skills update -g`, or `rm -r ~/.claude/skills/<name>`). Warn that a plugin copy with `disable-model-invocation: true` stops agents from invoking that skill, so it needs an `agent-<name>` door first; add the door yourself (copy an existing door, run the resolver by name, symlink it, name it in `CLAUDE.md` and `tests/test_agent_doors.py`).
2. Run `python3 scripts/build_vendor_skills.py --check`. On a drift, run `python3 scripts/build_vendor_skills.py`.
3. Run `python3 -m pytest tests/test_skillpath.py tests/test_agent_doors.py tests/test_vendor_skills.py -p no:xdist`. A red "router" test names a skill `/ask-matt` now routes to that is not in `config/external-skills.yaml`: register it (`referenced`, or `optional` if the plugin leaves it unlisted). A skill the router dropped: remove it from the manifest and from `docs/skill-workflow-map.md`.
4. A decision the Owner would make (a rename with two readings, a skill that replaces another) goes to the panel (`/autonomy`). The six carve-outs stay with the Owner: record a `needs-ratification` issue.
5. Run `python3 -m pytest -n auto` and `python3 -m pytest -p no:xdist`, both green; then commit and push and merge to `main`.
6. Report in the `/autonomy` form: what changed in the repo, the panel decisions, the `warning:` lines left for the Owner with the exact commands, and the test result.

Guard: `tests/test_agent_doors.py` (this skill is symlinked, named in `CLAUDE.md`, calls `autonomy`, and names each command it runs).
