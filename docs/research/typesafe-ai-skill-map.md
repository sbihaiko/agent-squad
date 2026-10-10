# `/typesafe:typesafe-ai` cookbook and best-practice map

This map lists what the `typesafe-ai` skill covers. It lists the 18 cookbooks and 4 patterns the skill points to.
It lists the best practices in the TypeSafe docs. It ends with a note on fit for agent-squad.
Language: US English, Concise STE100.

Source: skill body `typesafe` plugin 0.5.7, and the live docs at `https://docs.typesafe.ai`, read on 2026-09-29.
No API call was made and no key was used. Every claim comes from the skill or the docs, not from a probe.
The skill says the live docs are the source of truth. Re-read them before you write an integration.

## How to read this note

| Tag | Meaning |
| --- | --- |
| `[K]` | The skill body (`SKILL.md`), version 0.5.7. |
| `[D:page]` | Docs page `https://docs.typesafe.ai/<page>.md`. |
| `[D:cookbooks/name]` | A cookbook page. Its numbers are the cookbook's own, not re-run here. |
| UNVERIFIED | Not confirmed by a page I read. |

## 1. What the skill is

- **A guide for a coding agent.** It teaches the agent to write code that calls TypeSafe. It does not make Claude Code use TypeSafe as its model. [K] [D:introduction/coding-agents]
- **Call it** with `/typesafe:typesafe-ai`, or say "use the TypeSafe skill". [D:agent-skill]
- **It carries no API reference.** It tells the agent to read the live docs, starting at `llms.txt`. It also says to open the closest cookbook first. [K]
- **TypeSafe makes small judgments usable like programming primitives.** Code owns the workflow. The model supplies "programmable common sense". [K]
- **Jev is the first System One model.** It returns typed answers and probabilities. It does not write text, code, or reasoning. [D:concepts/system-one]
- **Training method:** RLCD, reinforcement learning for calibrated decisions. Calibration holds across groups of answers, not for one answer. [D:introduction/machine-learning-primer]

### Model facts

| Fact | Value | Source |
| --- | --- | --- |
| Current model | `jev-1.13.0`, aliased by `jev-latest` and `jev-preview` | [D:models] |
| Input | Text only: string, object, or array of text | [D:models] |
| Price | US$0.042 per million input tokens. Output tokens are free. | [D:models] |
| Rate limits | 250,000 tokens per second, 1,200 requests per minute. The docs say they change without notice. | [D:models] |
| Context | 64k tokens per request. 32k for `state` plus the longest question. | [D:models] |
| Best language | English. Other languages, including CJK, have lower accuracy. | [D:models] |
| Endpoint | `POST /v1/systemone` | [D:api] |
| Data handling | Not trained on customer requests. Zero data retention exists for enterprise plans. | [D:models] |

An alias moves when a new release ships. Pin the versioned ID when you tune thresholds. [D:models]

## 2. The three primitives

Each request holds one `state` and a map of questions. Every question sees the same state and runs on its own, in parallel. [D:primitives]

| Primitive | Answers | Returns | Limit |
| --- | --- | --- | --- |
| Choice | Which of these options? | `choice`, `probabilities`, `confidence` | 255 options |
| Score | Which ordered level? | `score`, `legend`, `probabilities`, `confidence` | 2 to 10 levels |
| Noul | Is this true? | `noul`, a probability from 0 to 1. No `confidence`. | none stated |

Limits: [D:api]. Choice options and Score levels are ordered by your `criteria`. A Score can fall between two levels.

Question fields:

- `type` and `instructions` are required. `criteria` is required for Choice and Score. It is optional for Noul.
- Question IDs are for your code. They are not sent to the model. [D:primitives]
- `instructions` and `criteria` accept a string, an object, an array, or null. [D:primitives/advanced]
- A backticked path such as `ticket.messages[0].text` points a question at one part of the state. [K]

## 3. The skill's routing table

The skill sends the agent to a different page per task. [K]

| Task | Start here |
| --- | --- |
| Understand the model | `concepts/system-one`, `concepts/how-to-build-with-system-one` |
| Explore what to build | `concepts/use-case-map`, then a cookbook |
| Prepare inputs and questions | `concepts/state`, `primitives`, then the primitive's page |
| Handle uncertainty | `confidence` |
| Write API code | `api`, `sdk/python`, or `sdk/javascript` |
| Update an old integration | `migrating-to-v1` (not read: UNVERIFIED) |

