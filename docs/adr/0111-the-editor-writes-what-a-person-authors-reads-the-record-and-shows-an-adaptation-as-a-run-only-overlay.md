# ADR-0111 — The editor writes what a person authors, reads the record, and shows an adaptation as a run-only overlay

## Status

Accepted, 2026-09-30. Panel decision under autonomy (issue #22). AGREED on what the editor writes, what it reads, the overlay, and the file shape. AGREED 2–1 on which gate fields it may write, on importing an invalid file, and on a `gates` registry. Lens A: Codex `gpt-6-sol`. Lens B: Fable 5.1. Lens C: Gemini 3.1 Pro (High), on the splits only. Lens A ruled on the `gates` registry in a second blind call, because the first brief did not ask it.


Amended 2026-09-30 by [ADR-0113](0113-a-dispatch-node-lets-an-agent-request-children-inside-an-author-fixed-envelope-and-code-validates-each-request.md): the editor writes each dispatch node's envelope and reads the `spawn_*` events. Spawned children are a run-only overlay under the dispatch card.

Amended 2026-10-01 (ticket #91): the page also shows run-only cards for the children of a dispatch, placed by the page and never written to the layout file. A card shows the cause of a failure (the reason code, and what it names). A gate that a dispatch runs on its children takes its state from the child checks. A node with no call in a finished run reads "not run". The side panel lists each failure with the output the record kept, or says that the record kept none. A record with a graph hash that differs still shows the timeline and the verdict only.

## Context

The editor is a later build and out of scope for this map. This record fixes the contract it will meet. The existing kaggle editor loads one flat JSON spec with name-reference edges, runs no schema check on import, reads no run record, and does not export node positions (`docs/research/kaggle-editor-contract.md`). ADR-0100 rule 2 makes a gate's fields read-only in the editor. ADR-0104 makes the `facts` registry closed and code-produced. ADR-0103 says a run never changes the graph file.

## Decision

1. **The editor writes what a person authors:** node names, kinds, ordered children, `next`, `when` over registered facts, the per-kind fields, budget minimums and maximums, deadlines, the `adapt` block with its `sees` list, `each` fields, each `dispatch` node with its envelope and `allow` list, workspaces, and the `run` block.
2. **Read-only in the editor:** a gate's check id and threshold parameters, and the `facts` and `gates` registries. The editor may pick a registered name and never adds one. A gate's name, its place in a loop's `children`, and its `next` are topology, and the editor writes them, or no graph with a gate could be built.
3. **The editor reads a run record as a read-only file** that a person opens. It may read every event kind: `run_start`, `outcome`, the proposal states, the `spawn_*` events, `resume`, and `verdict`. It never writes a record and never derives a graph edit from one (ADR-0102 rule 8, ADR-0103 rule 5).
4. **The editor runs the same JSON Schema and the same rules as the engine.** It shows each error on its node. It never blocks a save or an export. The engine refuses an invalid graph at load.
5. **Import loads any parseable JSON that has a `schema_version`.** It refuses a file that is not parseable or has no `schema_version`, with a message. Errors in a loaded file show on the nodes.
6. **An applied adaptation is a run-only overlay on the target's card.** It shows the proposal id, the kind, the old and requested value, the range checked, and the cited facts with instrument, source, and timestamp (ADR-0103 rule 7). Beside it the editor shows the adaptation report's deltas, with the instrument named, no label, and the line "one observation, not a measurement" (ADR-0109). The graph document is never changed by a run. The editor shows the overlay only when the record's `run_start` graph hash equals the hash of the loaded graph.
7. **The graph document is one JSON file** with `schema_version`, `name`, the `run` block, the `facts` registry, the `gates` registry, `root`, and a flat `nodes` array. The `gates` registry is closed: check ids and their parameter names. It is served from one source with the engine, so the two cannot drift.
8. **Layout lives in a sibling file,** never in the graph document. The graph hash in `run_start` is computed over the canonical form of the graph document, so moving a card never changes a run's identity.

## Considered options

- **Every gate field is read-only, and a gate is edited by hand** (Lens A, dissent on rule 2). Rejected 2–1: a gate is a node with a name and a place in a loop, and the editor could not build a graph with one. The read-only rule of ADR-0100 covers the check id and the thresholds, which never adapt. Reopen if a ruling makes a gate's `next` a threshold. Then a gate node is authored only by hand.
- **Refuse an invalid graph on import** (Lens A, dissent on rule 5). Rejected 2–1: a person could not load a half-authored file to see and fix its errors, and the engine is the authority at load. Reopen if the editor cannot render a node of an unknown kind. Then import refuses those structural classes only.
- **A `gates` registry is not in the document; a gate's `check` is a free string that the engine validates** (Lens C, dissent on rule 7). Rejected 2–1: the editor needs the closed list to offer a check id, and one source for the editor and the engine stops drift. Reopen if a schema serves one authoritative registry outside the graph with the same selection and load-time checks.
- **The editor blocks the save of an invalid graph.** Rejected: it puts a gate where a person is drafting, and the person's approval is the control (ADR-0010 rule 3).
- **No validation in the editor,** as in the kaggle editor. Rejected: it repeats the drift of two copies of the rules.
- **The editor writes an applied adaptation into the graph.** Rejected: it is a second author path, and nothing reverts by itself (ADR-0109 rule 6).
- **No run record in round one, and a report file as the only run input.** Rejected: it needs a per-run summary file, which ADR-0110 rejected, and the record is JSONL that a person can open. Reopen if a record is too large for a browser. Then a code-written index with ids and record positions and no values.
- **Layout as an optional `layout` field in the graph document.** Rejected: it puts view state in the artifact that a run hashes, and a graph diff would show layout noise (ADR-0016 rule 1). Reopen if a lost sidecar makes the owner ask for one file. Then hash the canonical form without `layout`.

## Consequences

GLOSSARY.md gains `Gates registry` and `Layout file`. The document shape gives ticket #23 the fields for the schema. The editor stays out of scope. Ticket #23 writes the schema, the Mermaid graph, and the final index.

## Guard

Tests that guard this rule:

- `tests/test_graph.py::test_refuses_a_gate_check_id_outside_the_registry`
- `tests/test_graph.py::test_refuses_a_document_with_no_schema_version`
- `tests/test_graph.py::test_moving_a_layout_leaves_the_hash_equal`
- `tests/test_graph.py::test_changing_a_node_changes_the_hash`
- `tests/test_graph.py::test_a_layout_key_inside_the_document_is_refused`
- `tests/test_graph.py::test_refuses_an_unregistered_fact_in_a_when_and_a_sees_name`
- `tests/test_report.py::test_the_overlay_shows_what_the_dashboard_needs_and_leaves_the_graph_and_hash_alone`
- `tests/test_spec_shape.py::test_the_check_id_is_registered_and_listed_in_the_fixture`
- `tests/test_dashboard_parity.py::test_dashboard_validation_parity`
- `tests/test_dashboard_parity.py::test_every_engine_rule_site_has_a_mutation_case`
- `tests/test_dashboard_parity.py::test_the_parity_guard_fails_and_never_skips_when_node_is_missing`
- `tests/test_dashboard.py::test_the_built_dashboard_is_current`
- `tests/test_dashboard.py::test_the_dashboard_embeds_the_engine_schema_registry_and_served_lists_not_a_copy`
- `tests/test_dashboard_edit.py::test_a_gate_check_and_thresholds_cannot_be_edited_but_its_place_can`
- `tests/test_dashboard_record.py::test_the_overlay_shows_only_when_the_record_hash_equals_the_graph_hash`
- `tests/test_dashboard_view.py::test_layout_is_a_sibling_file_the_document_never_holds_and_the_hash_ignores`
- `tests/test_dashboard_edit.py::test_an_added_node_gets_the_rules_of_the_engine_whatever_its_kind`
- `tests/test_dashboard_edit.py::test_an_added_gate_picks_a_registered_check_copies_its_thresholds_and_never_touches_the_registries`
- `tests/test_dashboard_edit.py::test_removing_a_node_cuts_the_link_from_its_parent_and_warns_about_each_link_it_breaks`
- `tests/test_dashboard_record.py::test_a_graph_hash_mismatch_keeps_the_timeline_and_the_verdict_only`
- `tests/test_dashboard_record.py::test_a_field_the_record_lacks_is_null_and_never_zero`
- `tests/test_dashboard_edit.py::test_a_rename_leaves_no_stale_reference_on_either_fixture`
- `tests/test_dashboard_edit.py::test_a_rename_updates_the_root_the_children_the_dispatch_entry_and_checks_and_every_outcome_reference`
- `tests/test_dashboard_edit.py::test_a_kind_change_keeps_the_name_the_place_and_the_fields_and_shows_the_engine_rules_on_the_node`
- `tests/test_dashboard_edit.py::test_a_node_changed_to_a_gate_picks_a_registered_check_and_a_gate_is_never_changed_to_another_kind`
- `tests/test_dashboard_record.py::test_the_reread_interval_comes_from_run_start_and_an_older_record_uses_one_second`
- `tests/test_dashboard_record.py::test_the_reread_interval_an_engine_run_records_is_the_one_the_dashboard_reads`
- `tests/test_dashboard_record.py::test_a_growing_record_runs_then_completes_and_its_totals_never_shrink`
- `tests/test_dashboard_record.py::test_the_node_in_progress_shows_the_time_since_its_call_started`
- `tests/test_dashboard_record.py::test_a_run_with_no_growth_for_the_stated_period_shows_as_stalled`
- `tests/test_dashboard_record.py::test_a_finished_run_is_never_stalled_and_a_verdict_other_than_completed_keeps_its_name`
- `tests/test_dashboard_record.py::test_a_reread_that_catches_a_half_written_last_line_drops_it_and_keeps_the_complete_lines`
- `tests/test_dashboard_record.py::test_a_call_with_no_deadline_never_makes_the_run_stalled`
- `tests/test_dashboard_edit.py::test_every_field_of_every_node_sits_in_exactly_one_group_and_every_kind_offers_its_schema_fields`
- `tests/test_dashboard_edit.py::test_an_unknown_field_is_a_node_error_in_both_and_a_field_edit_keeps_it_unchanged`
- `tests/test_dashboard_edit.py::test_a_field_shown_and_set_back_unchanged_leaves_the_graph_byte_identical`
- `tests/test_dashboard_edit.py::test_a_gate_check_thresholds_name_and_kind_cannot_be_set_and_its_next_can`
- `tests/test_dashboard_edit.py::test_a_field_edit_gives_the_engine_rules_on_the_node_whatever_the_value`
- `tests/test_dashboard_save_review.py::test_the_dashboard_page_builds_its_form_from_the_core_and_never_writes_a_registry`
- `tests/test_dashboard_edit.py::test_connect_and_disconnect_results_match_the_engine_for_every_pair_of_nodes`
- `tests/test_dashboard_edit.py::test_a_second_parent_is_allowed_and_the_engine_rule_shows_on_the_child_never_blocking`
- `tests/test_dashboard_save_review.py::test_the_hash_chip_hashes_the_bytes_that_save_writes_and_equals_the_engine_hash`
- `tests/test_dashboard_save_review.py::test_a_draft_copy_holds_five_fixed_keys_never_a_graph_key_and_is_offered_only_for_the_same_name_and_other_text`
- `tests/test_dashboard_save_review.py::test_the_layout_writer_drops_a_key_for_a_missing_node_keeps_the_rest_and_says_what_it_dropped`
- `tests/test_dashboard_save_review.py::test_the_in_place_save_stops_when_the_file_changed_and_a_validation_error_never_stops_it`
- `tests/test_dashboard_save_review.py::test_the_dashboard_saves_by_download_when_the_file_picker_is_absent_and_names_the_mode`
- `tests/test_dashboard_save_review.py::test_the_dashboard_page_writes_a_file_only_after_the_save_gate_and_never_autosaves_to_disk`
- `tests/test_dashboard_save_review.py::test_the_diff_compares_by_node_name_and_lists_added_removed_and_changed_fields`
- `tests/test_dashboard_save_review.py::test_a_diff_of_two_equal_documents_is_empty_and_an_order_change_is_no_change`
- `tests/test_dashboard_save_review.py::test_the_diff_names_a_changed_registry_and_a_field_that_appears_or_goes`
- `tests/test_dashboard_save_review.py::test_declared_caps_are_the_document_values_as_written_and_nothing_is_added_up`
- `tests/test_dashboard_save_review.py::test_the_command_template_carries_the_reviewed_hash_and_every_set_option`
- `tests/test_dashboard_save_review.py::test_the_command_template_is_null_with_an_error_for_each_bad_option_and_overrides_only_go_down`
- `tests/test_dashboard_save_review.py::test_the_review_sheet_is_plain_text_that_says_it_approves_nothing_and_holds_the_five_parts`
- `tests/test_dashboard_save_review.py::test_the_review_code_starts_no_process_and_makes_no_request_and_the_page_has_no_run_control`
- `tests/test_dashboard_record.py::test_tokens_per_second_is_tokens_out_over_elapsed_and_a_record_without_tokens_gives_null_never_zero`
- `tests/test_dashboard_record.py::test_a_call_with_zero_elapsed_gives_no_rate_and_two_entries_sum_their_tokens`
- `tests/test_dashboard_record.py::test_tokens_and_the_rate_show_a_dash_when_missing_and_a_short_unit_when_present`
- `tests/test_dashboard_edit.py::test_undo_walks_back_through_every_edit_and_redo_walks_forward_to_the_same_graph_and_layout`
- `tests/test_dashboard_edit.py::test_a_refused_edit_leaves_no_history_step`
- `tests/test_dashboard_edit.py::test_a_new_edit_after_an_undo_clears_the_redo_stack`
- `tests/test_dashboard_edit.py::test_history_keeps_at_most_its_limit_of_steps_and_drops_the_oldest`
- `tests/test_dashboard_edit.py::test_undo_does_not_change_the_graph_text_the_page_saves`
- `tests/test_dashboard_replay.py::test_the_cursor_folds_only_the_events_up_to_its_pos_into_each_card`
- `tests/test_dashboard_replay.py::test_the_verdict_shows_only_at_or_after_its_own_pos`
- `tests/test_dashboard_replay.py::test_a_cursor_outside_the_record_or_between_two_positions_lands_on_a_real_event`
- `tests/test_dashboard_replay.py::test_a_step_moves_one_event_and_a_step_to_the_end_pins_the_cursor`
- `tests/test_dashboard_replay.py::test_an_accepted_child_with_no_outcome_at_the_cursor_is_unresolved`
- `tests/test_dashboard_replay.py::test_a_hash_mismatch_keeps_the_timeline_and_the_verdict_to_the_cursor`
- `tests/test_dashboard_replay.py::test_a_large_record_opens_with_no_index_and_the_cursor_cuts_it_by_position`
- `tests/test_dashboard_legend_help.py::test_the_legend_has_a_row_for_every_card_color_the_page_paints_and_no_other`
- `tests/test_dashboard_legend_help.py::test_the_legend_swatches_use_the_card_classes_so_a_swatch_cannot_drift_from_its_color`
- `tests/test_dashboard_legend_help.py::test_the_help_lists_every_mouse_and_keyboard_control_the_page_answers_and_no_other`
- `tests/test_dashboard_totals.py::test_a_record_with_no_tokens_shows_unknown_in_the_bar_never_zero`
- `tests/test_dashboard_totals.py::test_the_bar_at_a_replay_cursor_shows_the_totals_up_to_that_position_only`
- `tests/test_dashboard_totals.py::test_partial_tokens_are_marked_as_a_floor_when_an_agent_call_has_a_spend_and_no_tokens`
- `tests/test_dashboard_totals.py::test_the_totals_in_the_statistics_are_the_bar_totals_so_the_two_cannot_drift`
- `tests/test_dashboard_legend_help.py::test_every_button_and_file_label_in_the_page_has_a_title_because_the_help_lists_the_buttons_from_their_titles`
- `tests/test_dashboard_failures.py::test_the_real_run_shows_four_children_in_request_order_with_what_their_calls_recorded`
- `tests/test_dashboard_failures.py::test_a_failed_landing_fails_the_child_and_its_conflict_text_is_the_evidence`
- `tests/test_dashboard_failures.py::test_the_failed_final_gate_and_main_carry_the_cause_and_the_failure_list_names_each_issue_once`
- `tests/test_dashboard_failures.py::test_a_node_that_never_ran_says_so_only_after_the_verdict`
- `tests/test_dashboard_failures.py::test_the_gates_a_dispatch_checks_take_their_state_from_the_children`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
