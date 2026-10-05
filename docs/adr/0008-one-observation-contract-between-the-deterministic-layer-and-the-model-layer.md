# ADR-0008 — One observation contract between the deterministic layer and the model layer

## Status

Accepted, 2026-09-29.

## Context

Deterministic code produces the facts the system runs on. Agents consume those facts and decide. That seam is where the system hinges.

A failure at the seam is not an agent failure. A generator can label stale data as live state. A step can fail and still exit 0. Hand-written state can contradict generated state.

The consumer is a language model. It trusts a script output that has the shape of a verdict.

## Decision

One module defines one observation shape and one `validate()` that **raises**.

1. **An observation is a fact plus a complete evidence block.** The block states what was measured and which instrument produced it. It names the source: host, repo, commit, file, or row. It gives the time of measurement and a severity.
2. **`validate()` refuses three kinds of observation.** It refuses one that carries a verdict. It refuses one that mints an identifier the script has no authority to mint. It refuses one that reports a measurement without naming its instrument. Refusal happens at production, not at assembly.
3. **Freshness is a required field. It is never an optional note.** Refuse a reading with no timestamp and no source commit. A stale reading shown beside fresh ones inherits their authority.
4. **Keep streams separate. The agent joins them.** Merging streams destroys the guards that each stream provides.
5. **A step that could not measure exits non-zero.** Exit 0 means "measured, and here is the reading". It never means "the command ran".
6. **Generated state names its generator and its inputs.** Nobody edits generated state by hand. No document restates a number from it. Documents point at it.
7. **Keep a self-test.** Use one fixture per refusal. Add false-positive guards that prove a legitimate observation still passes. Over-refusal strands a run.

## Consequences

A script can no longer drift into writing the judgment. Rafael can trust a number without re-deriving it.

Cost: every probe conforms to a stricter shape. A change to that shape needs a coordinated migration.

## Guard

None yet. This rule is a draft until a named guard test exists (ADR-0018).
