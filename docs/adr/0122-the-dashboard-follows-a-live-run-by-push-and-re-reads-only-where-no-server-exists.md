# ADR-0122 — The dashboard page follows a live run by push, and re-reads only where no server exists

## Status

Accepted, 2026-10-02. Owner request, spec [#104](https://github.com/mmctech-ai/agent-squad/issues/104), ticket [#105](https://github.com/mmctech-ai/agent-squad/issues/105). Amends [ADR-0118](0118-squad-dashboard-serves-one-run-folder-over-a-read-only-loopback-server.md) rules 2 and 5.

## Context

The page followed a live run by re-reading the whole record every `reread_seconds` (1 s), for every open page.
It showed a state up to one second old, and a long run moved hundreds of kilobytes per page per second over the
loopback. The owner asked for a push channel, so the page shows what the record holds as soon as the run writes it.

Two modes have no server to push from: a `file://` page that reads a run folder through a browser file handle, and a
folder-list snapshot. Those two must keep the re-read path.

## Decision

1. **The server offers one stream route beside the fixed paths of ADR-0118 rule 2.** The route joins that allowlist:
   no part of the URL joins a path, the Host check applies, and the server still answers `GET` only.
2. **The stream is Server-Sent Events over a chunked HTTP/1.1 response, not WebSocket.** The traffic is one way: the
   server pushes, the page sends nothing on this channel. SSE is plain HTTP, so the server keeps its standard-library
   handler, its allowlist, its Host check and its read-only posture (ADR-0117), and gains no dependency. The browser
   reconnects on its own.
3. **One watcher serves every open stream.** The server stats the record path every `dashboard.watch_seconds`
   (default 0.2 s) and reads the file only when its size or its modification time changed. It holds no descriptor
   between reads: every append replaces the whole file (ADR-0030), so the path is the fact to watch and an open
   descriptor is the wrong thing to hold.
4. **Each server-sent event carries one record line unchanged**, with its position as the event id. A page that
   reconnects sends `Last-Event-ID` and receives the events after that position.
5. **A position that skips one ends the stream with the reason `gap`.** The record's own reader refuses a gap, so the
   stream must not paint one: it says why it stopped instead.
6. **The stream ends when the verdict is on the wire, and says why.** The page closes the stream, because
   `EventSource` reconnects by itself and would otherwise loop.
7. **A connected stream holds the idle clock.** The server still exits when no request and no stream came for the idle
   time (ADR-0118 rule 5). A client that went away ends its handler thread with no noise.
8. **The page keeps one seam: the run source.** A source that offers a stream is followed by push. A source that does
   not is followed by the re-read interval, which keeps its meaning and its setting for the two modes with no server.
   No other page code learns which mode it is in.

## Considered options

- **WebSocket.** Rejected: it buys a client-to-server channel this feature does not use, and pays an upgrade
  handshake, a framing layer and a library for it. The server is the Python standard library.
- **A long poll.** Rejected: it is the re-read by another name, one connection per interval.
- **A push channel for the `file://` page too.** Rejected: no server exists there to push from.
- **A file-system watcher on the descriptor.** Rejected: the append replaces the file, so a descriptor watches the
  file that was there before the write.

## Consequences

The latency between a write and the page falls from the re-read interval to the watch step, and a page transfers only
the events it has not seen. A run folder costs one watcher thread, started by the first stream and ended by the
verdict. The server gains a chunked response path, and the page gains a second follow path that the source seam hides.

Cost: a settings file now carries two intervals — the watch step for a served page and the re-read interval for a
`file://` page. The re-read interval stays, because those two modes have no other way to follow a run.

The record's shape and its writer are untouched (ADR-0030): the run remains the only writer, and the stream is a
reader.

## Guard

Tests that guard this rule:

- `tests/test_dashboard_server.py::test_the_stream_answers_the_event_stream_content_type_and_live_events`
- `tests/test_dashboard_server.py::test_the_stream_sends_the_record_lines_unchanged_with_the_position_as_the_id`
- `tests/test_dashboard_server.py::test_a_client_that_connects_late_is_given_the_events_after_the_id_it_sends`
- `tests/test_dashboard_server.py::test_a_client_with_no_id_is_given_every_event_from_the_first`
- `tests/test_dashboard_server.py::test_the_stream_ends_when_the_verdict_is_on_the_wire`
- `tests/test_dashboard_server.py::test_a_position_gap_ends_the_stream_with_the_reason`
- `tests/test_dashboard_server.py::test_the_stream_refuses_a_foreign_host_and_every_method_but_get`
- `tests/test_dashboard_server.py::test_one_watcher_reads_the_record_for_every_open_stream`
- `tests/test_dashboard_server.py::test_a_stream_waits_for_a_record_that_is_not_written_yet`
- `tests/test_dashboard_server.py::test_a_client_that_went_away_leaves_no_thread_behind`
- `tests/test_defaults_wiring.py::test_an_unsound_watch_step_refuses_the_run`
- `tests/test_defaults_wiring.py::test_run_start_carries_the_dashboard_reread_interval_from_the_defaults_file`
- `tests/test_cli.py::test_dashboard_serves_the_run_and_prints_a_loopback_url_the_page_loads_with_no_click`
- `tests/test_cli.py::test_dashboard_takes_the_stream_watch_step_from_the_defaults_file`
- `tests/test_dashboard_page.py::test_a_source_that_is_given_a_stream_function_offers_the_stream_and_one_that_is_not_offers_none`
- `tests/test_dashboard_page.py::test_the_folder_handle_and_folder_list_sources_offer_no_stream_and_only_the_served_page_opens_one`
- `tests/test_dashboard_page.py::test_a_pushed_event_extends_the_events_and_a_pushed_verdict_ends_the_follow_and_closes_the_stream`
- `tests/test_dashboard_page.py::test_an_event_the_page_already_has_is_skipped_so_a_replay_after_reconnect_paints_nothing_twice`
- `tests/test_dashboard_page.py::test_a_position_gap_is_reported_and_never_painted`
- `tests/test_dashboard_page.py::test_the_end_event_closes_the_stream_so_the_browser_does_not_reconnect_in_a_loop`
- `tests/test_dashboard_page.py::test_an_end_for_any_other_reason_falls_back_to_the_re_read_with_the_reason`
- `tests/test_dashboard_page.py::test_a_dropped_stream_reconnects_then_gives_up_after_three_errors_and_a_closed_one_gives_up_at_once`
- `tests/test_dashboard_page.py::test_a_stream_that_throws_or_pushes_garbage_leaves_the_page_following_by_re_read`
- `tests/test_dashboard_page.py::test_the_page_follows_by_push_when_the_source_has_a_stream_else_by_the_timer_and_says_which`
- `tests/test_dashboard_page.py::test_no_control_in_the_page_sets_either_interval`

Open: the page side landed in ticket #106 and is exercised under node with a fake stream; the chunked path is not yet driven in a browser
