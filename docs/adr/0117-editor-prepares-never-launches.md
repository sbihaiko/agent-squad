# ADR-0117: The editor prepares a run and never launches it; approval is a person's act outside the graph; the page makes no model call

## Status

Accepted. Extends ADR-0111, which fixes what the editor writes, reads, and shows.

## Decision

1. **The editor prepares a run and never launches it.** It shows a command template: the graph file name and the engine options a person sets. It never starts a process.
2. **Approval is a person's act outside the graph file.** The graph holds no signature and no approval field, because either would change the graph hash. `run_start` proves what ran. It never proves approval. A shown cap or a matching hash approves nothing and authorizes no live write (ADR-0024).
3. **The review view shows declared caps and no computed spend bound.** It shows no whole-graph score or badge. A sum of caps is a false instrument: the engine enforces the run cap before each entry, and loops and dispatch children break any sum (ADR-0012). Errors never block review.
4. **The review sheet is plain text for a person.** No code reads it. It says that a shown cap and a matching hash approve nothing.
5. **A run binds to a hash through an engine option, not through the editor.** `--expect-graph-hash` refuses a run whose hash differs, before any record is written.
6. **The page has no key and no model call.** From `file://` it makes no request. From the run server it reads only its own origin (ADR-0118). Author help happens in an outside agent session, and the editor validates the JSON it loads. A credential is owner-only.
7. **The rules are a JS port with a pair guard.** The build embeds the schema, registries, and constants from Python. A parity test compares each `(node, rule)` pair with the Python validator, fails when `node` is absent, and never skips. A guard that can skip passes with no check (ADR-0018).
8. **A save never overwrites a changed file in silence.** A conflict offers Reload, Save a copy, or Overwrite. Only a file conflict blocks a save, never a validation error. A draft copy lives in browser storage and never touches a graph or layout file.
9. **The editor keeps a field it does not know.** It shows an error on the node and keeps the field on save. It never drops one in silence.
10. **The editor never recomputes a verdict or the engine's frontier.** It shows the record's verdict at its position and the recorded frontier verbatim. The set it computes is "unresolved children".
11. **One graph at a time.** A graph is a file the person names and keeps. The editor has no library and no history store. The diff of two opened files is a view, not a store.

Rejected: launch from the editor; a signature inside the graph; a local helper or Pyodide for the rules (both break one offline file); model help in the page (it needs a key).

## Guard

`tests/test_dashboard.py`. A change to this rule changes its tests in the same commit (ADR-0018).
