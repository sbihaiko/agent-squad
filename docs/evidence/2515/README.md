# #2515 Settings without the Loop theme: ego-browser check

A throwaway hub served a spare loopback port from this branch, with temporary home and state directories. Its
`loop-audit.jsonl` held two change lines, so the Loop tab drew a change list with entries.

Each render set the device metrics in the same ego-browser process that took the screenshot, in one private task space.

- `settings-1440.png`: 1440 px. The theme list reads Spend & limits, Subscriptions, Models & chains, Communication and
  Access. It holds no Loop theme, and the phone select carries the same five themes.
- `settings-1000.png`: 1000 px. The theme list keeps one line per theme, with its state line under the name. The page
  has no sideways scroll.
- `loop-1440.png`: 1440 px. The Loop tab keeps the Now tiles and the change list under the profiler. Three tiles share
  one row, and each change line stays on one line.
- `loop-1000.png`: 1000 px. The three tiles keep equal widths, the note and the Changes disclosure stay aligned on one
  left edge, and every change line fits its width.

Measured in the page, from the rendered boxes:

```
1440 px  innerWidth 1440  scrollWidth 1440  change line 1128 px  list entries 1 line each
1000 px  innerWidth 1000  scrollWidth 1000  change line  688 px  list entries 1 line each
```
