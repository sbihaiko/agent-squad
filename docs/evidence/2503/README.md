# #2503 Spend tab: the visual review

The renders were taken through ego-browser in one private task space, on the worktree's phone page and desktop page
served from a loopback server with neutral fixtures. Each render names its screen and its viewport.

| Render | Page | Viewport |
| --- | --- | --- |
| `render-spend.png` | phone, `#/spend` | 390 × 844, DPR 3 |
| `render-spend-scrolled.png` | phone, `#/spend`, scrolled to the legend | 390 × 844, DPR 3 |
| `render-spend-landscape.png` | phone, `#/spend` | 844 × 390, DPR 3 |
| `render-spend-desktop.png` | desktop Spend panel | 1280 × 900, DPR 2 |

The comparison used the mockups in #2498: `new-05-spend.png`, `new-05b-spend-scrolled.png`,
`new-05c-spend-hour.png` and `desk-spend-day.png`.

## What the review found and fixed

| Defect | Render | Fix |
| --- | --- | --- |
| The desktop pie drew DeepSeek and Claude Opus on one green, because nine folded models took every palette role before the drawn slices. | `render-spend-desktop.png` | The drawn slices claim their roles first, and a taken role passes to the next free one. A model never takes the fold's role. |
| The desktop caption crossed the ring, so part of `tokens · month` sat on a slice. | `render-spend-desktop.png` | The pie grows to a 160-unit box with a 48-unit hole, and the caption reads in full contrast. |
| The phone chart carried no scale reading. | `render-spend.png` | The bars name their peak (`max …`) and the live end (`now`), as the desktop chart does. |
| The floating project pill covered the last cost row, and the content had no pad under it. | `render-spend.png` | The Spend content keeps a 72 px bottom pad, so every cost row scrolls clear of the pill. |
| The chart took the full width in landscape. | `render-spend-landscape.png` | The chart stops at 420 px and stays centred. |

## What the review accepted

- The Spend tab draws the desktop panel stacked: the summary line, the hour, day and month toggle over the token bars,
  then the month pie over its legend. The toggle changes only the bars.
- Each legend row names its consumer (`loop · …` or `operator · …`), and the folded row names its consumer too.
- The pie's largest slice and `other` never share a colour, on the phone or on the desktop.
- The centre caption reads whole inside the hole, on the phone and on the desktop.
- The page header and the shared project pill belong to the page shell, not to this ticket.