The skill also lists six starting shapes for a design [K]:

| Shape | Idea | Docs to open |
| --- | --- | --- |
| Route and fill known arguments | One request selects a handler and fills its typed arguments. | `function_calling`, `fan-out` |
| Select instead of generate | Code finds candidates. A judgment picks one. Code copies it. | `pre_parsed_value_extraction`, `autoformat` |
| Find and judge evidence | Retrieve candidates, then rank or filter them. | `rerank_typesafe`, `hierarchical_classification` |
| Turn judgments into reusable data | Score dimensions once. Change weights in code. | `composite-scoring`, `autoresearch_feature_discovery` |
| Verify and escalate | Check claims against evidence. Send doubtful cases up. | `citation_check`, `sde_cascade` |
| Respond to changing state | Code keeps goals and observations. Fresh judgments guide the next bounded step. | none (no cookbook) |

The last shape has no cookbook. Keep inferred state apart from observed facts, and check freshness before you act. [K]

## 4. The four patterns

| Pattern | What it does | Rule to keep |
| --- | --- | --- |
| Speculative fan-out | Send every question the code might need in one request. Code ignores the irrelevant answers. | Extra questions still cost tokens. Measure cost and end-to-end latency. [D:patterns/fan-out] |
| Confidence-gated routing | Use confidence as a second axis. The answer says what. Confidence says whether to act. | Set the threshold by the cost of a wrong action. [D:patterns/confidence-routing] |
| Composite scoring | Score independent dimensions. Combine them with weights in code. | Change a weight, not a prompt. Keep the raw scores. [D:patterns/composite-scoring] |
| Intent routing | Classify a request. Send it to code, a specialist LLM, or a person. | Check confidence on the complexity score too. [D:patterns/intent-routing] |

## 5. Cookbook map

The index groups the cookbooks by task. [D:cookbooks] Levels are the index's own.

| Cookbook | Group | Level | Primitives | Job |
| --- | --- | --- | --- | --- |
| Self-consistency: nouls | Self-consistency | Beginner | Noul | Repeat a rubric. Route unstable probabilities to review. |
| Self-consistency: choices | Self-consistency | Beginner | Choice | Add an uncertain outcome to labels. |
| Parallel questions | Batching | Beginner | Noul, Choice, Score | Prove that batching questions keeps answers and cuts cost. |
| Re-ranking | How-to | Beginner | Noul | Re-order a search shortlist. |
| Line-by-line search | How-to | Beginner | Choice, Noul | Find the lines that answer a query, and detect "no answer". |
| Structure recovery | How-to | Beginner | Noul, Choice | Rebuild Markdown from stripped text. |
| Function calling | How-to | Intermediate | Choice, Noul | Turn a sentence into a typed function call. |
| Skill suggestion | How-to | Intermediate | Choice, Noul | Pick at most one skill from a large roster. |
| Entity alignment | How-to | Beginner | Score, Noul | Decide whether two records are the same thing. |
| Classifying RAG passages | How-to | Intermediate | Noul | Filter retrieved passages before generation. |
| Double-checking citations | How-to | Beginner | Choice | Verify that a quote supports a claim. |
| Guardrails for LLMs | How-to | Intermediate | Noul, Score | Screen inputs and outputs. |
| SDE cascade | Extraction | Intermediate | Noul | Verify a cheap extraction. Escalate only doubtful ones. |
| Date extraction | Extraction | Beginner | Choice | Read date parts. Resolve the date in code. |
| Pre-parsed value extraction | Extraction | Beginner | Choice, Noul | Pick one regex candidate. Copy it verbatim. |
| Hierarchical classification | Classification | Intermediate | Choice | Walk a taxonomy with beam search. |
| Autoresearch feature discovery | Classification | Advanced | Score, Noul | Turn text into features for a classical model. |
| Classification using confidence | Classification | Beginner | Choice | Report a broad label when confidence is low. |

### 5.1 Which cookbook to copy

