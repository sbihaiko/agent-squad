---
name: agent-squad
description: The web surfaces of the squad loop, the hub, its phone page and the run dashboard, drawn as one calm instrument panel.
colors:
  ground: "#0f1115"
  panel: "#161a21"
  card: "#1c212b"
  rule: "#2a303b"
  rule-strong: "#353d4b"
  edge: "#667085"
  hover: "#2e3846"
  ink: "#e6e8ee"
  ink-muted: "#8a93a3"
  signal-blue: "#5aa9ff"
  state-ok: "#3ecf8e"
  state-warn: "#f5b041"
  state-wait: "#e8d44d"
  state-err: "#ff6b6b"
  state-run: "#2dd4bf"
  state-none: "#b79cf6"
  state-drop: "#c678dd"
  on-tone: "#0b1a12"
  tint-ok: "#12302a"
  tint-warn: "#261e0f"
  tint-err: "#2a1212"
  tint-acc: "#0f1d2c"
  series-1: "#14b8c0"
  series-2: "#d88cd0"
  series-3: "#2cbcd8"
  series-4: "#b99126"
  series-5: "#ff8a5c"
  series-6: "#9ccc65"
  series-7: "#f48fb1"
  series-8: "#90a4ae"
typography:
  display:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "21px"
    fontWeight: 600
    lineHeight: 1.55
  headline:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.55
  field:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "16px"
    fontWeight: 400
  label:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    letterSpacing: "0.06em"
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "13px"
    fontWeight: 400
rounded:
  sm: "4px"
  md: "6px"
  lg: "8px"
  xl: "14px"
  full: "9999px"
spacing:
  "1": "4px"
  "2": "8px"
  "3": "12px"
  "4": "16px"
  "6": "24px"
components:
  tab:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "4px 12px"
    height: "32px"
  tab-active:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.signal-blue}"
  button:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "0 12px"
    height: "32px"
  button-hover:
    backgroundColor: "{colors.hover}"
  button-commit:
    backgroundColor: "{colors.state-warn}"
    textColor: "{colors.on-tone}"
  input:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.ink}"
    typography: "{typography.field}"
    rounded: "{rounded.sm}"
    padding: "0 6px"
    height: "32px"
  chip:
    textColor: "{colors.ink-muted}"
    typography: "{typography.label}"
    rounded: "{rounded.lg}"
    padding: "0 7px"
    height: "20px"
  card:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "8px 10px"
  dialog:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "16px"
---

# Design System: agent-squad

## Overview

**Creative North Star: "The Flight Deck"**

The hub, its phone page and the run dashboard read like the instrument panel of an aircraft in cruise. The operator looks to confirm that nothing needs a hand, and a look must settle that in seconds. Each mark carries a reading: a gauge, a state color, a count, an age. Nothing on the page is decoration.

The panel is dark and flat. Depth comes from three tonal steps, ground, panel and card, and from thin rules between them. Color is rare at rest, so a color on the page is news: green is done or healthy, amber needs a look, red is a fault. The one blue is the signal of interaction: the active tab, a link, the focus ring.

Components are quiet and exact. A control is a thin outline on a dark surface, sized to a fixed row, with no shadow at rest. Density is high on the desktop and relaxes on the phone, where every control grows to a thumb target.

**Key Characteristics:**

- Dark, flat surfaces in three tonal steps; shadows only under a layer that floats.
- Color reports state; it never decorates.
- One system font for the interface and one monospace face for code, paths and data.
- Fixed control rows: 32 px on the desktop, 44 px on the phone.
- Tabular numerals in every table, tile and count.
- Dark only: one dark theme on every surface, and no light variant.

## Colors

A cool near-black ground with one interaction blue and a closed set of state tones that never share a hue.

### Primary

- **Signal Blue** (`signal-blue`): the active tab, a link, the focus ring, the selected card's border and the default chart bar. It is the color of "this is where you are" and "this responds to you".

### Neutral

