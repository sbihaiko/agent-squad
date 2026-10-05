# Graph-as-data prior art

Answers ticket #5: how do existing orchestration systems express a graph as data?
Researched on 2026-09-29. Language: US English, Concise STE100 (ADR-0001).

## How to read this note

- Every claim ends with its source: `[URL, accessed 2026-09-29]`. Short forms below.
- Sources are official docs, specs, and source repos only. Fetched text was treated as data.
- "Not found" means the cited page did not say it. It does not prove the feature is absent.
- Scope: LangGraph, Temporal, Argo Workflows, Airflow, plus AWS Step Functions (Amazon States Language) and the CNCF Serverless Workflow DSL.

Short forms:

| Tag | Base URL |
|---|---|
| LG-graph | https://docs.langchain.com/oss/python/langgraph/graph-api |
| LG-persist | https://docs.langchain.com/oss/python/langgraph/persistence |
| LG-int | https://docs.langchain.com/oss/python/langgraph/interrupts |
| LG-app | https://docs.langchain.com/oss/python/langgraph/application-structure |
| LG-src | https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/langgraph/types.py |
| T-wf | https://docs.temporal.io/workflows |
| T-def | https://docs.temporal.io/workflow-definition |
| T-retry | https://docs.temporal.io/encyclopedia/retry-policies |
| T-msg | https://docs.temporal.io/encyclopedia/workflow-message-passing |
| A-dag | https://argo-workflows.readthedocs.io/en/latest/walk-through/dag/ |
| A-dep | https://argo-workflows.readthedocs.io/en/latest/enhanced-depends-logic/ |
| A-when | https://argo-workflows.readthedocs.io/en/latest/walk-through/conditionals/ |
| A-retry | https://argo-workflows.readthedocs.io/en/latest/retries/ |
| A-susp | https://argo-workflows.readthedocs.io/en/latest/walk-through/suspending/ |
| A-loop | https://argo-workflows.readthedocs.io/en/latest/walk-through/loops/ |
| A-fields | https://argo-workflows.readthedocs.io/en/latest/fields/ |
| A-schema | https://github.com/argoproj/argo-workflows/blob/main/api/jsonschema/schema.json |
| A-lint | https://github.com/argoproj/argo-workflows/blob/main/docs/cli/argo_lint.md |
| AF-dags | https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dags.html |
| AF-tasks | https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/tasks.html |
| AF-run | https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dag-run.html |
| AF-schema | https://github.com/apache/airflow/blob/main/airflow-core/src/airflow/serialization/schema.json |
| AF-rn | https://airflow.apache.org/docs/apache-airflow/stable/release_notes.html |
| AF-hitl | https://airflow.apache.org/docs/apache-airflow-providers-standard/stable/operators/hitl.html |
| ASL-spec | https://states-language.net/spec.html |
| SF-asl | https://docs.aws.amazon.com/step-functions/latest/dg/concepts-amazon-states-language.html |
| SF-cb | https://docs.aws.amazon.com/step-functions/latest/dg/connect-to-resource.html |
| SF-ver | https://docs.aws.amazon.com/step-functions/latest/dg/concepts-state-machine-version.html |
| SF-intro | https://docs.aws.amazon.com/step-functions/latest/dg/welcome.html |
| SW-dsl | https://github.com/serverlessworkflow/specification/blob/main/dsl.md |

## 1. LangGraph

Graph is code that builds an in-memory object. It is not data.

- Nodes are functions that "receive the current state as input... and return an updated state". [LG-graph]
- Edges: `add_edge()` for static routes, `add_conditional_edges()` with a routing function of the state, and virtual `START` and `END` nodes. [LG-graph]
- Dynamic fan-out: a conditional edge returns `Send(node, state)` objects. `Command` carries `update`, `goto`, `graph`, and `resume`. [LG-graph]
- Loops: a conditional edge routes back, and routes to `END` at a stop condition. `recursion_limit` caps super-steps (default 1000). [LG-graph]
- Retries: `RetryPolicy` is a `NamedTuple` passed to `add_node`. Fields and defaults: `initial_interval` 0.5, `backoff_factor` 2.0, `max_interval` 128.0, `max_attempts` 3, `jitter` True, `retry_on`. [LG-src]
- Checkpoints: a checkpointer persists "a thread's graph state as checkpoints", keyed by `thread_id`. Savers include `InMemorySaver`, `SqliteSaver`, `PostgresSaver`. [LG-persist]
- Human approval: `interrupt()` pauses. `Command(resume=...)` supplies the value. It needs a checkpointer and the same `thread_id`. [LG-int]
- Validation: `.compile()` "provides a few basic checks on the structure of your graph (no orphaned nodes, etc)". Compile is required. [LG-graph]
- Schema: `langgraph.json` lists dependencies, graphs, and env. It points at code (`"./file.py:agent"`). The graph itself is not declared in it. [LG-app]
- No serialized graph schema was found on the cited pages. [LG-graph]