| You need to | Copy |
| --- | --- |
| Decide with a safe "not sure" outcome | Self-consistency (both), Classification using confidence |
| Cut cost of many checks on one document | Parallel questions |
| Order candidates by meaning | Re-ranking |
| Point at a place in a document | Line-by-line search |
| Turn language into a call with typed arguments | Function calling |
| Choose one item from a big list, or none | Skill suggestion |
| Merge or drop records safely | Entity alignment |
| Keep bad context out of an LLM prompt | Classifying RAG passages, Guardrails |
| Check another model's output | Citation check, SDE cascade |
| Recover a value without generating text | Date extraction, Pre-parsed value extraction, Structure recovery |
| Traverse a deep taxonomy | Hierarchical classification |
| Feed a classical ML model | Autoresearch feature discovery |

### 5.2 Design lesson and result per cookbook

Each result is the cookbook's own number on its own data. None is re-run here.

**Self-consistency: nouls** [D:cookbooks/consistency_noul_cookbook]
- One insurance claim, 14 Noul questions, 15 repeats per condition. LLM conditions differ across repeats, even at temperature 0.
- Jev's mean per-question probability spread is 0.0102. One answer (`covered`) ranges 0.43 to 0.53, so it crosses a 0.5 cut.
- Lesson: replace the single cut with a band. Below 0.30 is no. From 0.30 through 0.70 is uncertain, for a person. Above 0.70 is yes. The band is illustrative.
- Caveat: the run adds a changing `uid` field. It cannot separate sensitivity to that field from repeat noise. One claim only.

**Self-consistency: choices** [D:cookbooks/consistency_choice_cookbook]
- One borderline post, 8 Choice questions, 15 repeats. Sampled 2026-09-11 on `jev-latest`.
- Raw label agreement is 90.8%. With the rule "top probability under 0.60 is uncertain", agreement is 99.2% and 74.2% of answers are automatic.
- Lesson: the rule uses the returned probabilities, not the `confidence` field. It adds no calls.
- Caveat: agreement is repeatability, not accuracy. One low-variance LLM setting scored 100% agreement without abstaining.

**Parallel questions** [D:cookbooks/parallel_questions]
- 13 questions (8 Noul, 2 Choice, 3 Score) over a 54,000-character article. Batching keeps the answers and cuts cost and time, by the cookbook's count 12.2 times and 10.0 times.
- Lesson: a question's answer does not depend on the other questions in the request.
- Caveat: the speed figure assumes single calls run one after another. Run them in parallel and the gap shrinks. The token saving stays.

**Re-ranking** [D:cookbooks/rerank_typesafe]
- BM25 builds a 30-passage shortlist for each of 40 legal queries. One Noul per query-passage pair re-orders it. 1,200 calls in total.
- Correct passage in first place rises from 5% to 18%. In the top ten it rises from 38% to 62%.
- Lesson: re-ranking cannot add a passage that fast search missed. Here the shortlist held the answer for all 40 queries.
- The cookbook says a real application asks several questions per pair.

**Line-by-line search** [D:cookbooks/semantic_find]
- Tag each line with an ID. One Choice over the IDs ranks lines. One Noul asks whether the document answers at all. Both go in one request.
- Lesson: Choice probabilities sum to 1, so a line always ranks first. The Noul detects "no answer". In one example the top line scored 0.86 while `exists` was 0.14.
- Over 255 lines: pick a window first, then rank inside it.
- The `exists` cut points are examples. Tune them per document.

**Structure recovery** [D:cookbooks/autoformat]
- Pass 1: one Noul per adjacent line pair asks whether a line break split a sentence. Pass 2: one Choice per block picks its type. Companion questions ride in pass 2.
- The model never writes text. Every output character comes from the input. Two round trips cost 10,211 tokens, 0.8 s, US$0.0015 for one memo.
- Lesson: ask the narrowest fact. "Picks up mid-sentence" gave 12 blocks. "Same paragraph" gave 17 and merged every list.
- Lesson: read direct evidence in code, such as blank lines and markers. Use two merge cuts: 0.2 after a line with no end punctuation, 0.5 after end punctuation.

**Function calling** [D:cookbooks/function_calling]
- Read closed sets from type hints: a `Literal` becomes a Choice, a `list[Literal]` becomes one Noul per member, a `bool` becomes a flag.
- A second Noul (`stated`) asks whether the command mentions an argument at all. If not, the function default applies.
- Lesson: the call's confidence is the lowest confidence among its judgments, not the product. Numbers, dates, and free text get no question and keep defaults.
- Lesson: write each question about the idea, not the parameter name.

