// Dashboard core module: undo and redo history (ticket #117). A factory: it takes what it uses from the modules below it, and returns its functions. No DOM here.
// scripts/build_dashboard.py bundles every file of dashboard/core/ in dependency order; nothing loads one file alone.

function historyModule({ clone }) {
  // ---- undo and redo (ticket #79): a pure history of graph-and-layout snapshots; it lives in the page and is never saved ----
  const snapshot = (doc, layout) => ({ doc: clone(doc), layout: clone(layout) });
  const histNew = (limit) => ({ past: [], future: [], limit: limit > 0 ? limit : 100 });
  // `before` is the state an edit is about to change. A new edit clears redo; the oldest step goes when the limit is reached.
  function histRecord(h, before) {
    const past = [...h.past, before];
    while (past.length > h.limit) past.shift();
    return { past, future: [], limit: h.limit };
  }
  function histUndo(h, now) {
    if (!h.past.length) return null;
    const past = h.past.slice(0, -1);
    return { h: { past, future: [...h.future, now], limit: h.limit }, state: h.past[h.past.length - 1] };
  }
  function histRedo(h, now) {
    if (!h.future.length) return null;
    const future = h.future.slice(0, -1);
    return { h: { past: [...h.past, now], future, limit: h.limit }, state: h.future[h.future.length - 1] };
  }

  return { snapshot, histNew, histRecord, histUndo, histRedo };
}
