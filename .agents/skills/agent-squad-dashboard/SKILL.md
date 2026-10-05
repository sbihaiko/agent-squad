---
name: agent-squad-dashboard
description: Open the run dashboard page for one run. Takes an optional run number, folder name or run id; with none it opens the latest run. It reads the run and never starts one.
---

# agent-squad-dashboard

Open the dashboard page for one run (ADR-0117: the command opens a page and never starts a run).

1. Run `uv run squad dashboard [RUN]` from the repository root, where `RUN` is the argument you were given.
   - No argument: the latest run under `runs/`.
   - A number: the position in `uv run squad dashboard --list`, oldest first.
   - A folder name or a run id also works.
2. The command prints the run, its folder, and the page URL, and opens the page in the default browser. One page opens per run. Pass `--no-open` to print only.
3. The page opens already loaded: the command starts a loopback server for the run folder (ADR-0118). It reads the run; its one write is a pause or resume request (ADR-0128). No click is needed. With `--file` the page is a `file://` page: tell the owner to click **Connect run folder** and choose the printed folder.
4. On an error (exit 2) show the message. It names the valid range. Do not retry with a guess.

With `--file`, Chrome or Edge gives the folder picker. Other browsers get a folder upload button.
