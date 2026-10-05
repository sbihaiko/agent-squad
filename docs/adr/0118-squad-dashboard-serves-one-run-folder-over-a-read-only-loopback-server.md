# ADR-0118 — `squad dashboard` serves one run folder over a read-only loopback server

## Status

Amended 2026-10-04 by [ADR-0128](0128-the-dashboard-may-pause-and-resume-a-launched-run-through-a-control-file-and-the-run-server-gains-one-write-route.md): the server has one write route, `POST /run/control`, a pause or resume request. Rules 1 and 4 read with that exception. It still starts no run.

Accepted, 2026-10-01. Owner request: the command with a run number must open the page already loaded, with no click on Connect run folder. Amends rule 6 of [ADR-0117](0117-the-editor-prepares-a-run-and-never-launches-it-approval-is-a-person-s-act-outside-the-graph-and-the-page-makes-no-model-call.md).

## Context

A page from `file://` cannot read a folder that a person did not choose in the browser picker. The owner wants the page to open loaded.

## Decision

1. **The command starts a server that serves one run folder.** It binds `127.0.0.1` only, on a free port. It answers `GET` only. Any other method gets 405.
2. **The path list is fixed.** The page, `/run/record.jsonl`, `/run/graph.json`, and `/run/info.json`. No part of the URL joins a file path. A real path outside the run folder, such as a symlink target, gets 404.
3. **The server checks the Host header.** Only `127.0.0.1:<port>` and `localhost:<port>` pass. A page on another origin cannot read a run through DNS rebinding.
4. **The server has no write route and starts no run** (ADR-0117 rule 1). It makes no model call.
5. **The server runs detached and exits when idle.** The idle time is two hours. The check that finds a running server is not use. One server serves one folder, and a second command reuses it.
6. **The page reads only its own origin, and only the run paths.** From `file://` it keeps the folder picker and makes no request. `--file` prints the `file://` URL.

## Considered options

- **A custom URL scheme or a browser extension.** Rejected: both need an install.
- **Pass the run in the URL.** Rejected: a record is too large, and it would not follow a live run.
- **Keep the picker only.** Rejected by the owner's words.

## Consequences

A local process listens on loopback while a run page is open. Any local process can read that run folder through it. A run folder holds no credential (ADR-0117 rule 6 and the engine's record rules). GLOSSARY.md gains `Run server`.

## Guard

Tests that guard this rule:

- `tests/test_dashboard_server.py::test_it_serves_the_page_the_record_and_the_graph_of_one_run`
- `tests/test_dashboard_server.py::test_a_wrapped_run_serves_its_nested_record`
- `tests/test_dashboard_server.py::test_a_record_that_grows_is_read_fresh_on_every_request`
- `tests/test_dashboard_server.py::test_a_path_off_the_allowlist_or_a_traversal_is_not_found`
- `tests/test_dashboard_server.py::test_a_symlink_that_leaves_the_run_folder_is_not_served`
- `tests/test_dashboard_server.py::test_a_symlink_that_stays_inside_the_run_folder_is_served`
- `tests/test_dashboard_server.py::test_a_wrong_host_header_is_refused_so_dns_rebinding_reads_nothing`
- `tests/test_dashboard_server.py::test_a_request_with_no_host_header_is_refused`
- `tests/test_dashboard_server.py::test_localhost_with_the_right_port_is_allowed`
- `tests/test_dashboard_server.py::test_only_get_is_answered_and_nothing_is_written`
- `tests/test_dashboard_server.py::test_it_binds_the_loopback_address_only`
- `tests/test_dashboard_server.py::test_it_exits_when_no_request_came_for_the_idle_time`
- `tests/test_dashboard_server.py::test_a_request_keeps_the_server_alive_past_the_first_idle_window`
- `tests/test_dashboard_server.py::test_a_detached_server_is_reused_for_the_same_run_and_exits_when_idle`
- `tests/test_dashboard_server.py::test_a_stale_state_file_is_dropped_and_a_new_server_starts`
- `tests/test_dashboard_server.py::test_the_server_module_has_no_route_that_writes_or_starts_a_run`
- `tests/test_dashboard_page.py::test_a_page_served_over_http_loads_its_run_with_no_folder_click_and_the_file_page_keeps_the_picker`
- `tests/test_dashboard_page.py::test_the_served_source_reads_the_record_and_the_graph_by_path_and_is_live`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
