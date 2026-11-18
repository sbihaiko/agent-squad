"""The full suite's CI shards: which test files each shard job runs, in both modes (#1518).

`ci.yml` runs the full suite in shard jobs by test path, never in matrix legs (ADR-0163): `full-xdist-<k>` runs
`python3 -m pytest -n auto $(python3 tests/ci_shards.py <k>)`, and `full-serial-<k>` runs the same files with
`-p no:xdist`. Every test file of `tests/` is in exactly one shard, and tests/test_tooling.py fails when a file is in no
shard or in two: a new test file is assigned here before a green shard set can stand for the full proof.

The partition is balanced by a declared weight, one per test function and ten per `heavy` mark, because a heavy call spends
at least a second and often a subprocess. The file count balances nothing on its own, and a rebalance is an edit of the
lists below, proved exact again by the same guard.

The balance is by time too, and the wave suites are what proves it: a file whose calls run past twenty seconds is spread
one per shard, because a sibling job's check is the wall clock of the slowest shard. Shard 3 held every wave suite once and
set the tier at nine minutes while its siblings finished in two (#1551).

usage: python3 tests/ci_shards.py <shard>   print the shard's test files, one line (shard 1 to SHARD_COUNT)
"""
import sys

SHARD_COUNT = 6

SHARDS: tuple[tuple[str, ...], ...] = (
    (
        "tests/test_ac_repeat.py", "tests/test_ac_verify_routes.py", "tests/test_actions.py", "tests/test_active_runs_write.py", "tests/test_adapt.py", "tests/test_agent_doors.py", "tests/test_spec_stall.py",
        "tests/test_bash_time.py", "tests/test_run_time.py","tests/test_board_define.py", "tests/test_board_labels.py", "tests/test_board_lag.py", "tests/test_browser_bounds.py", "tests/test_chain_fallback.py", "tests/test_chain_view.py",
        "tests/test_coding_model_tools.py", "tests/test_dashboard_layer_focus.py", "tests/test_dashboard_narrow.py", "tests/test_dashboard_page.py", "tests/test_dashboard_parity.py",
        "tests/test_dashboard_view.py", "tests/test_deadlock_validator.py","tests/test_edge_write.py","tests/test_wave_migrate.py","tests/test_each.py", "tests/test_entry_no_progress.py", "tests/test_fundroom.py", "tests/test_gate_lock.py",
        "tests/test_gh_budget_per_pass.py", "tests/test_glossary_name.py", "tests/test_graph.py", "tests/test_gui_test_e2e.py", "tests/test_gui_test_format.py",
        "tests/test_hub_accessibility.py", "tests/test_hub_attachment.py", "tests/test_hub_board_move_async.py", "tests/test_hub_board_pending.py", "tests/test_hub_board_prediction.py", "tests/test_hub_board_repo_scope.py",
        "tests/test_hub_board_stale.py", "tests/test_hub_board_task_count.py", "tests/test_hub_publish_restart.py", "tests/test_hub_child_transcript_usage.py", "tests/test_hub_gate_strip.py", "tests/test_hub_grok_quota_scan.py",
        "tests/test_hub_grok_refresh.py", "tests/test_hub_instruments.py", "tests/test_hub_lane_collapse.py", "tests/test_hub_lane_grid.py", "tests/test_hub_mobile_card_facts.py", "tests/test_hub_mobile_icons.py", "tests/test_hub_mobile_lanes.py",
        "tests/test_hub_mobile_motion.py", "tests/test_hub_mobile_reload.py", "tests/test_hub_mobile_text_targets.py", "tests/test_hub_mobile_warnings.py", "tests/test_hub_model_level_switch.py",
        "tests/test_hub_mux.py", "tests/test_hub_mux_terminals.py", "tests/test_hub_question_card.py", "tests/test_hub_resumed_verdict.py", "tests/test_hub_seam_binds_its_profile.py",
        "tests/test_hub_session_image.py", "tests/test_hub_status_spend.py", "tests/test_hub_swimlanes.py", "tests/test_hub_terminal_routes.py", "tests/test_hub_webapp.py", "tests/test_hub_work_branches.py",
        "tests/test_hubmux_shared_board.py", "tests/test_kind_chains.py", "tests/test_lead_survival.py", "tests/test_load_reading_stays_a_reading.py", "tests/test_machine_caps.py",
        "tests/test_mermaid.py", "tests/test_message_ceiling.py", "tests/test_model_naming.py", "tests/test_model_resolver_seam.py", "tests/test_modelreg_model_off.py",
        "tests/test_monitor.py", "tests/test_nightly_obligation.py", "tests/test_profiler_dataset.py", "tests/test_prune.py", "tests/test_qwen_key_gate.py",
        "tests/test_reap_leaks.py", "tests/test_repo_hygiene.py", "tests/test_resume.py", "tests/test_review_nochange.py", "tests/test_review_streak.py", "tests/test_run_state_vocabulary.py", "tests/test_state_binding_record.py",
        "tests/test_tailnet_env.py", "tests/test_tailnet_settings.py", "tests/test_terminal_comment.py", "tests/test_to_test_scenario_publish.py", "tests/test_to_test_scenario_tree_publish.py",
        "tests/test_transport_retry_record.py", "tests/test_two_flows.py", "tests/test_vendor_skills.py",
    ),
    (
        "tests/test_autonomy_fragment.py", "tests/test_board_archive.py", "tests/test_board_columns.py", "tests/test_board_definition_grammar.py", "tests/test_board_reset.py", "tests/test_child_issues.py", "tests/test_dashboard_graph_read.py", "tests/test_dashboard_totals.py",
        "tests/test_day_crossing_gate.py", "tests/test_dispatch_mutex.py", "tests/test_envelope.py", "tests/test_gate_evidence.py", "tests/test_gate_reuse.py",
        "tests/test_gate_scope.py", "tests/test_gh_spend.py", "tests/test_gui_test_subset.py", "tests/test_gui_test_vision.py", "tests/test_hub_adapter_errors.py",
        "tests/test_hub_attach.py", "tests/test_hub_board_card_number.py", "tests/test_hub_board_flow_charts.py", "tests/test_hub_board_pass.py", "tests/test_hub_board_title_builder.py", "tests/test_hub_card_sync.py", "tests/test_hub_sheet_sync.py",
        "tests/test_hub_cycle_countdown.py", "tests/test_hub_dash_forward.py", "tests/test_hub_gate_fact.py", "tests/test_hub_loop_log_stamp.py", "tests/test_hub_mobile_action_row.py",
        "tests/test_hub_mobile_grab_handle.py", "tests/test_hub_mobile_list_states.py",
        "tests/test_hub_mobile_filters_search.py", "tests/test_hub_mobile_move.py", "tests/test_hub_mobile_overlay_clearance.py", "tests/test_hub_mobile_profiler.py", "tests/test_hub_mobile_page_fills.py",
        "tests/test_hub_mobile_run_detail.py", "tests/test_hub_mobile_run_overview.py", "tests/test_hub_mobile_safe_area.py", "tests/test_hub_monitor_throughput_first.py",
        "tests/test_hub_no_alert_strip.py", "tests/test_hub_overlay_stream.py", "tests/test_hub_pr_review_chip.py", "tests/test_hub_process_lock.py", "tests/test_hub_procscan_cache.py", "tests/test_hub_quota_probe.py",
        "tests/test_hub_run_groups.py", "tests/test_hub_run_liveness.py", "tests/test_hub_runs_active.py", "tests/test_hub_runs_scope.py", "tests/test_hub_settings_view.py", "tests/test_hub_slack_settings.py",
        "tests/test_hub_state_default.py", "tests/test_hub_switcher.py", "tests/test_hub_tailnet.py", "tests/test_hub_token_burn.py", "tests/test_hubmux_profiles.py",
        "tests/test_issue_ledger.py", "tests/test_issue_state_gate_tries.py", "tests/test_issue_state_priority.py", "tests/test_issue_state_repo_cache.py",
        "tests/test_priority_backfill.py", "tests/test_birth_policy.py", "tests/test_issue_state_test_case.py", "tests/test_janitor_dashboard_ceiling.py", "tests/test_janitor_stranded_card.py",
        "tests/test_handhold.py", "tests/test_hub_live_cache.py", "tests/test_hub_live_work.py",
        "tests/test_janitor_hand_hold.py", "tests/test_janitor_takeover.py", "tests/test_retire.py", "tests/test_jev_screen_set.py", "tests/test_launch.py", "tests/test_launch_modules.py", "tests/test_launch_off_models.py", "tests/test_limits.py",
        "tests/test_livecheck.py", "tests/test_model_fallthrough.py", "tests/test_plugin.py", "tests/test_release_changelog.py", "tests/test_review_calibration.py",
        "tests/test_review_fix.py", "tests/test_review_grok_measure.py", "tests/test_run_liveness.py", "tests/test_scripts_manifest.py", "tests/test_shell_suites.py",
        "tests/test_slow_cap.py", "tests/test_stall.py", "tests/test_steprec.py", "tests/test_subject_config.py", "tests/test_tailnet_access.py", "tests/test_tailnet_check.py",
        "tests/test_tailnet_exposure_guard.py", "tests/test_to_test_scenario_areas.py", "tests/test_to_test_scenario_write_back_areas.py", "tests/test_voiced.py", "tests/test_voice_setup.py",
        "tests/test_operator_probe.py", "tests/test_operators.py", "tests/test_proxystop.py", "tests/test_launch_agents.py", "tests/test_workcmd.py",
        "tests/test_workcmd_counts.py", "tests/test_check_ste.py", "tests/test_drain_policy.py",
    ),
    (
        "tests/test_board_history_fixtures.py", "tests/test_board_write_unchanged.py", "tests/test_chat_facts.py", "tests/test_cli.py", "tests/test_config_write.py", "tests/test_dashboard_child_issues.py",
        "tests/test_dashboard_labels.py", "tests/test_dashboard_leak_guard.py", "tests/test_dashboard_replay.py", "tests/test_dashboard_server.py", "tests/test_dashboard_states.py",
        "tests/test_dashboard_tailnet.py", "tests/test_defaults.py", "tests/test_envelope_workspace.py", "tests/test_field_run.py", "tests/test_gate_tries_surface.py",
        "tests/test_gh_aggregate_reads.py", "tests/test_gh_interface_check.py", "tests/test_gh_throttle.py", "tests/test_hub_board_blockers.py", "tests/test_hub_board_priority.py", "tests/test_hub_chain_shape.py",
        "tests/test_hub_check_board.py", "tests/test_hub_ci_queued_chip.py", "tests/test_hub_history_writer.py", "tests/test_hub_host_guard.py", "tests/test_hub_lane_render.py",
        "tests/test_hub_live_card.py", "tests/test_hub_mobile_chat.py", "tests/test_hub_mobile_link.py", "tests/test_hub_mobile_photo.py", "tests/test_hub_mobile_refresh.py", "tests/test_hub_mobile_terminals.py",
        "tests/test_hub_mobile_usage.py",
        "tests/test_hub_model_boxes.py", "tests/test_hub_models_save_keeps_other_chains.py", "tests/test_operator_usage.py", "tests/test_hub_needs_human_handled.py", "tests/test_hub_overlay.py", "tests/test_hub_promote.py",
        "tests/test_hub_prs_two_repos.py", "tests/test_hub_quota.py", "tests/test_hub_run_highlight.py", "tests/test_hub_run_watch.py", "tests/test_hub_sidebar_bar.py",
        "tests/test_hub_spend_hourly.py", "tests/test_hub_test_pass_lost.py", "tests/test_hub_token_ledger.py", "tests/test_issue_state_kind.py", "tests/test_issue_state_log_dir.py",
        "tests/test_issue_state_unread.py", "tests/test_janitor_card_done_base.py", "tests/test_janitor_local_only_landing.py", "tests/test_janitor_rearm_freeze.py", "tests/test_janitor_zombie_line.py", "tests/test_loop_review_gate.py", "tests/test_models_v2.py",
        "tests/test_janitor_wave_marker.py",
        "tests/test_end_class.py", "tests/test_proc_reader.py", "tests/test_profiler.py", "tests/test_promote_dead_code.py", "tests/test_promote_gate_runner.py", "tests/test_report.py", "tests/test_review_route.py",
        "tests/test_review_stream.py", "tests/test_runners.py", "tests/test_runs.py", "tests/test_scaffold.py", "tests/test_sdk_adapter.py", "tests/test_shell_suite_runner.py",
        "tests/test_shell_suite_seam.py", "tests/test_spec_fold.py", "tests/test_spend.py", "tests/test_state_seam_callers.py", "tests/test_to_test_scenario_skill_doc.py",
        "tests/test_to_test_scenario_type_labels.py", "tests/test_token_burners.py", "tests/test_verdict.py", "tests/test_wave_land.py",
    ),
    (
        "tests/test_board_history.py", "tests/test_board_migrate.py", "tests/test_child_dispatch_no_pr.py", "tests/test_ci_runner_switch.py", "tests/test_coding_chain_envelope.py",
        "tests/test_dashboard_control.py", "tests/test_dashboard_groups.py", "tests/test_dashboard_node_model.py", "tests/test_dashboard_responsive.py", "tests/test_dashboard_single_card.py",
        "tests/test_dashboard_skills.py", "tests/test_deadlines.py", "tests/test_defaults_wiring.py", "tests/test_delivery_metrics.py", "tests/test_dispatch.py",
        "tests/test_engine_skeleton.py", "tests/test_facts.py", "tests/test_five_phases.py", "tests/test_gate_start.py", "tests/test_gh_read.py",
        "tests/test_gui_test_scenario.py", "tests/test_heartbeat.py", "tests/test_live_check_orphans.py", "tests/test_hub_board_flow_history.py", "tests/test_hub_board_no_kinds.py", "tests/test_hub_box_routes_page.py",
        "tests/test_hub_chart_stream.py", "tests/test_hub_empty_column_strip.py", "tests/test_hub_gate_queue.py", "tests/test_hub_github_mark_size.py", "tests/test_hub_issue_sheet.py",
        "tests/test_hub_lead_dots_e2e.py", "tests/test_hub_mobile_gesture_cleanup.py", "tests/test_hub_mobile_loops.py", "tests/test_hub_mobile_monitor.py", "tests/test_hub_mobile_polls.py", "tests/test_hub_mobile_runs.py",
        "tests/test_hub_mobile_usage_screen.py", "tests/test_hub_mobile_voice.py", "tests/test_hub_voice.py",
        "tests/test_hub_model_labels.py", "tests/test_hub_model_rows_routes.py", "tests/test_hub_models_save.py", "tests/test_hub_plans_save.py", "tests/test_hub_pr_gate_reason.py",
        "tests/test_hub_runs_ended.py", "tests/test_hub_runs_tab.py", "tests/test_hub_settings_clutter.py", "tests/test_hub_settings_shape.py", "tests/test_hub_stream.py",
        "tests/test_hub_swimlane_specs_closed.py", "tests/test_hub_tab_hashes.py", "tests/test_hub_terminal_e2e.py", "tests/test_issue_state_binding.py", "tests/test_issue_state_graphql_fallback.py",
        "tests/test_issue_state_holder.py", "tests/test_board_items_pagination.py", "tests/test_board_snapshot.py", "tests/test_janitor_wave_gating.py", "tests/test_jev_shadow.py", "tests/test_model_contract_live.py", "tests/test_model_label_scheme.py",
        "tests/test_modelreg.py", "tests/test_probe_gateway_cache.py", "tests/test_process_bound.py", "tests/test_profile_comment.py", "tests/test_real_app_guard.py",
        "tests/test_review_gate.py", "tests/test_review_retry.py", "tests/test_review_shadow.py", "tests/test_run_caps.py", "tests/test_run_router.py",
        "tests/test_sessions.py", "tests/test_setup.py", "tests/test_skills.py", "tests/test_squad_full_gate_machine_lock.py", "tests/test_squad_goal_state_split.py",
        "tests/test_squad_shell_suite_gate.py", "tests/test_subject_key.py", "tests/test_to_test_scenario_start_run.py", "tests/test_tooling.py", "tests/test_web_hex_budget.py",
        "tests/test_workcmd_evidence_assertion.py",
    ),
    (
        "tests/test_board_cfd.py", "tests/test_board_facts.py", "tests/test_board_flow_filters.py", "tests/test_board_leadtime.py", "tests/test_caps_import_light.py",
        "tests/test_chain_health.py", "tests/test_dashboard.py", "tests/test_dashboard_chrome.py", "tests/test_dashboard_edit.py", "tests/test_dashboard_groups_page.py",
        "tests/test_dashboard_harness.py", "tests/test_dashboard_legend_help.py", "tests/test_dashboard_model_label.py", "tests/test_dashboard_rework.py", "tests/test_dashboard_save_review.py",
        "tests/test_debt_filing.py", "tests/test_dispatch_request.py", "tests/test_dispatchers_single_flight.py", "tests/test_gate_attempts.py", "tests/test_gate_command_env.py",
        "tests/test_gateway_cache_usage.py", "tests/test_gateway_price.py", "tests/test_gh_aggregate_params.py", "tests/test_gh_cache.py", "tests/test_hub_board_etag.py",
        "tests/test_hub_board_freshness.py", "tests/test_hub_board_holder.py", "tests/test_hub_run_folder.py", "tests/test_hub_caps_resolution.py", "tests/test_hub_cost_usage_panel.py", "tests/test_hub_gauge_markup.py", "tests/test_hub_lane_head_columns.py",
        "tests/test_hub_lane_hidden_cols.py", "tests/test_hub_lane_mode_saved.py", "tests/test_hub_loop_flag.py", "tests/test_hub_mobile.py", "tests/test_hub_mobile_more.py",
        "tests/test_hub_mobile_run_control.py", "tests/test_hub_mobile_tap.py", "tests/test_hub_mobile_terminal_send.py", "tests/test_hub_model_row_narrow.py", "tests/test_hub_mux_tailnet.py",
        "tests/test_hub_nightly_fact.py", "tests/test_hub_occupancy_header.py", "tests/test_hub_run_row_read.py", "tests/test_hub_screenshot.py", "tests/test_hub_shipped_vocab.py",
        "tests/test_hub_spec_progress.py", "tests/test_hub_spend_limits.py", "tests/test_hub_subscriptions_labels.py", "tests/test_hub_terminal_start.py", "tests/test_hubmux_scope.py",
        "tests/test_inactive_border_contrast.py", "tests/test_janitor_pass_deadline.py", "tests/test_janitor_unheld_issue.py", "tests/test_lead_spec.py", "tests/test_live_paths_dropped.py", "tests/test_model_id_shape.py",
        "tests/test_modelreg_migrate.py", "tests/test_models_chains.py", "tests/test_no_pr_path.py", "tests/test_node_commit.py", "tests/test_outcomes.py",
        "tests/test_redact.py", "tests/test_revci_notice_is_not_a_state.py", "tests/test_review_timeouts.py", "tests/test_sandbox_signals.py", "tests/test_session.py",
        "tests/test_session_card.py", "tests/test_shipped_graph_kinds.py", "tests/test_spec_forecast.py", "tests/test_spec_shape.py", "tests/test_squad_child_request_suites.py",
        "tests/test_subject_run.py", "tests/test_supervisor.py", "tests/test_to_test_scenario.py", "tests/test_wave_limits.py", "tests/test_waves.py",
        "tests/test_web_tokens.py",
    ),
    (
        "tests/test_adapter_error_classes.py", "tests/test_adapter_refusals.py", "tests/test_adr_0145_waves.py", "tests/test_adr_index.py", "tests/test_board_fast_read.py",
        "tests/test_board_gate_record.py", "tests/test_board_pin.py", "tests/test_board_wait.py", "tests/test_board_wait_pass.py",
        "tests/test_board_priority.py", "tests/test_priority_rank.py", "tests/test_board_transitions.py", "tests/test_caps_partial.py", "tests/test_contract_probe.py", "tests/test_hub_card_wait.py",
        "tests/test_dashboard_child_review.py", "tests/test_dashboard_failures.py", "tests/test_dashboard_layout.py", "tests/test_dashboard_live_call.py", "tests/test_dashboard_pause.py",
        "tests/test_dashboard_record.py", "tests/test_gate_counts.py", "tests/test_gate_load_report.py", "tests/test_gh_callers.py", "tests/test_gh_door_child_merge.py", "tests/test_hub_board_cases.py",
        "tests/test_hub_board_filter_bar.py", "tests/test_hub_board_observation.py", "tests/test_hub_board_outcomes.py", "tests/test_hub_cache_split.py", "tests/test_hub_childcc_row.py",
        "tests/test_hub_compare_cache.py", "tests/test_hub_donut_sidebar.py", "tests/test_hub_fixture_seam_defaults.py", "tests/test_hub_loop_switch_page.py", "tests/test_hub_machine_load.py",
        "tests/test_hub_mobile_board.py", "tests/test_hub_mobile_empty_columns.py", "tests/test_hub_mobile_layers.py", "tests/test_hub_mobile_lead_chart.py", "tests/test_hub_mobile_nav_shell.py",
        "tests/test_hub_mobile_specs.py", "tests/test_hub_profiler.py",
        "tests/test_hub_mobile_phone_density.py",
        "tests/test_hub_mobile_push.py", "tests/test_hub_mobile_settings_theme.py", "tests/test_hub_mobile_visual_floor.py", "tests/test_hub_model_add_box.py", "tests/test_hub_model_off_switch.py", "tests/test_hub_models_version.py",
        "tests/test_hub_page_reload.py", "tests/test_hub_router_envelope.py", "tests/test_hub_router_queue_models.py", "tests/test_hub_run_share.py", "tests/test_hub_run_sheet.py",
        "tests/test_hub_spec_colors.py", "tests/test_hub_tab_title.py", "tests/test_hub_usage_layout.py", "tests/test_instructions.py", "tests/test_issue_state_wave.py",
        "tests/test_killed.py", "tests/test_lead_span.py", "tests/test_model_ctx.py", "tests/test_model_window.py", "tests/test_no_suite_wall_clock.py",
        "tests/test_not_startable_build.py", "tests/test_pause.py", "tests/test_premise_check.py", "tests/test_procs.py", "tests/test_ticket_premise.py", "tests/test_project_settings.py",
        "tests/test_quality_reading.py", "tests/test_quickstart_tailnet.py", "tests/test_record.py", "tests/test_retired_profile.py", "tests/test_review_parallel.py",
        "tests/test_run_monitor.py", "tests/test_salvage_note.py", "tests/test_sandbox.py", "tests/test_session_send.py", "tests/test_skillpath.py",
        "tests/test_suite_state_isolation.py", "tests/test_testselect.py", "tests/test_to_test_scenario_migrate.py", "tests/test_to_test_scenario_records_bugs.py", "tests/test_to_test_scenario_title_emoji.py",
        "tests/test_wave_review.py", "tests/test_web_hue.py", "tests/test_gh_fast_tier.py", "tests/test_macos_probe_workflow.py",
    ),
)

def shard_paths(shard: int) -> tuple[str, ...]:
    """The test files of <shard>, 1-based; a number outside 1..SHARD_COUNT is a defect, never an empty answer."""
    if not 1 <= shard <= SHARD_COUNT:
        raise SystemExit(f"ci_shards: shard {shard} is outside 1..{SHARD_COUNT}")
    return SHARDS[shard - 1]


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].isdigit():
        raise SystemExit("usage: python3 tests/ci_shards.py <shard>")
    print(" ".join(shard_paths(int(argv[1]))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