**Skill suggestion** [D:cookbooks/skill_suggestion]
- Request 1: one Choice over all 182 skills, plus three Noul questions on whether an action is wanted. A mean under 0.30 suggests nothing.
- Request 2: the top three, read with full text. One Choice and one "fits" Noul per candidate. A best "fits" under 0.30 drops all three.
- Wrong loads fell from 16.8% to 7.3%. Needless loads fell from 9.8% to 4.0%. With the right answer handed over, they were 2.5% and 1.2%.
- Lesson: the Choice settles which skill. The Noul questions settle whether to suggest any.
- Lesson: put the suggestion in its own system block after the roster, so prefix caching holds. Tell the agent it may ignore the suggestion.
- Caveat: an LLM wrote the covered requests from each skill's own text. They are easier than real ones. The agent was `claude-haiku-4-5`.

**Entity alignment** [D:cookbooks/entity_alignment]
- One Score with three levels (different, related, same) plus three field Noul questions. Alcohol content gets no question because it is arithmetic.
- Of 450 pairs, 360 are unlinked, 40 merged, 50 go to a curator.
- Lesson: no threshold is fitted. The cuts at 0.5 and 1.5 follow from how you word the levels. Wrong merges cost more than missed ones, so the middle level is for a person.

**Classifying RAG passages** [D:cookbooks/classifying_rag_passages]
- Four Noul questions per query-passage pair: relevant, holds answer evidence, contradicts the query's premise, contains a prompt injection.
- `route()` tests in fixed order: injection above 0.70 excludes. Contradiction above 0.70 goes to a conflict block. Relevance under 0.45 excludes. Evidence above 0.55 includes. Otherwise exclude.
- Lesson: the questions never ask "should we include it". That rule lives in code, where you edit a number.
- Lesson: keep evidence and conflict in separate prompt blocks. The injection score is a filter, not a security boundary. Treat every passage as untrusted.
- Cost scales with `k`. One request per passage.

**Double-checking citations** [D:cookbooks/citation_check]
- A string match finds fabricated quotes with no model. One Choice per surviving citation returns `verified`, `unsupported`, or `contradicted`. Below 0.8 confidence, a person confirms.
- On 8 citations: 4 accurate ones verified at 0.93 or higher. All 4 planted failures were caught or sent to a person.
- Caveat: the match is exact after normalization. A truncated quote reads as fabricated.

**Guardrails for LLMs** [D:cookbooks/llm_guardrails]
- Four hazard Noul questions plus one harm-severity Score, in one request. Run one battery on inputs and one on outputs.
- Each hazard has an action threshold and a lower review threshold. A named policy is those numbers under a name. The severity Score can turn a review into a block.
- Lesson: the model assesses. The application decides. The same probabilities give different decisions under different policies, with no new call.
- Caveat: 10 messages and 5 replies. It shows the mechanism, not a measured false-positive rate.

**SDE cascade** [D:cookbooks/sde_cascade]
- A cheap model extracts. Per-field Noul questions, framed so `true` means wrong, verify. Any flag above 0.7 escalates to a reasoning model.
- Gate on the maximum flag, not the mean. One clear flag must not be averaged away.
- The cookbook calls its 100-prompt chart "internal", historical, and not re-priced at the current Jev rate.
- Appendix A lists five traits of a good verifier signal. See section 7.

**Date extraction** [D:cookbooks/date_extraction_cookbook]
- Seven Choice questions read the parts: mode, month, day, year, day anchor, weekday, week offset. Code assembles the date.
- Year options run 1900 to 2050, plus `none` and `out_of_range`. Code fills a missing year and flags an out-of-range one.
- Lesson: a date's confidence is the lowest among the parts used. Below 0.60 goes to a person. "Next Thursday" is resolved by a code rule, with today pinned.

**Pre-parsed value extraction** [D:cookbooks/pre_parsed_value_extraction_cookbook]
- A regex over-finds candidates. A Choice picks one span or `none`. Code copies and normalizes it. The model cannot invent a value.
- Limits: 255 options. Names have no regex, so candidates need a roster, an entity recognizer, or an LLM.
- Caveat: the money parser assumes a comma groups thousands. Ask a Noul which convention the document uses.

**Hierarchical classification** [D:cookbooks/hierarchical_classification]
- One Choice per taxonomy node. Beam search keeps `K` paths by the geometric mean of edge probabilities. The ratio of the top two path scores measures separation.
- With `K` = 3, beam search hit 4 of 4 expected leaves. Greedy hit 2 of 4.
- Caveat: four examples. A gate is never tuned to a small sample, so that is an illustration, not a measurement.
- For depth over 10 layers, use `exp(mean(log(p)))` to avoid precision loss.

