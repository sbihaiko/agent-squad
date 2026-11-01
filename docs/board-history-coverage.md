# Board history coverage

What the GitHub history API really returns for the Squad Board (project 5 of `sbihaiko/agent-squad`). Read this before you build a reader on timelines or project item fields. The counts below move with every write to the board; measure again before you rely on one.

## Fixtures

Each fixture in `contrib/squad-goal/scripts/fixtures/board-history/` is the exact `gh api graphql` payload for one issue. The query reads `node(id)` on an `Issue`, `timelineItems(first: 100)` filtered to nine item types (added, removed, status changed, reopened, closed, parent added, parent removed, labeled, unlabeled), and `projectItems(first: 5)` with the Status field's `updatedAt`. Nothing is stripped.

| Case | Fixture | Source |
| --- | --- | --- |
| (a) card created in Todo by a writer | `created-in-todo.json` | issue 894: added, then Status Todo |
| (b) card added with no Status value | none | not on the board (see below) |
| (c) removed and re-added card | `removed-readded.json` | issue 234: removed, then added again |
| (d) reopened issue | `reopened.json` | issue 859: reopened after close |
| (e) re-parented ticket | none | not on the board (see below) |
| (f) label add event | `label-added.json` | issue 435 |
| (g) label remove event | `label-removed.json` | issue 737 |

Case (b) is absent. Every added event on the board has a later Status event. Case (e) is absent. Four issues carry a parent-removed event, and none gains a parent-added event afterwards.

## Measured on the board

- **Cards without a first Status event:** 0 of 609 project items (0 of 589 issues). Every issue timeline holds at least one status-changed event. Two single-issue passes agreed on all 589 issues.
- **updatedAt on a Status change:** the Status field's `updatedAt` moves on a Status change, and the issue's `updatedAt` does not. In 22 cards the issue's `updatedAt` is older than the last Status event, and the Status field's `updatedAt` equals that event's time to the second. The project item's own `updatedAt` also moves on writes that touch no Status, so use the field's `updatedAt`, not the item's. In 222 cards the item's `updatedAt` is later than the last Status event while the issue's is earlier.
- **Batch size:** a batched timeline read can return short timelines with no error. Measured against one-issue reads on all 589 issues: 10 issues per query left 5 timelines short, 20 left 31 short, 25 left 80 short, and 450 left 560 short. One issue per query returned every timeline complete. A 500-issue query is refused with "Resource limits for this query exceeded", so the GraphQL node limit binds above 450 issues, but the short timelines show that a query under the node limit can still read less than it asks for.
- **GraphQL points:** one issue per query costs 1 point (`rateLimit.cost`). A 450-issue query costs 5 points, and a 25-issue query costs 1 point.

## Guide for a reader

- Read timelines one issue per query, or check each `totalCount` against a one-issue read before you trust a batch.
- Read the Status change from the status-changed event, and the current Status from the field's `updatedAt`. Do not infer a change from the issue's `updatedAt`.
- A card can appear twice in one project. The board holds 609 items for 589 issues, so key on the issue number, not the item id.
