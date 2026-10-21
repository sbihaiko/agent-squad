# ADR-0150: A run never holds conflicting siblings

## Status

Accepted.

## Context

A run that packs several children lands them as one wave. Two children that change the same file, or that build parts of one spec, conflict at landing. A conflict found at landing costs the work of both children and a fix cycle. Most of these conflicts are visible before dispatch, from the spec link and the issue bodies.

## Decision

1. **A run never holds two children of one spec.**
2. **A run never holds two issues whose bodies name the same repository-relative file.**
3. **The check is deterministic and runs before dispatch.** It reads the spec link and the issue bodies. It uses no model. An issue that fails the check waits for a later run.
4. **Conflict handling at landing stays.** The check before dispatch lowers the conflicts; it does not prove there are none.

## Consequences

- Fewer children enter one run when the frontier is dense in one spec or one file. The loop accepts that cost in exchange for fewer failed landings.
- A file that an issue body does not name is not checked. Landing still catches that conflict.

## Guard

`contrib/squad-goal/scripts/test-autofrontier-one-run.sh` (case S) and `contrib/squad-goal/scripts/test-cluster.sh`. The frontier pass applies the rule in `siblings.py`; issues that name one file are one child (`cluster.py`), so they are never siblings.
