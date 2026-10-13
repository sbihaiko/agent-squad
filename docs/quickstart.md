# Quickstart

Run agent-squad on your own repository. You need `git`, [`uv`](https://docs.astral.sh/uv/) and a logged-in Claude Code. `uv` fetches Python 3.11 or later and the `claude-agent-sdk`.

## Install

```bash
claude plugin marketplace add sbihaiko/agent-squad
claude plugin install agent-squad@agent-squad --scope user
```
The repository is private, so ask the owner for access first. **The plugin's repository moved** from `mmctech-ai/agent-squad`, which no longer exists: a machine that still names it installs nothing. The uninstall-and-reinstall block, and the check that proves it took, are in [install.md](install.md#1-install-the-engine).

Without the plugin, clone the repository and use `uv run --extra sdk squad <command>` in place of `squad`. A plain `pip install` needs the `sdk` extra: `pip install 'agent-squad[sdk]'`.

## Prepare the project

Run these in your repository. The plugin commands do the same:

```bash
squad setup --test-command "python3 -m pytest"
squad setup --apply --test-command "python3 -m pytest"
squad setup --check
```
The first command prints a plan and writes nothing. `--apply` adds `CLAUDE.md`, `docs/adr/` and a memory index. It never overwrites a file. Commit the new files before a run.

## Configure

```bash
squad config show
squad config set limits.max_stalls 8
squad config check
```
`show` lists every setting with its value, unit and source layer. `set` writes `docs/squad/defaults.yaml` in your project. `check` exits 1 on a defect.

## Start a run

`work` takes a request and `task` takes one task. They are the same as `start` and `start --task`:

```bash
squad work --target . "add a function truncate(s, n) with tests"
squad task --target . "fix the failing date parser test"
```
In Claude Code, use `/agent-squad:work <request>` or `/agent-squad:task <task>`. A run costs money: the run spend cap is in `squad config show` (`run.spend_cap`).

The command prints the dashboard URL, the run id and the run folder. It ends with a verdict and an exit code: 0 passed, 1 failed, 10 needs a human, 2 bad usage.

## Dashboard

```bash
squad dashboard --list
squad dashboard
```
The page shows one run. It never starts a run.

## Resume

```bash
squad resume <run-id>
```
The verdict prints the exact command, with the absolute run folder, so it works from any directory.

Use it after a crash, a pause or a `needs-human` verdict. A resume refuses when a skill, the project settings or the autonomy setting changed since the run started.

## Where the run folder is

`runs/<run-id>/` under the directory you started from. Pass `--runs-dir` to move it. Git ignores it through `.git/info/exclude`, so your working tree stays clean.

| File | Content |
| --- | --- |
| `record.jsonl` | Every event of the run. |
| `graph.json` | The workflow the run used. |
| `monitor.log` | One JSON line per stuck or late call. It exists only when one happened. |
| `ws/` | The child worktrees. |

A passing child lands as a work commit and a merge commit on your current branch. A completed run removes the landed child's branch and worktree. A failed or `needs-human` run keeps them for `squad resume`.
