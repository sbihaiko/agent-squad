# #2500 Needs you and the Slack link: the visual review

The renders were taken through ego-browser in one private task space, on the phone page served from a loopback server
with neutral fixtures. The page was entered at `#/now` after a cleared storage, so the render shows the saved address
opening Now. The renders were taken again on the final head.

| Render | Address | Viewport | Fixture |
| --- | --- | --- | --- |
| `render-now.png` | `#/now` | 390 × 844, DPR 3 | two pending runs, one Slack link |
| `render-now-scrolled.png` | `#/now` | 390 × 844, DPR 3 | the same page, scrolled past the block |
| `render-now-slack-unavailable.png` | `#/now` | 390 × 844, DPR 3 | one pending run, no Slack connection |

The comparison used the mockups in #2498: `new-01-now.png` and `new-01b-now-scrolled.png`.

## The screens this change touches

The change touches one screen, Now. No other screen draws the Needs you block or the Slack link, so no other render
applies. Each render was compared with the mockup of the same state.

| Screen | Mockup | Verdict |
| --- | --- | --- |
| Now, the Needs you block | `new-01-now.png` | The heading with its count, one row per pending run with a bold issue number, the warn chip, the age and the spend, and below it the one Slack row with the channel and the arrow. A row and the link keep a 44 px target. |
| Now, the unavailable state | none | The dashed box carries the reason and no anchor, so a missing connection leaves no dead link. |

The review found one defect and fixed it. The channel sat at the far right of the Slack row, while the mockup puts it
right after "Answer in Slack". The arrow now takes the free space, and the channel follows the label.

## Open items

- [ ] **Acceptance criterion 6.** The owner checks on the iPhone that the link opens the Slack app on the channel. This
  is owner-only, and this ticket reports it as open, never as done.
- [ ] **The tiles/warnings order.** This render shows the tiles and the warnings below the Monitor card, where
  `new-01-now.png` puts them above it. The order belongs to Now's existing layout, and this ticket does not change it.
  It needs its own task, with `new-01-now.png` in #2498 as the reference.
