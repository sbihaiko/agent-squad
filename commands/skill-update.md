---
description: Check that every skill the plugin borrows still resolves, after `npx skills update` or `claude plugin update`. It reports and never edits `~`.
allowed-tools: Bash(uv run:*)
---

Check the borrowed skills after a skill update (ADR-0129).

```
uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m squad.skillpath --check
```

- Exit 0: say how many skills were checked.
- `missing:` (exit 1): a skill resolves nowhere. Name it. Do not improvise its steps.
- `warning:` lines: a personal copy differs from the plugin copy and shadows it. List each one, with `npx skills update -g` or `rm -r ~/.claude/skills/<name>` as the fix. Do not edit `~`: that is the owner's.
- To repair the vendored copy or the doors in the agent-squad repo, run `/squad-skill-update` there.
