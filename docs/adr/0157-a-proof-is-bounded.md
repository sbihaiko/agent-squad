# ADR-0157: A proof is bounded, and it never blocks the loop for long

## Status

Accepted. Owner decision, 2026-10-31: "either the proofs are fast, 30 minutes or less, or they do not block the rest, up to
two hours; beyond that they are accepted as indeterminate, with a note that the tip is not tested, after an assessment and a
pending reminder; more than two hours, never again."

## Context

The promotion proof ([ADR-0156](0156-a-full-proof-carries-across-a-covered-delta.md)) runs the full suite in both modes. On
this machine a full run takes 40 to 50 minutes, and a proof that reserves the machine for that long stops dispatch for the
whole time ([ADR-0145](0145-a-work-branch-lands-in-dev-by-fast-forward.md)). A slow proof then costs twice: the landing it
was waiting for, and every run the loop did not start while it held the machine. The owner set parallelism as the priority:
a proof that costs four run slots for an hour costs more delivery than the shield is worth.

A proof also has no upper bound. A suite that hangs holds the reserve until its own hold expires, three hours later.

## Decision

1. **A proof never holds dispatch.** The reserve bound is zero, and the proof writes no reserve, so the dispatchers read no
   hold and the loop takes its run slots beside the proof. An owner who wants the measurement shielded names a bound in
   `SG_PROMOTE_RESERVE_SECONDS`; with a bound the proof reserves the machine for that long and dispatch waits.
2. **Past a named reserve bound the reserve lifts.** The proof keeps running, and the loop dispatches beside it. The pass
   writes one line to the promote log naming the tip and the bound. The proof is never cancelled by this bound.
3. **At its deadline the proof is killed.** The deadline is two hours (`SG_PROMOTE_DEADLINE_SECONDS`). The suite and every
   child it started are killed, and the tip is recorded in `$SG_STATE/promote-indeterminate` as `<epoch> TAB <tip> TAB
   <bound>`, with one line in the promote log saying the tip is not tested.
4. **An indeterminate tip is not a pass and not a fail.** It writes no gate status and no commit status. Delivery still
   outranks the proof: the tip is promoted with a marker that names what no test reached, and a later pass proves it
   ([ADR-0164](0164-delivery-outranks-the-proof.md)). It is not marked in `promote-pass.tips`, so a later pass retries the
   same tip.
5. **An indeterminate tip needs an assessment and a pending reminder.** The record names the tip and the bound. An agent
   assesses what the killed proof leaves unproven, opens one issue labelled `bug` that says the tip is not tested, and that
   issue is the reminder.
6. **Two hours is the hard bound.** No pass runs a suite past it.

## Consequences

A landing covered by the delta carries in minutes (ADR-0156). A full proof costs at most 30 minutes of blocked dispatch, and
at most two hours of wall time. Past 30 minutes the loop is never idle because of a proof.

An indeterminate proof leaves a gap in the coverage of that tip. It is named in the state directory, in the promote log, and
in one issue, so the next full proof or the nightly closes it.

## Guard

`contrib/squad-goal/scripts/test-promote-pass.sh` guards rules 1 to 4: a held reserve lifts once the bound passes, with the
proof still running; a proof past its deadline is killed, promotes nothing, writes `promote-indeterminate`, and does not mark
the tip refused. Rules 5 and 6 are a draft: the assessment is an agent's act, and the hard bound is the same deadline the
guard proves.
