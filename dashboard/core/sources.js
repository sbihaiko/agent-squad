// Dashboard core module: page constants and run sources: legend, help, httpSource, followPush. A factory: it takes what it uses from the modules below it, and returns its functions. No DOM here.
// scripts/build_dashboard.py bundles every file of dashboard/core/ in dependency order; nothing loads one file alone.

function sourcesModule({ STATES }) {
  // The legend and the help are data. The page draws a swatch as a card group, so the card CSS paints it, and a Python test
  // holds the legend to the colors the page paints and the help to the controls the page answers.
  // A row with a `glyph` draws it in the swatch: the same glyph the card carries, so the color is never the only cue.
  // The icon of a node kind: one glyph and one class per kind, so a gate and an agent differ by shape as well as by color (#914).
  // The class names the kind's chip color in page.css, and the legend lists the same seven. An unknown kind keeps a glyph and its own class; it never throws.
  const KIND_LEGEND = [
    { kind: "agent", glyph: "\u25c6", label: "Agent: calls a model" },
    { kind: "gate", glyph: "\u2713", label: "Gate: a check that passes or fails" },
    { kind: "action", glyph: "\u25b6", label: "Action: a fixed step, no model" },
    { kind: "wait", glyph: "\u2016", label: "Wait: a person decides" },
    { kind: "loop", glyph: "\u21bb", label: "Loop: repeats until it passes" },
    { kind: "each", glyph: "\u2261", label: "Each: runs for every item" },
    { kind: "dispatch", glyph: "\u21c9", label: "Dispatch: starts children" },
  ];
  // The seven kinds, one row each, in the legend's own "Node kinds" group: the states table stays the one list of card states.
  const KIND_GLYPHS = Object.fromEntries(KIND_LEGEND.map((k) => [k.kind, k.glyph]));
  function kindMark(kind) {
    const k = typeof kind === "string" && kind ? kind : "unknown";
    return { kind: k, glyph: KIND_GLYPHS[k] || "\u25cb", cls: `k-${k}` };
  }
  const LEGEND = [
    ...STATES.filter((x) => x.legend).map((x) => ({ cls: x.cls, glyph: x.glyph, label: x.legend, group: "node" })),   // the card states: from the one STATES table
    { cls: "err", label: "Breaks a graph rule: fix it", group: "node" },   // an editor state (core.validate), never a record event: a run refuses a graph with one
    { cls: "sel", label: "Selected", group: "interaction" },
    { cls: "drop", label: "Target of the link you drag", group: "interaction" },
  ];
  // The hub serves and the dashboard embeds the same run-state table from contrib/squad-goal/scripts/run-states.json. Every page draws these rows;
  // `hub_only` names states the hub derives rather than the run record carrying them.
  const RUN_STATES = /*RUN_STATE_TABLE*/;
  // What a pill prints for a state, on any surface: the hub badges a run with the same words. The run header chip and the legend read this one map.
  const STATE_TEXT = Object.fromEntries(RUN_STATES.map((x) => [x.state, x.text]));
  // The top-bar verdict chip, one row per class it paints (a guard holds this list to the page's CSS and to the chip's class map).
  const LEGEND_CHIPS = RUN_STATES.filter((x) => !x.hub_only).map((x) => ({ cls: x.cls, label: x.label }));
  const HELP = [
    { how: "Drag empty space", what: "Pan the graph.", covers: ["pan"] },
    { how: "Mouse wheel or trackpad pinch", what: "Zoom at the cursor.", covers: ["wheel"] },
    { how: "Drag a node", what: "Move it. Only the layout file changes, never the graph.", covers: ["drag-node"] },
    { how: "Drag from a node's right port to another node", what: "Connect them. A refused link says why in the status line.", covers: ["drag-port"] },
    { how: "Click a link", what: "Select it.", covers: ["select-link"] },
    { how: "Click a group card, a square on it, or a row in the side panel's child table", what: "Select that group or child. Three or more children of one dispatch that finished with one outcome, or run on one harness, draw one group card.", covers: ["select-square"] },
    { how: "Double-click a group card", what: "Show one card per child in its place. Double-click one of those cards to fold the group back.", covers: ["expand-group"] },
    { how: "Tab to a card or a square, then Enter or Space", what: "Enter selects the card or the child, as a click does. Space opens a group card, or folds the group of a card that came from one.", covers: ["key: "] },
    { how: "Double-click a node", what: "Rename it. Every reference follows.", covers: ["double-press"] },
    { how: "Right-click a node or empty space", what: "Rename or remove the node, or add a node at that spot.", covers: ["contextmenu"] },
    { how: "Delete or Backspace", what: "Remove the selected link or node.", covers: ["key:Delete", "key:Backspace"] },
    { how: "Left or Right arrow", what: "Move the replay cursor one event back or forward, while a run record is open.", covers: ["key:ArrowLeft", "key:ArrowRight"] },
    { how: "Enter in the name field", what: "Apply the rename.", covers: ["key:Enter"] },
    { how: "Ctrl or Cmd + Z", what: "Undo. Add Shift to redo.", covers: ["key:z"] },
    { how: "Ctrl or Cmd + Y", what: "Redo.", covers: ["key:y"] },
    { how: "Drag the side panel's edge, or the arrow keys on its grip", what: "Set how wide the panel is — how tall it is when a narrow window makes it a bottom sheet. Home and End go to the narrowest and the widest; a double-click brings the default back.", covers: ["key:Home", "key:End"] },
    { how: "[", what: "Show or hide the side panel.", covers: ["key:["] },
    { how: "?", what: "Show or hide this help.", covers: ["key:?"] },
    { how: "Esc", what: "Close the menu and this help.", covers: ["key:Escape"] },
  ];

  // The source of a run that a loopback server serves: fetch the record and the graph by path. A missing file is null, like the other sources.
  // `fetchFn(path)` returns {ok, text()}; the page passes fetch with no-store, a test passes a map.
  // `streamFn(path, handlers)` opens a push stream and returns {close()}; a source built with it offers `stream`.
  // `paths` names the file each read opens, so a slow or failed read can say what it read.
  function httpSource(fetchFn, name, streamFn) {
    const get = async (p) => { try { const r = await fetchFn(p); return r && r.ok ? await r.text() : null; } catch (e) { return null; } };
    const src = { name, live: true, paths: { record: "run/record.jsonl", graph: "run/graph.json" }, graph: () => get("run/graph.json"), record: () => get("run/record.jsonl") };
    if (typeof streamFn === "function") src.stream = (handlers) => streamFn("run/events", handlers);
    return src;
  }

  // Follow a run by push. `events` is what the page already read; each pushed line is one more event.
  // hooks: changed(events), ended(), fail(reason), status(text, bad). A repeated position is skipped; a skipped one is a gap and is never painted.
  // A stream that errors reconnects by itself (the browser sends Last-Event-ID); after MAX_STREAM_ERRORS errors with no event between, the follow gives up.
  const MAX_STREAM_ERRORS = 3;
  function followPush(src, events, hooks) {
    let list = events.slice(), done = false, errors = 0, handle = null;
    const last = () => (list.length && Number.isInteger(list[list.length - 1].pos) ? list[list.length - 1].pos : 0);
    const stop = () => { done = true; if (handle && handle.close) try { handle.close(); } catch (e) { /* already closed */ } };
    const fail = (why) => { if (done) return; stop(); hooks.fail(why); };
    const handlers = {
      line(text) {
        if (done) return;
        errors = 0;
        let ev;
        try { ev = JSON.parse(text); } catch (e) { return fail("a pushed event was not JSON"); }
        if (!ev || !Number.isInteger(ev.pos)) return fail("a pushed event named no position");
        if (ev.pos <= last()) return;
        if (ev.pos !== last() + 1) return fail(`position gap: expected ${last() + 1}, got ${ev.pos}`);
        list = list.concat([ev]);
        hooks.changed(list);
        if (ev.event === "verdict") { stop(); hooks.ended(); }
      },
      end(reason) {
        if (done) return;
        if (reason === "verdict") { stop(); hooks.ended(); } else fail(`the server ended the stream: ${reason}`);
      },
      error(closed) {
        if (done) return;
        errors++;
        if (closed || errors >= MAX_STREAM_ERRORS) return fail("the stream dropped");
        hooks.status("The stream dropped; reconnecting from the last position.", true);
      },
    };
    try { handle = src.stream(handlers); } catch (e) { fail("the stream did not open"); }
    return { close: stop, handlers };
  }

  return { LEGEND, LEGEND_CHIPS, RUN_STATES, STATE_TEXT, HELP, httpSource, followPush, kindMark, KIND_LEGEND };
}
