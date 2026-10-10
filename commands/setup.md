---
description: Prepare a project for agent-squad. Runs `squad setup`, then the global setup skill through its agent door, and commits the files once.
argument-hint: "[--language <lang>] [--test-command <cmd>] [--tracker <name>] [--no-auto-commit]"
allowed-tools: Bash(uv run:*), Bash(python3 -m squad.skillpath:*), Bash(git:*)
---

Prepare the current project (the default ADR set, an ADR index, CLAUDE.md, AGENTS.md).

1. Show the plan. Nothing is written yet.

```
uv run --project "${CLAUDE_PLUGIN_ROOT}" squad setup --target "$PWD"
```

2. Interview parts (tracker, triage labels, domain docs): follow the global `setup-matt-pocock-skills` skill through its agent door `agent-setup-matt-pocock-skills`. Find the skill by name with `python3 -m squad.skillpath setup-matt-pocock-skills`, never by a path. Do not copy its text here. Exit 1 means it was removed or renamed upstream: stop that step and tell the owner. Tracker, labels and domain docs come from that skill's own templates. Pass what it learns to the CLI as `--tracker`, `--language`, `--test-command`.
3. Where the owner is away, the autonomy panel (`autonomy:panel`) answers the interview.
4. Write the files with the same options.

```
uv run --project "${CLAUDE_PLUGIN_ROOT}" squad setup --target "$PWD" --apply
```

5. Commit once: when the project accepted automatic commits (no `--no-auto-commit`), make one commit that holds every file `--apply` wrote, plus the files the skill wrote. Otherwise leave them staged (`git add`) and do not commit.

- An existing file is never overwritten. A second run changes nothing.
