# ADR-0135: A failed gui-test run's screenshots live on a dedicated evidence branch

## Status

Accepted (implements MesenAI #1190 under ADR-0272; runner lives in this fork per the owner's pick P1-A).

## Context

A failed run opens one bug listing each failed step with expected and observed values, its screenshot and the previous step's. An issue can only show an image inline from a URL that stays valid and renders. Actions artifacts expire and never render. Issue-attachment upload has no public API. Release assets work but need a release per run.

## Decision

1. **Screenshots are committed to an orphan-style branch `gui-test-evidence` of the target repo**, one file per capture (`<step-id>-<sha12>.png`), through the REST contents API. The issue embeds `https://github.com/<repo>/blob/gui-test-evidence/<file>?raw=true`, which renders for anyone who can see the repo and does not expire. The branch is never merged.
2. **Only the gate releases captures.** A capture of a `sensitive` step is stored only after a person approves its sha256 (ADR-0024; `gui_test_gate`). The bug says "held pending approval" instead of linking it.
3. **A clean run, or one with only `pending` steps, stores nothing and files nothing.**
4. **The bug goes through the target repo's `scripts/report-bug.sh`**, one issue per run, however many steps failed.
5. **The first write to the evidence branch of a repo needs the owner's grant** (ADR-0024); the store and the bug filer are injected, so tests never touch GitHub.

## Consequences

Evidence adds files to the target repo's object store; prune the branch when it grows. A public repo exposes the images publicly, which is why rule 2 gates by sensitivity.
