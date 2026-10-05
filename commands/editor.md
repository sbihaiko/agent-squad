---
description: Open the workflow editor. Optional workflow id; no id opens the dynamic workflow. Saves go to docs/squad/workflows/.
argument-hint: "[workflow-id]"
allowed-tools: Bash(uv run:*)
---

Open the workflow editor.

Run from the current project directory:

```
uv run --project "${CLAUDE_PLUGIN_ROOT}" squad editor $ARGUMENTS
```

- No id: the dynamic workflow. The command copies it to `docs/squad/workflows/dynamic.graph.json` on first use, so a save never edits the seed.
- An id: the file `docs/squad/workflows/<id>.graph.json`. A missing id exits 2 and lists the known ids. Show that message and stop.
- The command opens the editor page and prints the workflow file. Tell the owner to choose **Open graph** and pick that file; **Save graph** then writes it in place.
