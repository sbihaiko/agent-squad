# ADR-0029 — Needs-human is not success, and gets its own exit code

## Status

Accepted, 2026-09-29.

## Context

Scripts and SDK runs in this project work unattended. Other tools read their exit codes. These readers include a process supervisor, a parent script, and the run that started them.

An exit code that lies causes two faults. A command can fail to reach its target and still exit 0. A wrapper can treat every non-zero code as "not done" and retry work that already finished. A timeout can kill a run that had already delivered its effect.

A blocked run is not a defect. Nothing is broken. A person must act. If you merge that case into the generic failure code, you lose the distinction. A retry loop then follows.

## Decision

1. **One module owns the run verdict** (ADR-0019). The exit code is a pure function of that verdict. No wrapper infers a verdict from a signal.
2. **A timeout is not a verdict.** Before it classifies a run, the wrapper reads the target. It checks whether the effect happened. The observed effect decides, not the way the process ended.
3. **Use five codes** (a fifth, `30`, added 2026-09-30 by [ADR-0112](0112-a-run-stopped-on-request-has-its-own-verdict-stopped-and-exit-code-30.md)). Document them in `--help` and the README:
   - `0`: completed. The work finished. Its external effect is observed at the target.
   - `10`: needs-human. Work remains. Only a person can unblock it. Record the reason, for example `no-grant` or `question-unanswered`.
   - `20`: conflict. The target holds something the script will not overwrite. Report it. Never resolve it by guessing.
   - `30`: stopped. The caller asked for the stop and the run is otherwise sound. Record the stop node.
   - `1`: defect. The script or its inputs are wrong. Investigate it (ADR-0018).
4. **Reserve `0` for a fully completed run.** No partial outcome uses it. No command that failed to reach its target reports it.
5. **Every non-zero verdict names what remains, by step.** A reader can then tell "never ran" from "failed". A reader can also see where to resume.
6. **State the resume instruction in the message.** Give the action and the command verbatim. A person is being asked to act.
7. **A `10` has an owner and a deadline.** An unanswered question is not a pause. The caller records the deadline. When the deadline expires, the caller applies the declared default. It records that it did so by default.

## Consequences

A caller can branch on the code and be right. A dead channel raises an alarm. A delivered result is not queued again as undone.

Cost: five codes are a contract. Classify a new stopping condition into one of them. Add a code only by an ADR that names the caller who scripts on it (ADR-0112).

Cost: rule 2 makes each wrapper read its target before it reports. This is slower. It needs one cheap observation per action.

## Guard

Tests that guard this rule:

- `tests/test_verdict.py::test_exit_codes_are_a_pure_function_of_the_verdict`
- `tests/test_verdict.py::test_only_the_verdict_module_defines_the_exit_codes`
- `tests/test_verdict.py::test_each_row_of_the_fold_gives_its_verdict_and_code`
- `tests/test_verdict.py::test_remains_names_steps_that_never_ran_and_a_completed_run_has_none`
- `tests/test_resume.py::test_a_wait_ends_the_process_with_needs_human_and_exit_10`
- `tests/test_cli.py::test_stop_after_truncates_to_stopped_exit_30_with_the_resume_command`
- `tests/test_cli.py::test_run_exits_with_the_verdict_code_and_prints_the_message`

No open part remains. A change to this rule changes its tests in the same commit (ADR-0018).