**Autoresearch feature discovery** [D:cookbooks/autoresearch_feature_discovery]
- An LLM proposes questions. Jev answers them for every row. CatBoost trains on the answers and reports its worst rows. The next proposal reads that report.
- Held-out error in critic-score points: mean baseline 3.09, word counts 2.47, one Score for the target 2.15, 18 questions with no loop 1.87, 38 questions after five rounds 1.77.
- Rounds after the first proposal add 0.10 points, with a 95% interval of −0.147 to −0.050.
- Lesson: no question is filtered before it is answered. One rare-case question can be the most useful column.
- Cost: one request per row per round. Eight workers already hit a rate limit on a shared key. Score allows 10 levels. Eleven returns a server error.
- Caveat: one dataset, one run.

**Classification using confidence** [D:cookbooks/classification_using_confidence]
- One Choice over 75 industry groups. At confidence 0.9 or above, report the group. Below, report its broader division. No second call.
- Across 60 filings: the confident half is right 90% of the time. The other half is right 40% as a group and 70% as a division.
- The recipe reads `confidence`, not the winner's probability. A 0.45 winner with a 0.44 runner-up differs from a 0.45 winner with thin scattered mass.
- The label is self-reported by filers. The set was filtered to filings whose text supports the code.

## 6. Use-case categories

The use-case map groups ideas by industry and by decision shape. [D:concepts/use-case-map]

| Decision shape | Reach for it when |
| --- | --- |
| Classification | One known category should win. |
| Detection | You need the probability that one property is present. |
| Scoring | The answer sits on an ordered rubric. |
| Routing | A category selects the next code path. |
| Search | You match items to a natural-language query. |
| Retrieval | A workflow needs the most relevant context. |
| Ranking | Items need ordering by relevance or quality. |
| Verification | An artifact needs checking for specific failure modes. |
| ML feature extraction | A classical model needs semantic signals. |
| Structured data extraction | Known fields must come out of unstructured input. |

## 7. Best practices

### Question design

1. **One narrow judgment per question.** Ask what a knowledgeable person decides in a second. Split independent factors. Combine in code. [D:primitives] [D:concepts/how-to-build-with-system-one]
2. **Write the whole question in `instructions`.** The ID is not sent. [D:primitives]
3. **Noul: high means yes.** One condition per Noul. Make the yes/no boundary sharp. Add `true` and `false` criteria only when the boundary is subtle. Test with and without them. [D:primitives/noul]
4. **Noul is not a scale.** A 0.5 means yes and no are equally likely. It does not mean "medium". Use a Score for degree. [D:primitives]
5. **Choice: give the full option list.** Add `other` or `none` when the list may not cover the input. For confusable options, describe each with what it covers, what it is not for, and examples. [D:primitives/choice]
6. **Score: describe situations, not degrees.** Each level is judged alone. The model does not see level numbers. Use as many levels as you can describe, up to 10. One dimension per Score. Give a rare extreme its own level. [D:primitives/score]
7. **Keep instruction and criteria aligned.** A Noul whose `true` means no performs worse. [D:model-jaggedness/jev-1.13]
8. **Name the narrowest deciding fact.** "Picks up mid-sentence" beat "same paragraph" in structure recovery. [D:cookbooks/autoformat]
9. **Use structure only when it helps.** Use it for multi-part questions or supporting data such as a schema or a row. [D:primitives/advanced]

### State

10. **Send only what the questions need.** Accuracy falls as unrelated detail grows. Filter and retrieve in code first. [D:model-jaggedness/jev-1.13]
11. **Use an object with named fields.** Point questions at fields by backticked path. [D:concepts/state]
12. **Put current facts in the state.** Do not rely on weights for them. [D:concepts/how-to-build-with-system-one]
13. **Tag items with short IDs** when the answer must point at one. Both search and structure recovery do this. [D:cookbooks/semantic_find]
14. **Send text only.** Turn images, audio, and binaries into text first. [D:models]
15. **Treat state as untrusted data.** Injected instructions can move an answer. State the rule in the criteria and test edge cases. [D:model-jaggedness/jev-1.13]

### Composition

