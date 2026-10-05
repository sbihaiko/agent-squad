# ADR-0001 — US English and Concise STE100 for all project output

## Status

Accepted, 2026-09-29.

## Context

Rafael converses in Portuguese. The session output style changes from project to project.

Artifacts then drift in language and style. CLAUDE.md, the ADRs, the code comments, and the commit messages lose consistency.

## Decision

Write all project output in US English. Use the Concise STE100 output style. Use short sentences, active voice, and one idea per paragraph.

This holds when the conversation happens in another language. It holds when Concise STE100 is not the session default.

## Consequences

Artifacts stay uniform across sessions and languages.

Translate Rafael's input into US English before you record it in the repo. Non-English input is a signal to produce English output. Do not mirror the input language.

## Guard

None yet. This rule is a draft until a named guard test exists (ADR-0018).
