# ADR-0012 — A gate is a stop condition, never an optimization target; verify the instrument first

## Status

Accepted, 2026-09-29.

## Context

A pipeline tracks numbers, such as gate scores, open defects, and cost per run. Each number invites the wrong reflex. An agent that is measured on "open tickets" can close tickets. An agent that is measured on "time to first reply" can send an empty reply fast.

Instruments also fail. An agent's own status report can be false. Outside evidence can contradict it. A tool that opens a defective file without complaint is not an oracle for that file.

## Decision

1. **A check gates. Nobody optimizes against it.** No agent may take "the number improved" as its acceptance criterion. Moving the optimization into an agent prompt gives the same loop with a longer edge.
2. **Print the number. Act on the outcome.** A loop whose stop condition is a tracked metric is a defect, even when the arithmetic is sound.
3. **Check the property you want.** Replace "the ticket is closed" with "the deliverable exists and is correct". You can hold work to that standard. A threshold on a metric is only a claim about the instrument.
4. **Verify the instrument before you cite it.** A number from an agent's own narration is an indication, not a measurement. A measurement comes from outside the agent, such as the git remote, a test run, or a log. Name the command that produced it.
5. **Bound any check-triggered remediation.** Make one fix. Run one recheck. Then stop and report to Rafael. Repeated attempts against a threshold are the prohibited loop, whatever the intent.
6. **Numbers taken before an instrument was fixed are not measurements.** Do not cite them. Measure again with the fixed instrument for anything that carries a decision.
7. **A derived threshold may gate. It is not an exemption.** A threshold from the project's own accepted behavior may stop the loop. It must first be shown to stand for the property it claims. It obeys this same rule: a stop condition, never a target. This record does not prohibit numeric checks. It prohibits optimizing against any proxy.

## Consequences

A question about project health gets a command and its output, not an agent summary. A real defect leaves the instrument each time this rule fires. Earlier numbers are downgraded until measured again.

Cost: outside verification adds steps to every report. Sometimes the honest answer is "nothing we control moves this number". That answer is a finding, not a failure.

## Guard

Guard: none yet. This rule is a draft until a named guard test exists (ADR-0018).
