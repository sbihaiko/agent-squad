# #2512 Spend & limits: the Kanban group, ego-browser check

A throwaway server served a spare loopback port with temporary directories, never the live hub and never the live
profile. Its board cache carried one card count per column, so every column of the mockup had a row.

The Settings page was opened in ego-browser, in one private task space per goal. Each render sets the device metrics in
the same process that takes the screenshot, so the emulation holds for the capture.

- `kanban-1440.png`: 1440 px. The group reads as the spec #2427 mockup: the four headers Column, Cards now, Card limit
  and What the limit stops; a bar and "N of M" on Doing and Reviewing; "Kanban · cards · this board" with the state at
  the right; the footer names `docs/squad/defaults.yaml`.
- `kanban-1000.png`: 1000 px. The four columns stay aligned, every label stays on one line, no text overlaps, and no
  cell cuts its content. The group holds its own table layout there; no Settings row rule restacks the other rows.

Measured in the page, from the rendered boxes:

```
1440 px  innerWidth 1440  scrollWidth 1440  table 878 px  field x 623  labels 1 line
1000 px  innerWidth 1000  scrollWidth 1000  table 726 px  field x 557  labels 1 line
```

At both widths there is no sideways scroll, no cell whose content is wider than its box, and no pair of cells that
overlaps. Each bar and its count share one line, and an unlimited column draws its placeholder in full.
