# Charts in landscape: visual review

Ticket #2504, child of SPEC 2498. The renders come from ego-browser at a phone viewport, 390x844 at DPR 3, and
the chart screens at 844x390.

- `render-cfd-landscape.png` — the Cumulative Flow screen in landscape. The plot fills the viewport width, and the
  bands, the axis and the legend carry the desktop chart's markup.
- `render-lead-landscape.png` — the Lead Time screen in landscape. The dots, the rolling mean, its spread, the
  reference tags in the right gutter, the legend and the zoom buttons draw in the measured box.
- `render-loop-landscape.png` — the Loop screen in landscape. The four-column trip scrolls under the fixed header,
  because its text runs past the phone's short side.
- `render-lead-portrait.png` — the Lead Time screen in portrait. Only the rotate hint shows, and the plot is not drawn.

The fixtures stand in for the hub's answers: a CFD payload, a lead payload with five dots and their rolling band, and
the Loop trip view of the mobile profiler suite.
