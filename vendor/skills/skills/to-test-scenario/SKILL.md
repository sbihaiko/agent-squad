---
name: to-test-scenario
description: Turn a manual GUI test scenario (a Markdown script of cases, for example a pad-only run of the emulator) into a gui-test/1 script, one parent GitHub issue with one sub-issue per case, and a run on the squad's GUI test infrastructure whose results are written back to the sub-issues. Use only when the user asks for it.
disable-model-invocation: true
---

# To Test Scenario

Take a **test scenario** (a Markdown script a person follows, whose cases each have an id, a precondition, steps and an expected result) and make it executable: a `gui-test/1` script, a tree of issues (scenario, areas, one record per case), and a run whose verdicts land as one run issue per area (ADR-0275).

Sibling of `/to-tickets`: that skill slices **work** into tracer-bullet tickets; this one slices a **scenario** into cases that are **run**, not built. Reuse its publishing mechanics (issue template, `## Blocked by`, `wave:NN`, REST sub-issues), never its slicing rules.

Only a person invokes this skill (`disable-model-invocation`): it publishes issues. Never run it on your own initiative.

## Two halves

- **Judgment** — reading the Markdown, converting a case into steps, deciding whether an action or a check is decidable — is yours, here, in this file.
- **Mechanics** — coverage, the plan, publishing, idempotence, starting a run, writing results back — live in one deterministic module, `squad.to_test_scenario` (agent-squad), with three commands: `publish`, `start-run`, `write-back`. Call them; never reimplement them by hand, and never publish an issue the module did not plan.

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
python3 -c "from squad.to_test_scenario import build_plan, read_scenario, read_script; import json; \
print(json.dumps(build_plan(read_scenario('<scenario>.md'), read_script('<name>.gui-test.json'), granularity='case'), indent=2))"
```

A script whose steps carry no content hash is stale in every case: the plan stops on it until `publish --stamp` records the hashes.

Show: parent title, the areas (see the layout in step 4) with their case counts, batches with case counts, the `manual` cases, the `goals` cases, the variants the run covers, and the labels. Ask whether the granularity is right (**one record per case**, the default, or **per batch** when a large scenario would flood the board), whether an area name reads badly and needs an `area` field, whether any case should be merged or dropped, and whether the run may start right after publishing. Iterate until approved.

### 4. Publish

Publish **the plan the user approved**, through the module's `publish` command, with the real REST runner. Everything written for GitHub is **en-US**, with no `Co-Authored-By` and no tool-attribution line. Run it from the agent-squad fork checkout:

```bash
python3 -m squad.to_test_scenario publish <scenario>.md <name>.gui-test.json \
  --repo <owner/name> --board users/<owner>/projects/<n> [--granularity case|batch] [--stamp] [--dry-run]
```

**The layout.** The scenario is a tree, four levels deep, so no parent passes GitHub's 100 sub-issues:

```
Scenario issue            `Test-Scenario`; purpose, Areas table, dashboard; the only issue on the board
└─ Area issue             `Test-Area`; objective and a Cases table
   ├─ 📋 Cases            container (`Test-Group`); one sub-issue per case record
   └─ 📅 Runs YYYY-MM     container (`Test-Group`); one sub-issue per run of the area that month (UTC)
      └─ Run issue        `Test-Run`; totals, changes, one emoji row per case
```

Every issue carries exactly one **`Test-<Kind>`** label, one per node kind: `Test-Scenario` (the scenario),
`Test-Area` (an area), `Test-Group` (a container), `Test-Case` (a case record) and `Test-Run` (a run issue).
The older lowercase labels (`testcase`, `testarea`, `testrun`) are replaced on sight, so an issue never keeps both.

- **The area rule.** A batch goes to the area named by its optional `area` field; without one, the batch id up to its first `-` (`pause-pack-multi` → `pause`). A batch may carry an `objective`, which becomes the area's objective (the first one found wins); an area without one reads "(no objective stated)". Add `area` / `objective` to the script where a prefix reads poorly.
- **Case records.** One issue per case, labels `Test-Case` and `wave:NN`, titled `<verdict emoji> <case title>` starting at ⚪. A container over 100 cases splits by batch; one batch over 100 cases is refused before anything is created, and so is an area body over 60,000 characters.
- Links are made top-down (scenario → area → container → record); a record is never linked to the scenario. Before it creates a container, the module adopts one with the same title already under that parent, so a lost map never makes two.

**The verdict emoji** are one vocabulary for case rows, record titles, area titles and run titles (container titles never carry one):

| Emoji | Verdict | Results-file verdict |
|---|---|---|
| ✅ | passed | `passed` |
| ❌ | failed | `failed` |
| 🟡 | needs review | `needs review` |
| 👤 | manual: a person must walk it | `pending` |
| ⚪ | not run | `skipped`, `not executed` |

An aggregate (run, area, scenario) takes the worst verdict among its cases: ❌ > 🟡 > 👤 > ⚪ > ✅. A scenario of mostly `manual` cases reads 👤 until it is walked.

**`--dry-run`** prints the counts to create, link, move and update, asks GitHub nothing and writes nothing. Run it before the first real publish and before any migration, and show the counts to the user.

**Migrating a flat scenario.** A map without `"layout": "areas"` whose parent holds the case records directly is a flat scenario (the original one-sub-issue-per-case layout). `publish` migrates it in one resumable pass: create the areas and their 📋 containers; re-parent each known record into its container (a link change only: no issue is closed or deleted and no comment moves); create the records the old cap left out; write `"layout": "areas"` last, so an interrupted migration is still flat and resumes. A case whose area changed is re-parented the same way; an area the plan no longer has is listed in `removed_areas` and left open.

- REST through `gh api`, never GraphQL: a scenario creates hundreds of issues and would spend the GraphQL quota. **Only the parent joins the board** (`--board`, Status "To triage"); an area, a container, a record and a run get no card.
- `--stamp` records each case's content hash on its step in the script file. A script that predates hashes is stale in every case and the command stops without creating anything until it is stamped; stamp only a script you reviewed, and commit the change.
- The parent keeps `ready-for-agent` and `wave:01`. No area, container, record or run issue carries `ready-for-agent`: a case is run, not built, and the autofrontier must not pick it up as work.
- Sub-issues are linked natively with the sub-issue's numeric `id`, never its number.
- The map (`<name>.gui-test.issues.json`: the layout, the parent, per area its number, containers and runs, and per case its issue number, hash, area and container) is written beside the script after every issue or link. Publishing again **updates** what the map knows and creates nothing twice: the text after a title's emoji is rewritten and the emoji stays, the dashboard blocks are kept, and a record's open/closed state is never touched. A case the script no longer has is listed in `removed` and left open — only a person closes a case.

Do not close or edit any issue the scenario came from.

### 5. Start the run, and return

A run takes minutes, so this skill **never waits on it**. Start it through the run command and report; the results file is the run's own output.

```bash
python3 -m squad.to_test_scenario start-run <name>.gui-test.json [--batch <id>] [--out-dir <dir>] \
  --repo <owner/name> --board users/<owner>/projects/<n> --bug-helper <MesenAI>/scripts/report-bug.sh
