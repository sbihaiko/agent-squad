---
description: Open the workflow editor. Optional workflow id; no id opens the `default` workflow. Saves go to docs/squad/workflows/.
argument-hint: "[workflow-id]"
allowed-tools: Bash(uv run:*)
---

Open the workflow editor.

Run from the current project directory:

```
uv run --project "${CLAUDE_PLUGIN_ROOT}" squad editor $ARGUMENTS
```

- No id: the `default` workflow. The command copies it to `docs/squad/workflows/default.graph.json` on first use, so a save never edits the seed. `dynamic` is an old name for it: an existing `dynamic.graph.json` still wins when there is no `default` file. A new id creates its file from the seed.
- An id: the file `docs/squad/workflows/<id>.graph.json`; an id with no file is created from the seed.
- The command opens the editor page and prints the workflow file. Tell the owner to choose **Open graph** and pick that file; **Save graph** then writes it in place.
