# Install agent-squad

This page installs agent-squad, its dependencies and the third-party skills the agent nodes load.
It is written for an agent to follow step by step. Each step ends with a check. Run the check before the next step.

Every command is US English and shell. A step that fails prints one line that names the cause.

## What you need

| Tool | Why | Check |
| --- | --- | --- |
| Python 3.11 or later | The engine and its tests | `python3 -V` |
| [`uv`](https://docs.astral.sh/uv/) | Fetches Python and the test extras | `uv --version` |
| `git` | The run folder, the worktrees and the loop | `git --version` |
| Claude Code, logged in | Runs the model calls | `claude --version` |
| `jq` | The loop's scripts read JSON with it | `jq --version` |
| `gh`, authenticated | The loop reads and writes issues and the board | `gh auth status` |

`jq` and `gh` are needed by the **loop** only. The engine runs without them.

On macOS, install the two with `brew install jq gh` and then `gh auth login`.

## 1. Install the engine

```bash
claude plugin marketplace add sbihaiko/agent-squad
claude plugin install agent-squad@agent-squad --scope user
```

The repository is private: ask the owner for access first, and run `gh auth login` before the first command.

**The repository moved.** The plugin used to live at `mmctech-ai/agent-squad`, which no longer exists. A machine that still points at it installs nothing and reports a fetch error. Replace it in one block:

```bash
claude plugin uninstall agent-squad@agent-squad
claude plugin marketplace remove agent-squad
claude plugin marketplace add sbihaiko/agent-squad
claude plugin install agent-squad@agent-squad --scope user
claude plugin marketplace list | grep agent-squad
```

The last command must print `GitHub (sbihaiko/agent-squad)`. A source that still reads `mmctech-ai` means the removal or the add did not take; run the block again before you continue.

Check it: inside a project in Claude Code, type `/agent-squad:config show`. It prints the settings and their sources.

Without the plugin, clone the repository and call the CLI directly:

```bash
git clone https://github.com/sbihaiko/agent-squad
cd agent-squad
uv sync --extra sdk
uv run --extra sdk squad --help
```

A plain `pip install` needs the extra: `pip install 'agent-squad[sdk]'`.

## 2. Install the third-party skills

The agent nodes load skills that this project does not write. They are vendored under `vendor/skills/`, and
`vendor/skills/VERSIONS.json` pins the hash of every one. A run refuses to start when the vendored copy drifts from
its source; the message is `vendor/skills is not clean`.

The skills and their sources are in `config/vendor-skills.yaml`. The eight that ship:

| Skill | Loaded by |
| --- | --- |
| `tdd`, `diagnosing-bugs`, `codebase-design` | The dispatch envelope's children |
| `ask-matt` | The router node |
| `to-spec`, `to-tickets`, `to-test-scenario` | The commands that turn a request into work |
| `ego-browser` | The browser work of a GUI case |

A source written as `skill:NAME` is found **by name**, never by a path: `python3 -m squad.skillpath NAME` prints where
it resolves. Install or update a personal skill with the skills CLI, then re-vendor:

```bash
npx skills add <owner>/<repo>          # or: npx skills update
python3 scripts/build_vendor_skills.py # copies each source into vendor/skills, rewrites VERSIONS.json
git add vendor/skills && git commit -m "vendor skills: re-vendor after an update"
```

Check it:

```bash
python3 scripts/build_vendor_skills.py --check   # exits 1 when a source or a hash differs
python3 -m squad.skillpath --check               # exits 1 when a name no longer resolves
```

`contrib/squad-goal/scripts/vendor-guard.sh` does the same re-vendor by itself when the loop is running, so a drifted
copy never blocks the next run for long.

## 3. Prepare a project

Run these in the repository you want to work in. The plugin commands do the same.

```bash
squad setup --test-command "python3 -m pytest"           # prints the plan and writes nothing
squad setup --apply --test-command "python3 -m pytest"   # writes CLAUDE.md, docs/adr/ and the memory index
squad setup --check                                      # exits 1 on a defect
```

`--apply` never overwrites a file. Commit what it writes before a run.

Then set the project settings, which overlay the plugin defaults:

```bash
squad config show
squad config set limits.max_stalls 8
squad config check
```

## 4. Install the loop (squad-goal)

The loop drives a GitHub issue board to zero open issues. It needs `gh`, `jq` and one profile per repository.

```bash
# the skill, from this checkout
mkdir -p ~/.claude/skills
cp -R contrib/squad-goal ~/.claude/skills/squad-goal

# one profile per repository you want the loop to work
cp ~/.claude/skills/squad-goal/scripts/profiles/example.sh \
   ~/.claude/skills/squad-goal/scripts/profiles/<owner>-<repo>.sh
$EDITOR ~/.claude/skills/squad-goal/scripts/profiles/<owner>-<repo>.sh
```

The profile sets the repository, the board, the base branch, the test command and the halt settings. Every remaining
setting has a default in the shipped code. Profiles are live-only: they are never shipped and the refresh never edits
one. `refresh-from-fork.sh --check` names any profile that misses a setting the shipped code requires.

Check it:

```bash
bash ~/.claude/skills/squad-goal/scripts/env.sh && echo "$SG_REPO $SG_BASE"   # inside the repository
bash ~/.claude/skills/squad-goal/scripts/health.sh                            # one line per problem, or nothing
```

Start the loop, and open the one page that shows it:

```bash
bash ~/.claude/skills/squad-goal/scripts/watchd.sh run &   # the loop
bash ~/.claude/skills/squad-goal/scripts/hubctl.sh         # prints the hub URL
```

## 5. Verify the whole install

```bash
uv sync --extra dev --extra sdk
uv run --extra dev pytest -n auto                 # the suite, parallel
uv run --extra dev pytest -p no:xdist             # the suite, serial: both modes must pass
uv run ruff check squad tests && uv run mypy squad
bash contrib/squad-goal/scripts/shell-suites.sh   # the loop's own suites
```

To rehearse a CI runner that holds no MesenAI checkout and no node binary, set `SQUAD_CI_RUNNER=1`. The run deselects each fixture-bound test and prints its id and reason; with the switch unset and the fixtures present, nothing is deselected.

```bash
SQUAD_CI_RUNNER=1 uv run --extra dev pytest -p no:xdist
```

CI runs the first four on Python 3.11 and 3.13 on a landing, a push to `dev`, `stg` or `main`, and on no work-branch
push and no pull request (`docs/adr/0145-a-work-branch-lands-in-dev-by-fast-forward.md`); a work branch is proven by the
local gate above. A red check is a defect of the change, never of the check.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `vendor/skills is not clean` | A vendored skill drifted. Run `python3 scripts/build_vendor_skills.py` and commit the result. |
| `no squad-goal profile matched` | No profile resolves for this directory. Set `SG_PROFILE`, or add `scripts/profiles/<name>.sh`. |
| `spend_outside_budget` | A dispatch request asked for more than the graph's dispatch node allows. Read the budget with `contrib/squad-goal/scripts/envelope-spend.sh`. |
| A run ends `needs-human`, exit code 10 | A person must act. The reasons name the act. |
| The loop is quiet | `health.sh` names the reason; `hubctl.sh` shows the state the loop is in. |
