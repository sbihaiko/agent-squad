// The page's push stream. One EventSource; a runs event repaints through the page's own poll().
// A reconnect names the versions already painted, so the server sends no version twice.
(() => {
  if (!window.EventSource) return;
  const held = {};
  const open = () => {
    const q = Object.entries(held).map(([k, v]) => `${k}:${v}`).join(",");
    const es = new EventSource(q ? `${window.SQUAD_HUB_STREAM}?held=${q}` : window.SQUAD_HUB_STREAM);
    es.addEventListener("runs", e => {
      const v = JSON.parse(e.data).v;
      if (v <= (held.runs || 0)) return;
      held.runs = v;
      poll();
    });
    es.onerror = () => { es.close(); setTimeout(open, 3000); };
  };
  open();
})();
