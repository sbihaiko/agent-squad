// The page's push stream. One EventSource; each subject's newer version paints through its own hook: runs through
// poll(), a chart through squadChart. A dropped stream is named through squadStreamLost and the page keeps what it
// drew; the reconnect calls squadStreamBack. A reconnect names the versions already painted, so no version repeats.
(() => {
  if (!window.EventSource) return;
  const held = {};
  const paint = {runs: () => poll(), cfd: () => window.squadChart("cfd"), lead: () => window.squadChart("lead")};
  let lost = false;
  const open = () => {
    const q = Object.entries(held).map(([k, v]) => `${k}:${v}`).join(",");
    const es = new EventSource(q ? `${window.SQUAD_HUB_STREAM}?held=${q}` : window.SQUAD_HUB_STREAM);
    window.squadStreamLive = false;
    es.onopen = () => {
      window.squadStreamLive = true;
      if (lost) { lost = false; window.squadStreamBack(); }
    };
    for (const [name, repaint] of Object.entries(paint)) {
      es.addEventListener(name, e => {
        const v = JSON.parse(e.data).v;
        if (v <= (held[name] || 0)) return;
        held[name] = v;
        repaint();
      });
    }
    es.onerror = () => {
      window.squadStreamLive = false;
      lost = true;
      es.close();
      window.squadStreamLost();
      setTimeout(open, 3000);
    };
  };
  open();
})();
