# Design hand-off: the self-adapting orchestration graph

This folder holds the machine-readable part of the design for map #1. The engine that runs these documents is in `squad/`.

| File | Purpose |
|---|---|
| `graph.schema.json` | JSON Schema (2020-12) for one graph document. |
| `five-phases.graph.json` | The five dev_squad phases as a graph document. It validates against the schema. |
| `five-phases.mmd` | Mermaid view of the fixture. |
| `router.graph.json` | A dynamic flow: a router agent runs ask-matt and requests children in a `dispatch` envelope. It validates. |
| `router.record.jsonl` | Hand-authored run record for the router graph: three accepted spawns, one rejected, a crash, a resume, a verdict. |
| `check_graph.py` | Schema check plus the cross-reference rules a schema cannot state, including the dispatch rules. |
| `check_record.py` | Checks a record against its graph: envelope, request keys, resume frontier, folded verdict. |
| `build_fixture.py` | Generates the fixture and the Mermaid file, so they cannot drift apart. Run it from `docs/design/`. |
| `../../dashboard/agent-squad-run-dashboard.html` | The run dashboard: one HTML file, no server. Built by `scripts/build_dashboard.py` from the engine. See Dashboard below. |

## Check

```bash
python3 docs/design/check_graph.py docs/design/graph.schema.json docs/design/five-phases.graph.json
python3 docs/design/check_graph.py docs/design/graph.schema.json docs/design/router.graph.json
python3 docs/design/check_record.py docs/design/router.graph.json docs/design/router.record.jsonl
```

## Dashboard

The page is `agent-squad-run-dashboard`. It has a dark theme, a left control column, and a large graph area. Drag empty space to pan, use the wheel to zoom at the cursor, press Fit to see the whole graph, and drag a node to move it. Open `dashboard/agent-squad-run-dashboard.html` from disk. It loads a graph file, shows each node and edge, and shows each validation error on its node. Save is never blocked. It opens a run record read-only and shows an applied adaptation as an overlay only when the graph hash matches. A gate's check and thresholds and the two registries are read-only. Layout is saved to a sibling `.layout.json`, outside the graph hash.

- Build: `python3 scripts/build_dashboard.py`. It embeds the schema, `GATE_REGISTRY`, the rule constants, and a snapshot of the served lists (tools, skills, models, efforts) with its hash. The snapshot is stale once its sources change; rebuild. `--home` adds `~/.claude/skills`, and the committed page never uses it.
- The rules are a hand-written JS port in `dashboard/core.js`. `tests/test_dashboard_parity.py::test_dashboard_validation_parity` compares the (node, rule) pairs of the port with `squad.graph.validate` over both fixtures, one mutation per rule, and every node-field mutation. It needs `node` and fails without it.
- `test_the_built_dashboard_is_current` fails when the committed page differs from a fresh build.
- Known limits: the dashboard edits one node as JSON; the served lists are shown, not yet offered in a picker; a number such as `2.0` prints as `2` in a rule message (JS does not keep the difference).

## Where the rules live

The vocabulary is in `GLOSSARY.md`. The decisions are in `docs/adr/`. The schema encodes them.

## Provisional items

Each has a working default and an open `needs-ratification` ticket. The stopped-run verdict and the control model are settled.


## Known limits

- `squad/mermaid_check.py` parses the Mermaid file (structure, declared names, every node drawn) and a test rebuilds it to catch drift. It does not render or lex as Mermaid does: no Chrome exists on the build host, so a person still checks layout once.
- The real SDK adapter exists (`squad/sdk_adapter.py`, #44) and the subject runs use it (`docs/research/subject-runs-02-24.md`).
- An `outcome` event's `cap` is the spend granted to that call, in US dollars. It is not a turn cap.
- The schema does not check `next` against a node's outcomes. A later ticket may add that rule.

## Run record: per-call fields

An agent `outcome` event carries `model` and `effort` (what the engine asked for). It carries `tokens_in` and `tokens_out` only when the SDK result reports usage. `tokens_in` counts every prompt token: plain, cache write, and cache read. It also carries `cache_read` and `cache_write`, the cache share of `tokens_in`; they are 0 when the provider reports 0 and absent when it reports nothing, so an older reader and an older record stay valid. A missing token field means unknown, never zero. Tokens per second is derived by the reader (`tokens_out` over `elapsed`); the record does not store it. An older record without these fields stays valid. Guards: `tests/test_engine_skeleton.py::test_an_agent_outcome_records_the_model_the_effort_and_the_tokens_when_known`, `tests/test_engine_skeleton.py::test_an_outcome_with_unknown_usage_carries_no_token_field_and_still_reads`, `tests/test_sdk_adapter.py::test_the_result_usage_becomes_tokens_in_and_tokens_out`, `tests/test_sdk_adapter.py::test_a_result_with_no_usage_leaves_tokens_unknown_never_zero`, `tests/test_sdk_adapter.py::test_the_cache_split_is_recorded_next_to_the_tokens_and_a_reported_zero_stays_zero`.

## Single-card mode of the run dashboard

`dashboard/agent-squad-run-dashboard.html` takes one query parameter: `?card=<name>` paints a single card of the
panel and nothing else (page state `cards.js`, data `core.cardBody`). It exists for the picture-in-picture window the
hub floats over its maximized board (`contrib/squad-goal/scripts/hub.py`), which frames the run's own dashboard at
that URL: the two live on different ports, so the window reads the same card the panel draws instead of a second
rendering that drifts.

- `?card=groups` — the run's children grouped by dispatch and state, unfinished first. It is the only card the panel
  names today; a name it does not have is answered with a message inside the card, never an empty frame.
- The page follows the run exactly as it does without the parameter (the same push stream, or the reread timer), so
  the card is live rather than a snapshot; the mode only cuts what is painted.
- A page that answered the mode posts `{squadCard: "<name>"}` to the window that framed it, which is how the window
  tells a dashboard that knows the parameter from an older one that does not.

## Run the engine

```bash
python3 -m squad.cli run GRAPH --script script.json [--stop-after NODE] [--spend-cap N] [--deadline S] [--max-parallel N] [--tier-pin T]
python3 -m squad.cli resume RUN_ID [--from NODE] [--answer pass|fail] --script script.json
python3 -m squad.cli report RUN_ID
python3 -m pytest
```

The exit code is the verdict: 0 completed, 1 failed, 10 needs-human, 20 conflict, 30 stopped, 2 usage error.