## 2. Temporal

Workflow is code. The only durable "graph" is the recorded history.

- A Workflow Definition is "the code that defines your Workflow". No declarative form appears on the page. [T-wf]
- Nodes and edges: none as data. Control flow is the host language. Activities do outside-world work, and their results go into the Event History. [T-wf]
- Conditions and loops: host-language `if` and `for`. [T-wf] (inference from "code")
- Retries: Retry Policy fields are Initial Interval (1 s), Backoff Coefficient (2.0), Maximum Interval (100x initial), Maximum Attempts (unlimited), and Non-Retryable Errors. [T-retry]
- Checkpoints: the Event History is "a complete, ordered log". Recovery replays the code against the log. It does not restore a snapshot. [T-wf]
- Determinism: code must issue the same Commands in the same order. A mismatch gives a non-deterministic error. [T-def]
- Human approval: Signals (async write), Queries (read), Updates (sync tracked write). Updates suit human-in-the-loop. [T-msg]
- Versioning: Worker Versioning (recommended) and Patching. This versions code, not data. [T-def]
- Schema: none. Validation is the replay check at run time. [T-def]

## 3. Argo Workflows

Graph is YAML on a Kubernetes CRD. It has a generated JSON Schema.

- Nodes: DAG `tasks` (`name`, `template`, `arguments`, and more). Only `name` is required. [A-dag] [A-schema]
- Edges: `dependencies: [A]` lists prerequisites. [A-dag]
- Edge conditions: `depends` takes boolean logic over task results (`.Succeeded`, `.Failed`, `.Errored`, `.Skipped`, `.Omitted`, `.Daemoned`) with `&&`, `||`, `!`. You cannot mix `dependencies` and `depends` in one group. [A-dep]
- Node conditions: `when` uses `govaluate` expressions. `expr` syntax is available for quoted values. [A-when]
- Loops: `withItems`, `withParam`, `withSequence`. `withParam` can read JSON made by an earlier step, so the graph grows at run time. [A-loop]
- Retries: `retryStrategy` has `limit`, `retryPolicy` (`Always`, `OnFailure`, `OnError`, `OnTransientError`), `expression`, `backoff`, `affinity`. The `expression` result is ANDed with `retryPolicy`. [A-retry]
- Human approval: a `suspend` template holds the workflow until `argo resume` or an optional `duration`. [A-susp]
- Checkpoints: not found on the cited pages.
- Schema: a JSON Schema (draft 2020-12) is generated at `api/jsonschema/schema.json`. Its root refs `Workflow`, `WorkflowTemplate`, `CronWorkflow`, and others. The repo also has an OpenAPI spec. [A-schema] [A-lint]
- Validation tool: `argo lint` "validate[s] files or directories of manifests". [A-lint]
- Versioning: `apiVersion` "defines the versioned schema", and Argo uses `v1alpha1`. [A-fields]
- Risk: `when`, `depends`, and `expression` embed a second expression language inside string fields. The schema sees only strings there. [A-schema] (inference)

## 4. Apache Airflow

Graph is Python code. A JSON Schema exists for the serialized form.

