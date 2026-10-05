---
description: Start a run for one task. Uses the dynamic workflow unless --workflow names an id from docs/squad/workflows/.
argument-hint: "<task> [--workflow <id>]"
allowed-tools: Bash(uv run:*)
---

Start a run for one task.

Run from the current project directory. The current directory is the run's target.

```
uv run --project "${CLAUDE_PLUGIN_ROOT}" --extra sdk squad start --task --target "$PWD" $ARGUMENTS
```

- A task is one unit of work: the router asks for the fewest children that finish it. A request that needs planning goes to `/agent-squad:work`.
- No `--workflow`: the dynamic workflow. `--workflow <id>`: the file `docs/squad/workflows/<id>.graph.json`. A missing id exits 2 and lists the known ids. Show that message and stop.
- Report the run folder and the verdict line. Offer `/agent-squad:dashboard` to watch the run. Do not retry a failed run.
