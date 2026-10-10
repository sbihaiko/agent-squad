# ADR-0029: Needs-human is not success, and gets its own exit code

## Status

Accepted.

## Context

Scripts and SDK runs work unattended, and other tools read their exit codes. An exit code that lies causes two faults. A command can fail to reach its target and still exit 0. A wrapper can treat every non-zero code as "not done" and retry work that already finished. A blocked run is not a defect: a person must act. If it shares the generic failure code, a retry loop follows.

## Decision

1. **One module owns the run verdict** (ADR-0019). The exit code is a pure function of that verdict. No wrapper infers a verdict from a signal.
2. **A timeout is not a verdict.** Before it classifies a run, the wrapper reads the target and checks whether the effect happened. The observed effect decides, not the way the process ended.
3. **Use five codes.** Document them in `--help` and the README:
   - `0`: completed. The work finished. Its external effect is observed at the target.
   - `1`: failed. The script or its inputs are wrong. Investigate it (ADR-0018).
   - `10`: needs-human. Work remains. Only a person can unblock it. Record the reason.
   - `20`: conflict. The target holds something the script will not overwrite. Report it. Never resolve it by guessing.
   - `30`: stopped. The caller asked for the stop and the run is otherwise sound.
4. **Reserve `0` for a fully completed run.** No partial outcome uses it.
5. **Every non-zero verdict names what remains, by step.** A reader can tell "never ran" from "failed" and see where to resume.
6. **State the resume instruction in the message,** with the command verbatim.
7. **A `10` has an owner and a deadline.** An unanswered question is not a pause. When the deadline expires, the caller applies the declared default and records that it did so.

A new code needs an ADR that names the caller who scripts on it.

## Guard

`tests/test_verdict.py`, `tests/test_resume.py`, `tests/test_cli.py`.
