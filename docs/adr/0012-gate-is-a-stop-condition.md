# ADR-0012: A gate is a stop condition, never an optimization target; verify the instrument first

## Status

Accepted.

## Context

A pipeline tracks numbers, such as gate scores, open defects and cost per run. Each number invites the wrong reflex: an agent measured on "open tickets" closes tickets. Instruments also fail. An agent's own status report can be false, and a tool that opens a defective file without complaint is not an oracle for that file.

## Decision

1. **A check gates. Nobody optimizes against it.** No agent takes "the number improved" as its acceptance criterion. Moving the optimization into an agent prompt gives the same loop.
2. **Print the number. Act on the outcome.** A loop whose stop condition is a tracked metric is a defect.
3. **Check the property you want.** Replace "the ticket is closed" with "the deliverable exists and is correct".
4. **Verify the instrument before you cite it.** A number from an agent's own narration is an indication. A measurement comes from outside the agent, such as the git remote, a test run or a log. Name the command that produced it.
5. **Bound any check-triggered remediation.** Make one fix, run one recheck, then stop and report to the owner.
6. **Numbers taken before an instrument was fixed are not measurements.** Measure again for anything that carries a decision.
7. **A derived threshold may gate.** It must first be shown to stand for the property it claims, and it is a stop condition, never a target. This rule does not prohibit numeric checks. It prohibits optimizing against a proxy.

## Guard

None yet; a draft.