16. **Use code when code can decide.** Rules, arithmetic, and exact lookups stay in code. [D:concepts/how-to-build-with-system-one] [K]
17. **Fan out.** Ask all independent questions over one state in one request. Ask speculative ones. A second request is right only when code needs an answer to build the next state or options. [D:patterns/fan-out] [D:primitives]
18. **Use Choice for "which" and Noul for "whether".** A Choice is relative. Each Noul is absolute and can be low for every option. Skill suggestion and line search use both. [D:model-jaggedness/jev-1.13] [D:cookbooks/skill_suggestion]
19. **Select, do not generate.** Code finds candidates. A Choice picks. Code copies. [D:cookbooks/pre_parsed_value_extraction_cookbook]
20. **Keep counting, dates, and numbers in code.** Let the model extract parts. Let code compare and compute. [D:model-jaggedness/jev-1.13]
21. **Keep raw judgments reusable.** Changing a weight or a filter needs no new inference when the evidence and question meanings are unchanged. [K]
22. **Use weights for trade-offs and separate questions for "any violation" rules.** [K]
23. **Keep policy in one place.** Put questions, thresholds, weights, and actions in one file. A person reviews those first. [D:agent-skill] [D:cookbooks/classifying_rag_passages]

### Confidence and thresholds

24. **Only Choice and Score carry `confidence`.** It summarizes how concentrated the probabilities are. It does not measure workflow correctness. [D:confidence] [K]
25. **Use three paths.** High confidence acts. Medium confirms or gathers evidence. Low goes to a person or a fallback. [D:confidence]
26. **Scale the threshold with risk.** A wrong balance screen is recoverable. A wrong transfer is not. [D:confidence]
27. **Treat every cookbook threshold as an example.** Set yours from labeled data and the cost of each error. Plot confidence against accuracy. [D:concepts/how-to-build-with-system-one] [K]
28. **Prefer an uncertain band to a single cut.** A value near a cut flips the action for no real reason. [D:cookbooks/consistency_noul_cookbook]
29. **Do not threshold when you only need the best option.** Take the top option. Use probabilities if you need a specific statistic. [D:agent-skill]
30. **Aggregate confidence with the minimum, not the product.** One wrong argument spoils a call. [D:cookbooks/function_calling]
31. **Do not port a threshold between primitives.** Separate questions obey no arithmetic identities. [D:model-jaggedness/jev-1.13]
32. **Low confidence is not always a defect.** Several acceptable options can spread probability. Ignore uncertainty on unused branches. [K]
33. **Typed output guarantees the interface, not the truth.** Validate in your domain. [K]

### Verification and escalation

34. **Check deterministic facts first.** A string match finds a fabricated quote with no model. [D:cookbooks/citation_check]
35. **Make each verifier signal narrow and grounded.** Frame it so `true` means wrong, and state what true and false mean. Aggregate per field with the maximum. Keep the verifier independent and cheap. Look for a signal that is high on errors and low on correct answers. [D:cookbooks/sde_cascade]
36. **Escalate the doubtful few** to a person or a reasoning model. [K] [D:cookbooks/sde_cascade]
37. **Word a suggestion so the agent may ignore it.** A confident wrong suggestion persuades more than none. [D:cookbooks/skill_suggestion]
38. **Test representative cases and the resulting behavior.** On failure, inspect the exact state, questions, candidates, answers, composition, and outcome. Separate missing evidence, model error, code error, and service failure. [K]

### Operations

39. **Retry 429 and 529 with exponential backoff.** The SDKs do this by default. [D:api] [D:sdk/python/api/retries]
40. **Keep the key server-side.** The Python SDK reads `TYPESAFE_API_KEY`. [K] [D:sdk/python/usage]
41. **Know what the SDK logs.** At `debug` level it logs request and response bodies. It redacts secret headers, not bodies. Keep `debug` off where state holds private text. [D:sdk/python/usage]
42. **Enable HTTP/2 for many concurrent requests.** [D:sdk/python/usage]
43. **Log the versioned model ID** each response reports. [D:models]
44. **Budget for scale.** Cost grows with rows times rounds. Raise worker counts slowly. [D:cookbooks/autoresearch_feature_discovery]

### Working with a coding agent

