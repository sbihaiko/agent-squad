---
name: to-test-scenario
description: Turn a manual GUI test scenario (a Markdown script of cases, for example a pad-only run of the emulator) into a gui-test/1 script, one parent GitHub issue with one sub-issue per case, and a run on the squad's GUI test infrastructure whose results are written back to the sub-issues. Use only when the user asks for it.
disable-model-invocation: true
---

# To Test Scenario

Take a **test scenario** (a Markdown script a person follows, whose cases each have an id, a precondition, steps and an expected result) and make it executable: a `gui-test/1` script, a parent issue with one sub-issue per case, and a run whose verdicts land on those sub-issues.

Sibling of `/to-tickets`: that skill slices **work** into tracer-bullet tickets; this one slices a **scenario** into cases that are **run**, not built. Reuse its publishing mechanics (issue template, `## Blocked by`, `wave:NN`, REST sub-issues), never its slicing rules.

Only a person invokes this skill (`disable-model-invocation`): it publishes issues. Never run it on your own initiative.

## Two halves

- **Judgment** — reading the Markdown, converting a case into steps, deciding whether an action or a check is decidable — is yours, here, in this file.
- **Mechanics** — coverage, the plan, publishing, idempotence, writing results back — live in one deterministic module, `squad.to_test_scenario` (agent-squad). Call it; never reimplement it by hand, and never publish an issue the module did not plan.

## Process

### 1. Gather the scenario

The argument is a path (for example `docs/validation/process/play-pad-only-test-script.md`) or an issue number. Read the whole file. List every case id with its section, precondition and variant tags (for example `[W]` windowed, `[F]` full screen) and report the count.

### 2. Reuse or convert the script

Look for `<name>.gui-test.json` beside the Markdown (format `gui-test/1`, ADR-0272). Check the pair with the module before doing anything else:

```bash
python3 -c "from squad.to_test_scenario import case_ids, gap_report; import json,pathlib; \
print(gap_report(pathlib.Path('<scenario>.md').read_text(), json.loads(pathlib.Path('<name>.gui-test.json').read_text())))"
```

- **`complete: true`** → reuse the script. Do not reconvert.
- **A gap either way** → report it and resolve it: a case id with no step is manual coverage about to be dropped; a step with no case is a step the scenario never asked for. Reuse the script as it is when it is simply newer than the Markdown, and say so; convert only what is missing.

Converting: one step per case, and **the step id is the case id**. Each step carries `precondition`, an `action` (`pad.press`, `pad.release`, `pad.chord`, `key.*`, or `nav.goal` when the path is not what is under test), `wait`, an objective `check` (`ui.screen`, `ui.focused`, `ui.visible`, `ui.dialogs`) when one decides the case, the expected text, the variant values, a severity, and a `role`:

- `under-test` (default) — the case itself.
- `setup` / `recovery` — a step that only reaches a state. It gates the other steps of its batch: the module puts each of them one wave later and names the setup case in their `## Blocked by`.
- `mode: manual` — a case that needs a physical pad, audio or a human reading. Keep it in the same script: it is tracked and commented like any other case.

`nav.goal` belongs to setup, preconditions and recovery only, never to a step under test (ADR-0271); `nav.goal` and sweep cases end `pending` in CI, because no model key exists there (ADR-0271 Decision 6 A).

Ask the user about any case whose action or check you cannot decide, and never invent an id the adapter does not advertise. Validate the result against the adapter's capabilities (`squad.gui_test.validate(script, capabilities)`) **before anything is published**: an unsupported action must be rejected naming what is missing. Then render the Markdown view with `squad/gui_test_render.py` — the JSON is the source, and the rendered Markdown is never edited by hand.

Fixtures (ROM by No-Intro sha1, pack, settings profile) are declared in the script's `fixtures`, never inlined in steps. A hash you do not know stays a `<no-intro sha1 ...>` placeholder: the run reports the missing fixture, and nobody invents a hash.

### 3. Quiz the user

Build the plan and present it before any issue exists:

```bash
python3 -c "from squad.to_test_scenario import build_plan; import json,pathlib; \
print(json.dumps(build_plan(json.loads(pathlib.Path('<name>.gui-test.json').read_text()), \
pathlib.Path('<scenario>.md').read_text(), granularity='per-case', \
script_path='<scenario>.md'), indent=2))"
```

