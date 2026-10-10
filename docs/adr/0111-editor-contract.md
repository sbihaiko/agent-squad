# ADR-0111: The editor writes what a person authors, reads the record, and shows an adaptation as a run-only overlay

## Status

Accepted.

## Context

The editor loads and saves the graph document. The engine owns the rules: a gate's fields are read-only (ADR-0100), the facts registry is closed and code-produced (ADR-0103), and a run never changes the graph file.

## Decision

1. **The editor writes what a person authors:** node names, kinds, ordered children, `next`, `when` over registered facts, per-kind fields, budget minimums and maximums, deadlines, the `adapt` block with its `sees` list, `each` fields, each `dispatch` node with its envelope and `allow` list (ADR-0113), workspaces, and the `run` block.
2. **Read-only in the editor:** a gate's check id and threshold parameters, and the `facts` and `gates` registries. The editor may pick a registered name and never adds one. A gate's name, its place in a loop's `children`, and its `next` are topology, and the editor writes them.
3. **The editor reads a run record as a read-only file.** It reads every event kind. It never writes a record and never derives a graph edit from one.
4. **The editor runs the same JSON Schema and rules as the engine,** from one source. It shows each error on its node. It never blocks a save or an export. The engine refuses an invalid graph at load.
5. **Import loads any parseable JSON that has a `schema_version`.** It refuses anything else with a message. Errors in a loaded file show on the nodes.
6. **An applied adaptation is a run-only overlay on the target's card.** It shows the proposal id, the kind, the old and requested value, the range checked, and the cited facts with instrument, source, and timestamp. Beside it, the editor shows the adaptation report's deltas with the instrument named, no label, and the line "one observation, not a measurement" (ADR-0109). Spawned children are a run-only overlay under the dispatch card. The overlay shows only when the record's graph hash equals the hash of the loaded graph. On a mismatch, the page shows the timeline and the verdict only.
7. **The graph document is one JSON file** with `schema_version`, `name`, the `run` block, the `facts` registry, the `gates` registry, `root`, and a flat `nodes` array. The `gates` registry is closed: check ids and their parameter names.
8. **Layout lives in a sibling file,** never in the graph document. The graph hash is computed over the canonical form of the document, so moving a card never changes a run's identity.

Rejected: blocking the save of an invalid graph (the person's approval is the control); no validation in the editor (two copies of the rules drift); writing an applied adaptation into the graph (a second author path); layout inside the document (view state in a hashed artifact).

## Guard

`tests/test_graph.py`, `tests/test_report.py`, `tests/test_dashboard.py`, `tests/test_dashboard_edit.py`, `tests/test_dashboard_parity.py`, `tests/test_dashboard_record.py`. A change to this rule changes its tests in the same commit (ADR-0018).
