---
description: Start a run from a request. Uses the dynamic workflow unless --workflow names an id from docs/squad/workflows/.
argument-hint: "<request> [--workflow <id>]"
allowed-tools: Bash(uv run:*)
---

Start a run from the owner's request.

Run from the current project directory. The current directory is the run's target.

```
uv run --project "${CLAUDE_PLUGIN_ROOT}" --extra sdk squad start --target "$PWD" $ARGUMENTS
```

- No `--workflow`: the dynamic workflow, so a router agent decides the children.
- `--workflow <id>`: the file `docs/squad/workflows/<id>.graph.json`. A missing id exits 2 and lists the known ids. Show that message and stop.
- The run uses the real SDK adapter. Its spend and deadline caps are the workflow's.
- Report the run folder and the verdict line. Offer `/agent-squad:dashboard` to watch the run. Do not retry a failed run.
