# ADR-0121 — A call in flight records where it has got to, and a child card reads running like a node card

## Status

Accepted, 2026-10-02. Owner report, ticket [#103](https://github.com/mmctech-ai/agent-squad/issues/103). Amends [ADR-0051](0051-a-call-has-a-deadline-and-a-stuck-agent-is-found-without-a-caller.md) rule 4.

## Context

The owner watched a run on the dashboard and said a node never showed the running state: it went straight to its
result. Two defects sat behind that.

**The record said nothing about a call between its start and its end.** `squad/sdk_adapter.py` read the SDK stream and
dropped every message except the `ResultMessage`, so the only live figure in the record was the time since
`call_start.started_at`. ADR-0051 rule 4 already names this shape: "A record that says running while its process is
dead is abandoned... the last turn was never recorded." A reader could not tell a live call from an abandoned one.

**A dispatch child card never painted the running state.** `dashboard/core.js::runChildren` gave a child in flight the
state `started` and no time, and `renderChildren` built its class from `c.outcome` alone. So a child card kept a dashed
outline and three dashes for its whole call — sixteen minutes in the 24-issue run — while the graph node beside it
painted `running` with a live timer. All the work of a router run sits in its children, so the owner watched the one
surface that never moved.

## Decision

1. **A call in flight writes `progress` events through the adapter's sink.** Each carries the node, the entry, the
   session, the assistant messages so far, the context the newest message read, and the last tool the call used.
2. **At most one `progress` event per call per `progress.seconds`**, a setting in `config/agent-defaults.yaml`. The
   interval of `null` writes none at all. The setting is the lever on the record's growth: every event costs one
   atomic rewrite of the record file (ADR-0030).
3. **A progress figure is the newest reading, never a sum.** Probed on a real call, one assistant message reports the
   prompt of that response, so the newest message gives the size of the context now. Adding them would count the same
   context once per turn.
4. **`messages` is not the result's `turns`, and neither label stands for the other.** Probed on a real call, one turn
   reaches the stream as two assistant messages, so a live counter of messages and the final count of turns differ. A
   card labels them apart: `msg` for the live count, `t` for the outcome's.
5. **Nothing is claimed before the CLI reports it.** An unreported figure is null and reads unknown, never zero
   (ADR-0012). The CLI reports no output tokens before the result, so a progress event claims none.
6. **A child card in flight reads the same state a node card reads.** One rule decides the state, the clock and the
   live figures for both (`openCalls`), and the page paints what the core decides.

## Consequences

A reader can tell a live call from an abandoned one without watching the process. The dashboard shows a call moving:
its time, its messages, its context, its last tool.

Cost: the record grows with every interval, and each append rewrites the whole file. A run of many parallel children
should raise `progress.seconds` rather than add a second event kind.

Not done here: the monitor (ADR-0051 rule 3) still finds a stuck call from `call_start` alone. It can read `progress`
later. The token split a call reports per cache bucket stays unrecorded (ticket #102).

## Guard

Tests that guard this rule:

- `tests/test_dashboard_live_call.py::test_a_child_in_flight_reads_running_with_the_time_since_its_call_started`
- `tests/test_dashboard_live_call.py::test_a_child_in_flight_with_no_clock_reads_still_started`
- `tests/test_dashboard_live_call.py::test_a_child_that_has_not_started_carries_no_state_at_all`
- `tests/test_dashboard_live_call.py::test_a_child_that_finished_is_not_running`
- `tests/test_dashboard_live_call.py::test_the_last_progress_event_of_a_call_in_flight_is_what_the_card_shows`
- `tests/test_dashboard_live_call.py::test_a_progress_event_of_a_finished_call_does_not_mark_it_running`
- `tests/test_dashboard_live_call.py::test_a_call_left_open_by_a_finished_run_never_reads_as_running_again`
- `tests/test_dashboard_live_call.py::test_a_node_in_flight_carries_its_live_figures_and_its_tool`
- `tests/test_dashboard_live_call.py::test_the_child_card_paints_the_class_the_core_decides_and_shows_its_live_figures`
- `tests/test_dashboard_live_call.py::test_the_running_card_shows_the_live_turns_and_tokens`
- `tests/test_sdk_adapter.py::test_each_assistant_message_records_the_progress_of_the_call`
- `tests/test_sdk_adapter.py::test_a_long_interval_holds_the_progress_events_and_the_result_still_lands`
- `tests/test_sdk_adapter.py::test_an_interval_of_none_writes_no_progress_at_all`
- `tests/test_sdk_adapter.py::test_an_adapter_with_no_sink_records_nothing_and_never_fails`
- `tests/test_sdk_adapter.py::test_the_progress_interval_comes_from_the_agent_defaults`
- `tests/test_sdk_adapter.py::test_a_progress_event_with_no_usage_yet_leaves_the_context_unknown_never_zero`
- `tests/test_sdk_adapter.py::test_a_progress_event_waits_out_the_interval_and_the_interval_restarts_at_each_write`
- `tests/test_sdk_adapter.py::test_a_message_that_reports_no_whole_count_leaves_the_last_context_reading_alone`
- `tests/test_defaults_wiring.py::test_the_progress_interval_is_a_setting_and_an_unsound_one_refuses_the_run`
- `tests/test_record.py::test_a_progress_event_carries_where_its_call_is_and_refuses_without_it`

Open: the monitor still finds a stuck call from call_start alone; the per-call cache split stays unrecorded (ticket #102).
