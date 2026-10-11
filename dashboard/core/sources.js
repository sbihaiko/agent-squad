// Dashboard core module: page constants and run sources: legend, help, httpSource, followPush. A factory: it takes what it uses from the modules below it, and returns its functions. No DOM here.
// scripts/build_dashboard.py bundles every file of dashboard/core/ in dependency order; nothing loads one file alone.

function sourcesModule({ STATES }) {
  // The legend and the help are data. The page draws a swatch as a card group, so the card CSS paints it, and a Python test
  // holds the legend to the colors the page paints and the help to the controls the page answers.
  // A row with a `glyph` draws it in the swatch: the same glyph the card carries, so the color is never the only cue.
  const LEGEND = [
    ...STATES.filter((x) => x.legend).map((x) => ({ cls: x.cls, glyph: x.glyph, label: x.legend, group: "node" })),   // the card states: from the one STATES table
    { cls: "err", label: "Rule error on the node", group: "node" },
    { cls: "sel", label: "Selected", group: "interaction" },
    { cls: "drop", label: "Target of the link you drag", group: "interaction" },
  ];
  // One run-state vocabulary: the top-bar verdict chip, its legend and the hub's run badges (contrib/squad-goal/scripts/hub.py RUN_STATES) name and
  // paint a state alike; a Python test holds the two tables equal. `color` is a palette variable; a `hub_only` state is one the hub derives, so the chip never shows it.
  const RUN_STATES = [
    { state: "completed", cls: "v-ok", label: "Run completed", color: "ok" },
    { state: "verified", cls: null, label: "Failed or unverified run, verified by hand", color: "ok", hub_only: true },
    { state: "unverified", cls: null, label: "Work landed, the final gate had no command", color: "ok", hub_only: true },
    { state: "failed", cls: "v-bad", label: "Run failed", color: "err" },
    { state: "needs-human", cls: "v-human", label: "Run needs a person", color: "warn" },
    { state: "running", cls: "v-live", label: "Run in progress", color: "run" },
    { state: "starting", cls: null, label: "Run starting", color: "run", hub_only: true },
    { state: "stalled", cls: "v-wait", label: "Run stalled", color: "warn" },
    { state: "no verdict yet", cls: "v-none", label: "No verdict yet", color: "none" },
  ];
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
    { how: "[", what: "Show or hide the side panel.", covers: ["key:["] },
    { how: "?", what: "Show or hide this help.", covers: ["key:?"] },
    { how: "Esc", what: "Close the menu and this help.", covers: ["key:Escape"] },
  ];

  // The source of a run that a loopback server serves: fetch the record and the graph by path. A missing file is null, like the other sources.
  // `fetchFn(path)` returns {ok, text()}; the page passes fetch with no-store, a test passes a map.
  // `streamFn(path, handlers)` opens a push stream and returns {close()}; a source built with it offers `stream`.
  function httpSource(fetchFn, name, streamFn) {
    const get = async (p) => { try { const r = await fetchFn(p); return r && r.ok ? await r.text() : null; } catch (e) { return null; } };
    const src = { name, live: true, graph: () => get("run/graph.json"), record: () => get("run/record.jsonl") };
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

  return { LEGEND, LEGEND_CHIPS, RUN_STATES, HELP, httpSource, followPush };
}
