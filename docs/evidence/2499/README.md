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
| `render-now-lead-landscape.png` | `#/now/lead` | 844 × 390, DPR 3 |

The comparison used the mockups in #2498: `new-01-now.png`, `new-02-work.png`, `new-03-specs.png`, `new-04-runs.png`
and `new-05-spend.png`.

## What the review found

The five tabs draw in the order the route table names them, with the current tab marked, and no sixth tab. Each tab
draws its own screen, and the Spend tab draws the cost screen as its root. The Now lead chart opens from its saved
address and fills the landscape width.

The spec's visual-review item stays **open**. The mockups are the target of SPEC 2498, and they show parts that this
ticket does not draw. Each missing part belongs to another slice of the spec:

| Missing part | Mockup | Slice that owns it |
| --- | --- | --- |
| Spend summary line and the model pie | `new-05-spend.png` | #2503, Spend tab reuses the desktop panel |
| Now decisions row ("Needs you") and the Slack link | `new-01-now.png` | #2500, Needs you and the Slack link on Now |
| Lead chart summary line, control row, reference values and closed-date axis | `new-11-lead-landscape.png` | #2504, Charts in landscape |

The renders below show this ticket's screens as they stand. They do not match the mockups at those parts, so this
ticket keeps the visual-review item open until its own slices land. The lead render was taken through ego-browser on
the page served from a loopback server, at 844 × 390 with DPR 3, on a saved address.
