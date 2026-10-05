# ADR-0127 — A plain `squad run` writes the graph it ran into its run folder

## Status

Accepted, 2026-10-03. Extends [ADR-0118](0118-squad-dashboard-serves-one-run-folder-over-a-read-only-loopback-server.md). [ADR-0117](0117-the-editor-prepares-a-run-and-never-launches-it-approval-is-a-person-s-act-outside-the-graph-and-the-page-makes-no-model-call.md) read-only holds.

## Context

The dashboard serves `<folder>/graph.json`. A folder made by `squad run` alone held only `record.jsonl`, so the page opened with "no graph".

## Decision

1. `squad run` writes the loaded graph document to `<run dir>/graph.json` before the run starts, unless that file exists.
2. The copy is the hash input: `graph.canon_hash` of the file equals `graph_hash` in `run_start`. The record format and the hash do not change.
3. The engine never reads the copy. The dashboard only reads it.

Guard: `tests/test_cli.py::test_a_plain_run_leaves_the_graph_it_ran_in_its_run_folder_and_the_dashboard_serves_it`.