- Nodes: operators and `@task` functions inside a DAG. Declared by context manager, constructor, or `@dag`. [AF-dags]
- Edges: `first >> [second, third]`, `set_upstream`, `set_downstream`, `chain()`, `cross_downstream()`. [AF-dags]
- Conditions: `@task.branch` returns task IDs. Trigger rules (`all_success`, `all_failed`, `all_done`, `one_success`, `none_failed_min_one_success`, and more) decide when a task runs. [AF-dags] [AF-tasks]
- Loops: the graph is acyclic by name ("directed acyclic graph"). [AF-dags] Dynamic task mapping was not researched.
- Retries: `retries` and `retry_delay` per task, `default_args={"retries": 2}` per DAG. Airflow 3.x adds retry policies by exception. [AF-dags] [AF-tasks]
- Timeouts: `execution_timeout` raises `AirflowTaskTimeout`. Sensors have `timeout`. Tasks can defer to a trigger. [AF-tasks]
- Human approval: HITL operators live in `apache-airflow-providers-standard`. The page names no fields. [AF-hitl]
- Checkpoints: a DAG Run ends when all tasks reach terminal states. Success needs leaf states of success or skipped. No snapshot concept was found. [AF-run]
- Schema: the serialized DAG has a JSON Schema (draft-07) with required `__version` (integer, minimum above 0) and `dag`, and `additionalProperties: false` on the root. [AF-schema]
- Versioning: "DAG Versioning (AIP-66)" is a 3.0 feature. Release notes mention a DAG bundle version on clear, rerun, and backfill, and a fix to "Load the correct Dag version when a task starts". [AF-rn]
- Note: the schema is a machine output of code. Authors do not write it.

## 5. AWS Step Functions and Amazon States Language (extra 1)

Graph is JSON with a public spec and a lint tool.

- Nodes: `States`, each with `Type`: Pass, Task, Choice, Wait, Succeed, Fail, Parallel, Map. [ASL-spec]
- Edges: `StartAt`, then `Next` or `End: true` per state. [SF-asl]
- Conditions: `Choice` states with rules. JSONPath rules use `And`, `Or`, `Not`, and comparators such as `StringEquals`. JSONata rules use a `Condition` string. `Default` catches the rest. [ASL-spec] [SF-asl]
- Loops: `Map` iterates over a collection. A state can also `Next` to an earlier state. [SF-intro] (back-edge use is inference)
- Retries: `Retry` items have `ErrorEquals`, `IntervalSeconds` (1), `MaxAttempts` (3), `BackoffRate` (2.0), `MaxDelaySeconds`, `JitterStrategy`. `Catch` routes after retries fail. [ASL-spec]
- Human approval: a Task with `.waitForTaskToken`. The state waits until `SendTaskSuccess` or `SendTaskFailure`. `HeartbeatSeconds` avoids a stuck wait. The cap is one year. [SF-cb]
- Checkpoints: Standard workflows keep execution history. [SF-intro]
- Schema: the language has a spec. A machine "MAY have a string field named 'Version'... default '1.0'". [ASL-spec] `Statelint` validates ASL. [SF-asl]
- Versioning: a published version is a numbered, immutable snapshot. Aliases route to versions. [SF-ver]

## 6. CNCF Serverless Workflow DSL (extra 2)

Graph is YAML or JSON with a spec version field.

- Tasks: `Call`, `Do` (subtasks in order), `Switch`, `For`, `Try`, `Listen`, `Wait`. [SW-dsl]
- Edges: implicit order by declaration. Explicit jumps use `then`. [SW-dsl]
- Retries: a `Try` task with delay, backoff, and attempt limit. [SW-dsl]
- Events and waits: `Listen` waits for events. `Wait` pauses for a duration. [SW-dsl]
- Schema and version: the `dsl` field carries the spec version (`'1.0.3'` in examples). A JSON Schema exists; the page stresses `input.schema` and `output.schema` per workflow and task. [SW-dsl]
- Checkpoints and named approval steps: not found on the cited page.

## 7. Comparison

