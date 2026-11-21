# #2501 Work and Specs: the visual review

The renders were taken through ego-browser in one private task space. The phone page is the one the hub composes from
this tree. A loopback server served it with fixtures shaped like the hub's answers. The device metrics were 390 × 844
at DPR 3, the phone the mockups are drawn for.

| Render | Address | Viewport | Fixture |
| --- | --- | --- | --- |
| `render-work.png` | `#/work` | 390 × 844, DPR 3 | one Backlog column with a spec lane and four tickets |
| `render-specs.png` | `#/specs` | 390 × 844, DPR 3 | two specs, one at 0 of 4 and one at 9 of 10 |

The comparison used the mockups in #2498: `new-02-work.png` and `new-03-specs.png`.

## The screens this change touches

The change touches two screens, Work and Specs. Each render was compared with the mockup of the same state.

| Screen | Mockup | Verdict |
| --- | --- | --- |
| Work | `new-02-work.png` | The column switcher with the count, the column tabs, the spec lane header with its count and its one `→ Todo`, the cards with their blocked-by chips, the board-read line and the Squad Board link all match. |
| Specs | `new-03-specs.png` | The two spec rows with their title, progress bar, count and legend match, and each row is one anchor with its arrow. |

The review found one defect and fixed it. The Specs row is an anchor, and it drew in the browser's default link color,
underlined. The mockup draws the title in the page's text color. The row now takes the page's text color and no
underline.

The header's second line reads `none live · no alert` here and `loop on` in the mockup. The project switcher's meta
draws that line on every screen. This ticket changes neither Work's nor Specs' header, so the line stays as it is.

## Open items

- [ ] **Acceptance criterion 7 on a real phone.** These renders use a fixture board. The owner's own board, in the
  GitHub app tap, stays an owner check.