```

It launches `scripts/gui_test_run_script.py` (the `gui-test/1` runner of the squad fork, MesenAI #1222) detached, and prints at once: the **results file** it will write, the pid, the log, and the **write-back command** to run afterwards. Show the user those three and stop. The run needs `MESENAI_ROOT` (a MesenAI checkout holding `scripts/gui_test/mesen_gui_adapter.py`), `MESEN_GUI_BINARY` (the built launcher of a build carrying the test hook) and `MESEN_CORE_LIB` (the `MesenCore` library of that build); a missing variable or a core that cannot load is a **failure, never a skip**. The runner validates the script against the adapter's capabilities before launching anything and does not file bugs: the write-back does (step 6).

**If the run command is not there**, `start-run` starts nothing and says `run command unavailable`: report that the scenario is published and stop. Never simulate a run, never hand a verdict to a case.

### 6. Write results back (a separate step)

Once the results file exists — later, by the user or by CI, never by this skill waiting — run the command `start-run` printed:

```bash
python3 -m squad.to_test_scenario write-back <results file> --map <name>.gui-test.issues.json \
  --repo <owner/name> --board users/<owner>/projects/<n> --bug-helper <MesenAI>/scripts/report-bug.sh
```

It takes a lock beside the map (`<map>.lock`) and fails fast if another write-back holds it. On a map in the areas layout it writes:

- **One run issue per area the run touched**, closed as completed, in that area's `📅 Runs YYYY-MM` container: the totals (`✅ n · ❌ n · 🟡 n · 👤 n · ⚪ n`), the changes against the area's newest earlier run containing the case, and one emoji row per case with its record, evidence and bug links. A run is never edited afterwards; a re-run is a new run issue. A partial `--batch` run lists only its cases, and a case the run did not reach is absent, not ⚪.
- **The dashboards**, rewritten between their markers from the run blocks: the scenario's latest run, Runs table, Flaky list and open bugs, and each area's Trend table. Nothing outside the markers is touched.
- **Record state**: ✅ sets the title emoji and closes the record; ❌ and 🟡 set it and reopen; 👤 and ⚪ never touch a record, so a person's manual closure survives. Records get no per-run comment.
- **Bugs: the write-back owns bug filing, one bug per failing case.** A ❌ case files a bug only when it has none open (found through the record's `Bug: #N` line, else a search of open `bug` issues for the exact title `gui-test ❌ <script> · <case id>`); a later failing run comments on that bug. Priority follows the step's `severity` (`critical` → P0, `major` → P1, otherwise P2). The first later run in which the case passes closes the bug with a comment naming the run, and a case that fails again files a new bug that links the closed one.
- A step whose batch has no area in the map is reported in `unknown_cases` and not written. A malformed file, an empty run and a missing core write nothing.

A flat map (not yet migrated) keeps the older behaviour: a comment per case on its sub-issue, and `passed` closes it. Migrate it with `publish` first.

Screenshots reach an issue only through the **privacy gate** and the evidence branch — the skill never uploads a capture itself, and no resident or personal data goes into a script, an issue or a screenshot.

The run never closes the scenario: **a person closes it** after reading the results.

### 7. Commit the map

The map is the cache the next publish and write-back start from. **After every write-back, commit `<name>.gui-test.issues.json` beside the script** (and the script, if `--stamp` changed it). Losing the map costs re-adoption calls, never duplicate issues, but a committed map keeps those calls off the next run.

## Rules

- Never wait on a run: start it, name the results file and the write-back command, and return.
- Commit the map after a write-back.
- A run that executed no step is not a pass, and a missing core fails rather than skips.
- Never fabricate a result, a hash or a case id.
- Nothing is published before the user approves the plan, and nothing is published on a coverage gap.
- The script format, the target adapter and the runner are not this skill's to change (`gui-test/1`, ADR-0272).

## This repository writes no wave label (#1965, #2132)

This rule supersedes every wave sentence above. A case record carries `Test-Case` and no wave label. The scenario
parent carries `ready-for-agent` and no wave label. No area, container, record or run issue carries one either.

The module writes no wave label, so no label of that shape reaches GitHub. The order of a case comes from its
`## Blocked by` edge, and the sub-issue link is native.