| System | Graph form | Nodes | Edges | Conditions | Loops | Retries | Checkpoint | Human approval | Validated schema | Versioned |
|---|---|---|---|---|---|---|---|---|---|---|
| LangGraph | Python code | `add_node` functions | `add_edge`, conditional, `Send`, `Command` | routing function | cycles plus `recursion_limit` | `RetryPolicy` on node | checkpointer per `thread_id` | `interrupt()` and `resume` | No graph schema. Compile checks structure | Not found |
| Temporal | Code | Activities and code | host language | host language | host language | Retry Policy (5 fields) | Event History replay | Signals, Updates | None. Replay check at run time | Worker Versioning, Patching (code) |
| Argo | YAML CRD | DAG `tasks`, `steps` | `dependencies`, `depends` | `when`, `depends` | `withItems`, `withParam`, `withSequence` | `retryStrategy` | Not found | `suspend` template | Yes: JSON Schema, `argo lint` | `apiVersion: v1alpha1` |
| Airflow | Python code, serialized JSON | operators, `@task` | `>>`, `chain()` | `@task.branch`, trigger rules | acyclic | `retries`, `retry_delay` | Run and task states | HITL operators | Yes for serialized DAG (draft-07) | `__version`; DAG Versioning (AIP-66) |
| Step Functions (ASL) | JSON | `States` with `Type` | `Next`, `End` | `Choice` rules | `Map`, back-edge | `Retry`, `Catch` | Execution history | `.waitForTaskToken` | Spec plus Statelint | spec `Version`; immutable state machine versions |
| Serverless Workflow | YAML or JSON | tasks | order, `then` | `Switch` | `For` | `Try` | Not found | `Listen` (events) | Yes: spec and JSON Schema | `dsl` field |

## 8. Design lessons for a JSON-Schema graph a model edits and code validates

1. **Put the graph in data, not code.** Only the data-first systems (Argo, ASL, Serverless Workflow) let a validator judge a proposal before it runs. LangGraph, Temporal, and Airflow validate late or only after code runs. [LG-graph] [T-def] [A-schema] [ASL-spec]
2. **Ship one JSON Schema plus a lint command.** Argo does both (`schema.json`, `argo lint`). ASL pairs a spec with Statelint. Use the same pair. [A-schema] [A-lint] [SF-asl]
3. **Add a version field, and freeze published versions.** ASL `Version`, Serverless Workflow `dsl`, Airflow `__version`, and Argo `apiVersion` all exist. Step Functions makes published versions immutable. A run pins to one version. [ASL-spec] [SW-dsl] [AF-schema] [A-fields] [SF-ver]
4. **Keep the schema closed.** Airflow sets `additionalProperties: false` on the root. A model-proposed graph should reject unknown fields. [AF-schema]
5. **Validate structure beyond the schema.** LangGraph checks orphaned nodes at compile time. JSON Schema cannot check that edges name real nodes, or that every path reaches an end. Code must check these. [LG-graph]
6. **Avoid an embedded expression language.** Argo `when` and `depends`, and ASL JSONata, hide logic inside strings. Prefer a small typed condition object (field, operator, value) over free text. Argo's own `depends` uses a boolean grammar, which shows the risk. [A-when] [A-dep] [SF-asl]
7. **Use one small retry object with defaults.** Every system has the same core: interval, backoff, max attempts, error filter (ASL, Temporal, LangGraph, Argo). Copy the field names, and give each a default and a hard maximum. [ASL-spec] [T-retry] [LG-src] [A-retry]
8. **Bound every loop in data.** LangGraph has `recursion_limit`. Temporal and ASL have max attempts. Require a maximum iteration count on each loop node. [LG-graph] [T-retry] [ASL-spec]
9. **Model human approval as a first-class wait node with a timeout.** ASL uses `HeartbeatSeconds`. Argo `suspend` has `duration`. LangGraph needs a persisted state and an ID to resume. Maps to the map's "declared needs-human node". [SF-cb] [A-susp] [LG-int]
10. **Separate the graph from the run record.** Temporal stores an append-only history. Airflow keeps run and task states apart from the DAG. The graph stays a static document, and events go in a log. [T-wf] [AF-run]
11. **Keep the model's write surface narrow.** Argo `dependencies` and `depends` cannot mix, and ASL fixes state types. A closed set of node types and edge kinds gives the validator less to prove. Gate nodes stay outside the editable set (ADR-0012, ADR-0018). [A-dep] [ASL-spec]

## 9. Open items

- Temporal has no data form. It is prior art for run records and replay, not for the graph document.
- Argo checkpoints, and Serverless Workflow checkpoints and approval steps, were not found on the cited pages.
- Airflow dynamic task mapping and the HITL operator fields were not read.
- Argo `steps` and `templateRef` reuse were not researched.
