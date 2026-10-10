# Jev in shadow mode on the subject runs

Run on 2026-09-30. Language: US English, Concise STE100. Data: `docs/research/jev-shadow-pairs.json` (one record per run).

## What was wired

`squad/jev.py` scores two things before each agent call and the engine records them as `diagnostic` events:

| Use | Input | Jev question |
|---|---|---|
| Skill choice (map 3.5) | the node's instructions, first entry only | a Choice over the skills in `~/.claude/skills` plus `none`, and a Noul "does a specialized skill apply" |
| Fetched-content screen (map 3.7) | repo text that enters the agent context: the issue file and `CLAUDE.md`, once per run | a Noul "does the text instruct an AI" |

The research check (map 3.3) has no step in the subject graph, so it is not wired.

Guards (all in `tests/test_jev_shadow.py`):

- One total deadline per call (20 s), retries included. A timeout, an error or a bad answer is `no_suggestion`. A shadow that hangs or raises is cut and recorded, and the step goes on.
- No gate, route or verdict reads a diagnostic. A test refuses the words `jev` and `diagnostic` in the verdict, gate, fact and artifact modules, and a run with Jev at p = 1.0 keeps its verdict.
- The key is read once and removed from the environment, so no agent tool inherits it. A test sends a key to a local server that echoes it in an error, then searches the run directory for it. `scripts/run_subject.py` repeats that search after every real run and exits 3 on a hit. It found none in 36 runs.
- The model of every node stays `claude-sonnet-5-5`. Jev is pinned to `jev-1.13.0`.

## Result

36 runs of 17 issues (02 to 13, 15 to 19, 22), two per issue. Every run was labeled by the held-out test (`ac_verify`): 25 passed, 11 failed. 144 Jev calls, none without a suggestion. Measured Claude spend 10.84 USD (OAuth login, not a billed amount). Jev spend is unverified.

n is 36 runs, and each issue gives 2 runs. The runs of one issue are not independent. This report makes no threshold claim. A gate stays unchanged.

Observations only:

- The spec-node skill choice has a low confidence (median 0.27 for pass, 0.28 for fail) and picks `none` in most runs. The actor-node choice picks `implement` in every run, with wanted 0.89 to 0.93. Scores of passing and failing runs overlap on every row of the summary table. These scores do not separate the outcomes in this set.
- The `CLAUDE.md` screen scores 0.87 to 0.90 in every run. That file is an instruction file for an agent, so a high score is correct for the question as asked. It is not a defect of the file. The score is constant, so it carries no information on the outcome.
- The issue-text screen scores 0.03 to 0.05 in 32 runs, 0.20 and 0.21 for issue 17, and 0.37 and 0.39 for issue 19. All of these runs passed. The two issues (17, 19) have text that Jev rates higher. This is a pattern in 4 runs. It is a hypothesis to read, not a setting.
- The failures carry no Jev signal that the runs lack: the 11 failing runs score inside the range of the 25 passing runs on every row.

## Not done

- No recall measure for the screen: the subject issues hold no planted instruction.
- Skill choice has no "correct skill" label here. The pairing is with the run outcome, so it says nothing about whether the pick was right.
- The roster comes from `~/.claude/skills`. It leaves out the skills under the subject's `.claude/skills`, if any.

## Pairs

Every row is one run. `not reached` means the run ended before `ac_verify`. No run was in that state.

Runs 36, labeled by the held-out test 36 (pass 25, fail 11). Jev calls 144, with no suggestion 0.

