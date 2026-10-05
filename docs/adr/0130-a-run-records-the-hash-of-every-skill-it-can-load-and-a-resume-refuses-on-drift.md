# ADR-0130 — A run records the hash of every skill it can load, and a resume refuses on drift

## Status

Accepted, 2026-10-05, by the panel in the Owner's place (`/autonomy`). Design only; the build is a ticket. Builds on ADR-0129 (find a skill by name) and ADR-0107 (a resume refuses a changed graph).

## Context

A run loads skills only from the committed `vendor/skills` (`config/agent-defaults.yaml`), never from `~`. A skill update therefore reaches a run only when `scripts/build_vendor_skills.py` rebuilds that copy. `vendor/skills/VERSIONS.json` holds a tree hash per skill. But the record names no skill: `run_start` has the graph hash and nothing about skills. `squad resume` checks the graph hash and ignores skills. A dispatch `allow.skills` entry has a `version` string that nothing compares to `VERSIONS.json`. After a rebuild, a past run cannot show which skill text it used, and a resume would run its later nodes on new text.

The Owner wants: a new run uses the updated skill; a past run keeps a true reference to the version it used.

## Decision

1. **A skill's version is its tree hash.** The hash in `VERSIONS.json` (`squad.skillpath.tree_hash`) is the identity. The source (`skill:NAME` and the plugin version, where there is one) is provenance only. A personal copy has no semver, so only a hash fits every skill.
2. **`run_start` records them.** A new field `skills` maps each skill the graph can load to `{hash, source}`. Its scope is every per-node `skills` entry and every dispatch `allow.skills` entry. It also records the git commit of `vendor/skills`. A run that starts with an uncommitted change in `vendor/skills` is refused, so git always holds the recorded hash. The byte check covers every vendored skill, not only the graph's, so a stale vendored skill is drift. A skill the graph names but `vendor/skills` lacks has no hash and is recorded as absent.
3. **Graphs pin or float.** A dispatch `allow.skills` `version` is a hash. A fixed workflow pins it and the envelope checks it against `VERSIONS.json`. A graph with no pin takes the hash current at start. Either way the hash is fixed for that run.
4. **A resume refuses on drift.** `Replay.refuse` compares the recorded hashes with `VERSIONS.json` now. A difference raises `ResumeRefused` that names each skill, the recorded hash, the current hash, and the recorded git commit. It writes no event, as on a graph-hash difference. There is no silent fallback to the new text. A later opt-in flag to accept drift needs its own ADR.
5. **No snapshot in the run folder.** A recorded hash that is gone from `vendor/skills` stops the resume with the error above, and git history supplies the text (`git log -- vendor/skills/VERSIONS.json`). Revisit this if a run folder must resume without the repo, or if history is rewritten.
6. **Reports and the dashboard show each run's skill hashes.** A run recorded before this change reads "unrecorded".

## Panel record

Lenses: A = Codex `gpt-6-sol`; a second lens = Gemini 3.1 Pro (High) through `agy` in place of Lens B, because the fork that ran this panel could not start the `panel-lens-b` agent. Lens C = `agy` Claude Opus 5.5 (High), run blind on Q4 and Q5.

- Q1, Q2, Q3, Q6: `AGREED` (A, Gemini). Codex wrote "both, hash authoritative"; Gemini wrote "hash". Decision 1 keeps both, with the hash authoritative.
- Q4: `AGREED` to refuse. Codex would resume from a saved copy and refuse only if its bytes fail; Gemini and Opus refuse on a difference. All three refuse on a failed check, so decision 4 holds.
- Q5: `AGREED 2–1`: stop with a clear error and rely on git (Gemini, Opus). Dissent, Codex verbatim: "Snapshot at run start. Copy the loadable vendor plugin into the run folder and verify it before calls. Git history alone does not provide a stable runtime path." Opus's condition for a snapshot (a dirty `vendor/skills` at start; a run folder resumed without the repo; rewritten history) is why decision 2 refuses a dirty tree.
- The panel skill pins Lens C to `claude-opus-4-6-thinking`. `agy` no longer lists that id (it lists Claude Opus 5.5). The skill needs the new id.

## Guards

All guards have landed (ADR-0018). `tests/test_skills.py` (#139): `run_start` carries the hash and source of every skill the graph can load, and the git commit of `vendor/skills`; a record without the field still loads. `squad/skills.py` owns the hash rule. `tests/test_skills.py` (#140): a start refuses, with no record written, on a modified, staged or untracked path under `vendor/skills` and on bytes that differ from `VERSIONS.json`; a clean tree starts. `tests/test_skills.py` (#142): a resume refuses on a changed hash, names the skill, both hashes and the recorded commit, and writes no event; a record with no `skills` field resumes as before; the `resume` event carries the current hashes. `tests/test_envelope.py` (#141): a dispatch pin that matches `VERSIONS.json` passes, a mismatch is rejected whole (`skill_pin_mismatch`), and a missing pin floats to the current hash; the graph schema takes a `version` only as 64 hex digits. The dashboard shows each run's hashes, and a record without them reads "unrecorded": `tests/test_dashboard_skills.py` (#143).
