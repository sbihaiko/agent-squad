---
description: Show or change the project settings file docs/squad/defaults.yaml. Subcommands show, set, reset, init, check.
argument-hint: "show | set <key> <value> | reset <key> | init | check"
allowed-tools: Bash(uv run:*)
---

Run one `squad config` subcommand on the current project.

Run from the project directory. The current directory is the target.

```
uv run --project "${CLAUDE_PLUGIN_ROOT}" squad config --target "$PWD" --stdin <<'SQUAD_CONFIG'
$ARGUMENTS
SQUAD_CONFIG
```

- `show`: every setting with its value, unit and source layer.
- `set <key> <value>`: write one key, for example `limits.max_stalls 8`.
- `reset <key>`: remove one key. The file keeps the others.
- `init`: create the project file with only the keys changed so far.
- `check`: report every defect of the project file. Exit 1 on a defect.
- The arguments stay inside the here-document, so no character of them reaches the shell.
- Exit 1 or 2: show the message and stop. Do not retry with a guess.