| Issue | Run | Held-out | Spec: pick | conf | wanted | Actor: pick | conf | wanted | Screen issue p | Screen CLAUDE.md p |
|---|---|---|---|---|---|---|---|---|---|---|
| 02 | 20260930-212230 | pass | none | 0.32 | 0.40 | implement | 0.65 | 0.92 | 0.04 | 0.88 |
| 02 | 20260930-212851 | pass | none | 0.32 | 0.39 | implement | 0.65 | 0.91 | 0.04 | 0.89 |
| 03 | 20260930-212404 | fail | none | 0.29 | 0.38 | implement | 0.59 | 0.91 | 0.03 | 0.90 |
| 03 | 20260930-212851 | fail | none | 0.30 | 0.38 | implement | 0.57 | 0.91 | 0.03 | 0.89 |
| 04 | 20260930-212404 | pass | code-review | 0.27 | 0.45 | implement | 0.65 | 0.91 | 0.05 | 0.88 |
| 04 | 20260930-212851 | pass | code-review | 0.25 | 0.39 | implement | 0.59 | 0.90 | 0.05 | 0.87 |
| 05 | 20260930-212404 | pass | none | 0.28 | 0.38 | implement | 0.58 | 0.93 | 0.04 | 0.89 |
| 05 | 20260930-212851 | pass | none | 0.30 | 0.39 | implement | 0.65 | 0.93 | 0.03 | 0.88 |
| 06 | 20260930-212404 | pass | code-review | 0.26 | 0.47 | implement | 0.63 | 0.92 | 0.03 | 0.89 |
| 06 | 20260930-212948 | fail | none | 0.25 | 0.49 | implement | 0.55 | 0.92 | 0.04 | 0.89 |
| 07 | 20260930-212512 | pass | none | 0.32 | 0.37 | implement | 0.63 | 0.89 | 0.03 | 0.89 |
| 07 | 20260930-212950 | pass | none | 0.31 | 0.35 | implement | 0.64 | 0.89 | 0.03 | 0.89 |
| 08 | 20260930-212513 | fail | none | 0.26 | 0.44 | implement | 0.68 | 0.93 | 0.03 | 0.88 |
| 08 | 20260930-212958 | pass | none | 0.24 | 0.44 | implement | 0.62 | 0.92 | 0.03 | 0.89 |
| 09 | 20260930-212522 | fail | none | 0.29 | 0.38 | implement | 0.55 | 0.92 | 0.04 | 0.89 |
| 09 | 20260930-213008 | fail | none | 0.38 | 0.38 | implement | 0.61 | 0.92 | 0.04 | 0.88 |
| 10 | 20260930-212523 | pass | none | 0.30 | 0.36 | implement | 0.62 | 0.91 | 0.03 | 0.89 |
| 10 | 20260930-213052 | pass | none | 0.31 | 0.37 | implement | 0.64 | 0.91 | 0.03 | 0.89 |
| 11 | 20260930-212611 | pass | none | 0.30 | 0.38 | implement | 0.74 | 0.89 | 0.04 | 0.89 |
| 11 | 20260930-213053 | pass | none | 0.26 | 0.35 | implement | 0.66 | 0.90 | 0.04 | 0.89 |
| 12 | 20260930-212623 | pass | none | 0.31 | 0.37 | implement | 0.65 | 0.91 | 0.04 | 0.89 |
| 12 | 20260930-213120 | fail | none | 0.33 | 0.39 | implement | 0.69 | 0.90 | 0.04 | 0.89 |
| 13 | 20260930-212624 | pass | none | 0.24 | 0.44 | implement | 0.63 | 0.91 | 0.03 | 0.90 |
| 13 | 20260930-213137 | fail | none | 0.23 | 0.40 | implement | 0.67 | 0.90 | 0.04 | 0.88 |
| 15 | 20260930-212625 | fail | none | 0.24 | 0.37 | implement | 0.65 | 0.90 | 0.05 | 0.89 |
| 15 | 20260930-213155 | pass | code-review | 0.25 | 0.38 | implement | 0.66 | 0.90 | 0.05 | 0.90 |
| 16 | 20260930-212704 | fail | none | 0.28 | 0.40 | implement | 0.65 | 0.90 | 0.04 | 0.89 |
| 16 | 20260930-213156 | pass | none | 0.27 | 0.41 | implement | 0.64 | 0.89 | 0.04 | 0.89 |
| 17 | 20260930-212705 | pass | none | 0.24 | 0.35 | implement | 0.62 | 0.89 | 0.21 | 0.89 |
| 17 | 20260930-213219 | pass | none | 0.26 | 0.33 | implement | 0.65 | 0.90 | 0.20 | 0.88 |
| 18 | 20260930-212726 | pass | none | 0.26 | 0.39 | implement | 0.56 | 0.91 | 0.04 | 0.90 |
| 18 | 20260930-213223 | pass | none | 0.25 | 0.39 | implement | 0.59 | 0.90 | 0.04 | 0.87 |
| 19 | 20260930-212728 | pass | none | 0.28 | 0.34 | implement | 0.61 | 0.90 | 0.37 | 0.90 |
| 19 | 20260930-213254 | pass | none | 0.25 | 0.36 | implement | 0.62 | 0.92 | 0.39 | 0.89 |
| 22 | 20260930-212802 | fail | none | 0.24 | 0.40 | implement | 0.54 | 0.92 | 0.04 | 0.89 |
| 22 | 20260930-213300 | pass | none | 0.33 | 0.37 | implement | 0.53 | 0.91 | 0.04 | 0.89 |

| Score | Held-out | n | min | median | max |
|---|---|---|---|---|---|
| spec skill_choice wanted | pass | 25 | 0.33 | 0.38 | 0.47 |
| spec skill_choice wanted | fail | 11 | 0.37 | 0.39 | 0.49 |
| spec skill_choice confidence | pass | 25 | 0.24 | 0.27 | 0.33 |
| spec skill_choice confidence | fail | 11 | 0.23 | 0.28 | 0.38 |
| actor skill_choice wanted | pass | 25 | 0.89 | 0.91 | 0.93 |
| actor skill_choice wanted | fail | 11 | 0.90 | 0.91 | 0.93 |
| actor skill_choice confidence | pass | 25 | 0.53 | 0.63 | 0.74 |
| actor skill_choice confidence | fail | 11 | 0.54 | 0.61 | 0.69 |
| spec content_screen:issue p | pass | 25 | 0.03 | 0.04 | 0.39 |
| spec content_screen:issue p | fail | 11 | 0.03 | 0.04 | 0.05 |
| spec content_screen:CLAUDE.md p | pass | 25 | 0.87 | 0.89 | 0.90 |
| spec content_screen:CLAUDE.md p | fail | 11 | 0.88 | 0.89 | 0.90 |
