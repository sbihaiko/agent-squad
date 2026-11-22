# #2693 Communication read view: the visual review

The renders were taken through ego-browser in one private task space, on a loopback hub served from neutral fixture
files. Each render opens the Settings tab at `#set-slk`, the Communication theme.

| Render | Address | Viewport |
| --- | --- | --- |
| `render-1280.png` | `#set-slk` | 1280 × 1000 |
| `render-820.png` | `#set-slk` | 820 × 1200, scrolled to the top |
| `render-820-b.png` | `#set-slk` | 820 × 1200, scrolled to the Roles section |
| `render-820-c.png` | `#set-slk` | 820 × 1200, scrolled to the last workspace and the Project events |

The comparison used the overview mockup in #2682. The wide render draws the Now tiles and the four sections in the
mockup's order: Personas, Roles, Slack workspaces and Project events. Each field carries the chip of its target file.
The narrow render stacks each table cell under its own label, so no column is clipped.

## What the review found

The wide render matches the mockup's layout, hierarchy and spacing for this slice. Two differences belong to slices that
this ticket does not draw:

| Difference | Mockup | Slice that owns it |
| --- | --- | --- |
| The workspace tiles state the live connection, not only the file facts | two workspace tiles on Now | the live-connection slice of SPEC 2682 |
| The dropdowns, the Edit buttons, the Add rows and the role matrix | every section | #2696 to #2699, the edit views |

The first narrow render had two defects: the tables were clipped at the panel's right edge, and the file chip stretched
across its row. Both are fixed: a narrow panel stacks each cell under its label, and the chip keeps its own width.

The Slack workspaces heading names both files, so its two chips share one group. The first render split them across the
heading by the `space-between` rule; grouping them fixed the gap. On a narrow panel the group wraps to a second line
instead of clipping at the right edge.

## The failure path

A rule that does not resolve draws the same page: the Now tiles, the four headings and their empty-state text, with the
reason in a defects banner above them. The reason is a message, never a replacement for the page.

## Limits

The fixtures name placeholder ids and a local file named `probe.slack.local.yaml`. The screenshot was taken on these
fixtures, so the renders show fixture data, not the owner's project.
