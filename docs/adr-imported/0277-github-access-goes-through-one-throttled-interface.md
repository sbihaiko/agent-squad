# ADR-0277: Every GitHub read goes through one throttled interface, REST and GraphQL behind it

- Status: accepted
- Date: 2026-10-12
- Related: ADR-0274 (the dispatch-time slice gate, which reads the same API), ADR-0273

> This decision belongs to the loop's tooling register in `sbihaiko/agent-squad`,
> where the tickets it sources live. `ADR-0273`, which it cites, stays in the
> emulator's register in `sbihaiko/MesenAI`.

## Context

The squad loop talks to GitHub from a dozen shell scripts, each owning its own
`gh api` call. Measured against the loop's own logs, the cost is dominated by
**repetition**, not by volume: `fastloop.sh` beats every 30 s, and each beat has
`autobug.sh` and `autofrontier.sh` re-read the open-PR list, per-PR detail,
labels and board items that the previous beat read and that have not changed.
`forkclose.sh` adds a per-branch `pulls` + `pulls/<n>/files` + `issue view` for
every candidate, every pass, including the branches a slice guard will refuse
anyway.

Two failure modes follow, and both were observed on 2026-10-12:

- **The secondary/abuse limit.** GitHub answers `403` with its own request id,
  while `/rate_limit` still reports core `5000/5000`. Nothing in the loop reads
  it as a throttle signal, so the next beat retries immediately and deepens the
  hole. A duplicated `watch.sh` doubled every read and is what turned this into
  an exhaustion.
- **The refusal read as data.** `gh api` writes the error body to *stdout* and
  does not honour `-q` on a failed request, so `opr=$(gh api ...)` yields the
  words of the rate-limit message where a JSON list was expected. The loop then
  reports `the open-PR list is not a list: ; nothing dispatched` — and, before
  the wrapper that fixed the worst of it, treated the message's words as PR
  numbers. The contract this decision pins is that three outcomes stay
  distinct: an answer, a refusal, and a failure before GitHub reached a verdict.

Neither mode is fixable one call site at a time: any script that keeps its own
`gh api` recreates both. The non-goals are deliberate — this is **not** a queue,
a scheduler or a rate governor that delays work; it does not change which facts
the loop reads, only how many requests it spends to read them.

## Decision

One interface owns every GitHub read the loop makes. No script under
`contrib/squad-goal/scripts/` calls `gh api` directly for a read again; the
interface is the only caller, and it exposes the three outcomes of the Context
above as its return contract.

**Transport is chosen by shape, not by preference.**

- **GraphQL** for aggregate reads: the open PRs of a repository with their head
  ref, draft flag, labels, review-thread counts and check rollup; the open issues
  of a repository with their labels and blocking edges; the labels of a
  repository. One query replaces the per-PR and per-issue fan-out, and the
  fields are requested explicitly so the response is bounded.
- **REST** for Project v2 items and field/option ids (no GraphQL equivalent the
  loop can rely on), for every write (`PATCH`/`POST` on issues, comments, cards),
  and for any endpoint where a GraphQL equivalent is not demonstrably cheaper.

**Every read is throttled by one shared state, keyed on the checkout, not on the
process.**

- A **token bucket** bounds requests per interval, generous enough that a healthy
  loop never waits and small enough that a duplicated reader cannot double the
  rate. The budget is a config key with a documented default, not a constant
  buried in a script.
- A **cooldown** is written to `$SG_STATE/api-cooldown-until` (an epoch second)
  on the first `403`/`429` that GitHub itself attributes to a limit. While that
  timestamp is in the future, the interface **refuses to make remote reads** and
  returns the refusal outcome; callers back off for that turn instead of
  retrying. The cooldown is shared, so a second process — a duplicated
  `watch.sh`, a manual `forkclose.sh` — cannot keep hammering behind the first
  one's back. It carries jitter, so two wake-ups do not collide on the same
  second.
- A `403` is **never** an empty list and never an answer. The interface returns
  a distinct refusal carrying GitHub's own message and request id, and the caller
  must name it in its log rather than acting on an absent result.

**Identical reads inside one window share one response.** The interface keeps a
per-resource cache — a file under `$SG_STATE` with a TTL for the cheap facts and
an `ETag`/head-SHA key for the ones that are expensive to re-derive — and a
request that repeats inside the window is answered from it. A `304`/unchanged
head refreshes the entry without spending a fresh parse.

**The loop can see what it spends.** The interface appends one line per call to
`$SG_STATE/api.log`: timestamp, transport, route, cache hit or miss, and the
outcome. `health.sh` reports an exhausted cooldown and a sustained miss rate as
health causes of their own, so the next exhaustion is a diagnosis already made
rather than an afternoon of archaeology.

## Consequences

- The honest cost is a new seam the loop's tests must drive: the interface has to
  be injectable (a stub transport), or every test of a script that reads GitHub
  becomes a network test. The existing per-script `gh` stubs become stubs of the
  interface, which is the cheaper shape anyway.
- A cache is a correctness risk wherever a stale answer changes a decision.
  Nothing that gates a **write** may be read from cache: the merge, the close,
  and the card move read live. The cache is for the facts a beat re-reads to
  decide whether it has anything to do.
- The cooldown is deliberately blunt: it stops *reads*, so a pass that could have
  made progress on a cached fact still waits it out. That is the intended
  trade — a slowed loop is recoverable and a secondary-limit lockout is not.
- The interface cannot fix a caller that asks for the same expensive thing on
  every beat by design; it can only make the repetition cheap. A route that is
  genuinely unnecessary still has to be deleted.
- Until the live skill is refreshed from the fork, the scripts in use keep their
  own `gh api` calls, and this decision is not in force. The change lands in
  `contrib/squad-goal/` and reaches the loop through `refresh-from-fork.sh`,
  which is the same one-way path every tooling change takes.
