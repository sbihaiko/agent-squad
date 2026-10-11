<div align="center">

# agent-squad

**Give a coding agent a request. Get back a reviewed, tested change, with a record of every step.**

A self-adapting orchestration graph on the [Claude Agent SDK](https://docs.claude.com/en/docs/claude-code/sdk).
The workflow is data you can read, edit, and review. Code enforces every limit.

On top of that engine this checkout carries **squad-goal**: an autonomous loop that drives a GitHub issue board to zero open issues, and the **hub**, the one page that shows the board, the runs and the models.

![Python](https://img.shields.io/badge/python-3.11%20%7C%203.13-blue)
![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-orange)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[Quick start](#quick-start) · [The squad-goal loop](#the-squad-goal-loop) · [Commands](#commands) · [The hub](#the-hub) · [The fork workflow](#the-fork-workflow) · [How it works](#how-it-works) · [Docs](#docs)

</div>

---

## Why agent-squad

One long agent session drifts. It forgets the plan, skips the tests, and tells you it is done. You cannot review what you cannot see.

agent-squad splits the work across short, focused sessions and puts a graph around them.

- **The workflow is a file.** A graph document lists the nodes and the order. You open it in an editor, change it, and commit it.
- **A model asks, code decides.** A router agent requests child agents. Code checks each request against limits the author fixed. A request over any limit is rejected whole.
- **A model never merges.** A child that passes its checks lands in your repo in request order. A conflict fails that child and nothing else.
- **Gates are deterministic.** A gate runs code, not a model. It stops the run or repeats a loop. Nothing tunes a gate to make it pass.
- **Every run leaves a record.** One append-only file holds every event. You replay a run, resume it after a crash, or hand it to someone else.
- **A run has caps.** Spend and wall-clock time are limited per node and per run. A call also stops when it keeps failing at one step with the same failure; total turns are not capped. Reaching a deadline is its own outcome, not a failure.

## Quick start

You need [Claude Code](https://claude.com/claude-code), [`uv`](https://docs.astral.sh/uv/) and `git`. Nothing else: the first `/agent-squad:work` or `/agent-squad:task` lets `uv` fetch Python 3.11 or later and the `claude-agent-sdk` (the `sdk` extra), which carries its own Claude Code binary. Node is not required.

Install the plugin once:

```bash
claude plugin marketplace add mmctech-ai/agent-squad
claude plugin install agent-squad@agent-squad --scope user
```

Then, inside any project in Claude Code:

```text
/agent-squad:work add a retry with backoff to the uploader
```

A router agent reads the request and your `CLAUDE.md`. It asks for children. Code runs them, checks them, and lands the ones that pass. The command prints the run folder and the verdict.

Open the workflow editor:

```text
/agent-squad:editor
```

Run one narrow task instead of a request:

```text
/agent-squad:task fix the failing date parser test
```

The step-by-step page, with setup, config, resume and the run folder, is [docs/quickstart.md](docs/quickstart.md).

> Requires Python 3.11 or later, [`uv`](https://docs.astral.sh/uv/), and a logged-in Claude Code.

## The squad-goal loop

The engine above runs what you ask it to. The loop decides what to ask: it takes a set of GitHub issues and does not stop until each one is closed with evidence, its card is `Done`, and the end-to-end command is green on the final base branch.

The skill is [`contrib/squad-goal/SKILL.md`](contrib/squad-goal/SKILL.md), installed as `/squad-goal`:

```text
/squad-goal 1817 1820 1822
```

It plans the ticket graph from the issues and their `## Blocked by` lists, dispatches the whole startable frontier as one squad run (a router, then one child per root cause), and then runs itself:

```text
issues ──► frontier (stack.sh) ──► squad run: router ──► children, one worktree each
              │                                              │
     board + issues updated live                     PRs opened, CI watched
              │                                              │
   Done ◄── forkclose / autopilot ◄── review chain ◄── autofix rounds ◄── verdict
```

- **`watchd.sh` / `watch.sh`** are the event loop: they tail the live runs, print one line per event (`RUN … verdict`, `NEW PR`, `REVIEW`, `STALL`, `DEAD RUN`), and call the steps below on a cadence. `watchtail.sh` keeps the loop alive across turns; `launch.py` starts a run and refuses to die with the session.
- **`autofrontier.sh`** re-dispatches the frontier the moment a blocker closes; **`stack.sh`** answers whether an issue can start, must stack on exactly one open parent PR, or must wait.
- **`review.sh`** walks the review chain of [`models.json`](contrib/squad-goal/scripts/models.json) — a model that fails for infrastructure reasons is skipped, and the answer must be the checklist shape [`revformat.py`](contrib/squad-goal/scripts/revformat.py) enforces. Fix rounds review the delta over the previous finding set, not the whole diff again.
- **`autopilot.sh`** owns the PR: it re-runs CI, charges fix rounds to `autofix.sh` (capped; past the cap a panel decides), merges when the review approves and CI is green, and keeps the card in step (`Testing` → `Done`).
- **`autobug.sh`** fixes bugs and debt without being asked: every open `bug` issue with no PR naming it, clustered by root cause, one child and one PR per cluster.
- **`forkclose.sh`** lands tooling work that has no PR (see [The fork workflow](#the-fork-workflow)); **`janitor.py`** releases issues a dead run stranded and reports the loop's own failures; **`reconcile.py`** derives the board from GitHub facts; **`wtclean.sh`** removes worktrees a finished run left behind.
- **`models.json`** is the whole model policy: the coding chain, the review chain, the vision chain, per-model time windows, per-model on/off switches, retry budgets and timeouts. Nothing is hard-coded in a script. Per-repository settings live in the profiles ([`scripts/profiles/`](contrib/squad-goal/scripts/profiles/example.sh)): `SG_REPO`, `SG_PROJECT`, `SG_BASE`, `SG_FORK_DIR`, `SG_FORK_BASE`, the test commands.

The loop's operating rules are in the skill, and they are the reason it can run unattended: GitHub is live state and is updated immediately; a stall is recoverable, never a wait; a failing gate is a defect, never retuned; every event and decision lands on the issue it belongs to.

## Commands

| Command | What it does |
| --- | --- |
| `/squad-goal <issues>` | The autonomous loop: drives the listed issues to Done. |
| `/agent-squad:work <request>` | Starts a run from a request. The router decides what children it needs. |
| `/agent-squad:task <task>` | Starts a run for one task. The router asks for the fewest children that finish it. |
| `/agent-squad:dashboard [run]` | Opens the dashboard page for one run; the latest when empty. It never starts a run. |
| `/agent-squad:skill-update` | After a skill update, checks that every borrowed skill still resolves. It never edits `~`. |
| `/agent-squad:config <show\|set\|reset\|init\|check>` | Shows or changes the project settings file `docs/squad/defaults.yaml`. |
| `/agent-squad:editor [id]` | Opens the workflow editor on a workflow. No id opens the dynamic workflow. |

Every start command takes `--workflow <id>`. Without it, the run uses the **dynamic workflow**. With it, the run uses `docs/squad/workflows/<id>.graph.json`. The editor saves to the same folder.

The same commands exist as a CLI:

```bash
squad work "add a retry with backoff to the uploader"          # same as `squad start`
squad task "fix the failing date parser test" --workflow five-phases   # same as `squad start --task`
squad editor
squad dashboard            # latest run
squad resume <run-id>      # continue after a crash or a wait
```

## The hub

One page for the whole loop — the board, every live run, the models and the weekly quota:

```bash
contrib/squad-goal/scripts/hubctl.sh          # prints the URL, http://127.0.0.1:7700/
```

- The board columns are read from the GitHub Project by REST: status, priority, size, blockers, the open and closed PRs.
- Every live run embeds its own dashboard; the run's dashboard server is started headless, so no tab is opened for it.
- The models panel is the same data `models.json` holds: chains, per-model windows, switches, the quota cache.
- It is one Python file — `http.server` from the standard library and the HTML/CSS/JS inline — with no build step and no framework. Its state lives in `<repo>/runs/.hub` and ignores itself.

## The fork workflow

This checkout is a **fork**: the engine tracks upstream (0.2.0 installed as `local/base-0.2.0`), and everything built on top of it is committed locally on `local/next`. Two kinds of work live side by side, and the loop keeps them apart:

- **Product work** goes to the subject repository: a branch, a PR whose body carries one `Refs #N` line per issue, CI, the review chain, the merge, the card to `Done`.
- **Work that exists only in the fork** (the loop's own scripts, the hub, this README) is **fork-only**: the issue carries the `Fork-only work` marker or the `fork-only` label, the child commits on `gui/<N>` in `local/next` and pushes nothing, and `forkclose.sh` merges it, runs the fork suite, comments the evidence, closes the issue and moves the card to `Done`. There is no PR because there is nothing to open one against.

Keeping the running loop and the fork in step:

```bash
contrib/squad-goal/scripts/sync-to-fork.sh        # live skill -> fork, refuses to overwrite newer work
contrib/squad-goal/scripts/refresh-from-fork.sh   # fork -> live skill (--check compares, changes nothing)
contrib/squad-goal/scripts/export-agent-squad.sh  # the ZIP for upstream
```

## How it works

```text
request ──► router agent ──► spawn requests ──► envelope check ──► child agents
                                                     │                  │
                                              rejected whole       worktree each
                                                                        │
              verdict ◄── final gate ◄── landing (code merges) ◄── scope + security gates
```

The graph has seven kinds of node:

| Node | Role |
| --- | --- |
| **agent** | One model session with a declared model, tools, and budget. |
| **gate** | A deterministic check. No model call. |
| **loop** | Repeats its children up to a maximum. The maximum only shrinks at run time. |
| **each** | Runs its children once per item, in parallel or in sequence. |
| **action** | Deterministic code with a declared side effect, such as a commit or a merge. |
| **wait** | Holds the run until a person acts. The process exits with code 10 and resumes later. |
| **dispatch** | Lets an agent request children at run time, inside an **envelope** the author fixes. |

An **authored flow** has no dispatch node, and you wire every step. A **dynamic flow** has one, and a router agent chooses the children. Both ship in this repo:

- [`docs/design/five-phases.graph.json`](docs/design/five-phases.graph.json): scout, spec, decompose, execute, verify, with actor-critic loops and gates.
- [`docs/design/router.graph.json`](docs/design/router.graph.json): the dynamic workflow, and the default.

A run ends with one verdict. The exit code follows from it.

| Verdict | Exit code |
| --- | --- |
| completed | 0 |
| failed | 1 |
| needs-human | 10 |
| conflict | 20 |
| stopped | 30 |

## The run dashboard

One page per run. It loads the graph, follows the record live, and lets you replay any run event by event.

```bash
squad dashboard
```

The replay scrubs a finished run from its first event to its last.

- Drag, zoom, and edit nodes. Validation errors show on the node, and a save is never blocked.
- Replay a run with a cursor. A live run stays pinned to the last event.
- Pause or resume a launched run. This is the page's only write.
- See which skills a run could load, by hash.

The page is one HTML file with no build step. A loopback server feeds it, and the server exits when idle. Under the loop it is reached through [the hub](#the-hub), which embeds it.

## Subject runs

The subject is a billing engine with 24 issues (`bench/subject/`). Two scripts run it:

```bash
uv run scripts/run_subject.py 7                    # one issue, the five-phase graph
uv run scripts/run_router.py 1,2,3,...,24          # a set, with the dynamic router graph
```

[`config/subject.yaml`](config/subject.yaml) names the model of every node. It ships with `deepseek-flash`, served by the local DeepSeek proxy at `127.0.0.1:8016` (`ANTHROPIC_BASE_URL` must point at it; start the proxy first).

The CLI prices a model it does not know at about 14 times its real rate, so a spend cap sized for Anthropic fires early. The file sets `router.spend_cap: "off"`: no node and no run carries a spend cap, and the router asks for children without a `spend`. Set it to `"on"` to restore the caps. A turn or stall cap still stops a child that keeps failing.

## Principles

- **A failing gate is a defect.** You investigate it. You never retune the threshold to pass.
- **The record is the truth.** A transcript is not run truth. The verdict comes from the record.
- **Skills are pinned.** A run records the hash of every skill it can load. A resume refuses when one differs.
- **Decisions are written down.** [The ADRs](docs/adr/README.md) say why the code looks the way it does, and the [CHANGELOG](CHANGELOG.md) says what each release of the fork changed and why. An ADR is a guide, not a lock.

## Develop

```bash
uv sync --extra dev --extra sdk
python3 -m pytest -n auto       # parallel
python3 -m pytest -p no:xdist   # serial: both modes must pass
ruff check squad tests && mypy squad
```

The fork's own scripts are covered by shell suites that run every `test-*.sh` next to them:

```bash
contrib/squad-goal/scripts/shell-suites.sh                      # the loop's suites (~160 s)
uv run --extra dev pytest tests/test_gui_test_*.py -p no:xdist  # the emulator-facing suites
```

CI runs ruff, mypy, and the suite on Python 3.11 and 3.13 for every push and every pull request ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

## Docs

| Read | For |
| --- | --- |
| [`contrib/squad-goal/SKILL.md`](contrib/squad-goal/SKILL.md) | The autonomous loop: its invariants, the dispatch, the recovery rules. |
| [`CHANGELOG.md`](CHANGELOG.md) | What every release of this fork changed, with the issue that asked for it. |
| [`GLOSSARY.md`](GLOSSARY.md) | The words this project uses, and the words it avoids. |
| [`docs/adr/`](docs/adr/README.md) | Every durable decision, with its guard. |
| [`docs/design/`](docs/design/README.md) | The graph schema, the fixtures, and the checkers. |
| [`docs/skill-workflow-map.md`](docs/skill-workflow-map.md) | The skill flow from request to ship. |
| [`CLAUDE.md`](CLAUDE.md) | The rules for agents that work in this repo. |

## License

[MIT](LICENSE). Copyright (c) 2026 Rafael Aguilher da Costa.

## Status

agent-squad is the next version of `dev_squad`, rebuilt on the Claude Agent SDK. This checkout is the fork at **0.2.30** on `local/next` (upstream 0.2.0 as `local/base-0.2.0`), and it changes fast: the loop, the hub and the fork workflow are built here, released release by release, and sent upstream as a ZIP.
