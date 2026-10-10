---
name: to-test-scenario
description: Turn a manual GUI test scenario (a Markdown script of cases, for example a pad-only run of the emulator) into a gui-test/1 script, one parent GitHub issue with one sub-issue per case, and a run on the squad's GUI test infrastructure whose results are written back to the sub-issues.
disable-model-invocation: true
---

# To Test Scenario

Take a **test scenario** (a Markdown script a person follows, with cases that each have an id, a precondition, steps and an expected result) and make it executable: a `gui-test/1` script, a parent issue with one sub-issue per case, and a run whose verdicts land on those sub-issues.

Sibling of `/to-tickets`: that skill slices **work** into tracer-bullet tickets; this one slices a **scenario** into cases that are **run**, not built. Reuse its publishing mechanics (issue template, `## Blocked by`, `wave:NN`, `gh` + REST sub-issues), not its slicing rules.

## Process

### 1. Gather the scenario

The argument is a path (for example `docs/validation/process/play-pad-only-test-script.md`) or an issue number. Read the whole file. List every case id with its section, precondition and variant tags (for example `[W]` windowed, `[F]` full screen); report the count.

### 2. Reuse or convert the script

Look for `<name>.gui-test.json` beside the Markdown (format `gui-test/1`, ADR-0272).

- **It exists and covers every case id of the Markdown** → reuse it. Do not reconvert. Report case ids in the Markdown that have no step in the JSON, and steps with no case, and ask before continuing past a gap.
- **It does not exist (or has gaps)** → convert. One step per case: `id` = the case id, `precondition`, `action` (`pad.press`, `pad.release`, `pad.chord`, `key.*`, or `nav.goal` when the path is not what is under test), `wait`, an objective `check` (`ui.screen`, `ui.focused`, `ui.visible`, `ui.dialogs`) when one decides the case, the expected-screen text, the variant values, a severity, and a `role` (`under-test` by default; `setup`/`recovery` for steps that only reach a state). A case needing a physical pad, audio or a haptic reading is `manual`. Batches follow the Markdown sections, each with its own setup and teardown. Fixtures (ROM by No-Intro sha1, pack, settings profile) are declared, never inlined in steps.
  Ask the user about any case whose action or check you cannot decide; never guess an id the adapter does not advertise.
- Validate with `squad.gui_test.validate` against the adapter's capabilities and render the Markdown from the JSON with `gui_test_render.py`; the JSON is the source, never edit the rendered Markdown.

### 3. Quiz the user

Present: parent title, batches with case counts, which cases are `manual`, which are `nav.goal`/sweep (these are `pending` in CI, ADR-0271 Decision 6 A), the variants the run covers, and the labels. Ask whether the granularity is right (one sub-issue per case, or per batch), whether any case should be merged or dropped, and whether the run may start right after publishing. Iterate until approved.

### 4. Publish

Everything written for GitHub is **en-US** (repo CLAUDE.md), and no `Co-Authored-By` or "Generated with Claude Code" line.

1. Ensure the labels exist: `test-case`, `ready-for-agent`, one `wave:NN` (`gh label create ... --force`).
2. **Parent issue**: title `Test scenario: <name>`; body = purpose, script path, fixtures, variants, a case table, and a `## Blocked by` section (`None` unless the scenario depends on an unfinished feature). Labels: `ready-for-agent`, `wave:01`. It tracks the run; it is closed only by a person after reading the results.
3. **One sub-issue per case**, in script order, labels `test-case` and the batch's `wave:NN` (a case blocked by its batch's setup case gets one more than the setup). **Never `ready-for-agent`** on a case: a case is run, not built, and the autofrontier must not pick it as work. Body: case id, precondition, steps, expected result, variant tags, `manual` or `automated`, and `## Blocked by` naming the setup case it needs.
4. Link each as a native sub-issue of the parent (REST `POST /repos/<repo>/issues/<parent>/sub_issues` with `sub_issue_id` = the sub-issue's numeric `id`, not its number). Use the REST API; `gh pr create`/GraphQL exhausts the quota.
5. Add everything to the board with Status "To triage" via the repo's board helper. Record the case id → issue number map next to the script (`<name>.gui-test.issues.json`).

Do not close or edit any issue the scenario came from.

### 5. Run

Run the script on the squad's GUI test infrastructure through `scripts/gui_test_run_script.py <script.json> --adapter mesen-gui` (needs `MESENAI_ROOT`, `MESEN_GUI_BINARY` and `MESEN_CORE_LIB`; if the core cannot load, the run **fails**, never skips). It writes one result per step: `passed`, `failed (expected X, observed Y)`, `needs review`, `pending`.
If the wrapper does not exist yet, say so and stop after publishing: do not simulate a run.

### 6. Write results back

For every case, comment on its sub-issue with the verdict, the observed value, the variant and the evidence link (screenshots go through the privacy gate and reach an issue only via the evidence branch, never as a CI artifact). Then:

- `passed` → close the sub-issue.
- `failed` → leave open, comment the failure, and let the run file **one bug** through `scripts/report-bug.sh` with the failed steps (never a bug per step).
- `needs review` / `pending` / `manual` → leave open and list them on the parent; they never turn a build red.

Finish with one comment on the parent: counts per verdict, the run id, the script sha, the platform and the SHA of the build under test.

## Rules

- A run that executed no step is not a pass.
- Never put resident or private data in a script, issue or screenshot.
- Do not invent fixture hashes: `<no-intro sha1 ...>` placeholders stay placeholders and the run reports the missing fixture.
