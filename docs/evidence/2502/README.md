# Renders for #2502: Runs and Loop control on the phone

The screens this ticket changes, rendered by ego-browser at 390 x 844 with device scale factor 3, on the phone page
served by the hub. The plain Renders come from the page built at this tree's head. The `Before` links are the mockups in
#2498.

| Screen | Render | Mockup |
| --- | --- | --- |
| Runs list | `render-runs.png` | `new-04-runs.png` |
| Run detail, live run | `render-run-live.png` | `new-07-run-live.png` |
| Run detail, needs a decision | `render-run-needs-decision.png` | `new-06-run-needs-decision.png` |
| Loops list | `render-loops.png` | SPEC 2498, Loops |
| Loop control | `render-loop.png` | `new-10-loop.png` |

## What the review changed

1. Each detail screen draws one back control, at the top of the screen. The footers hold no back and a screen with no
   action draws no action row.
2. The run's action row names its one primary by the screen it opens, so "Desktop dashboard" becomes "Overview" over the
   dashboard and back. The dots menu left the row: it held the same action the primary holds.
3. An ended run no longer draws the rule of a pause it does not offer. Its row holds the dashboard alone.

## What the review kept on purpose

The pause control reads "Pause the run" and the caption under it states both limits (a pause lets a running call finish,
a resume does not restart an ended run). A guard asserts this text (#831), so the render keeps it.

## Fixture

The renders come from a loopback server with neutral facts: one live run, one run that needs a decision, one ended run,
two projects, and one open loop. It runs from `.scratch/serve2502.py` and is not part of the product.
