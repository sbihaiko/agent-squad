# ADR-0181: A persona leaves a project only where no place holds it

## Status

Accepted.

## Context

The Personas theme of the hub offers the removal of a persona from the roster projects. A persona is a row of a project's two Slack files, and other places of the same files and of the profile's client map name it: the project row, a workspace, a channel, and the default persona of a client that holds the project. A removal that ignored those places would leave a file that names a persona it no longer declares.

## Decision

1. **A place that names the persona holds it.** The policy module answers one holder string for each place of the project's committed file and local file that names the persona id: `project in <file>`, `workspace "<label>" in <file>`, and `channel "<label>" of workspace "<label>" in <file>`. A client of the profile's map holds the persona when its default persona is that id and the client names the project. A client that names another project never answers a holder. Each file reads on its own: a file that does not read answers no holder of its own and no client default, while the readable file still answers its holders, so a broken file never hides a place or yields a phantom client holder. Guard: `tests/test_slack_policy.py`.
2. **A holder text names each place in one shape.** A place reports the display name its files resolve for its id, else its `label`, else its `name`, else its id after the marker `unnamed`, in parentheses, so a bare Slack id never stands alone. A refusal reads `refused: held by <place>; <place>`. Guard: `tests/test_slack_policy.py`, `tests/test_personas.py`.
3. **One order decides the refusal.** A target outside the roster, a project with no worktree, a file whose read answered a defect, a held persona and a committed file that does not declare the id are each refused by name, in that order. A holder outranks a missing committed row, and one refused project never stops the others. Guard: `tests/test_personas.py`, `tests/test_hub_personas_edit.py`.
4. **A refusal names the persona from the target project alone.** The name a refused removal states comes from the target project's own committed file, and the bare id answers when that file declares no name for it. Another project's declaration never names this project's persona. Guard: `tests/test_personas.py`.
5. **A preview writes nothing; a confirm writes each project from its own worktree.** The removal route builds each target's committed document through the rules module and calls the project's save path without a confirm, so the answer names each file and its diff and writes no file. With a confirm the route writes, commits and lands each project from its own worktree, and one refused project never stops the others. Guard: `tests/test_hub_personas_edit.py`.
6. **`confirm` is the boolean true or absent.** Any other value is refused with `confirm: true or absent`, so a caller that meant to confirm never reads a preview as a success. Guard: `tests/test_hub_personas_edit.py`.
7. **The removal drops the row of that id alone.** The committed document loses every row of that id and keeps every other row, every other key and their order. A persona only the local file declares is read-only and answers no document. Guard: `tests/test_personas.py`.

## Consequences

A removal is refused while a place still names the persona, so the owner clears that place first. The holder text is the page's whole statement of why a project keeps a persona, so its shape stays stable and names each place with its file. A project whose file does not read reports the defect instead of a holder or a missing row, so the owner fixes the file before a removal.
