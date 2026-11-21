# Charts in landscape: visual review

Ticket #2504, child of SPEC 2498. Each render comes from ego-browser with a CDP device-metrics override at a phone
viewport, DPR 3: the chart screens at 844x390 and the same screens in portrait at 390x844. ego-browser writes the
screenshot in CSS pixels, so a landscape image is 844x390 wide.

The page is `hub.MOBILE_PAGE`, and a loopback server answers the `/api/*` routes with the fixtures of the browser
suite. The fixtures stand in for the hub's answers: a three-day Cumulative Flow with two bands, a Lead Time view of
twenty issues with its rolling band, and the Loop trip of the mobile profiler suite.

## Landscape screens

- `render-loop-landscape.png` — the Loop screen at 844x390. It draws the desktop chart's two lanes, not the phone's
  stacked lanes: the Tokens and Machine gutters, the axis ticks, the per-column means, the trip bar and the column
  labels. The probe read the two lane titles `Tokens` and `Machine` and no `.ptlane` element. This matches the
  two-lane chart of the mockup `new-14-loop-landscape.png`; the payload's four columns take the place of the mockup's
  five.
- `render-cfd-landscape.png` — the Cumulative Flow screen at 844x390. The drawing fills the measured box (viewBox
  `0 0 844 183` in a box of 844x183), and each band carries the color role the payload sends. This matches the mockup
  `new-13-cfd-landscape.png`, with two bands where the mockup has nine.
- `render-lead-landscape.png` — the Lead Time screen at 844x390. The drawing fills the measured box (viewBox
  `0 0 820 83` in a box of 820x83). The reference values stand in the legend alone, below the plot, as the mockup
  `new-11-lead-landscape.png` places them in a right gutter that the phone does not have.

## Portrait screens

- `render-loop-portrait.png`, `render-lead-portrait.png`, `render-cfd-portrait.png` — each screen at 390x844. The
  screen shows the rotate hint alone ("Rotate to view"), and no tab bar. The probe read the chart rule on, the hint
  visible, the tab bar hidden and the plot at zero width. The mockup `new-11b-lead-portrait.png` shows the same hint.

## Defects the comparison found and fixed

1. The legend takes its row from the plot's flex box, so the first measurement was the box before that row. The Lead
   drawing was made at a height of 131 inside a box of 83, and the Cumulative Flow at a height of 210 inside a box of
   183. The browser then scaled the drawing down and centred it, which left empty margins. Each draw now measures the
   plot again after it places the legend, and draws once more when the box changed.
2. The first and the last date label were centred on the axis, so the right edge of the box cut the last one
   ("Nov 20" read as "Nov"). The end labels now turn inward: the first starts at the axis and the last ends there.

The chart rules run under node alone (ADR-0137), and `tests/test_hub_mobile_chart_landscape.py` proves the frame, the
end labels, the payload's colors and the second measurement. `tests/test_hub_mobile_chart_browser.py` proves the same
behavior in a real browser, including that each viewBox equals its CSS box.
