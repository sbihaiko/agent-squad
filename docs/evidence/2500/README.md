# #2500 Needs you and the Slack link: the visual review

The renders were taken through ego-browser in one private task space, on the phone page served from a loopback server
with neutral fixtures. The page was entered at `#/now` after a cleared storage, so the render shows the saved address
opening Now.

| Render | Address | Viewport | Fixture |
| --- | --- | --- | --- |
| `render-now.png` | `#/now` | 390 × 844, DPR 3 | two pending runs, one Slack link |
| `render-now-scrolled.png` | `#/now` | 390 × 844, DPR 3 | the same page, scrolled past the block |
| `render-now-slack-unavailable.png` | `#/now` | 390 × 844, DPR 3 | one pending run, no Slack connection |

The comparison used the mockups in #2498: `new-01-now.png` and `new-01b-now-scrolled.png`.

## What the review found

The Needs you block draws the same as the mockup: the heading with its count, one row per pending run with a bold issue
number, the warn chip, the age and the spend, and below it the one Slack row with the channel and the arrow. A row and
the link keep a 44 px target.

The review found one defect and fixed it. The channel sat at the far right of the Slack row, while the mockup puts it
right after "Answer in Slack". The arrow now takes the free space, and the channel follows the label.

The unavailable state draws as a dashed box with no anchor, so a missing connection leaves no dead link.

The spec's visual-review item stays **open** for the owner's iPhone check (acceptance criterion 6). The render also
shows the tiles and the warnings below the Monitor card, where the mockup puts them above it. This ticket does not
change that order.
