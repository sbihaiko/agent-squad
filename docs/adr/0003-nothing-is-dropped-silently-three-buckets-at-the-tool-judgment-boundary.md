# ADR-0003 — Nothing is dropped silently: three buckets at the tool/judgment boundary

## Status

Accepted, 2026-09-29.

## Context

A deterministic filter can cut input before a model reads it. A silent cut leaves no trace. Nothing in the system can then report the gap.

The two error directions cost different amounts. A false positive costs one extra model call. A false negative loses an item and leaves no trace, because the output looks clean either way.

## Decision

1. **Use three buckets. Put each input item in exactly one.**
   - *Handled*: the system processed, answered, and recorded the item.
   - *Needs human*: the rule cannot decide. The item goes to Rafael with its source attached.
   - *Dropped*: the item is provably out of scope. Remove it from the work queue. Write it to a drop ledger with the rule that dropped it and the source id.
2. **The deterministic layer filters. The model judges.** The filter routes by mechanical properties such as channel and sender. It never decides whether a request has merit. It never rewrites the request.
3. **The drop ledger is mandatory output. It is not a debug flag.** It lives in the same store as the queue and keeps the same retention. It is the only guard against the false-negative direction.
4. **Ambiguity escalates. It never guesses.** Group ambiguous items losslessly into runs by a mechanical property. This keeps the number of decisions bounded.
5. **Every downstream claim carries provenance.** It names the bucket, the source id, the channel, and the author. A claim without that provenance is invalid.

## Consequences

Rafael can answer "what did we not see?" from a file, not from memory.

The cost is an escalation path that a human must service. The classification contract must also stay stable when the filter changes.

Someone must read the ledger. Otherwise it becomes a write-only file.

## Guard

None yet. This rule is a draft until a named guard test exists (ADR-0018).
