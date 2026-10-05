# ADR-0117 — The editor prepares a run and never launches it; approval is a person's act outside the graph; the page makes no model call

## Status

Accepted, 2026-10-01. Panel decisions under autonomy on the map [#58](https://github.com/mmctech-ai/agent-squad/issues/58). Extends [ADR-0111](0111-the-editor-writes-what-a-person-authors-reads-the-record-and-shows-an-adaptation-as-a-run-only-overlay.md) and amends none. Lens B was Gemini 3.1 Pro through `agy` in each ticket, because a fork cannot start `panel-lens-b`.

## Context

ADR-0111 fixes what the editor writes, reads, and shows. The map #58 decided the rest of the full flow: authoring, open and save, review, replay, and build shape. Most of those decisions are cheap to reverse and stay in their ticket comments. This ADR holds only those that are hard to reverse and surprising. This ADR does not restate ADR-0111, ADR-0113, or ADR-0114.

## Decision

1. **The editor prepares a run and never launches it** ([#64](https://github.com/mmctech-ai/agent-squad/issues/64)). It shows a **command template**: the graph file name and the engine options a person sets. It never starts a process. Launch from the editor and a standing approval wait for the owner ([#69](https://github.com/mmctech-ai/agent-squad/issues/69)). The default is "never launches".
2. **Approval is a person's act outside the graph file** (#64). The graph holds no signature and no approval field, because it would change the hash (ADR-0114 rule 1). `run_start` proves what ran. It never proves approval. A shown cap or a matching hash approves nothing and authorizes no live write (ADR-0010 rule 3, ADR-0024).
3. **The review view shows declared caps and no computed spend bound** (#64). It shows no whole-graph score or badge. A sum of caps is a false instrument: the engine enforces the run cap before each entry, and loops and dispatch children break any sum (ADR-0012). Errors never block review (ADR-0111 rule 4).
4. **The review sheet is plain text for a person.** No code reads it. It says that a shown cap and a matching hash approve nothing (#64).
5. **A run binds to a hash through an engine option, not through the editor** (#64). `--expect-graph-hash` refuses a run whose hash differs, before any record is written ([#68](https://github.com/mmctech-ai/agent-squad/issues/68)).
6. **The page has no key and no model call** ([#61](https://github.com/mmctech-ai/agent-squad/issues/61), [#66](https://github.com/mmctech-ai/agent-squad/issues/66)). From `file://` it makes no request. From the run server it reads only its own origin ([ADR-0118](0118-squad-dashboard-serves-one-run-folder-over-a-read-only-loopback-server.md)). Author help happens in an outside agent session, and the editor validates the JSON it loads. Only [#67](https://github.com/mmctech-ai/agent-squad/issues/67) reopens this rule, and a credential is owner-only.
7. **The rules are a JS port with a pair guard** (#61). The build embeds the schema, registries, and constants from Python. A parity test compares `(node, rule)` pairs with `squad.graph.validate`, fails when `node` is absent, and never skips. A guard that can skip passes with no check (ADR-0018).
8. **A save never overwrites a changed file in silence** ([#62](https://github.com/mmctech-ai/agent-squad/issues/62)). A conflict offers Reload, Save a copy, or Overwrite. It blocks on a file conflict only, never on a validation error. A draft copy lives in browser storage and never touches a graph or layout file.
9. **The editor keeps a field it does not know** ([#60](https://github.com/mmctech-ai/agent-squad/issues/60)). It shows an error on the node and keeps the field on save. It never drops one in silence.
10. **The editor never recomputes a verdict or the engine's frontier** ([#63](https://github.com/mmctech-ai/agent-squad/issues/63)). It shows the record's `verdict` only at its `pos`. It shows the recorded `frontier` verbatim. The set it computes is "unresolved children".
11. **One graph at a time.** A graph is a file the person names and keeps. The editor has no library and no history store. The diff of two opened files is a view, not a store (#64). Managing many graphs is out of scope on the map #58.

## Considered options

- **A computed spend bound with loop and child multipliers** (Lens B, #64). Rejected 2–1. Reopen if the owner asks for a spend forecast. It then needs a labeled set first (ADR-0012).
- **Launch from the editor.** Deferred to the owner (#69). Two lenses said only the authorization is owner-only. One said the helper itself is.
- **Approval or a signature inside the graph.** Rejected: it changes the hash and adds an author path.
- **A local helper or Pyodide for the rules** (Lens B, #61). Rejected 2–1: both break one offline file. Reopen if a Python runtime in the browser falls under about 1 MB, or the owner accepts a launch command.
- **Model help in the page.** Rejected (#66): it needs a key or a helper.
- **A library of graphs in the editor.** Out of scope on the map: it is storage, and it is cheap to add later.

## Consequences

The editor stays a pure reader and writer of files. The owner keeps launch and approval. A wrong baseline in a diff is visible, because the panel shows both hashes. GLOSSARY.md gains `Review sheet` and `Command template`.

## Guard

Tests that guard this rule:

- `tests/test_dashboard.py::test_the_dashboard_reaches_only_its_own_origin_and_only_the_run_paths`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