Show: parent title, batches with case counts, the `manual` cases, the `goals` cases, the variants the run covers, and the labels. Ask whether the granularity is right (**one sub-issue per case**, the default, or **per batch** when a large scenario would flood the board), whether any case should be merged or dropped, and whether the run may start right after publishing. Iterate until approved.

### 4. Publish

Publish **the plan the user approved**, through the module, with the real REST runner. Everything written for GitHub is **en-US**, with no `Co-Authored-By` and no tool-attribution line.

```bash
python3 - <<'PY'
from squad.to_test_scenario import rest_runner, run_scenario
...
PY
```

- `rest_runner(repo, project=..., bug_helper=...)` — REST through `gh api`, never GraphQL: a scenario creates dozens of sub-issues and would spend the GraphQL quota. `project` is the board every issue joins at Status "To triage"; `bug_helper` is the checkout's `scripts/report-bug.sh`, the one route a failed run's bug takes.
- The parent carries `ready-for-agent` and `wave:01`. Each case carries `test-case` and its `wave:NN`, and **never `ready-for-agent`**: a case is run, not built, and the autofrontier must not pick it up as work.
- Sub-issues are linked natively through `POST /repos/<repo>/issues/<parent>/sub_issues` with the sub-issue's numeric `id`, never its number.
- The case-id → issue-number map is saved beside the script (`<name>.gui-test.issues.json`) by passing `map_path=`. Pass `saved=` and `parent=` from that map on every later invocation: publishing twice **updates** the parent and the existing sub-issues and creates nothing. A case the script no longer has is listed in `removed` and left open — only a person closes a case.

Do not close or edit any issue the scenario came from.

### 5. Run

The run command is MesenAI `scripts/gui_test_run_script.py` (MesenAI #1222, its own issue): one script in, one run report out — `run_id`, the script sha, `platform`, `build_sha` and one result per step (`passed`, `failed (expected X, observed Y)`, `failed (unknown id)`, `needs review`, `pending`).

```bash
python3 scripts/gui_test_run_script.py <script.json> --adapter mesen-gui [--out <results.json>]
```

It needs `MESENAI_ROOT` (a MesenAI checkout holding `scripts/gui_test/mesen_gui_adapter.py`), `MESEN_GUI_BINARY` (the built launcher of a build carrying the test hook) and `MESEN_CORE_LIB` (the `MesenCore` library of that same build); a missing variable or a core that cannot be loaded is a **failure, never a skip**. It validates the script against the adapter's capabilities *before* launching anything, exits non-zero on a failed step, lists `needs review`/`pending` without turning the run red, and reports a run that executed no step as a failure.

Inject it as the module's `run_command`: a callable that runs the command on the script and returns the report it wrote (`--out`, default `./gui-test-results/<name>-<run id>.json`), with `core_available` set from whether the core and the binary loaded. **If the command does not exist yet, publish, say the run is unavailable and stop** — the module returns `reason: "run command unavailable"`, no verdict is written, and you never simulate a run or hand a verdict to a case.

### 6. Write results back

The module does this from the run report:

- `passed` → comment and close the case's sub-issue.
- `failed` → comment the verdict with expected and observed, leave the case open, and open **exactly one** bug for the whole run through `scripts/report-bug.sh`, listing every failed case. Never one bug per case.
- `needs review` / `pending` / `manual` → comment, leave open, and list on the parent. They are doubt and human residue: visible, never red.
- A case with no result in the run is reported, not passed; an empty run writes nothing.

Screenshots reach an issue only through the **privacy gate** and the evidence branch — the skill never uploads a capture itself, and no resident or personal data goes into a script, an issue or a screenshot.

The parent gets one final comment with counts per verdict, the run id, the script sha, the platform and the SHA of the build under test. The run never closes the parent: **a person closes it** after reading the results.

## Rules

- A run that executed no step is not a pass, and a missing core fails rather than skips.
- Never fabricate a result, a hash or a case id.
- Nothing is published before the user approves the plan, and nothing is published on a coverage gap.
- The script format, the target adapter and the runner are not this skill's to change (`gui-test/1`, ADR-0272).
