# ADR-0171: A loop defect is reviewed by Fable or Astra before it is built

## Status

Accepted. The owner's decision says: "the loop-bug drain classes are written in ADR-0146 decision 10 and ADR-0171 decision 2, and no run needs to quote a comment to build them."

## Context

A defect in the loop's own machinery was reviewed by the general review chain, which starts with a fast, low-cost model.
Fable and Astra were not in that chain at all, so the defect that most needs the strongest judgment got the weakest one.

A bare `loop-bug` also reached the build with no review of its proposed fix. [ADR-0146](0146-a-loop-defect-is-fixed-outside-the-loop.md)
then halted dispatch for `loop-bug:dispatch`, `loop-bug:landing` and `loop-bug:infra` only, so the loop built a bare `loop-bug`
like any other bug.

## Decision

1. **A loop defect has its own chain.** The `loop_defect` chain in `contrib/squad-goal/scripts/models.json` starts with
   `claude-fable-5-2`, then `gpt-6-astra`. Its remaining entries are the review chain, in its own order. DeepSeek stays
   reachable and is never first. A model outside its window or switched off is skipped by the existing rules, so the
   fallback still runs.
2. **The label selects the chain.** A review reads the issue's labels once. An issue that carries `loop-bug`, or any label
   that begins with `loop-bug`, is reviewed on the loop-defect chain. Every other issue keeps the review chain. A failed label
   read keeps the review chain and says so in one line on stderr. The class a suffix names (`loop-bug:landing`,
   `loop-bug:publish`, or another) changes the restrictions the issue holds, never the chain it is reviewed on: the strongest
   reviewer reads every class, because the class is a judgement about the defect's reach.
3. **A loop defect is planned before it is built.** `review.sh --plan <issue>` reviews the issue alone: its title, body and
   acceptance criteria, with no branch and no diff. It walks the loop-defect chain. Its record is the record of a branch
   review, under the issue's key: the verdict in `review-<key>.md`, and the reviewed body hash in `review-<key>.sha`.
4. **Only an approval of the current body releases the build.** The plan review exists, its first line is
   `VERDICT: APPROVE`, and its recorded body hash equals the issue's current body. A review of an older body is stale and
   does not count. The build is a hand build outside the loop: no loop-bug enters normal dispatch, whatever its drain class,
   so `autobug.sh` dispatches none, approved or not (ADR-0146).
   **The hand build may run as a Workflow, and its landing is the hand landing.** A Workflow is one way to run the build
   outside the loop, and `ffland` is the landing path. The build stays a hand build under every class: `loop-bug`,
   `loop-bug:landing`, `loop-bug:publish` and any other class. The owner set this rule:

   > "Through workflows" means that the hand build may run as a Workflow, and the landing path is the hand landing
   > (ffland). No loop-bug enters normal dispatch, whatever its drain class.
5. **The review does not lift the drain.** An approved plan releases the build and nothing else: a class that holds
   dispatch stays drained until the issue closes with its fix published (ADR-0146). A `loop-bug:publish` holds only the
   publish, so the loop keeps working beside it.

## Consequences

- A loop defect gets the strongest reviewer the chain offers. The fast model answers only when both are out of reach.
- An edit to a `loop-bug` issue reopens its plan review. An approval binds to a body, never to an issue number alone.
- The autobug pass does not start a plan review. It holds the issue and names the command. A person or an agent runs
  `review.sh --plan`.

## Guard

`contrib/squad-goal/scripts/test-loopbug-review.sh` (the loop-defect chain's head and second entry, its review-chain tail,
the label selection in `review.sh`, the plan verdict and its staleness, and the sourcing of the gate) and
`contrib/squad-goal/scripts/test-autobug.sh` case 32 (a `loop-bug` with no approving plan review of its current body is held
and named, and an approval of an older body, or a `CHANGES` review, holds it too).