45. **Brainstorm first.** Review the plan before the agent builds. [D:agent-skill]
46. **Edit the questions yourself.** The page says agents write weak questions. [D:agent-skill]
47. **Have the agent validate assumptions.** Keep the skill updated. A stale skill invents request fields. [D:agent-skill]
48. **Try cheap test queries.** Give the agent a key and permission to run them, or use the playground. [D:agent-skill] [D:introduction/quickstart]

## 8. Known failure modes of `jev-1.13`

Source: [D:model-jaggedness/jev-1.13], last reviewed by the vendor on 2026-09-17.

| Failure mode | Do this instead |
| --- | --- |
| Literal reading | Write the exact condition. Put boundary cases in the criteria. |
| Math and counting | Keep arithmetic in code. Ask one question per item, then add up. |
| Numeric forms such as hex colors | Convert in code. Pass a named bucket. |
| Score used to interpolate a number | Use it for thresholds only. |
| Date and time comparison | Extract parts with Choice. Compare in code. |
| Indirection and double negatives | Reduce hops. Point to the relevant state. |
| Large state with irrelevant detail | Filter first. Use a Noul to filter if you cannot. |
| Adversarial content | Write precise criteria. Test edge cases before launch. |
| Contradictory instructions and criteria | Align them. |
| Structural invariants | Do not expect `P(x)` and `1 − P(not x)` to match. Ask each decision one way. |
| Text generation | Use a generative model. |

## 9. Conflicts and gaps in the docs

| Item | What the pages say |
| --- | --- |
| Batching gain | Cookbook: 12.2 times cheaper, 10.0 times faster. Primitives page: 11.5 times and 9.6 times. |
| Choice size | API: up to 255 options. Classification cookbook: "reliably up to roughly 240". |
| Latency | Build guide: about 100 ms. Use-case map: 150 ms. Cookbooks measured 111 ms and 114 ms. |
| Confidence floor | Confidence page example: 0.5, with 0.9 for high stakes. Routing pattern example: 0.6, with 0.85. Both are examples. |
| Model version | Most cookbooks ran `jev-1.12`. The current model is `jev-1.13.0`. Only the two self-consistency cookbooks name `jev-1.13.0`. Results and thresholds may not carry over. |
| Prices in cookbooks | The consistency cookbooks say their cost figures are historical, not verified billing. |
| Confidence formulas | The confidence page says a separate cookbook will compare formulas. It is not in the index. |

Not read: the migration guide, the JavaScript SDK pages, and the HTTP API request schema beyond limits and errors.
The installed skill (0.5.7) was not diffed against `SKILL.md` on GitHub main.

## 10. Fit for agent-squad

This section is a suggestion. Nothing here is decided. Each item needs a ticket on the wayfinder map first.

**Where the shapes could fit**

| agent-squad need | TypeSafe shape | Note |
| --- | --- | --- |
| Pick a skill or agent for a node | Skill suggestion | The cookbook targets an agent with a large skill roster. |
| Cheap critic in an actor-critic loop | SDE cascade, citation check | Per-criterion Noul, maximum aggregate, escalate to a reasoning model. |
| A run stops for a person | Confidence-gated routing | A low-confidence branch maps to a declared needs-human node. |
| Screen node inputs and outputs | Guardrails | One request per message. |
| Propose an adaptation from run facts | Choice over a fixed menu | The wayfinder map decided that code validates and applies a model's proposal. |

**Rules from the ADRs that apply**

- A threshold taken from your own data must first be shown to stand for the property it claims. It stays a stop condition, never a target.
- Cookbook thresholds are examples. Do not copy them into a gate.
- A Jev answer is a judgment, so it sits on the model side of the observation seam of the observation contract. It is not a measured fact.
- The SDE cascade lists an independent, cheap verifier as a trait of a good signal. No agent-squad ADR that I read sets this rule (UNVERIFIED).

**Owner-only actions this would trigger**

- A key is a credential, and calls spend money. Both are owner-only in `CLAUDE.md`.
- A call sends text to an external service. Check what leaves the repo before any run.
- Record the decision as an issue labeled `needs-ratification` when someone proposes adoption. This note does not propose it.

**Limits to weigh**

- Text only, English strongest, 32k tokens for state plus the longest question.
- It cannot generate, count, or compare numbers and dates.
- The rate limits can change without notice.

**Open questions for later tickets**

- Which run observations may reach a Jev question, under the observation contract?
- Does a per-node judgment need a model call at all, or does code decide?
- What labeled data would set a threshold, given that gates never adapt at run time?
