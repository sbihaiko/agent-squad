# ADR-0106 — A run option is graph data when it changes a node; otherwise it is an engine option recorded at run start

## Status

Accepted, 2026-09-30. Panel decision under autonomy (issue #16). AGREED on the sorting rule, `parallelMode`, no-git, `autoCommit`, `fastPath`, and run-level settings. The verdict of a stopped run is PROVISIONAL (issue #18). Lens A: Codex `gpt-6-sol`. Lens B: Fable 5.1. Lens C: Gemini 3.1 Pro (High), on the stopped-run verdict only.

## Context

dev_squad has 17 config keys. Four change the shape of the run: `stopAfter`, `parallelMode`, `autoCommit`, and `fastPath` (`docs/research/dev-squad-adaptivity.md`, section 3). `parallelMode` alone does many things: it changes fan-out, swaps the merge node for commit and revert nodes, and changes the actor prompt (defect D13). ADR-0102 already made isolation, `each.mode`, `each.join`, and `each.max_parallel` graph data, and made a missing git a preflight defect. ADR-0101 rule 4 made `stop_after` an engine option.

## Decision

1. **The sorting rule.** An option is graph data when it changes which nodes exist or a node's declared setting. It is then a field on a node, or a registered fact that a `when` reads. An option is an engine option when it only says how this invocation runs the same graph: where to stop, the run directory, a dry run, a lower run cap. The editor shows graph data on node cards and engine options in a run panel.
2. **`stop_after` is an engine option:** `--stop-after <node name>`. An engine option may name nodes, as validated node names. The validator refuses a name that is not a node of the graph. The engine truncates after that node exits. The `run_start` and `verdict` events name the stop node.
3. **A run truncated by `--stop-after` gets verdict `stopped`, exit 30, reason `stopped_after:<node>`** (owner ruling on issue #18, ADR-0112). The message holds the resume command.
4. **`parallelMode` is retired.** `each.mode` and `each.max_parallel` on the node are the graph data. A missing git is a preflight defect, never a swap. A graph that needs worktrees declares action nodes, and the preflight refuses a run without git.
5. **`autoCommit` is a node.** The author includes a commit `action` node or leaves it out. A graph with the node and a graph without it are two graph versions. The other dev_squad keys follow the same rule: `strictBatch` is `each.join`, retry and revision caps are loop `max`, and `models` and `modelOverrides` are the `model` of each agent node.
6. **`fastPath: off` is a registered fact.** Code produces `tier_pin` from the invocation argument. Its instrument is the CLI argument and its source is the position of the `run_start` event. The derived `tier` reads it. A model never sets it (ADR-0104 rules 1 and 4).
7. **Run-level defaults are a `run` block in the graph document:** the run deadline default, the `max_parallel` default, and the run spend cap. An engine option may override a `run` value only downward.
8. **Every run writes a `run_start` event** with the graph version hash, every engine option, and every override. The verdict module reads it.
9. **An engine option never overrides a node's model or budget.** That is the graph author's edit or an applied proposal (ADR-0103).

## Considered options

- **Every option is an engine option, and the graph selects a named variant.** Rejected: a graph would hold a second graph inside it, and the editor could not show one card per node.
- **Every option is graph data.** Rejected: a stop point is one invocation's choice, and a node field would make the editor's card lie.
- **`autoCommit` as a fact from an invocation argument that a `when` reads.** Rejected: a fact's source is an artifact, the record, or the target, and a flag that hides a topology change is defect D13. Reopen if one graph file must run with and without a commit on the same day and two graph versions cost more than one `when`.
- **`fastPath` as an engine option that overrides the derived `tier`.** Rejected: it bypasses the registry, and the fact carries no instrument (ADR-0104 rule 7).
- **A `stop_point` node field.** Rejected: it puts one invocation's choice in the graph document.
- **A stopped run is `completed` (Lens B) or `needs-human` (Lens C).** Rejected by the owner on issue #18 for a fifth verdict, ADR-0112.
- **Assign no verdict until a later amendment (Lens A).** Rejected for now: the engine needs a value. Kept in issue #18.
- **Run-level settings as engine options only.** Rejected: the validator needs the run deadline default at validation time (ADR-0102 rule 3).
- **An engine override of a node's model or budget.** Rejected: a second author path that the record cannot name.

## Consequences

GLOSSARY.md gains `Engine option` and `Run block`. Ticket #17 resumes a run that a stop truncated. The schema gains a `run` block, and the record gains a `run_start` event. The editor gets a run panel beside the node cards.

## Guard

Tests that guard this rule:

- `tests/test_graph.py::test_refuses_stop_after_naming_an_unknown_node`
- `tests/test_facts.py::test_tier_is_not_ready_before_the_scout_runs_and_tier_pin_overrides`
- `tests/test_actions.py::test_a_missing_git_is_a_preflight_defect_and_never_a_swap`
- `tests/test_actions.py::test_a_graph_with_no_action_needs_no_git`
- `tests/test_cli.py::test_an_option_may_lower_a_run_value_and_never_raise_it`
- `tests/test_cli.py::test_an_option_never_overrides_a_node_model_or_budget`
- `tests/test_cli.py::test_tier_pin_is_a_fact_sourced_from_the_run_start_position`
- `tests/test_cli.py::test_an_unknown_node_name_is_refused`
- `tests/test_cli.py::test_stop_after_truncates_to_stopped_exit_30_with_the_resume_command`
- `tests/test_cli.py::test_a_lowered_run_cap_stops_the_run_with_the_cap_reason`
- `tests/test_defaults_wiring.py::test_the_engine_level_keys_reach_the_sdk_options_and_the_node_keys_do_not_change`
- `tests/test_defaults_wiring.py::test_a_precompact_hook_logs_each_compaction_to_the_run_record_with_its_timeout`
- `tests/test_defaults_wiring.py::test_run_start_records_the_defaults_file_hash_and_the_engine_level_keys`
- `tests/test_defaults_wiring.py::test_a_run_with_no_defaults_flag_and_no_sdk_records_none`
- `tests/test_defaults_wiring.py::test_an_unsound_defaults_file_refuses_the_run`
- `tests/test_cli.py::test_a_matching_expected_graph_hash_runs_and_run_start_holds_it`
- `tests/test_cli.py::test_a_mismatched_expected_graph_hash_exits_2_names_both_hashes_and_writes_no_record`
- `tests/test_cli.py::test_a_layout_move_still_matches_the_expected_graph_hash`
- `tests/test_cli.py::test_resume_refuses_a_mismatched_expected_graph_hash`
- `tests/test_defaults_wiring.py::test_run_start_carries_the_dashboard_reread_interval_from_the_defaults_file`
- `tests/test_defaults_wiring.py::test_an_unsound_reread_interval_refuses_the_run`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
