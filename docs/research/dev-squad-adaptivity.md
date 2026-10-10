# What adapts in dev_squad today, and what it lacks

This note answers wayfinder ticket #4. It lists every run-time adaptation in dev_squad and every config key. It marks each one as a topology change or a node-setting change. It lists the defects that come from the pipeline being code instead of data.

Source: `../dev_squad` at plugin version 2.0.90 (`dev-squad/VERSION`). Read-only. I opened the code. I did not rely on the README. The README and `docs/CONFIGURATION.md` lag the code (section 6).

## 0. How to read this note

Paths are relative to `dev_squad/dev-squad/workflow/` unless a path starts with `docs/`, `commands/`, or `CHANGELOG.md`. These are the short names:

| Short name | File |
| --- | --- |
| `W` | `squad.workflow.js` (5,412 lines; the whole pipeline) |
| `PC` | `lib/parse-config.js` |
| `PR` | `bin/prepare-run.js` |
| `RL` | `lib/route-learnings.js` |
| `PL` | `lib/promote-learnings.js` |
| `RM` | `lib/resume-mode.js` |
| `CL` | `CHANGELOG.md` (repo root of dev_squad) |

A citation `W:288-300` means file `W`, lines 288 to 300.

Two terms:

- **Topology change.** The run has different nodes, or different edges between nodes. A node is added, skipped, or removed. A loop is entered or left. An exit edge is taken. The fan-out width changes.
- **Node-setting change.** The graph is the same. One node gets a different parameter: a model, a cap, a threshold, a prompt block, or a gate parameter.

Some items can be modeled either way. The note says so where it matters.

## 1. Summary

