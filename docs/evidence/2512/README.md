# #2512 Spend & limits: the Kanban group, ego-browser check

A throwaway server served a spare loopback port with temporary directories, never the live hub and never the live
profile. Its board cache carried one card count per column, so every column of the mockup had a row.

The Settings page was opened in ego-browser, in one private task space per goal. Each render sets the device metrics in
the same process that takes the screenshot, so the emulation holds for the capture.

- `kanban-1440.png`: 1440 px. The group reads as the spec #2427 mockup: the four headers Column, Cards now, Card limit
  and What the limit stops; a bar and "N of M" on Doing and Reviewing; "Kanban · cards · this board" with the state at
  the right; the footer names `docs/squad/defaults.yaml`.
- `kanban-1000.png`: 1000 px. The four columns stay aligned, no label wraps into its field, and no text overlaps. A
  Settings row whose label and control cannot share the narrow column moves its control under the label.

Measured in the page at 1000 px:

```
innerWidth 1000   documentElement.scrollWidth 1000   # no sideways scroll
```

There is no cell whose content is wider than its box, and the 2x close-up of the group shows each bar and its count on
one line with the placeholder of an unlimited column fully drawn.
