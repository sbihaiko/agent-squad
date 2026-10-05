# ADR-0028 — Project state is read from the target, never from a hand-written file

## Status

Accepted, 2026-09-29.

## Context

A run needs the state of a project. Examples are the number of open tickets, what is deployed, and whether a service is up. The obvious place to keep that state is a prose document that people update by hand.

A hand-written state file is a claim about the target. A past session made that claim at a past moment. That session no longer exists. The claim goes stale without warning.

A cache has the same fault one layer down. A generator that counts from a local mirror reports the mirror, not the target. If the mirror refresh runs after the generator, every run starts one generation behind.

The target always tells the truth, because the question is about the target.

## Decision

1. **The target is the authority on project state.** The git remote, the running services, the queue, and the run records answer questions about themselves. Read the project. Do not read a document about the project.
2. **A hand-written document holds no state numbers.** It names the generator that produces the number. It does not hold the number.
3. **Generate state files. Never edit them.** A hand edit turns evidence back into a claim.
4. **A generator reads the source, not a mirror or a cache.** If a mirror must be refreshed first, the refresh runs inside the same script. No caller can then order the steps wrongly.
5. **Every generated file states when it ran and what it read.** Record the timestamp, the host, and the commit. A stale file is then visible as stale.
6. **Do not rely on prose for ordered steps.** Prose that must be followed in order is code with no compiler. Put such a sequence in an executable script and test it.
7. **An agent report of "done" is a claim.** Verify it at the target. Use `git ls-remote` or the service logs and status.
8. **A hand-written document and a generated one can disagree.** The generated one wins. The hand-written one is a defect to fix (ADR-0018).

## Consequences

A run measures instead of trusting. A document can go stale without misleading anyone, because it holds no numbers.

Cost: each state question needs a generator. A generator needs reachable sources. If the source is down, the honest answer is "unknown". Do not report the last number someone wrote.

Risk: a generator that reads the wrong source gives confident, wrong evidence. Rule 5 makes the source visible. The failure is then auditable, not silent.

Guard: none yet. This rule is a draft until a named guard test exists (ADR-0018).
