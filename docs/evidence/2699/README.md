# #2699 Communication roles and members editor: the visual review

The renders were taken through ego-browser in one private task space, on a loopback hub served from neutral fixture
files. Each render opens the Settings tab at `#settings`, the Communication theme, and shows the Roles and Members
sections.

| Render | Address | Viewport |
| --- | --- | --- |
| `render-1280-roles-members.png` | `#settings` | 1280 × 1900 |
| `render-820-roles-members.png` | `#settings` | 820 × 1900 |

No roles mockup is attached to #2699, #2682 or the repository, so the review is qualitative against the design rules of
the Communication theme: hierarchy, alignment, spacing, wrapping, overlap, cut text and contrast.

The wide render draws one role per row: the id, a checkbox per action, the deploy list and Remove. A role with five
actions wraps its action labels into two lines inside the actions column, and the row keeps one height. The members
section draws one member per row with Slack workspace, channel, role, user ids and Remove, and the add row below the
last member. The user ids input holds the whole list, `U0OWNER001, U0OWNER002`, with no cut.

The narrow render stacks each field under its own label. The action labels stay on one line each, the user ids input
takes the full panel width, and no value is clipped and no label is lost.

## What the review found and fixed

| Defect | Fix |
| --- | --- |
| The `U0OWNER001, U0OWNER002` value was cut inside the user ids input at 1280 px | the workspace and channel columns gave width to the user ids column |
| The narrow action labels broke mid-word (`approve_ and_mov e`) | the field width rule skipped the checkboxes, so each label kept its width |
| The narrow action checkboxes took uneven widths | the action group stacks one label per line under a fixed label column |
| The narrow user ids input cut the second user id | the user ids cell stacks its label above the full-width input |

## Limits

The fixtures name placeholder ids, and the local file is `4.slack.local.yaml`. The renders show fixture data, not the
owner's project.