- **Hangar Black** (`ground`): the page ground and the fill of every input field.
- **Console Slate** (`panel`): the header, the sidebar, tabs, buttons and dialogs.
- **Instrument Slate** (`card`): a card, a tile and a row that sits on a panel.
- **Hairline** (`rule`): the decorative rule between rows and the resting border of a card or a field. It may sit below 3:1.
- **Strong Hairline** (`rule-strong`): the border of a hovered row or button.
- **Edge** (`edge`): every boundary that carries meaning, at 3:1 on panel and card: a selected or hovered card, a dialog border, a state mark, a switch track.
- **Hover Slate** (`hover`): the fill of a hovered button.
- **Readout White** (`ink`): body text, values and headings.
- **Dial Gray** (`ink-muted`): labels, captions, ages and secondary facts.

### State tones

- **Go Green** (`state-ok`): done, healthy, under the limit, the loop switch on.
- **Caution Amber** (`state-warn`): needs a look, a staged unsaved edit, a value above its default.
- **Hold Yellow** (`state-wait`): waiting in a queue.
- **Fault Red** (`state-err`): a failure, a refusal, a value at its limit.
- **Live Teal** (`state-run`): work in progress now.
- **Idle Lilac** (`state-none`): no state to report.
- **Dropped Violet** (`state-drop`): abandoned or dropped work.

Each tone has a dark tint fill (`tint-ok`, `tint-warn`, `tint-err`, `tint-acc`) and a tint border for a toned surface. Text on a solid tone fill uses `on-tone`.

### Series

Eight chart series (`series-1` to `series-8`) color board columns and lanes in charts. A column takes its series from the board definition, never from a second list.

### Named Rules

**The Color Is News Rule.** A surface at rest is neutral. A color appears only when it reports a state, and a reader can name the state from the color alone.

**The Separate Hues Rule.** The state tones, the series and the accent are one set. No two share a CIELAB hue within 12 degrees at a comparable lightness, and the accent is never the first series.

**The Edge Contrast Rule.** A boundary that carries meaning uses `edge` at 3:1 or more. A decorative rule uses `rule` and may sit lower.

## Typography

**Interface Font:** system-ui (with -apple-system, Segoe UI, sans-serif)
**Data Font:** ui-monospace (with SFMono-Regular, Menlo, monospace)

**Character:** The platform's own face, so the panel reads as a native instrument and loads nothing. The monospace face appears only for code, file paths, commands and identifiers.

### Hierarchy

- **Display** (600, 21px): the project name in the header. One per page.
- **Headline** (600, 17px): a panel title and the heading of a sheet or a dialog.
- **Body** (400, 15px, line height 1.55): rows, values, settings labels and prose.
- **Field** (400, 16px): the text inside an input or a select. At 16 px the phone does not zoom on focus.
- **Label** (600, 13px, letter spacing 0.06em, uppercase): a block heading such as Now or Settings. Without uppercase, 13 px is the caption size for ages, units and notes.
- **Mono** (400, 13px): code spans, paths and commands.

### Named Rules

**The Tabular Numerals Rule.** Every table, tile, count and gauge readout uses tabular numerals, so a changing value never shifts its column.

**The Five Sizes Rule.** The scale has five sizes: 13, 15, 16, 17 and 21 px. A new element takes one of them.

## Layout

The desktop page is a header bar over two columns: a collapsible sidebar on the left (Monitor, Usage, Spend, Live work) and the active tab's pane on the right. A pane may split again: Settings puts a 200 px theme list beside the panel of the selected theme, inside a 1120 px maximum width.

Spacing steps are 4, 8, 12, 16 and 24 px. A card's gaps are 10 px across and 8 px down on the desktop, and 12 px and 10 px on the phone.

At 600 px and below, the page becomes one column. The sidebar stacks above the pane, the tabs take their own scrolling row, a theme list becomes a select, and every control grows to 44 px. A panel that holds a table reacts to its own width through a container query, not to the viewport: a narrow column moves the secondary cell to a line of its own instead of clipping a label.

## Elevation & Depth

The system is flat and layered by tone. Ground, panel and card are three steps of lightness, and a thin rule separates them. A surface at rest casts no shadow.

A shadow appears only under a layer that floats above the page: a dialog or a popover, over a scrim.

