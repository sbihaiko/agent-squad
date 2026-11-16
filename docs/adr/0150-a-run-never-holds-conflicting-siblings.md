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
5. **The file check crosses runs, in both dispatchers.** A live run holds every file its issues name. The frontier cuts an issue of a held file from the holding branch (`slicecut.py decide`). The bug lane starts no child of its own on such a file: it reads the same map (`slicecut.py holds`) and holds the bug for a later pass, and its line names the holding run.

## Consequences

- Fewer children enter one run when the frontier is dense in one spec or one file. The loop accepts that cost in exchange for fewer failed landings.
- A bug waits while a live run edits a file its body names. The hold ends when that run records its verdict, and the bug still has its card in Todo.
- A file that an issue body does not name is not checked. Landing still catches that conflict.

## Guard

`contrib/squad-goal/scripts/test-autofrontier-one-run.sh` (case S), `contrib/squad-goal/scripts/test-cluster.sh` and `contrib/squad-goal/scripts/test-autobug.sh` (case 40: the bug lane holds a bug whose file a live run holds, and names that run); `contrib/squad-goal/scripts/test-slicecut.sh` (case H1) proves the map the two dispatchers read. The frontier pass applies the rule in `siblings.py`; issues that name one file are one child (`cluster.py`), so they are never siblings.