1. dev_squad has one fixed graph: Scout, Spec, Decompose, Execute, Verify (`W:27-33`). It is code. Every adaptation is an `if` inside that code.
2. The run-time adaptations that change topology are few and concrete. They are tier routing, stop-after truncation, the fast-path escalation backstop, dependency skipping, parallel or sequential fan-out, exit edges after a merge, and the conditional nodes (visual gate, learning gate, three auditors). Section 2 lists them.
3. Everything else is a node-setting change: loop caps, model choice, prompt blocks from memory, and gate parameters. Most of these settings are constants in code. Only 17 keys are configurable (section 3).
4. The only part of the graph that is already data is the task DAG. The Decompose node returns `tasks`, `batches`, and `dependsOn` under a schema (`W:2263-2300`). The engine runs it. The phase graph above it is not data.
5. Defects that trace to "pipeline as code" are in section 5. The largest classes are: one fact in many places, a sandbox that forces duplicate code, settings with no consumer, and gates wired to state that does not exist yet.
6. dev_squad adapts by skipping review nodes (tier routing). The map says gates never adapt (map #1). Section 7 lists this as a question for the schema.

## 2. Run-time adaptations

"Decides" tells who makes the choice: **code** (deterministic), **model** (an agent's output), or **human** (a config value or a hand edit).

### 2.1 Topology changes

| # | Adaptation | Trigger and decides | Effect | Evidence |
| --- | --- | --- | --- | --- |
| T1 | Tier routing: trivial, medium, large | Scout model gives `sizeEstimate {estFiles, estLoc}`. Code maps it. Code derives "sensitive" from the resolved paths. | Trivial skips Spec, Decompose, and the critic. Medium skips Decompose. Large runs all. | `W:281-300`, `W:4420-4428`, `W:4437`, `W:4514`, `W:4572`, `W:4637` |
| T2 | Missing estimate forces large | Code. | An unmeasured task runs the full graph. | `W:294` |
| T3 | Stop-after forces large | Human key `stopAfter`. Code. | A partial run always builds the real spec and plan. | `W:4422-4423` |
| T4 | Verify or visual intent lifts trivial to medium | Code reads the task text and `visualVerify === true`. | A requested gate is never skipped. | `W:4424-4425`, `W:359-377` |
| T5 | Fast-path escalation backstop | Code, after the actor. It checks the real diff: more than 1 file, more than 40 added lines, a sensitive path, or zero verification commands. | A trivial task re-enters the critic node. | `W:3915-3927`, `W:4024-4041`, `W:301-309` |
| T6 | Stop-after truncation | Human key `stopAfter`: scout, spec, plan, execute. | The run takes an early exit edge with status `stopped`. | `W:4401`, `W:4495`, `W:4558`, `W:4998` |
| T7 | Deterministic pre-scan bypasses the critic | Code. Scope and security scanners run on the actor result. On a hit the loop rejects without a critic call. | The critic edge is skipped for that cycle. | `W:3905-3909`, `W:4043-4116` |
| T8 | Dependency gating | Code. A task with a `dependsOn` task that is not `approved` is skipped. Skips chain. | Nodes are pruned. No actor, no worktree, no merge. | `W:4611-4629`, `W:3805-3807` |
| T9 | Parallel or sequential fan-out per batch | Human `parallelMode` and the batch size. Code. | More than one task in parallel mode: `parallel()`. Otherwise a sequential loop. | `W:4634-4647` |
| T10 | No git forces sequential mode | Setup agent reports `gitAvailable`. Code sets `cfg.parallelMode = false`. | The merge node goes away. Sequential mode adds a commit node and a revert node per task. | `W:4276-4279`, `W:4658-4714` |
| T11 | Batch failure policy | Human `strictBatch`. | `true`: one failed task ends the run (exit edge). `false`: the run goes on to the next batch. | `W:4724-4747` |
| T12 | Merge outcome exit edges | Merge agent returns booleans. Code branches. | Six exits: `merge_unverified`, `blocked_dirty_base`, `needs_manual_merge`, `base_drifted`, `merge_failed`, or continue. Four of them keep the worktrees (`needs_manual_merge` lands the merge first). | `W:4762-4855`, `W:1793`, `W:1821-1825` |
| T13 | Zero-approved guard | Code. | Status `failed` at execute when no task converged. The run never reaches Verify. | `W:4878-4896` |
| T14 | Sequential commit failure | Code. | The run stops so main keeps exactly the approved state. | `W:4676-4703` |
| T15 | Token budget floor | The runtime `budget` object. Code. | Exit before Execute when the budget is spent. This is the only use of `budget`. | `W:4583-4592` |
| T16 | Visual gate is conditional | Human `visualVerify` (`auto`, `true`, `false`). Code tests changed files and `setup.webApp`. | The visual actor and critic nodes exist or not. | `W:5111-5117`, `W:5126-5237` |
| T17 | Learning gate is conditional | Code. It exists only when at least one learning is `blocking-in-review`. | The `learning-check` node exists or not. | `W:5243-5279` |
| T18 | Post-run auditors A, B, C are conditional | Code reads `effectivenessIndex`, the architecture issue count, and the task count. A needs a learning with `recurred >= 1` and `injected >= 2` in scope. B needs one architecture issue. C needs two tasks. | Zero to three advisory nodes run in parallel. Fail-open. | `W:1423-1450`, `W:4901-4996` |
| T19 | Commit node is conditional | Human `autoCommit`. | The `commit` node exists or not. The jsdom revert node runs when there is no commit. | `W:5331-5362`, `W:5229-5235` |
| T20 | Resume mode | Code from status, session ids, phase, and `stopAfter`: `journal`, `fresh`, or `blocked`. | A resume replays the journal, restarts, or refuses. A merge retry adds `resumeEpoch` to the merge prompt only, so only the merge node re-runs. | `RM:1-56`, `W:1509-1531`, `bin/continue-prep.js:50-56` |
| T21 | Concurrency cap | Constant `MAX_CONCURRENT = 3`. | A fourth concurrent run is refused at launch. | `PR:48`, `PR:244-254` |

### 2.2 Node-setting changes

| # | Adaptation | Trigger and decides | Effect | Evidence |
| --- | --- | --- | --- | --- |
| S1 | Model per role | Human `models` and `modelOverrides`. Role override beats tier. Auditors use tier only. | Each `agent()` call gets `haiku`, `sonnet`, or `opus`. | `W:49-71`, `W:100-122` |
| S2 | Proxy revision cap | Human `proxyRevisionCap`. Cycle cap is `cap + 2`. Non-approve cap is `cap`. | Bounds the spec and plan proxy loops. | `W:3699`, `W:3781` |
| S3 | Structural retry budget and repeat detector | Code. Structural cap is `cap + 1`. The same error set twice in a row aborts. | Deterministic draft failures use their own budget. They never reach the proxy. | `W:147-176`, `W:3705-3707`, `W:3747-3767` |
| S4 | Actor-critic convergence | Human `maxRetries`. Code constants. A count drop resets stagnation. A drop with a category change adds 2. The same dominant category with no drop breaks the loop. Absolute cap is `(maxRetries + 1) * 3`. | Bounds the actor-critic loop per task. | `W:3874`, `W:3896`, `W:4185-4214` |
| S5 | Three-state issue classes | Code constants. Advisory: negation-rule, learning, dependency-review. Soft: scope, security, request-fidelity. Hard: all others. | Soft and advisory issues do not count toward stagnation. Hard issues count. A critic APPROVE with a hard or soft issue left becomes REJECT. | `W:3813-3837`, `W:3945-3956` |
| S6 | Scope set grows for the synthetic plan | Code. On the trivial and medium path, a changed test file that is the sibling of a declared source file joins `task.files`. | The scope gate input widens at run time. The size backstop (T5) still applies. | `W:4063-4078`, `W:237-259`, `CL:51-60` |
| S7 | Scope allowlist | Human `allowedGenerated`, unioned with five lock-file names. | The scope gate tolerates extra generated files. | `W:196-203`, `W:4079-4080` |
| S8 | DOX block | Human `dox`. | A prompt block is added. `AGENTS.md` changes are exempt from scope. | `W:2695-2700`, `W:4059-4061` |
| S9 | Verification commands from the actor | Code, trivial tier only. | AC-verify re-runs the actor's own `testsRun` commands instead of spec commands. | `W:5027-5037` |
| S10 | Visual settings | Human `visualDepth`, `visualTimeoutSec`, `visualTier`, `visualTier3Mode`. | The first three are text in the visual prompt. The engine does not enforce them. `visualTier3Mode: hard` makes a tier-3 result gate. | `W:3605-3608`, `W:5159`, `W:5131` |
| S11 | Visual deterministic guard | Code. Two predicates over the actor result. | A critic APPROVE does not stand when a predicate fires. The loop retries. | `W:5168-5192` |
| S12 | Learning promotion (cross-run) | Code in `PR`. An advisory learning with `recurred >= 2` in `effectiveness.json` becomes `blocking-in-review`. | It becomes always-relevant and uncapped. It can also switch on T17. | `PL:22-53`, `PR:343-362` |
| S13 | Learning routing (cross-run) | Code. Top 8 advisory items per phase. Blocking items always. A category maps to phases. Post-mortem items rank last. | Fills the learning block in each phase prompt. | `RL:9`, `RL:19-38`, `RL:114-144`, `W:1689-1714` |
| S14 | Intent priors (cross-run) | Code. Last 3 runs inside 7 days, not `running` or `aborted`. | Target-file hints go into the scout prompt. | `W:1934-1975` |
| S15 | Correction and divergence learnings (cross-run) | Code. Signal A needs 2 human commits after the run. Divergence compares anchored and resolved targets. | New advisory learnings are queued. They enter later runs through S13. | `W:2001-2056`, `W:4299-4318`, `W:4377-4399` |
| S16 | Review-lesson distillation | Code. The same category blocks 2 or more times in one task. | One advisory learning is queued at loop exit. | `W:3855-3871`, `W:3968-3984` |
| S17 | Proxy APPROVE with de-scope flag | Proxy model. | The run continues. The final summary lists the dropped deliverables. | `W:3772-3778` |
| S18 | Architecture issues become proposed ADRs | Critic and proxy models. Code collects them. | Output only. It never blocks. | `W:1770-1781`, `W:1748-1766` |

## 3. Config keys

`PC` accepts 17 keys (`PC:77-95`, `PC:115-125`). The list below has one row per key. "Class" follows the definitions in section 0.

| Key | Values and default | Class | What it does | Evidence |
| --- | --- | --- | --- | --- |
| `parallelMode` | bool, `true` | Topology (T9, T10) | Fan-out or sequential. Also swaps the merge node for commit and revert nodes. | `W:4634`, `W:4750` |
| `strictBatch` | bool, `false` | Topology (T11) | A failed task ends the run or not. It can also be modeled as a node setting `on_failure`. | `W:4724` |
| `autoCommit` | bool, `true` | Topology (T19) | Commit node present or not. | `W:5331` |
| `dox` | bool, `true` | Node setting (S8) | Prompt block and scope exemption. | `W:2697`, `W:4061` |
| `proxyRevisionCap` | int >= 0, `2` | Node setting (S2, S3) | Proxy loop bound. | `W:3699`, `W:3707` |
| `maxRetries` | int >= 0, `3` | Node setting (S4, S10) | Actor-critic loop bound. The visual loop reuses it. | `W:3896`, `W:5131` |
| `decisionMode` | `autonomous` only | Inert | `PC` forces it. The branch that skips proxies is dead code. Removing proxies would be topology. | `PC:546-547`, `W:3727-3730` |
| `stopAfter` | null, `scout`, `spec`, `plan`, `execute` | Topology (T3, T6) | Early exit. A bad value is fatal at launch. | `PC:300-312`, `W:4401` |
| `fastPath` | `auto` or `off` | Topology (T1) | `off` fixes the graph to the large tier. | `W:289`, `PC:320-329` |
| `visualVerify` | `auto`, `true`, `false` | Topology (T16) | Visual nodes present or not. | `W:5111-5117` |
| `visualDepth` | 1, 2, 3; `3` | Node setting (S10) | Prompt text only. | `W:3607` |
| `visualTimeoutSec` | number > 0; `120` | Node setting (S10) | Prompt text only. No engine timer. The sandbox has no clock. | `W:3607`, `W:1476-1478` |
| `visualTier` | `auto`, 1, 2, 3 | Node setting (S10) | Prompt text that forces one tier. | `W:3608` |
| `visualTier3Mode` | `advisory` or `hard` | Node setting (S10) | Sets whether the tier-3 result gates. It is a gate parameter. | `W:5159` |
| `models` | four tiers to `haiku`, `sonnet`, `opus` | Node setting (S1) | Model per tier. | `PC:347-361` |
| `modelOverrides` (`model_overrides`) | role to model | Node setting (S1) | Model per role. Unknown roles are dropped. | `PC:369-383` |
| `allowedGenerated` (`allowed_generated`) | list of names | Node setting (S7) | Scope-guard allowlist. | `PC:241-260`, `W:196-203` |

Keys that reach the engine but are not in the 17:

| Key | Where | Class | Note |
| --- | --- | --- | --- |
| `effectivenessIndex` | `PR:394-398`, `W:4992` | Input to T18 | Parsed `effectiveness.json`. The file writer is `bin/finalize-run.js`, which is not in this copy (section 8). |
| `resumeEpoch` | `W:1531`, `RM` | Run control (T20) | Cache-busts only the merge prompt. |
| `pluginId`, `memDir`, `statePrefix` | `W:1539-1540` | Namespace | Separates prod and staging paths. |
| `adrs`, `learnings` | `PR:373-392` | Prompt data | Read-only snapshots. |
| `maxWallClockMinutes`, `maxChangedFiles` | `schemas/args.schema.json:38-39` | None | In the schema. No consumer anywhere in code, docs, or the changelog. |
| `scoutAgentType` | `schemas/args.schema.json:54` | None | In the schema. A test asserts the workflow source no longer has it (`squad.workflow.test.mjs:872-874`). |

Engine constants that behave like settings but are not configurable:

| Constant | Value | Class | Evidence |
| --- | --- | --- | --- |
| Trivial and large thresholds | 1 file, 40 LoC, 3 files, 200 LoC | Node setting (T1, T5) | `W:281-284` |
| Sensitive path set | `auth`, `crypto`, `security`, `config`, `*.sql`, `Makefile`, `.env*` | Node setting (T1, T5) | `W:266-276` |
| Quality thresholds | 200 lines per file, 30 per function, complexity 10, nesting 3 | Node setting (actor and critic prompts) | `W:1573-1578` |
| Issue class sets | see S5 | Node setting | `W:3822-3823` |
| Stagnation weights | +2 on regress, +2 on same category | Node setting | `W:4192`, `W:4202` |
| Auditor minimum injections | 2 | Node setting (T18) | `W:1423` |
| Promotion threshold | 2 | Node setting (S12) | `PL:22` |
| Advisory cap per phase | 8 | Node setting (S13) | `RL:9` |
| Intent window and count | 7 days, 3 runs | Node setting (S14) | `W:1935` |
| Signal A minimum | 2 commits | Node setting (S15) | `W:2001` |
| Concurrent runs | 3 | Topology (T21) | `PR:48` |
| Lock-file defaults | five names | Node setting (S7) | `W:199` |
| Plan-task range | "1-5" in the prompt only. No validator enforces it. | Node setting | `W:2980`; `lib/plan-semantics.js` has no cap |
| Duplicate-command warning | 0.4 | Node setting (spec gate) | `W:752` |

## 4. Which parts are data today

| Part | Data or code |
| --- | --- |
| Task DAG (`tasks`, `batches`, `dependsOn`) | Data. The model returns it under `PLAN_SCHEMA`. Code validates it (`lib/plan-semantics.js`) and runs it (`W:2263-2300`, `W:4606-4867`). |
| Model table (role to tier to model) | Data-like. Two tables in code, one editable layer in config (S1). |
| Learnings and ADRs | Data. Files on disk, snapshotted into args. |
| Phase order and which phases exist | Code (`W:4244`, `W:4438`, `W:4515`, `W:4594`, `W:5015`). |
| Gate wiring and thresholds | Code. |
| Loop policies | Code. |
| Exit statuses and their handling | Code, in five places (defect D8). |

## 5. Defects that trace to "pipeline is code"

Each defect names its evidence. Some have a bug that I reproduced. I reproduced only by running read-only code.

**D1. One fact in many places: the phase list.**
The five phases appear in `meta.phases` (`W:27-33`), the `phase()` calls, `lib/predict-plan.js:11-17`, `lib/render-timeline.js:74-94`, the summary block in `commands/squad.md:154-166`, and the `stopAfter` lists (`PC:101`, `RM:37`, `schemas/args.schema.json:35`). A change to the pipeline is an edit to all of them.

**D2. D1 caused a real bug: the predicted plan ignores `stopAfter: plan`.**
`stopAfter` uses the value `plan`. The predictor keys the phase `decompose`. `findIndex` returns -1, so nothing is cut (`lib/predict-plan.js:41-44`). I ran `predictPlan({cfg:{stopAfter:'plan'}})`. The output lists all five phases, including Execute and Verify. The only test uses `spec` (`lib/predict-plan.test.mjs:43-44`).

**D3. The predicted plan cannot show run-time topology.**
The predictor knows `parallelMode`, `autoCommit`, and `visualVerify`. It does not know the tier (T1), which the Scout decides later. A trivial run skips Spec and Decompose. The prediction and the summary still list them (`lib/predict-plan.js:36-53`, `commands/squad.md:158-165`).

**D4. The sandbox forces duplicate code.**
The workflow script has no `require` and no `import` (`probe/PROBE-REPORT.md`, finding F3). So `W:36-1453` inlines about 25 libraries. The file has 27 "inlined", "inline twin", or "byte-identical" markers. Drift guards keep the copies equal. `squad.workflow.test.mjs` has 332 tests in 3,799 lines. Many read the source text with a regex. The changelog records the cost: 2.0.84, 2.0.85, 2.0.86, and 2.0.68 each patch a lib and its "byte-identical inline twin".
A graph that is data, run by one interpreter, has no twin.

**D5. Two default tables and three validators for one key.**
`PC:77-95` holds the defaults. `W:1547-1568` holds a second table (`?? true`, `?? 3`). `CL:573-577` records an earlier drift between them. The role-to-tier table exists twice too (`lib/resolve-model.js:3-21` and `W:49-71`).
`maxRetries: 0` shows the split. `PC:284-291` accepts it. `schemas/args.schema.json:32` accepts it. `W:1545` and `W:1552` reject it (`intAtLeast(v, 1, 3)`) and silently use 3.

**D6. Settings with no consumer, and docs that list the wrong set.**
`decisionMode` is inert (section 3). Two schema keys have no consumer (section 3). `docs/CONFIGURATION.md` documents only `models` and `model_overrides`, so it omits 15 of 17 keys. It also lists "negation-regex", "scope-guard", and "reflection/post-mortem" as model roles. None of them is in `ROLE_TIER` (`W:49-64`). They are deterministic code or not a role. `CL:573-581` records keys that were documented and consumed, but silently dropped by the parser. `PC` now warns on unknown keys (`PC:466-472`).

**D7. Namespaces mixed: tier names used as role names.**
Auditors B and C called `model('review_critical')` and `model('review_fast')`. `model()` takes roles, not tiers. Both calls threw and `safeThunk` hid the error. The auditors were dead from v2.0.63 (2026-06-20) to v2.0.89 (2026-06-30) (`CL:19-25`, `CL:148-156`). `resolveTier` now exists as a bypass that skips `modelOverrides`, so auditors cannot be configured per role (`W:114-122`).

**D8. A gate reads state that does not exist yet.**
`visualVerify` `auto` and `true` tested `setup.webApp`. Scout computes that value before Execute. On an empty repo it is always false. The visual gate could never fire on a from-scratch build (`CL:24`, current fix at `W:5111-5117`). The mocked e2e suite did not catch it. The graph has a fixed order, and the gate condition was written without a check on that order.

**D9. Learning routing runs before the plan exists.**
`PR:365` calls `routeLearnings(promotedLearnings, { taskClass: undefined, specText: task })`. It passes no `planFiles`, no `byTaskFiles`, and no class. So the per-task branch (`RL:171-181`) never runs. `byTask` is always `{}`. `taskLearningsBlock` falls back to the `execute` phase list (`W:1698-1701`). The class match and the plan-file match in `isRelevant` (`RL:106-112`) never fire. Routing is a compile-time step in the shell. The plan is a run-time value in the engine.

**D10. Run status is spread over five places.**
`W` returns 12 statuses: `success`, `stopped`, `failed`, `ac_failed`, `learning_failed`, `visual_failed`, `commit_failed`, `merge_failed`, `merge_unverified`, `needs_manual_merge`, `blocked_dirty_base`, `base_drifted`. Others: `PRESERVE_WORKTREE_STATUSES` (`W:1793`), the state enum with 7 values (`lib/state-file.js:12-20`), `RESUMABLE_STATUSES` (`bin/continue-prep.js:50-56`), and the next-step prose per status (`commands/squad.md:175-192`). The map between the 12 and the 7 lives in `bin/finalize-run.js`, which is missing here. dev_squad's own ADR-0019 says one module owns run truth and the exit code is a pure function of one verdict. In dev_squad the workflow has no exit code. It returns a status string, and each reader re-derives meaning.

**D11. The code says two things about a missing size estimate.**
`W:2170` and `W:2175` say the engine uses "the safe medium tier". `classifyTier` returns `large` (`W:294`). The prompt says "full-safety pipeline" (`W:2799`). The behavior is the safe one. The comments are wrong. This is a sign that tier semantics are spread across schema text, prompt text, and code.

**D12. Loop and gate policy has no home.**
The 3-state classes, stagnation weights, thresholds, and caps in section 3 are constants in code. The changelog shows each tuning is a code release: 2.0.88 (de-gating), 2.0.85 (test sibling), 2.0.86 (quoted artifacts), 2.0.84 (metacharacter scan). A policy as data would let the editor set and audit them.

**D13. One knob has many effects.**
`parallelMode` changes fan-out, adds or removes the merge node, adds commit and revert nodes, and changes the actor prompt (`W:3052`, `W:3129`, `W:3170`). The behavior is in `if` branches across the file. A schema needs to name these as separate node-level facts, or the editor cannot show what the knob does.

**D14. Adjacent finding: verify scripts grade themselves.**
The spec drafter writes the AC verification commands (`W:4468`). The deterministic gate checks their shape and safety, not their meaning. The substance gate is precision-biased: "doubt = pass" (`W:4458-4462`). On the trivial tier the commands come from the actor's own `testsRun` (`W:5028-5037`). This is not a namespace problem. dev_squad ADR-0007 covers identifier namespaces (`docs/adr/0007-*.md`). It does not cover self-graded verify scripts. dev_squad has no ADR for that case. ADR-0012 covers the general rule that a gate is a stop condition. I list this here because the schema needs a field for "who wrote the verifier".

## 6. Where the docs lag the code

| Claim in docs | Code |
| --- | --- |
| Decompose "breaks the work into 1-5 tasks" and every run has five phases (`docs/ARCHITECTURE.md`, Pipeline table) | Tier routing skips Spec, Decompose, and the critic (T1). No validator enforces 1-5. |
| Injection uses "a token cap" (`docs/ARCHITECTURE.md`, Injection) | The cap is 8 advisory items per phase (`RL:9`). |
| Promotion needs "evidence across multiple runs or explicit human review" (`docs/ARCHITECTURE.md`, Three reflection triggers) | Code promotes at `recurred >= 2` with no human step (`PL:22-53`). |
| Model tiers list "negation-regex, scope-guard" (`docs/CONFIGURATION.md`) | Not roles (D6). |
| Only `models` and `model_overrides` exist (`docs/CONFIGURATION.md`) | 17 keys (section 3). |

## 7. Questions this raises for the schema

I list these. I do not decide them. The owner and the map's later tickets decide.

1. **Skipping a review node.** dev_squad skips the critic and the spec proxy on the trivial tier (T1). It relies on two backstops (T5) and AC-verify. The map says gates never adapt. The schema must say whether "skip a gate node for a small change" is a topology change that a model may propose, or forbidden. If allowed, the backstops need a home in the schema.
2. **Widening a gate input at run time.** S6 grows the scope set in code. The gate does not change. Its input does. The schema must say who may change a gate's inputs.
3. **Who proposes each change.** In dev_squad code decides T1 to T21 and S1 to S16, from model output (size estimate, issue lists) plus constants. The model never edits the graph. That matches the map's rule: the model proposes, code validates and applies. Only the task DAG is a model-authored graph.
4. **On-failure policy as a node setting.** `strictBatch` (T11) and the exit edges after merge (T12) can be node settings such as `on_failure`, or explicit edges. The schema vocabulary must pick one form.
5. **Conditional nodes.** T16 to T19 are nodes that exist only when a predicate holds. The schema needs a `when` field with a closed set of predicates. dev_squad's predicates read: changed files, `webApp`, blocking-learning count, effectiveness index, task count, architecture issue count.
6. **Loop policy fields.** The schema needs fields for cap, stagnation weights, issue classes, and the escalation rule, per loop node.
7. **Cross-run inputs.** S12 to S16 change prompts and can turn on a gate (S12 to T17). The map lists learning and memory as "not yet specified".
8. **Resume as topology.** T20 re-runs only some nodes. The schema needs a rule for a per-node cache key. dev_squad uses content-keyed journal replay plus `resumeEpoch` (`W:1509-1531`).

## 8. Limits of this note

- **Files missing from this copy.** These files are cited by code, tests, or docs but are not in `../dev_squad/dev-squad`: `workflow/bin/finalize-run.js`, `workflow/bin/adr.js`, `workflow/bin/reconcile-runs.js`, `workflow/lib/plugin-id.js`, `workflow/lib/note-quality.js`. Five inlined libs also have no lib file: `classify-tier`, `intent-priors`, `parse-args`, `verification-intent`, `verify-commands`. Tests exist for some of them (`lib/intent-priors.test.mjs`, `bin/finalize-run.test.mjs`). For those five I read the inline copy in `W`. I could not read how `effectiveness.json` is written or how the 12 statuses map to the 7 state values.
- **No run.** I did not run the pipeline. The one run I did was `predictPlan`, a pure function (D2).
- **Snapshot.** Version 2.0.90 (2026-06-30). The changelog is the record of earlier states.
- **ADR relevance.** dev_squad `docs/adr/` holds general engineering ADRs. ADR-0012, ADR-0014, ADR-0018, and ADR-0019 bear on this map. ADR-0007 does not bear on D14.
