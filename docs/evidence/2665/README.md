# Renders for #2665: color meaning and empty state on the Now layers

The three screens this ticket changes, rendered by ego-browser at 390 x 844 and at 844 x 390 with device scale
factor 2, on the phone page this tree serves with the live loop and project facts. Each render is compared with the
right half of its "after" image in SPEC 2660, at `docs/spec-assets/mobile-polish/` on commit `ea2b1189`.

| Screen | Portrait | Landscape | After image |
| --- | --- | --- | --- |
| Loops layer | `portrait-loops.png` | `landscape-loops.png` | `now-loops.png` |
| Project sheet | `portrait-profile.png` | `landscape-profile.png` | `now-profile.png` |
| Loop-bugs layer, nothing open | `portrait-bugs.png` | `landscape-bugs.png` | `now-bugs.png` |

## What the review found

1. The off loop's name reads in `ink` (`--fg`), so Fault Red keeps the meaning of a fault. The running loop keeps
   `state-ok` (`--ok`).
2. "All projects" reads in `signal-blue` (`--acc`), not in a healthy-state tone.
3. The Loop-bugs layer with nothing open writes "No open loop-bug in this project." where it draws itself, in
   `ink-muted` (`--mute`), so a redraw keeps the line.
4. A scrolling Now layer ended under the fixed tab bar in landscape, so its last line was unreachable. Every
   scrolling Now layer now takes a bottom inset of one tab bar.

## How the renders were read

Every color was read as the value the browser paints, and each value is the DESIGN.md role that holds it:

| Surface | Painted value | DESIGN.md role | DESIGN.md line |
| --- | --- | --- | --- |
| An off loop's name | `rgb(230, 232, 238)` | `ink` | 12 |
| The on loop's name | `rgb(62, 207, 142)` | `state-ok` | 15 |
| "All projects" | `rgb(90, 169, 255)` | `signal-blue` | 14 |
| The empty Loop-bugs message | `rgb(138, 147, 163)` | `ink-muted` | 13 |

Each pair clears 4.5:1 on its ground: 15.4:1, 9.5:1, 7.1:1 and 6.1:1. No screen cuts text or scrolls sideways at
either metric, and the last line of each scrolling layer stays above the tab bar.

## Fixture

The renders come from a loopback server that serves this tree's phone page with the two live payloads,
`/api/loop` and `/api/hub`, captured from the running hub at render time. The server and the render script live
under `.scratch` and are not part of the product.
