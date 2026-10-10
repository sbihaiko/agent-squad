# ADR-0118: `squad dashboard` serves one run folder over a loopback server with one write route

## Status

Accepted. Rule 6 of ADR-0117 reads: the page reads only its own origin.

## Context

A page from `file://` cannot read a folder that a person did not choose in the browser picker. The page must open loaded, with no click, and follow a live run.

## Decision

1. **The command starts a server that serves one run folder.** It binds `127.0.0.1` only, on a free port. It makes no model call and starts no run.
2. **The path list is fixed.** The page, `/run/record.jsonl`, `/run/graph.json`, `/run/info.json`, `/run/control.json`, and one stream route. No part of the URL joins a file path. A real path outside the run folder, such as a symlink target, gets 404.
3. **The server checks the Host header.** Only `127.0.0.1:<port>` and `localhost:<port>` pass. A page on another origin cannot read a run through DNS rebinding.
4. **The server answers `GET` and one `POST`.** Any other method gets 405.
5. **The server runs detached and exits when idle.** The idle time is two hours. A connected stream holds the idle clock. One server serves one folder, and a second command reuses it.
6. **The page reads only its own origin, and only the run paths.** From `file://` it keeps the folder picker and makes no request. `--file` prints the `file://` URL.
7. **A plain `squad run` writes the graph it ran to `<run dir>/graph.json`** before the run starts, unless the file exists. The copy is the hash input: its canonical hash equals `graph_hash` in `run_start`. The engine never reads the copy.

### Push

8. **A live run is followed by push: Server-Sent Events over chunked HTTP/1.1.** The traffic is one way, so the server keeps its standard-library handler, its allowlist, its Host check, and no new dependency. Rejected: WebSocket and long poll.
9. **One watcher serves every open stream.** It stats the record path every `dashboard.watch_seconds` and reads the file only when its size or modification time changed. It holds no descriptor, because every append replaces the whole file (ADR-0030).
10. **Each event carries one record line unchanged**, with its position as the event id. A reconnect sends `Last-Event-ID` and receives the later events.
11. **A position that skips one ends the stream with the reason `gap`.** The stream never paints a gap.
12. **The stream ends when the verdict is on the wire, and says why.** The page then closes it, so `EventSource` does not reconnect in a loop.
13. **The page has one seam, the run source.** A source with a stream is followed by push. A source with none (a `file://` page, a folder-list snapshot) is followed by the re-read interval. No other page code learns the mode.

### Pause and resume

14. **A request is a line in `control.jsonl` in the run folder:** `{"action": "pause"|"resume", "nodes": [...]}`. No `nodes` means the whole run. The file only grows and sits beside the record, never in it, because the engine rewrites the record on every event and a second writer would lose a request. An invalid line is skipped.
15. **The engine folds the file at each node entry.** A held node does not start. A running call finishes, so its reserved spend is never lost (ADR-0108). A resume of a node under a whole-run pause releases that node and keeps the rest held.
16. **A pause ends the process, like a wait.** The engine records a `paused` event with `node`, `scope`, `held`, and `completed`, then halts. The verdict is `stopped` with the reason `paused:<node>`. The page shows these lists as recorded and works out no frontier (ADR-0117). A paused run holds no process.
17. **In a dispatch, a held child does not start and its siblings go on.** A child that runs `after` a held child is held with it. The process ends when no child can run. A resume runs the held children and not the finished ones.
18. **`squad resume` releases every hold.** The person who types the command resumes. The page may send a resume request, but it never starts a process: it shows `squad resume <run>` for the person to type.
19. **The one write route is `POST /run/control`.** It appends one line to the control file and writes nothing else. It needs the Host check, an `Origin` that is the page's own (or none), `Content-Type: application/json`, a body of at most 4 KB, and a valid request: an action and at most 100 node names matching `[A-Za-z0-9_.:-]{1,200}`. A run that has ended gets 409. A run has ended on any verdict except the `stopped` that a pause writes.
20. **A request grants no approval.** It changes only this run's schedule on this machine. It is not a consequential live write (ADR-0024).

Rejected: a signal or socket to the engine (no pidfile, and dispatch children share one process); holding the process in place (the deadline keeps counting and worktrees stay pinned); killing a running call at a pause (its reserved cap stays as unmeasured spend).

## Consequences

Any local process can read the run folder, and can pause the run, through the loopback port. A run folder holds no credential. If a local process that pauses a run proves a real nuisance, the write route goes behind a token.

## Guard

`tests/test_dashboard_server.py`, `tests/test_dashboard_page.py`, `tests/test_dashboard_control.py`, `tests/test_dashboard_pause.py`, `tests/test_pause.py`, `tests/test_cli.py`. A change to this rule changes its tests in the same commit (ADR-0018).
