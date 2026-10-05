# ADR-0129 — A borrowed skill is found by name at run time, never by a path

## Status

Accepted, 2026-10-05. Ticket #135. Amends the doors rule in `CLAUDE.md` (2026-09-30): it changes only how a door and the vendor builder locate a skill.

## Context

This repo borrows skills it does not own: the eight `agent-*` doors open a global skill, `config/vendor-skills.yaml` copies seven into `vendor/skills`, and the docs name more. Each door and each vendor source read `~/.claude/skills/<name>`. Two update channels move that ground:

- `npx skills update` rewrites or removes `~/.claude/skills` and `~/.agents/skills` entries (links, a lock file).
- `claude plugin update` installs a plugin into a new versioned folder (`mattpocock-skills/1.2.0` to `1.2.3`) and sorts skills into buckets (`engineering/`, `productivity/`, `in-progress/`, `deprecated/`). The old folder stays in the cache.

On 2026-10-05 the personal copies already differed from the 1.2.3 plugin for 12 skills, and four vendored skills had drifted from their source with no test red, because the drift check ran only by hand. A path that an update moves or a removal empties would have failed a door with a vague "file not found", or worse, run a stale copy.

## Decision

1. **Find by name.** `python3 -m squad.skillpath NAME` (`squad/skillpath.py`) prints the `SKILL.md` path. First hit: the personal copy in `~/.claude/skills`. Next: the skills that the newest install of a trusted plugin lists in its `plugin.json`. Claude Code ranks a personal skill above a plugin skill for a typed command, so the Owner and a door read the same file. `~/.agents/skills` is not a source: Claude Code reaches it only through the links in `~/.claude/skills`.
2. **Not a hit:**
   - a dangling link (an `npx skills remove` leftover) or an empty `SKILL.md`;
   - a skill the plugin does not list, so `deprecated/`, `in-progress/` and example folders stay out;
   - a skill that only an older install of the plugin has (the newer install dropped it);
   - a project-scope install, outside its own project;
   - a plugin the manifest does not trust. A door runs the skill's text with the door's authority, so a plugin that ships a skill of the same name is not read.
3. **A missing skill stops that skill.** Exit 1 and a message: removed or renamed upstream. The door does not improvise the steps. It opens an issue that names the skill, and the agent carries on with other work.
4. **A manifest registers every borrowed name.** `config/external-skills.yaml` holds `plugins` (trusted), `referenced` (named in the docs, opened by no door) and `harness` (Claude Code's own commands). The doors' targets and the `skill:NAME` sources in `config/vendor-skills.yaml` count without a listing. `docs/skill-workflow-map.md` may name only registered commands.
5. **The vendor builder uses the same lookup.** A `skill:NAME` source is found by `squad.skillpath`. A path source still works. The builder and the resolver share one list of files left out of a copy and of its hash.
6. **Drift is reported, never repaired.** `python3 -m squad.skillpath --check` exits 1 when a skill that a door opens or the builder copies resolves nowhere. It warns for a missing reference, and for a personal copy that differs from the plugin copy it shadows. It never edits or deletes a file under `~`. `python3 scripts/build_vendor_skills.py --check` reports a vendored copy that differs from its source. Rebuild with the script and commit.
   - `/ask-matt` routes to other skills, so its door tells the agent to run the resolver for each skill it recommends, and a skill that no longer resolves is not recommended. A live guard reads the router and fails when it routes to an unregistered or unresolvable skill (`optional` in the manifest excepts a skill that the plugin leaves unlisted).
7. **Personal wins.** A personal copy that differs from the plugin copy shadows it, as in Claude Code. The report names both with the day each was written, so the Owner sees which is newer without a diff. On 2026-10-05 the personal copies were the newer ones: `npx skills update` rewrote them that day, and the plugin copy dates from 2026-09-23. The Owner settles a difference with `npx skills update` or by removing a copy. Deletion is the Owner's action.
   - Panel, 2026-10-05, `AGREED` (Codex and Fable, blind; no Lens C needed): option A, keep personal first. Option B fails because a plugin version number orders plugin installs among themselves, not a plugin against a git checkout, and here the plugin is the older copy. Option C makes a lagging upstream release a permanent red that only a deletion clears: a gate used as a target (ADR-0012), not a defect (ADR-0018). Lens B's reasons rest on `~/.agents/.skill-lock.json` and `installed_plugins.json`, which I checked. Its claim that Claude Code ranks a personal skill first rests on ADR-0129 and the session's skills list (`Path: userSettings:`). What would change the call: Claude Code loading the plugin copy for a typed command, or an `npx skills update` copy that is behind the plugin.
8. **Tests may move the home.** `SQUAD_SKILL_HOME` replaces the home directory for the resolver.

## Considered options

Keep the paths and add a test: rejected, the test still fails on the first move. Prefer the plugin copy over the personal copy: rejected, the Owner's typed command would then differ from a door. Vendor every borrowed skill into the repo: rejected, it freezes upgrades and doubles the drift. Auto-delete a stale personal copy: rejected, deletion is an owner-only action.

## Consequences

An upgrade, a bucket move or a removal changes what a door reads or stops it with a clear message; none breaks it silently. Doors depend on the repo root as the working directory (the resolver is a module of this repo). A personal copy that differs from the plugin copy is reported on every `--check` until the Owner settles it. The vendored copy was rebuilt on 2026-10-05 from the current personal copies, which cleared its drift. Those personal copies are newer than the 1.2.3 plugin copies and differ from them for 12 skills. The report lists them and nothing needs fixing.

Guard: `tests/test_skillpath.py` and `tests/test_agent_doors.py` (each door names the resolver and no path). The first simulates a plugin update, a bucket move, a removal, an unlisted skill, a dangling link, an untrusted plugin, project scope, the version order, the manifest, the doc map and the vendor builder. Two live tests run only where `~/.claude/skills` exists: every skill a door opens still resolves, and the vendored copy matches its sources. After an update they go red until the Owner reinstalls the skill or rebuilds the copy (ADR-0018, ADR-0062).
