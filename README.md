<div align="center">

# agent-squad

**Give a coding agent a request. Get back a reviewed, tested change, with a record of every step.**

A self-adapting orchestration graph on the [Claude Agent SDK](https://docs.claude.com/en/docs/claude-code/sdk).
The workflow is data you can read, edit, and review. Code enforces every limit.

On top of that engine this checkout carries **squad-goal**: an autonomous loop that drives a GitHub issue board to zero open issues, and the **hub**, the one page that shows the board, the runs and the models.

![Python](https://img.shields.io/badge/python-3.11%20%7C%203.13-blue)
![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-orange)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[Quick start](#quick-start) · [The squad-goal loop](#the-squad-goal-loop) · [Commands](#commands) · [The hub](#the-hub) · [Where tickets live](#where-tickets-and-learnings-live) · [The fork workflow](#the-fork-workflow) · [How it works](#how-it-works) · [Docs](#docs)

</div>

---

## Quick start

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

**The loop is a set of small, single-purpose scripts** under [`contrib/squad-goal/scripts/`](contrib/squad-goal/scripts/). Each one is driven by `watch.sh` on a cadence, and each is covered by a shell suite (`test-*.sh`) beside it.

### Dispatch and the frontier

- **`watchd.sh` / `watch.sh`** are the event loop: they tail the live runs, print one line per event (`RUN … verdict`, `NEW PR`, `REVIEW`, `STALL`, `DEAD RUN`), and call the steps below on a cadence. `watchtail.sh` keeps the loop alive across turns; `launch.py` starts a run and refuses to die with the session; `fastloop.sh` runs the fast passes (`autofrontier`, `autobug`) on a ~30 s beat so a dispatchable issue is picked up in seconds instead of on the slow cycle's beat.
- **`single.sh`** runs a command unless a previous one under the same name is still alive; **`serial.sh`** runs a command holding a named lock, so two emulator-facing suites never collide.
- **`autofrontier.sh`** re-dispatches the frontier the moment a blocker closes; **`stack.sh`** answers whether an issue can start, must stack on exactly one open parent PR, or must wait.
- **`mkreq.sh`** builds the child's request: the issue's requirements, its evidence rules, and the fork preamble — or the PR preamble when the issue's address is the subject repository. Its **slice guard** refuses a request that names a file an unmerged branch already touches, before a child is ever spawned. A bundled frontier request loses only the ticket that names the contested file: the dispatcher names it with the file and the holding branch, frees its marker and sends the rest; a batch where every ticket is contested still refuses.
- **`cluster.py`** groups the ready work by root cause so one child owns one cause.

### Review, fix and land

- **`review.sh`** walks the review chain of [`models.json`](contrib/squad-goal/scripts/models.json) — a model that fails for infrastructure reasons is skipped, and the answer must be the checklist shape [`revformat.py`](contrib/squad-goal/scripts/revformat.py) enforces. Fix rounds review the delta over the previous finding set, not the whole diff again.
- **`autofix.sh`** charges fix rounds (capped; past the cap **`autopanel.sh`** puts one read-only Opus panel in the challenger stance on the PR and the issue).
- **`autopilot.sh`** owns the PR: it re-runs CI, dispatches the fix rounds, merges when the review approves and CI is green, and keeps the card in step (`Testing` → `Done`). **`prepush.sh`** checks the pushed head before CI is asked; **`revci.sh`** watches CI and reports the review's notice.
- **`autobug.sh`** fixes bugs and debt without being asked: every open `bug` issue with no PR naming it, clustered by root cause, one child and one PR per cluster. **`autohunt.sh`** runs when the loop has been idle: one read-only hunt that files reproducible defects (the `bug` label only) instead of waiting for a person to find them.
- **`check-tdd-red.sh`** refuses a bug fix whose PR does not carry the red test; **`check-gh-interface.sh`** refuses a
  loop script that reaches GitHub with a call site of its own, naming the script and the route — a write as well as a
  read, because a refused read looks like an answer and a refused write looks like success (ADR-0277). Every script
  reaches GitHub through the door instead: `. "$(dirname "$0")/ghcall.sh"`, then `gh_api` for a read, `gh_get` for a
  read GitHub answers with another representation, `gh_send` for a write. The call sites that have not moved yet are
  the shrinking budget in `gh-direct-calls.txt`, and a script that file does not name may not reach GitHub at all;
  **`acscan.sh`** writes a read-only brief for listing the acceptance criteria an issue's citations conflict over;
  **`acpr.sh`** runs a run's acceptance command against the pushed PR branch, in a throwaway worktree.
- **`forkclose.sh`** lands the work of the loop's own repository, which has no PR ([The fork workflow](#the-fork-workflow)); **`fork-gate.sh`** is the gate that pass runs, and **`gate-suites.sh`** decides which suite set a diff needs (`full` boots the emulator, `offline`, `shell`, `python`).

### Keeping the loop honest

- **`health.sh`** reads the loop's own state and names what is wrong (`HEALTH frontier-idle`, a stranded dispatch, a base that has been red too long); **`health-install.sh`** installs a launchd agent that runs it every two minutes, outside the loop, because a loop cannot notice its own death. **`heartbeat.sh`** finds a hung test host.
- **`janitor.py`** releases issues a dead run stranded, expires stale dispatches and reports the loop's own failures; **`reconcile.py`** derives the board from GitHub facts; **`wtclean.sh`** removes worktrees a finished run left behind; **`ensure_labels.sh`** idempotently ensures the repository's labels.
- **`forkrescue.py`** places a fork commit a child left on the wrong branch onto the branch the land reads (fast-forward only), and **`forkstrand.py`** reports a fork commit no landing path reads — so a card cannot sit at Code Complete looking finished while the land waits for a branch nobody wrote.
- **`cqflush.sh`** retires a comment the API refuses, so a rejected write cannot retry forever.
- **`models.json`** is the whole model policy: the coding chain, the review chain, the vision chain, per-model time windows, per-model on/off switches, retry budgets and timeouts. Nothing is hard-coded in a script.

### Settings and the contract they carry

Per-repository settings live in a profile ([`scripts/profiles/example.sh`](contrib/squad-goal/scripts/profiles/example.sh)): `SG_REPO`, `SG_PROJECT`, `SG_BASE`, `SG_FORK_DIR`, `SG_TOOLING_REPO`, the test commands. Profiles are live-only — never shipped, never edited by the refresh.

A profile is not free-form: shipped code may hard-require a setting, and `env.sh` declares that contract in one place (`SG_REQUIRED_SETTINGS`, `<setting>:<the setting that engages it>`). `refresh-from-fork.sh --check` reads it against every live profile and names the profile, the setting and the cause when a profile engages a route whose code refuses without the setting.

The loop's operating rules are in the skill, and they are the reason it can run unattended: GitHub is live state and is updated immediately; a stall is recoverable, never a wait; a failing gate is a defect, never retuned; every event and decision lands on the issue it belongs to.

## Commands

| Command | What it does |
| --- | --- |
| `/squad-goal <issues>` | The autonomous loop: drives the listed issues to Done. |
| `/squad-hub` | Starts the hub and prints its URL (see [The hub](#the-hub)). |
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

One page for the whole loop — the board, every live run, the models, the weekly quota and the test panel:

```bash
contrib/squad-goal/scripts/hubctl.sh          # prints the URL, http://127.0.0.1:7700/
```

- The board columns are read from the GitHub Project by REST: status, priority, size, blockers, the open and closed PRs.
- Every live run embeds its own dashboard; the run's dashboard server is started headless, so no tab is opened for it.
- The models panel is the same data `models.json` holds: chains, per-model windows, switches, the quota cache. The quota cache is also what `codex-reset.py` reads to say when the Codex window frees up.
- It is one Python file — `http.server` from the standard library and the HTML/CSS/JS inline — with no build step and no framework. Its state lives in `<repo>/runs/.hub` and ignores itself. `verify-view-domain-firewall.sh` guards the rule that a page only reaches what its view is allowed to see.

## Where tickets and learnings live

**The repository an issue lives in is its address, and the address is the route** (#1869). An issue in the subject repository is delivered as a branch, a PR and a merge. An issue in the loop's own repository (`SG_TOOLING_REPO`) is delivered there as a local commit on the branch the land reads, with no PR, because there is nothing to open one against.

The label (`fork-only`, then `squad-only`) and the `Fork-only work:` body line that used to stand for this are **retired**: a label a human maintains cannot be the route, and the issue that should carry it and does not would be routed the other way in silence. `check-squad-only-retired.sh` fails while any live script still reads one of them.

Two consequences worth knowing before you file something:

- With **no** `SG_TOOLING_REPO` named, nothing is fork-only: the loop reads one repository and lands nothing in a second one. The retired route never comes back as a fallback.
- A bare number carries no address, so it resolves against the repositories the loop operates — the profile's repository first, the tooling one second.

What the loop learned by running is written down in [`docs/learnings/`](docs/learnings/README.md) — one file per episode, with the measurements, the traps and a checklist. Prefer a file there over a comment buried in an issue: an issue records a decision or a piece of work, a learning records what happened.

## The fork workflow

This checkout is a **fork**: the engine tracks upstream (0.2.0, kept as `local/base-0.2.0`), and everything built on top of it is committed on **`main`**. The loop and the fork grow together:

- Its own work lands on `main` as an ordinary commit — the fork is the repository that owns the tooling.
- The **live skill** (`~/.claude/skills/squad-goal/`) is a copy of `contrib/squad-goal` on `main`, and the loop runs the copy.

Keeping both in step:

```bash
contrib/squad-goal/scripts/sync-to-fork.sh        # live skill -> fork, refuses to overwrite newer work
contrib/squad-goal/scripts/refresh-from-fork.sh   # fork -> live skill (--check compares, changes nothing)
contrib/squad-goal/scripts/export-agent-squad.sh  # the ZIP for upstream
```

`refresh-from-fork.sh --check` exits 1 naming every shipped file where live and `main` disagree, and names any live profile that is missing a setting the shipped code requires. It never edits a profile. `vendor-guard.sh` re-vendors the personal skills when they drift from `vendor/skills/VERSIONS.json`.

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
- **Distance is the cost.** A branch that waits is a branch that conflicts: measure how far a candidate has drifted before blaming its tests. The measurements are in [`docs/learnings/`](docs/learnings/README.md).

## Develop

```bash
uv sync --extra dev --extra sdk
python3 -m pytest -n auto       # parallel
python3 -m pytest -p no:xdist   # serial: both modes must pass
ruff check squad tests && mypy squad
```

The fork's own scripts are covered by shell suites that run every `test-*.sh` next to them:

```bash
contrib/squad-goal/scripts/shell-suites.sh                      # the loop's suites
contrib/squad-goal/scripts/fork-gate.sh "<gui suite command>"   # the gate a fork landing runs
```

CI runs ruff, mypy, and the suite on Python 3.11 and 3.13 for every push and every pull request ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

## Docs

| Read | For |
| --- | --- |
| [`contrib/squad-goal/SKILL.md`](contrib/squad-goal/SKILL.md) | The autonomous loop: its invariants, the dispatch, the recovery rules. |
| [`docs/learnings/`](docs/learnings/README.md) | What running the loop taught: the measurements, the traps, the checklists. |
| [`CHANGELOG.md`](CHANGELOG.md) | What every release of this fork changed, with the issue that asked for it. |
| [`GLOSSARY.md`](GLOSSARY.md) | The words this project uses, and the words it avoids. |
| [`docs/adr/`](docs/adr/README.md) | Every durable decision of the tooling, with its guard. |
| [`docs/adr-imported/`](docs/adr-imported/README.md) | Two decisions written in the emulator's register before the tooling had one, kept verbatim. |
| [`docs/design/`](docs/design/README.md) | The graph schema, the fixtures, and the checkers. |
| [`docs/skill-workflow-map.md`](docs/skill-workflow-map.md) | The skill flow from request to ship. |
| [`CLAUDE.md`](CLAUDE.md) | The rules for agents that work in this repo. |

## License

[MIT](LICENSE). Copyright (c) 2026 Rafael Aguilher da Costa.

## Status

agent-squad is the next version of `dev_squad`, rebuilt on the Claude Agent SDK. This checkout is the fork at **0.2.31** on `main` (upstream 0.2.0 kept as `local/base-0.2.0`), and it changes fast: the loop, the hub and the fork workflow are built here, released release by release, and sent upstream as a ZIP.
