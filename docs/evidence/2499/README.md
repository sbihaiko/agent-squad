# #2499 phone navigation: the visual review

The renders were taken through ego-browser in one private task space, on the phone page served from a loopback server
with neutral fixtures. Each screen was entered at its own address, so the render shows what a saved address opens.

| Render | Address | Viewport |
| --- | --- | --- |
| `render-now.png` | `#/now` | 390 × 844, DPR 3 |
| `render-work.png` | `#/work` | 390 × 844, DPR 3 |
| `render-specs.png` | `#/specs` | 390 × 844, DPR 3 |
| `render-runs.png` | `#/runs` | 390 × 844, DPR 3 |
| `render-spend.png` | `#/spend` | 390 × 844, DPR 3 |
| `render-spend-landscape.png` | `#/spend` | 844 × 390, DPR 3 |

The comparison used the mockups in #2498: `new-01-now.png`, `new-02-work.png`, `new-03-specs.png`, `new-04-runs.png`
and `new-05-spend.png`.

## What the review found

The five tabs draw in the order the route table names them, with the current tab marked, and no sixth tab. Each tab
draws its own screen, and the Spend tab draws the cost screen as its root.

The review found no defect in what this ticket changes. The mockups show the end state of SPEC 2498: the Spend summary
line and the model pie arrive with the Spend slice, and the decisions and Slack row on Now arrive with the decisions
slice. Those parts are out of this ticket's scope, so the renders do not match the mockups there.
