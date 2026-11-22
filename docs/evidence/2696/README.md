# #2696 Communication workspace and channel editor: the visual review

The renders were taken through ego-browser in one private task space, on a loopback hub served from neutral fixture
files. Each render opens the Settings tab at `#set-slk`, the Communication theme, and opens the channel detail of the
`guest` workspace.

| Render | Address | Viewport |
| --- | --- | --- |
| `render-1280-guest.png` | `#set-slk` | 1280 × 1100, the `guest` channel open |
| `render-820-guest.png` | `#set-slk` | 820 × 1200, the `guest` channel open |
| `render-1280-refusal.png` | `#set-slk` | 1280 × 1100, a second `guest` workspace refused |

The comparison used the `guest` channel mockup in #2682 (prototype, variant D). The detail opens in place under the
channel row, as the mockup draws it. The wide render shows the workspace line with its class tag, its transport and the
chip of its file, and the channel table with Answers and Polling beside Mode, Language and Persona. The detail carries
one labelled control per field: Id, Name, Mode, Language, Persona, Roles, Announce, Answer as, Transcribe a clip, Voice
reply, Poll mode, the poll timings, Draft for, the schedule line, and Remove channel.

The narrow render stacks each table cell under its own label, and the detail keeps two columns, so no field is clipped
and no value loses its label.

## What the review found and fixed

| Defect | Fix |
| --- | --- |
| The stacked table dropped the label of every editor cell | each cell carries its label, as the read view does |
| The open detail sat in the 112 px label column of a stacked table | the detail cell spans the full row below 1100 px |
| Both poll families drew at once, which the mockup does not do | the detail draws the fields of the chosen poll mode |
| An empty poll box beside a stated default read as a missing value | each poll box carries its default as a placeholder |

## Differences that belong to other slices

| Difference | Mockup | Slice that owns it |
| --- | --- | --- |
| The Members column | each workspace row | #2699, the role members |
| The preview of the reply with mark, language, tone and voice | the `guest` channel detail | #2697 and #2698 |
| The live connection tile on Now | the two workspace tiles | the live-connection slice of SPEC 2682 |

## Criterion 6 in the browser

The refusal render is the live page after the owner adds a second `guest` workspace. The hub answers the mockup's
sentence and the policy module's reason, and the page shows both above the workspace rows:

`One guest Slack workspace at a time: 4.slack.local.yaml: workspaces[1].id: the machine serves one guest workspace at a
time; a second guest workspace is refused`

## Limits

The fixtures name placeholder ids, and the local file is `4.slack.local.yaml`. The renders show fixture data, not the
owner's project.
