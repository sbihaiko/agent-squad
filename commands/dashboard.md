---
description: Open the run dashboard page for one run. Optional run number, folder name or run id; no argument opens the latest run. It reads a run and never starts one.
argument-hint: "[run]"
allowed-tools: Bash(uv run:*)
---

Open the dashboard page for one run (ADR-0117: the command opens a page and never starts a run).

Run from the project directory that holds `runs/`:

```
uv run --project "${CLAUDE_PLUGIN_ROOT}" squad dashboard $ARGUMENTS
```

- No argument: the latest run under `runs/`.
- A number: the position in `squad dashboard --list`, oldest first. A folder name or a run id also works.
- The command prints the run, its folder and the page URL, and opens the page in the default browser. The page opens already loaded, from a loopback server (ADR-0118). It reads the run; its one write is a pause or resume request (ADR-0128).
- Exit 2: show the message. It names the valid range. Do not retry with a guess.
