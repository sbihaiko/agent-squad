# ADR-0133: A defect an agent finds is its own

## Status

Accepted.

## Context

An agent that finds a problem often names its origin and stops: "pre-existing", "from an earlier run", "not my change", "someone else's". The name moves the defect out of the report and into nobody's hands. Who caused it, and when, does not change what it is. ADR-0062 says this for a red test; the same holds for every defect.

## Decision

1. **A problem, error or defect an agent finds is that agent's from the moment it finds it.** It does not matter who caused it, or whether it came before this run or during it.
2. **Fix the cause in the same run.** "Pre-existing", "not mine", "already broken" and "out of scope" are not valid end states.
3. **If the agent cannot fix it, it escalates.** Examples are a lock, a repo the agent does not own, a missing credential, or an owner-only action. It sends the evidence and the obstacle to the owner and records an issue. It does not park the defect.
4. **A report names each defect found and its repair or escalation.** It never says "done" or "green" while a known defect stands.
5. **This holds for every agent in the workflow,** and for each fork or child it starts.

## Guard

None yet; a draft.
