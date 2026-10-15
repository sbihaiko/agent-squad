---
description: Start a run from a request. Uses the `default` workflow unless --workflow names an id from docs/squad/workflows/.
argument-hint: "<request> [--workflow <id>] [--name <name>]"
allowed-tools: Bash(uv run:*), Skill, AskUserQuestion
---

Start a run from the owner's request.

Run from the current project directory. The current directory is the run's target.

```
uv run --project "${CLAUDE_PLUGIN_ROOT}" --extra sdk squad start --no-watch --target "$PWD" --stdin <<'SQUAD_REQUEST'
$ARGUMENTS
SQUAD_REQUEST
```

- No `--workflow`: the `default` workflow, so a router agent decides the children.
- `--workflow spec-to-build`: the plugin's own workflow for a request that names `/to-spec`, `/to-tickets` and a build. The `default` workflow warns when a request names a skill it cannot run.
- `--workflow <id>`: the file `docs/squad/workflows/<id>.graph.json`. A missing id exits 2 and lists the known ids. Show that message and stop.
- `--name <name>`: the run id and the run folder name (letters, digits, `.`, `_`, `-`). It stays inside the here-document like `--workflow`, so no character of it reaches the shell. A name that is not allowed, or taken, exits 2 with the shared message. Show it and stop.
- The run uses the real SDK adapter. Its spend and deadline caps are the workflow's.
- Exit code 10 means the run ended `needs-human`. Read `autonomy.enabled` and `autonomy.max_resumes` from the `run_start` event of `<run folder>/record.jsonl`. With autonomy on and fewer than `max_resumes` events `autonomy_resume` in the record:
  1. Invoke the `autonomy` skill. The question is the run's reasons (the verdict line and the `needs-human` outcome reasons). Settle it with the cheapest option to reverse. An owner-only action (the list in `prompts/_autonomy.md`) is never settled this way: stop and report it.
  2. Run `uv run --project "${CLAUDE_PLUGIN_ROOT}" --extra sdk squad resume <run folder> --auto-source autonomy`. It records one `autonomy_resume` event (the resume count and the source) and continues the run. Add `--answer pass` or `--answer fail` when the skill's decision answers a held `wait` node.
  3. Read the new exit code. Repeat while it is 10 and resumes are left.
- A run never ends for lack of a definition: autonomy settles it first. With autonomy off, with no resumes left (the resume command then exits 2 and says why), when the skill cannot settle the question, or when the question is an owner-only action, pause and ask: put the run's reasons to the owner with `AskUserQuestion`, give the options and a recommendation, and wait. Resume (`squad resume <run folder>`, with `--answer pass` or `--answer fail` for a held `wait` node) only after the owner answers, and do the owner's grant yourself first when it is a file or setting you may write. If the owner cannot be asked, the run stays `needs-human`: report the verdict as it stands. The CLI never calls a model; only this command's session runs the skill and asks, and a bare `squad start` from a shell ends `needs-human`.
- Start it in the background of the session (the Bash tool with `run_in_background`). Do not wait for it to end.
- The command prints these lines first: `dashboard: <page URL>`, `run: <id>`, and `run folder: <folder>`. Read them from the background output and tell the owner the page URL, the run folder and the monitor log path, `<folder>/monitor.log`, at once. The page opens by itself; do not open a browser.
- The monitor writes one JSON line to `monitor.log` for each stuck, abandoned or deadline-missed call. Read each new line and tell the owner what it says.
- The exit code and the last line (the verdict) come when the background command ends. Report them. Do not retry a failed run.
