# ADR-0155: A premise is checked, never assumed; one round settles what reading and running can, and asks the owner the rest

## Status

Accepted.

## Context

A published spec mixes three kinds of sentence and treats them alike. "The registry already resolves this spelling" is a fact that reading the repository settles. "The wave lands in under ten minutes" is a fact only a run settles. "The comment goes on the spec" is not a fact at all: it is the owner's decision. A spec whose decisions are read as facts, and whose facts are read as decisions, costs a round of tickets built on a sentence nobody verified. Asking the owner to answer everything is the second failure: the owner is asked about facts that a `sed` would settle, and every answer arrives without the check that would have refuted the premise.

Four sources shaped this decision:

- **`ub-mode`** reads a spec adversarially: it names each unstated assumption, then classifies it. From it comes the premise list itself, and the rule that a premise the spec never states is still a premise to check.
- **`grilling`** refuses to accept an answer it can test, and keeps asking until the answer stops moving. From it comes the demand that a fact carries a citation rather than an assurance.
- **`to-questionnaire`** turns a decision the agent cannot answer into a form for the person who can, one question at a time, each with the answer the agent would give. From it comes the recommended answer each question carries, so "yes" is a whole answer, and the cap on how many questions one round may ask.
- **`domain-modeling`** checks a claim against the words the project already uses, and reports the mismatch instead of quietly choosing a meaning. From it comes the rule that reading the repository refutes a premise as readily as it proves one, and that a refutation is a result.
- **`wayfinder`** splits work too large for one session into decision tickets, and resolves them one at a time, so a dependency is answered before the question that needs it. From it comes the rule that a question depending on another open question waits a round.

The model table is one registry with one resolver ([ADR-0147](0147-one-model-registry.md)), which already states which entries have tools and which are retired. A check that reads a spec and a repository needs a model that can read: a model whose entry says `tools: false` reads the prompt alone, so it cannot do this work at all.

## Decision

1. **A premise is checked, and the check is one of five states.** `proven` (reading confirms it), `refuted` (reading contradicts it), `needs a probe` (only running settles it), `decided` (the owner answered), `open` (nothing has settled it yet). Every premise of the spec carries one of the five, and no other state exists. Guard: `tests/test_premise_check.py`.
2. **A fact is settled by reading, with a citation the repository carries.** A `fact` finding names a `path:line`; `squad/premise_check.py` reads that file and that line. The fact is `proven` when the citation is there and supports it, `refuted` when the reading contradicts it, and `open` when the repository does not carry the citation. A citation nobody can read is no proof, so a fabricated one leaves its premise open instead of closing it. Guard: `tests/test_premise_check.py`.
3. **A fact with proof is never a question.** A `proven` or `refuted` premise never appears as a question in the round, whatever recommendation its finding carries. The owner is never asked to confirm what reading already settled. Guard: `tests/test_premise_check.py`.
4. **What only running proves goes to a probe.** A `run` finding is `needs a probe` and names the run that settles it. A round never runs the probe: it records what must be run, and a later round reads the result. Guard: `tests/test_premise_check.py`.
5. **A decision belongs to the owner, and a round asks at most five.** A `decision` finding answered is `decided`, with the answer and who gave it. An unanswered one is asked, and each question carries a recommended answer worded so that "yes" accepts it. A round asks at most five; the rest wait for the next round, so a spec never arrives as a wall of questions. A finding with no recommendation is reported and not asked: a question without a proposed answer spends the owner's time twice. A proxy answers in the owner's absence only as decision 10 allows. Guard: `tests/test_premise_check.py`.
6. **A question that depends on an open question waits.** A finding names the premise it waits on. The round does not ask it while that premise is still `open`; it is listed with the premise it waits on. When the dependency is `proven`, `refuted` or `decided`, the question is asked in order. Guard: `tests/test_premise_check.py`.
7. **The round records one comment on the spec, rewritten in place.** The comment carries the marker `<!-- premise-check: spec=<n> -->` and holds one table: premise, state, proof or answer, and who answered. A round finds its marker and updates that comment; a spec with no marker gets one. The spec never collects copies of the same check, and a later round replaces the earlier one. Guard: `tests/test_premise_check.py`.
8. **A model that cannot read never reviews.** The reviewer comes from the review chain of the model registry, and the chain's first entry that can read and may run now is chosen; a named reviewer is checked the same way. An entry with `tools: false`, an unknown spelling and a retired entry are refused by name. A round that finds no reviewer stops and names why; it never falls back to a model that cannot read the repository. Guard: `tests/test_premise_check.py`.
9. **One invocation is one round.** The skill asks its questions and writes its one comment, and stops. It never loops to completion in one turn. The module reads the repository and writes one comment; it never runs a probe. The owner answers, and the next round reads the answers. Guard: `tests/test_premise_check.py`.
10. **A named proxy answers while the owner is away, and the owner's answer always wins.** A run names at most one proxy: a registry model, or the autonomy panel. With no proxy named, every question waits for the owner. A proxy must differ from the reviewer, and a round refuses one that equals it. Each proxy answer records its model id as `proxy: <model id>`, resolved through the registry, and a run refuses an answer that names another model. A proxy answers a decision only, and only when it is confident. It leaves the question open when the question touches an owner-only action, read from the autonomy fragment, or when it approves a ticket breakdown. An owner answer, given now or recorded in the spec, overrides a proxy answer for the same premise. Guard: `tests/test_premise_check.py`.

## Consequences

- A spec arrives with its checkable claims already checked, so a ticket built from it starts from a citation instead of an assurance.
- The owner's answers are spent on decisions instead of facts, and each question arrives with an answer to accept or reject.
- A refuted premise is a result: the spec is wrong, and the round says so with the line that contradicts it.
- The reviewer is a registry entry with tools, so a cheap model that cannot read the repository can never produce a check that looks like one.
- A proxy's answer is recorded with its model id, so a decision made while the owner was away is never read as the owner's.

## Guard

`tests/test_premise_check.py` proves each rule above: the five states, the citation that must read, a proven fact never asked, a run-only fact as a probe, the five-question cap, the dependent question that waits, the one comment rewritten in place, the reviewer that comes from the registry with tools, and the named proxy: a proxy equal to the reviewer refused, each answer carrying its model id, an owner answer overriding a proxy, an owner-only or breakdown question or an unconfident answer left open, and no proxy set leaving every question to the owner. The skill's door is proved by `tests/test_agent_doors.py`.
