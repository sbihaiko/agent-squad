# ADR-0147: One model registry, one resolver, and a chain is a list of registry ids

## Status

Accepted.

## Context

Model ids were parsed by prefix in many scripts, and each script held its own alias and route table. The same model had several spellings, the wire string and the route were guessed from the spelling, and a retired id could still reach a caller. There was no single place to state which subscription serves a route, which models are retired, or what an API-equivalent price is. A caller thus carried knowledge that belongs to one table, and an update had to find every copy.

The engine records the graph document and hashes it, so a run must not change the document it was given. History files (`review-times.tsv`, `token-usage.tsv`) pin their format and their raw ids. The live machine config is written only by the owner's command or the hub.

## Decision

1. **The registry is the only model table.** `config/models.registry.json` holds every model: `id`, `label`, `provider`, `subscription`, `efforts`, `routes`, `capabilities`, `display_price`, `aliases`, `retired` and `gateway_only`. The shipped copy `contrib/squad-goal/scripts/registry.json` is byte-identical, and a guard test pins the identity.
2. **A route states how it is called.** `cli` holds an argv template; `gateway` holds `model`, the `served` spellings and `auth`; `direct` is one POST to `/v1/messages` with no tools.
3. **Owner rule is route auth data.** Each route carries `auth`: `subscription`, `api_key` or `unverified`. The validator refuses an `api_key` route on a provider that is not DeepSeek. `unverified` is allowed and shown as a warning; the owner attests or removes such a route.
4. **One resolver reads the registry.** `squad/modelreg.py` is stdlib only, with a byte-identical copy at `contrib/squad-goal/scripts/modelreg.py`. It loads the registry, chains, eligibility and quota, and it builds the wire string. It lands and is published before any caller depends on it.
5. **Readers normalize; writers keep raw.** `normalize(raw)` never raises. An unknown spelling returns `known=False` and passes through as data. No ledger, run record or host graph changes its format.
6. **The wire string is the raw spelling when the route serves it.** `invocation` returns `raw` unchanged when `raw` is in `served`, so every current caller sends the same string and keeps its behavior. A difference in behavior is a separate decision.
7. **A chain is a list of registry ids.** `models.json` v2 holds `chains`, and each entry names an id with `route`, `effort`, `hrs`, `on`, `timeout_s` and `ctx`. The resolver reads v1 and v2; when a file holds both shapes it is mixed, `chains` wins, and `check` names the stray keys.
8. **The graph hash covers the raw document.** The engine never rewrites a graph before `canon_hash`, so an in-flight run resumes and `--expect-graph-hash` keeps its meaning. Resolution is recorded at run start and applied only when the run opts in.
9. **A bad entry is isolated.** `load` validates each entry alone: a bad entry is dropped and named by `check`, and the other entries still work. A JSON parse error is a loud, named failure.
10. **Prefix parsing has one home.** Code that parses a model id by prefix lives only in files that carry the header `modelreg: seam-clean`; the seam test scans the shipped scripts and the dashboard for a prefix operation or a `[1m]` literal in code.
11. **Machine config is never touched by `live-guard`.** The live files change only by the owner's command or by the hub ([ADR-0140](0140-the-live-copy-follows-in-the-cycle.md)). The live migration is an atomic owner write, and the live guard keeps its behavior.

## Consequences

- A new model or a changed spelling is one registry entry, not a search through callers.
- A spelling that the registry does not know passes through, so a leaked test id fails where it is used, not in the resolver.
- The registry is data under test: a parse error or a bad entry cannot pass the gate, and `check` names the entry to fix.
- Until a caller moves to the resolver, it keeps its own parsing and its behavior; each such move is its own landing with no behavior change.

## Guard

`tests/test_model_resolver_seam.py` (copy identity and the seam scan) and `tests/test_modelreg.py` (registry validation, `normalize`, `invocation`, `chain`, `eligible` and `quota`) ([ADR-0018](0018-failing-gate-is-a-defect.md)).