### Shadow Vocabulary

- **Soft** (`--shadow-soft`): a small floating layer such as a popover.
- **Deep** (`--shadow-deep`): a modal dialog over the scrim.

Inset strokes (`box-shadow: inset 3px 0 0 var(--acc)`) mark a row's state at its edge. They are a stroke, not depth.

### Named Rules

**The Flat At Rest Rule.** A card, a row or a button at rest has no shadow. Depth only marks a layer that floats.

## Shapes

Corners are small and functional. A field uses 4 px, a button and a tab use 6 px, and a card, a chip and a dialog use 8 px. A large container may use 14 px. A full pill is only for a switch and a small count badge. Every border is 1 px; 2 px and 3 px strokes mark state, never structure.

## Components

### Buttons

Quiet outlines that answer the hand without shouting.

- **Shape:** gently rounded (6px), on the fixed control row (32px desktop, 44px phone).
- **Default:** panel fill, a 1 px `rule` border and `ink` text.
- **Hover / Focus:** the fill moves to `hover` and the border to `rule-strong`. Focus draws a 2 px Signal Blue ring at a 2 px offset.
- **Commit:** a button that writes a staged change turns Caution Amber with `on-tone` text while an unsaved change exists. It stays neutral when nothing waits.
- **Disabled:** reduced opacity and a default cursor.

### Tabs

- **Style:** outlined buttons in a row, panel fill, 6 px corners.
- **Active:** the text and the border turn Signal Blue. The fill does not change.
- **Phone:** the row takes the full width and scrolls on its own line.

### Chips

- **Style:** a 1 px `rule` outline, `ink-muted` text, 13 px semibold, 8 px corners, 20 px tall.
- **State:** a chip that reports a state takes the tone's color for its text and border, and a tint fill when it must stand out on a card.

### Cards / Containers

- **Corner Style:** 8px.
- **Background:** `card` on a `panel` ground.
- **Shadow Strategy:** none; see Elevation & Depth.
- **Border:** a 1 px `rule` at rest. Hover and selection move it to `edge` or Signal Blue.
- **Internal Padding:** 8 px down and 10 px across.

### Inputs / Fields

- **Style:** `ground` fill, a 1 px `rule` border, 4 px corners, 16 px text, and dark native controls (`color-scheme: dark`).
- **Focus:** a 2 px Signal Blue ring.
- **Staged:** a field whose value differs from the default or from the saved value takes Caution Amber text and a tint border.
- **Error:** a refused field takes a Fault Red border and names the problem in a status line below.

### Navigation

- **Header:** the project name as a menu button, a project switch and the tabs: Kanban, Runs, Flow, Loop and Settings.
- **Sidebar:** collapsible panels with a disclosure triangle and a one-line summary when closed.

### Monitor Gauges

The signature component. CPU, RAM and GPU are drawn as small dial gauges with a needle and a tabular readout under each. A reading at or above 95% paints that one cell Fault Red; the rest of the strip keeps its color.

### Settings Theme

Every Settings theme draws the same two blocks: a Now block of read-only tiles and a Settings block of editable rows (a label on the left, a control on the right). One Save bar at the foot shows the diff before a write.

## Do's and Don'ts

### Do:

- **Do** take every color from `web-tokens.css` through its custom property. Hub, phone and dashboard share that one file.
- **Do** keep body text at 4.5:1 or more, and every meaningful boundary at 3:1 or more.
- **Do** size every control to the row: 32 px on the desktop and 44 px at 600 px and below.
- **Do** use tabular numerals wherever a number can change.
- **Do** let a narrow panel move a secondary cell to its own line instead of clipping a label, a value or a placeholder.
- **Do** honor `prefers-reduced-motion`: a transition stops, and the state change stays.

### Don't:

- **Don't** write a hex literal in a surface's CSS. A new value becomes a named token first.
- **Don't** put a shadow under a card, a row or a button at rest.
- **Don't** use a state tone for decoration or for emphasis that is not a state.
- **Don't** add a light theme. The system is dark only (`color-scheme: dark`), on every surface.
- **Don't** add a sixth type size or a second interface font.
