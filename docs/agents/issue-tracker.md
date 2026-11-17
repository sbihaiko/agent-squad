# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..." --label "<type>"`. Use a heredoc for multi-line bodies. Every agent-created issue has one type label: `bug` for a defect, `enhancement` for a non-defect change, or `spec` for a specification. Do not retry without the type label when GitHub rejects another label.
- **Title a spec's child**: a child of a spec carries the spec's number as the first words of its title — `[SPEC <n>] <title>`. The sub-issue link is the relation; the prefix is what a person reads where relations are not drawn (the issue list, a search result, a board card). `/to-tickets` applies it to every child it publishes, and a ticket whose parent is not a spec carries no prefix.
- **Board column of a spec and of its children**: a spec is a document, not work, and its column is a fold over its sub-issues: **Backlog** with none, **Doing** once any child reached Doing, **Closed** when every child is terminal and one is Closed, **Abandoned** when every child is terminal and none is Closed. A spec already Closed or Abandoned whose child reopens keeps its column and carries the `reverted` tag. The loop closes a spec issue with at least one sub-issue, every one closed, with one comment that names the condition; a spec with no sub-issue stays open. A spec and every ticket cut from it are created with their card in **Backlog**; a bug is created in **Todo**. The loop never moves a card into Backlog or Todo; only the owner does. Todo plus no holder is the dispatcher's go signal, so the owner starts a ticket by moving its card to Todo. The column never expresses a blocking edge — `stack.sh` holds a ticket whose blocker is open. `/to-tickets` publishes each child with its prefix, its sub-issue link, its native blocking edges, and its card in Backlog; the dispatch pass writes `ready-for-agent` when it takes the card, so no publisher adds it.
- **Approve a spec's breakdown**: only the owner. The autonomy panel never approves a breakdown unless the owner asked for it in the session; with the owner away, the breakdown waits.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

The repo is `sbihaiko/agent-squad` (private; it moved from `mmctech-ai/agent-squad`, which no longer exists). Infer it from `git remote -v`; `gh` does this automatically when run inside a clone.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)_

When set to `yes`, PRs run through the same labels and states as issues, using the `gh pr` equivalents:

- **Read a PR**: `gh pr view <number> --comments` and `gh pr diff <number>` for the diff.
- **List external PRs for triage**: `gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments` then keep only `authorAssociation` of `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, or `NONE` (drop `OWNER`/`MEMBER`/`COLLABORATOR`).
- **Comment / label / close**: `gh pr comment`, `gh pr edit --add-label`/`--remove-label`, `gh pr close`.

GitHub shares one number space across issues and PRs, so a bare `#42` may be either: resolve with `gh pr view 42` and fall back to `gh issue view 42`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets.

- **Map**: a single issue labelled `wayfinder:map` and one type label, holding the Notes / Decisions-so-far / Fog body. `gh issue create --label enhancement --label wayfinder:map`.
- **Child ticket**: an issue linked to the map as a GitHub sub-issue (`gh api` on the sub-issues endpoint). Where sub-issues aren't enabled, add the child to a task list in the map body and put `Part of #<map>` at the top of the child body. Labels: `wayfinder:<type>` (`research`/`prototype`/`grilling`/`task`). Once claimed, the ticket is assigned to the driving dev.
- **Blocking**: GitHub's **native issue dependencies**, the canonical, UI-visible representation. `contrib/squad-goal/scripts/edge_write.py` adds an edge with `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`, where `<blocker-db-id>` is the blocker's numeric **database id** (`gh api repos/<owner>/<repo>/issues/<n> --jq .id`, _not_ the `#number` or `node_id`). GitHub reports `issue_dependencies_summary.blocked_by` (open blockers only, the live gate). The writer calls the deadlock validator before each write, refuses an invalid or unknown edge with its cycle path, and reads each accepted edge back. A rerun writes each missing edge once. A `Blocked by` line in a body is never an edge, and no edge is added by hand. A ticket is unblocked when every blocker is closed.
- **Frontier query**: list the map's open children (`gh issue list --state open`, scoped to the map's sub-issues / task list), drop any with an open blocker (`issue_dependencies_summary.blocked_by > 0`, or an open issue in the `Blocked by` line) or an assignee; first in map order wins.
- **Claim**: `gh issue edit <n> --add-assignee @me`, the session's first write.
- **Resolve**: `gh issue comment <n> --body "<answer>"`, then `gh issue close <n>`, then append a context pointer (gist + link) to the map's Decisions-so-far.
