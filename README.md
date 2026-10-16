<div align="center">

# agent-squad

**Give a coding agent a request. Get back a reviewed, tested change, with a record of every step.**

A self-adapting orchestration graph on the [Claude Agent SDK](https://docs.claude.com/en/docs/claude-code/sdk).
The workflow is data you can read, edit and review. Code enforces every limit.

On top of that engine this checkout carries **squad-goal**: an autonomous loop that drives a GitHub issue board to zero open issues, and the **hub**, the one page that shows the board, the runs and the models.

![Python](https://img.shields.io/badge/python-3.11%20%7C%203.13-blue)
![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-orange)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[Quick start](#quick-start) · [The loop](#the-squad-goal-loop) · [Commands](#commands) · [The hub](#the-hub) · [How delivery works](#how-delivery-works) · [How it works](#how-it-works) · [Docs](#docs)

</div>

---

## Quick start

```bash
claude plugin marketplace add sbihaiko/agent-squad
claude plugin install agent-squad@agent-squad --scope user
```

The repository is private. Ask the owner for access before you run the first command, and have `gh auth login` done.

**The repository moved.** The plugin used to live at `mmctech-ai/agent-squad`. That repository no longer exists, and a checkout that still names it cannot install or update. Replace it in one block:

```bash
claude plugin uninstall agent-squad@agent-squad
claude plugin marketplace remove agent-squad
claude plugin marketplace add sbihaiko/agent-squad
claude plugin install agent-squad@agent-squad --scope user
```

Check it: `claude plugin marketplace list` must show `agent-squad` with source `GitHub (sbihaiko/agent-squad)`. A source that still reads `mmctech-ai` means a step was skipped, and every later command fails on a repository that is not there.

Then, inside any project in Claude Code:

```text
/agent-squad:work add a retry with backoff to the uploader
```

A router agent reads the request and your `CLAUDE.md`, and asks for children. Code runs them, checks them and lands the ones that pass. The command prints the run folder and the verdict.

`/agent-squad:task <task>` runs one narrow task instead of a request, and `/agent-squad:editor` opens the workflow editor. The step-by-step page, with setup, config, resume and the run folder, is [docs/quickstart.md](docs/quickstart.md).

To install everything from scratch, including the third-party skills, follow [docs/install.md](docs/install.md).

> Requires Python 3.11 or later, [`uv`](https://docs.astral.sh/uv/), and a logged-in Claude Code.

## The squad-goal loop

The engine runs what you ask it to. The loop decides what to ask: it takes a set of GitHub issues and does not stop until each one is closed with evidence, its card is `Done`, and the gate is green on the base branch.

The skill is [`contrib/squad-goal/SKILL.md`](contrib/squad-goal/SKILL.md), installed as `/squad-goal`:

```text
/squad-goal 1817 1820 1822
```

It plans the ticket graph from the issues and their `## Blocked by` lists, dispatches the whole startable frontier as **one** squad run — a router, then one child per root cause — and then runs itself:

```text
issues ──► frontier (stack.sh) ──► squad run: router ──► children, one worktree each
              │                                              │
     board + issues updated live                     branches pushed, checks watched
              │                                              │
   Done ◄── close pass / branchland ◄── review chain ◄── branchfix rounds ◄── verdict
```

**The loop is small single-purpose scripts** under [`contrib/squad-goal/scripts/`](contrib/squad-goal/scripts/). `watch.sh` drives them on a cadence, and every one is covered by a shell suite (`test-*.sh`) beside it.

### Dispatch

- **`watchd.sh` / `watch.sh`** are the event loop: they tail the live runs, print one line per event (`RUN … verdict`, `BRANCHREVIEW`, `BRANCHLAND`, `STALL`, `DEAD RUN`), and call the steps below. `fastloop.sh` runs the fast passes on a ~30 s beat, so a dispatchable issue is picked up in seconds instead of the slow cycle's minutes.
- **`single.sh`** runs a command unless one under the same name is still alive; **`serial.sh`** runs a command holding a named lock, so two emulator-facing suites never collide.
- **`autofrontier.sh`** re-dispatches the frontier the moment a blocker closes. It reads the run's own envelope — the graph the engine will run — for the child slot count and the per-child spend, so a request never asks for more than the node allows. **`envelope-spend.sh`** prints that budget for every pass that spawns.
- **`stack.sh`** answers whether an issue can start (`READY`) or must wait for its blocker's branch to land (`WAIT`).
- **`mkreq.sh`** builds the child's request: the issue's requirements, its evidence rules and the preamble its route needs. Its **slice guard** refuses a request that names a file an unmerged branch already touches, before a child is spawned; a bundled request loses only the ticket that names the contested file.
- **`cluster.py`** groups the ready work by root cause, so one child owns one cause.

### Review, fix and land

- **`review.sh`** walks the review chain of [`models.json`](contrib/squad-goal/scripts/models.json). A model that fails for infrastructure reasons is skipped, and the answer must be the checklist shape [`revformat.py`](contrib/squad-goal/scripts/revformat.py) enforces. A fix round reviews the delta since the last finding set, not the whole diff again.
- **`branchreviewpass.sh`** reviews each pushed work branch tip that has no kept review, through **`review.sh`** and its chain. **`branchfixpass.sh`** dispatches one fix run for a tip whose review says `CHANGES`, and stops when a round does not change the findings (`SG_BRANCHFIX_STALLS`, ADR-0134).
- **`branchland.sh`** lands a tip whose review approved and whose checks are green in `dev`, through **`ffland.sh`** (rebase on the base, one fast-forward push, the branch deleted), then closes the issue and moves the card to Done. **`prepush.sh`** checks the pushed head before CI is asked.
- **`check-tdd-red.sh`** refuses a bug fix whose push carries no red test. **`check-gh-interface.sh`** refuses a loop script that reaches GitHub with a call site of its own: every script goes through the door beside it (`. ghcall.sh`), because a refused read looks like an answer and a refused write looks like success (ADR-0277). `acscan.sh` writes a read-only brief of the acceptance criteria an issue's citations conflict over; `acpr.sh` runs a run's acceptance command against the pushed work branch in a throwaway worktree.
- **`forkclose.sh`** lands the work of a repository that pushes no branch (see [How delivery works](#how-delivery-works)). **`fork-gate.sh`** is the gate that pass runs, and **`gate-suites.sh`** decides which suite set a diff needs (`full` boots the emulator, `offline`, `shell`, `python`).

### Keeping the loop honest

- **`health.sh`** reads the loop's own state and names what is wrong (`HEALTH frontier-idle`, a stranded dispatch, a base that has been red too long). **`health-install.sh`** installs a launchd agent that runs it every two minutes, outside the loop, because a loop cannot notice its own death. **`heartbeat.sh`** finds a hung test host.
- **`janitor.py`** releases issues a dead run stranded, expires stale dispatches, and reports the loop's own failures. It derives the card column from the base's own state: a run whose branch is still ahead of the base leaves the card at `Code Complete`, never `Done`, because the card in `Done` over an unlanded branch is a false delivery. **`ensure_labels.sh`** idempotently ensures the repository's labels; **`wtclean.sh`** removes worktrees a finished run left behind.
- **`forkrescue.py`** places a fork commit a child left on the wrong branch onto the branch the land reads (fast-forward only), and **`forkstrand.py`** reports a fork commit no landing path reads, so a card cannot sit at `Code Complete` while the land waits for a branch nobody wrote.
- **`models.json`** is the whole model policy: the coding, review and vision chains, the per-model time window, the per-model on/off switch, the retry budgets and the timeouts. Nothing is hard-coded in a script.

### Settings and the contract they carry

Per-repository settings live in a profile ([`scripts/profiles/example.sh`](contrib/squad-goal/scripts/profiles/example.sh)): `SG_REPOS`, `SG_PROJECT`, `SG_BASE`, `SG_FORK_DIR`, the test commands. Profiles are live-only — never shipped, never edited by the refresh.

A profile is not free-form: shipped code may require a setting, and `env.sh` declares that contract in one place (`SG_REQUIRED_SETTINGS`, `<setting>:<the setting that engages it>`). `refresh-from-fork.sh --check` reads it against every live profile and names the profile, the setting and the cause. A retired setting keeps working: `env.sh` maps `SG_TOOLING_REPO` onto `SG_REPOS` and warns once.

The loop's operating rules are in the skill, and they are why it can run unattended: GitHub is live state and is updated at once; a stall is recoverable, never a wait; a failing gate is a defect, never retuned; every event and decision lands on the issue it belongs to.

## Commands

| Command | What it does |
| --- | --- |
| `/squad-goal <issues>` | The autonomous loop: drives the listed issues to Done. |
| `/squad-hub` | Starts the hub and prints its URL. |
| `/agent-squad:work <request>` | Starts a run from a request. The router decides what children it needs. |
| `/agent-squad:task <task>` | Starts a run for one task. The router asks for the fewest children that finish it. |
| `/agent-squad:editor [id]` | Opens the workflow editor. No id opens the dynamic workflow. |
| `/agent-squad:dashboard [run]` | Opens the dashboard page for one run; the latest when empty. It never starts a run. |
| `/agent-squad:config <show\|set\|reset\|init\|check>` | Shows or changes the project settings file `docs/squad/defaults.yaml`. |
| `/agent-squad:skill-update` | After a skill update, checks that every borrowed skill still resolves. It never edits `~`. |

Every start command takes `--workflow <id>`. Without it, the run uses the **dynamic workflow**; with it, the run uses `docs/squad/workflows/<id>.graph.json`. The editor saves to the same folder.

The same commands exist as a CLI:

```bash
squad work "add a retry with backoff to the uploader"                  # same as `squad start`
squad task "fix the failing date parser test" --workflow five-phases  # same as `squad start --task`
squad editor
squad dashboard            # latest run
squad resume <run-id>      # continue after a crash or a wait
```

## The hub

One page for the whole loop — the board, every live run, the models, the weekly quota and the test panel:

```bash
contrib/squad-goal/scripts/hubctl.sh            # prints the URL, http://127.0.0.1:7700/
contrib/squad-goal/scripts/hubctl.sh --restart  # stops the hub that answers and starts it from the files on disk
```

One process serves every repository, on one path each. The board columns are read from the GitHub Project by REST: status, priority, size, blockers, the open and closed pull requests. Every live run embeds its own dashboard, started headless so no tab opens for it.

The models panel is the model policy you can edit: the three chains with rank, effort, time window and an on/off switch per row, the quota cache (`codex-reset.py` reads the same cache), and the **router's own queue**. That queue is the dispatch envelope of the run graph — the models the router may ask a child for, in order — so the panel reads it from the graph, writes it there, and restamps the record the graph ships beside. A reader who cuts a model from a chain can no longer miss that the router may still ask for it.

The footer of the page is one line of readings: the last landing, the open child worktrees as `<cleanable>/<total>` (`wt 10/21`), the live occupancy against the ceilings, and the subscription tokens left. A reading is never a rule: no path ends, parks or blocks a run because of a number on that line. The worktree block is also a button: one press runs the loop's own cleanup pass, so the number is freed by the same rules that counted it.

The page is one Python file — `http.server` from the standard library, and the HTML, CSS and JS inline — with no build step and no framework. Its state lives in `<repo>/runs/.hub` and ignores itself. `verify-view-domain-firewall.sh` guards the rule that a page only reaches what its view may see.

## How delivery works

**The address of an issue is its route.** A bare number resolves against the repositories the loop operates — the profile's own repository first.

An issue carries a route, and the route decides how its work is delivered:

| Route | Deliverable | Closes the issue |
| --- | --- | --- |
| Default | A **work branch** (`fix/<n>…` or `gui/<n>…`) pushed to the remote, landed in `dev` by a fast-forward push | The landing pass, after the review approved its tip and its checks are green |
| `squad-only` label, or a `Fork-only work` line in the body | A **local commit** on `gui/<n>`, merged by `forkclose.sh`, pushed nowhere | The close pass, after its gate is green |

The work branch is the unit of work: no loop script opens, reads or merges a pull request (#361, ADR-0145). A branch names its issue in its own name (`fix/<n>…`). `branchreviewpass.sh` reviews each tip once, `branchfixpass.sh` dispatches one fix run for a tip whose review says `CHANGES`, and `branchland.sh` lands the tip whose review approved and whose checks are green: `ffland.sh` rebases it in a throwaway clone and pushes the tip straight to the landing branch. The landing pass then closes the issue through the API and moves the card to Done.

The branch route is the parallelism: several branches are in flight at once, while one machine runs one full gate at a time. Landing closes the issue itself, so no card can claim a delivery the base never took.

What the loop learned by running is written in [`docs/learnings/`](docs/learnings/README.md) — one file per episode, with the measurements, the traps and a checklist. Prefer a file there over a comment buried in an issue.

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
| **agent** | One model session with a declared model, tools and budget. |
| **gate** | A deterministic check. No model call. |
| **loop** | Repeats its children up to a maximum. The maximum only shrinks at run time. |
| **each** | Runs its children once per item, in parallel or in sequence. |
| **action** | Deterministic code with a declared side effect, such as a commit or a merge. |
| **wait** | Holds the run until a person acts. The process exits with code 10 and resumes later. |
| **dispatch** | Lets an agent request children at run time, inside an **envelope** the author fixes. |

An **authored flow** has no dispatch node and you wire every step. A **dynamic flow** has one, and a router agent chooses the children. Both ship here:

- [`docs/design/five-phases.graph.json`](docs/design/five-phases.graph.json): scout, spec, decompose, execute, verify, with actor-critic loops and gates.
- [`docs/design/router.graph.json`](docs/design/router.graph.json): the dynamic workflow, and the default.

A run ends with one verdict, and the exit code follows from it:

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

- Drag, zoom and edit nodes. Validation errors show on the node, and a save is never blocked.
- Replay a finished run with a cursor; a live run stays pinned to the last event.
- Pause or resume a launched run. This is the page's only write.
- See which skills a run could load, by hash.

The page is one HTML file with no build step. A loopback server feeds it and exits when idle. Under the loop it is reached through [the hub](#the-hub), which embeds it.

## Subject runs

The subject is a billing engine with 24 issues (`bench/subject/`):

```bash
uv run scripts/run_subject.py 7                    # one issue, the five-phase graph
uv run scripts/run_router.py 1,2,3,...,24          # a set, with the dynamic router graph
```

[`config/subject.yaml`](config/subject.yaml) names the model of every node. It ships with `deepseek-flash`, served by the local DeepSeek proxy at `127.0.0.1:8016` (`ANTHROPIC_BASE_URL` must point at it; start the proxy first).

The CLI prices a model it does not know at about 14 times its real rate, so a spend cap sized for Anthropic fires early. The file sets `router.spend_cap: "off"`: no node and no run carries a spend cap. A turn or stall cap still stops a child that keeps failing.

## Develop

```bash
uv sync --extra dev --extra sdk
python3 -m pytest -n auto       # parallel
python3 -m pytest -p no:xdist   # serial: both modes must pass
ruff check squad tests && mypy squad
```

The loop's own scripts are covered by shell suites that run every `test-*.sh` beside them:

```bash
contrib/squad-goal/scripts/shell-suites.sh    # the loop's suites
```

This checkout is a **fork**: the engine tracks upstream (0.2.0, kept as `local/base-0.2.0`), and everything built on top of it is committed on `main`. The **live skill** (`~/.claude/skills/squad-goal/`) follows `contrib/squad-goal` on `main` through `publish-live.sh`, and the loop runs the copy.

```bash
contrib/squad-goal/scripts/publish-live.sh        # fork -> live skill, an explicit file list (--check compares, changes nothing)
contrib/squad-goal/scripts/live-guard.sh          # the watch cycle's step: reads the live skill's drift, publishes it, names what it read
contrib/squad-goal/scripts/export-agent-squad.sh  # the ZIP for upstream
```

## Principles

- **A failing gate is a defect.** You investigate it. You never retune the threshold to pass.
- **The record is the truth.** A transcript is not run truth; the verdict comes from the record.
- **Skills are pinned.** A run records the hash of every skill it can load, and a resume refuses when one differs.
- **Decisions are written down.** [The ADRs](docs/adr/README.md) say why the code looks the way it does, and the [CHANGELOG](CHANGELOG.md) says what each release changed and why. An ADR is a guide, not a lock.
- **Distance is the cost.** A branch that waits is a branch that conflicts: measure how far a candidate has drifted before blaming its tests.

## Docs

| Read | For |
| --- | --- |
| [`docs/install.md`](docs/install.md) | Install the engine, the skills and the loop, step by step, with a check per step. |
| [`docs/quickstart.md`](docs/quickstart.md) | Run agent-squad on your own repository. |
| [`contrib/squad-goal/SKILL.md`](contrib/squad-goal/SKILL.md) | The autonomous loop: its invariants, the dispatch, the recovery rules. |
| [`docs/learnings/`](docs/learnings/README.md) | What running the loop taught: measurements, traps, checklists. |
| [`CHANGELOG.md`](CHANGELOG.md) | What every release of this fork changed, with the issue that asked for it. |
| [`GLOSSARY.md`](GLOSSARY.md) | The words this project uses, and the words it avoids. |
| [`docs/adr/`](docs/adr/README.md) | Every durable decision of the tooling, with its guard. |
| [`docs/design/`](docs/design/README.md) | The graph schema, the fixtures and the checkers. |
| [`docs/skill-workflow-map.md`](docs/skill-workflow-map.md) | The skill flow from request to ship. |
| [`CLAUDE.md`](CLAUDE.md) | The rules for agents that work in this repo. |

## License

[MIT](LICENSE). Copyright (c) 2026 Rafael Aguilher da Costa.

## Status

agent-squad is the next version of `dev_squad`, rebuilt on the Claude Agent SDK. This checkout is the fork at **0.2.31** on `main` (upstream 0.2.0 kept as `local/base-0.2.0`), and it changes fast: the loop, the hub and the fork workflow are built here, released release by release, and sent upstream as a ZIP.
