# ADR-0160: A model omitted from a chain is investigated and recovered

## Status

Accepted. Owner instruction: a model taken out of a chain is investigated, and the agent tries to recover it.

## Context

A review model that times out three times in a row leaves the chains for a window (#749): `review.sh` records the
streak in `review-timeouts.json`, and every pass skips the model until the window ends. The record names how many
timeouts, never why, and nothing reads it: the model returns only when the window expires, whatever changed in
between.

That window hid a real defect. DeepSeek answered a review in about 17 seconds through the direct gateway call. A
registry change gave the model tools for its coding chain, and the review pass read the same capability: every
review then went through `claude -p` with a JSON schema, at about 335 seconds a call, most killed at the deadline.
Three kills took the model out for six hours, and the omission was read as "this model is slow" instead of "this
lane is wrong".

An omission is a claim about a lane at one moment. It is never a verdict about a model, so it may not stand
unexamined for its whole window.

## Decision

An omission is investigated, and recovery is attempted before the window ends.

- The investigation reads the lane the model actually uses, not the model's reputation: the review route, the
  runner, the deadline, and the durations in `review-times.tsv`, compared with a window where the model answered.
- Recovery probes the same lane the review pass uses: the direct gateway call with thinking off for a reviewer with
  no tools, the CLI otherwise. A real answer clears the streak (`review-timeouts.py clear`), and the next chain
  tries the model again. A failed probe keeps the streak and reports its cause.
- A lane the probes prove wrong is a defect to fix, not a model to leave out. The fix changes the route, and the
  recovery then proves it.

`model-recover.sh` is the one door: it takes a model, follows its registry route, probes it with a deadline, and
reports one of `MODEL RECOVERED`, `MODEL NOT RECOVERED` with the streak's reason, or `MODEL NOT OMITTED`.

## Consequences

- A model that answers again is back in the chain in minutes, not after its window.
- The omission stays honest: it is cleared only by a real answer on the real lane, never by an operator's hope.
- The probe costs one small call. That call is cheaper than a six-hour window spent on a model that was never
  broken.
- A lane that is wrong is named by the recovery, so the same defect cannot hide behind a second omission.

## Guard

`contrib/squad-goal/scripts/test-model-recover.sh`: a real answer clears the streak and the probe sends
`thinking: disabled`; a failed answer keeps the streak and names its cause; a model with no streak is left alone.
