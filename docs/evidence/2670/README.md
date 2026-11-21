# Renders for #2670: visual sweep of every changed mobile screen

The eleven screens of SPEC 2660, rendered by ego-browser at 390 x 844 and at 844 x 390 with device scale factor 2,
on the phone page this tree serves with the live loop and project facts. Each render is compared with the right half
of its "after" image in SPEC 2660, at `docs/spec-assets/mobile-polish/` on commit `ea2b1189`.

| Screen | Portrait | Landscape | After image |
| --- | --- | --- | --- |
| Now | `portrait-now.png` | `landscape-now.png` | `now.png` |
| Loops layer | `portrait-now-loops.png` | `landscape-now-loops.png` | `now-loops.png` |
| Project sheet | `portrait-now-profile.png` | `landscape-now-profile.png` | `now-profile.png` |
| Loop-bugs layer | `portrait-now-bugs.png` | `landscape-now-bugs.png` | `now-bugs.png` |
| Work | `portrait-work.png` | `landscape-work.png` | `work.png` |
| Work search | `portrait-work-search.png` | `landscape-work-search.png` | `work-search.png` |
| Runs | `portrait-runs.png` | `landscape-runs.png` | `runs.png` |
| Spend | `portrait-spend.png` | `landscape-spend.png` | `spend.png` |
| Lead time | `portrait-land-lead.png` | `landscape-land-lead.png` | `land-lead.png` |
| Cumulative flow | `portrait-land-cfd.png` | `landscape-land-cfd.png` | `land-cfd.png` |
| Loop profiler | `portrait-land-profiler.png` | `landscape-land-profiler.png` | `land-profiler.png` |

## What the sweep found

One defect, on the landscape loop profiler. The profiler's body kept the tab bar's bottom inset of 64 px, but a
chart screen draws no tab bar. The chart body also hid its overflow, so it clipped the trip's bar and its column
labels. Both sat below the fold with nothing to scroll. The body now takes only the safe-area inset on a chart
screen, and its chart body never clips.

No other screen showed an open defect. Hierarchy, alignment, spacing, wrapping, overlap, cut text and contrast
match the after image at both metrics. The Now screen carries more decision rows than the after image, and each
row wraps whole.

## The red test

The page test `test_the_loop_chart_keeps_no_tab_bar_inset_and_never_clips_its_trip` in
`tests/test_hub_mobile_chart_landscape.py` failed before the fix:

```
>       assert chart.get("padding-bottom") == "env(safe-area-inset-bottom)", chart
E       AssertionError: {'display': 'flex', 'flex-direction': 'column', 'overflow': 'hidden', 'overflow-x': 'hidden', ...}
E       assert None == 'env(safe-area-inset-bottom)'
FAILED tests/test_hub_mobile_chart_landscape.py::test_the_loop_chart_keeps_no_tab_bar_inset_and_never_clips_its_trip
```

It passes after the fix, with the rest of its file (22 tests).

## States that live data does not show

A fixture answered the payloads with the empty and the long-content shapes. The `state-` renders show four of them:

| State | Portrait | Landscape |
| --- | --- | --- |
| Empty Runs: "No runs are drawn." | `state-portrait-empty-runs.png` | `state-landscape-empty-runs.png` |
| Long decision rows on Now | `state-portrait-long-now.png` | `state-landscape-long-now.png` |
| Refused start, three loop-bugs open | `state-portrait-long2-loop.png` | `state-landscape-long2-loop.png` |
| Long project slug in the sheet | `state-portrait-long2-profile.png` | `state-landscape-long2-profile.png` |

The empty Runs message sits in its own line, and no screen leaves a blank area that hides a control. Long rows
wrap without a cut. The refused start names its cause, and its Start button stays visible with its own mark. A long
project slug wraps inside the sheet row and keeps the repo line under it.

## Fixture

The renders come from a loopback server that serves this tree's phone page with the live payloads from the running
hub. The fixture maps an API path to the body it answers, so the sweep could render the empty, long-content and
alert shapes. The server, the fixture and the render script live under `.scratch` and are not part of the product.
