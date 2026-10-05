# ADR-0128 — The dashboard may pause and resume a launched run through a control file, and the run server gains one write route

## Status

Accepted, 2026-10-04. Panel decision under autonomy on ticket [#107](https://github.com/mmctech-ai/agent-squad/issues/107), from the owner's ruling on [#69](https://github.com/mmctech-ai/agent-squad/issues/69) (2026-10-02). AGREED on the control file, the scope rule, and the `paused` event. AGREED 2–1 on who writes the file: the dashboard (Lens A, Codex `gpt-6-sol`, and Lens C, Gemini 3.1 Pro High, blind) over the CLI only (Lens B, Fable 5.1). Amends rules 1 and 4 of [ADR-0118](0118-squad-dashboard-serves-one-run-folder-over-a-read-only-loopback-server.md). Keeps [ADR-0117](0117-the-editor-prepares-a-run-and-never-launches-it-approval-is-a-person-s-act-outside-the-graph-and-the-page-makes-no-model-call.md) rule 1: the page never launches a run.

## Context

The owner allows the editor and the dashboard to pause and resume a run that is already launched, by run id. The scope is one task, a set of tasks, or the whole run. There is no stop action. A pause shows where the run stands. The open part was how the control reaches the run. ADR-0118 gave the run server no write route.

## Decision

1. **A request is a line in `control.jsonl` in the run folder.** `{"action": "pause"|"resume", "nodes": [...]}`. No `nodes` means the whole run. The file only grows, and it sits beside the record, never in it, because the engine rewrites the record on every event (ADR-0030) and a second writer would lose a request. A line that is not a valid request is skipped.
2. **The engine folds the file at each node entry.** A held node does not start. Scope is the next entry only: a call that is running finishes, so its reserved spend is never lost (ADR-0108). A resume of a node under a whole-run pause releases that node and keeps the rest held.
3. **A pause ends the process, like a wait.** The engine records a `paused` event, raises `Halt("pause")`, and the verdict is `stopped` (exit 30) with the reason `paused:<node>`. The `paused` event holds `node`, `scope`, `held`, and `completed`: the engine's own lists, and the resume point. The page shows them as recorded and works out no frontier (ADR-0117 rule 10).
4. **In a dispatch, a held child does not start and its siblings go on.** A child that runs `after` a held child is held with it. The process ends when no child can run. A resume runs the held children and not the ones that finished.
5. **`squad resume` releases every hold.** The person who types the command resumes. The dashboard can send a resume request, but it never starts a process (ADR-0117 rule 1): the page shows `squad resume <run>` for the person to type.
6. **The run server has one write route: `POST /run/control`.** It appends one line to the control file and writes nothing else. It needs the Host check of ADR-0118 rule 3, an `Origin` that is the page's own (or none), a `Content-Type` of `application/json`, a body of at most 4 KB, and a valid request: an action, and at most 100 node names that match `[A-Za-z0-9_.:-]{1,200}` (a node or a child, never one `each` item: the engine holds by name). Any other method or path keeps the 405 of ADR-0118. A run that has ended gets 409: a run is ended by any verdict except the `stopped` that a pause writes. `GET /run/control.json` shows what was asked.
7. **A request grants no approval.** It is not a consequential live write (ADR-0024): it changes only this run's own schedule on this machine, and the owner ruled it in #69. It does not lift a grant that a node needs (ADR-0010 rule 3).

## Considered options

- **The CLI writes, the page stays read-only** (Lens B). Rejected 2–1: the owner's ruling names the dashboard as the place that pauses. Reopen if a local process that pauses a run proves a real nuisance. The write route would then go behind a token.
- **A signal or a socket to the engine process.** Rejected: no run pidfile exists, and dispatch children share one process, so a signal has no scope.
- **Hold the process and wait in place.** Rejected: the run deadline keeps counting while it waits, and a held process pins its worktrees. A paused run holds no process.
- **Kill a running call at a pause.** Rejected: the call becomes `crashed` and its whole reserved cap stays as unmeasured spend (ADR-0108).

## Consequences

A local process can pause a run through the loopback port, as it can already read it (ADR-0118). A pause costs no spend and loses no work. The verdict `stopped` now has two sources, `--stop-after` and a pause, told apart by the reason. GLOSSARY.md gains `Control file`.

## Guard

Tests that guard this rule:

- `tests/test_pause.py` (the fold, the hold, the `paused` event, the resume, a dispatch child and its `after`)
- `tests/test_dashboard_control.py` (the one write route, its checks, the ended run, no other write)
- `tests/test_dashboard_pause.py` (the page shows the recorded resume point and posts only to the control path)
- `tests/test_dashboard.py::test_the_dashboard_reaches_only_its_own_origin_and_only_the_run_paths`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
