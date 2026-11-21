// The page's push stream. One EventSource per page hands each subject to its hook: runs through the desktop's poll(), the
// charts through squadChart, the board to squadStream listeners. A dropped stream is named through squadStreamLost and the
// page keeps what it drew; the reconnect calls squadStreamBack. A reconnect names the versions the page holds, so the
// server sends nothing twice.
(() => {
  if (!window.EventSource || !window.SQUAD_HUB_STREAM) return;
  const SUBJECTS = ["runs", "cfd", "lead", "board", "ping"];
  const held = {}, listeners = {}, wanted = new Set();
  let es = null, lastAt = 0, lost = false;
  const url = () => {
    const parts = [];
    const h = Object.entries(held).map(([k, v]) => `${k}:${v}`);
    if (h.length) parts.push(`held=${h.join(",")}`);
    if (wanted.size) parts.push(`want=${[...wanted].join(",")}`);
    return parts.length ? `${window.SQUAD_HUB_STREAM}?${parts.join("&")}` : window.SQUAD_HUB_STREAM;
  };
  const emit = (name, data) => { for (const fn of listeners[name] || []) { try { fn(data); } catch (e) { console.error(e); } } };
  const onEvent = (name, d) => {
    lastAt = Date.now();
    if (name === "ping") return;
    if (d.v <= (held[name] || 0)) return;
    held[name] = d.v;
    if (name === "runs") { if (typeof poll === "function") poll(); }
    else if (name === "cfd" || name === "lead") { if (typeof window.squadChart === "function") window.squadChart(name); }
    else emit(name, d.data);
  };
  const open = () => {
    const cur = new EventSource(url());
    es = cur;
    window.squadStreamLive = false;
    cur.onopen = () => {
      if (cur !== es) return;
      window.squadStreamLive = true;
      if (lost) { lost = false; if (typeof window.squadStreamBack === "function") window.squadStreamBack(); }
    };
    for (const name of SUBJECTS) cur.addEventListener(name, e => onEvent(name, JSON.parse(e.data)));
    cur.onerror = () => {
      cur.close();
      if (cur !== es) return;
      window.squadStreamLive = false;
      lost = true;
      if (typeof window.squadStreamLost === "function") window.squadStreamLost();
      setTimeout(() => { if (cur === es) open(); }, 3000);
    };
  };
  const reopen = () => { if (es) es.close(); open(); };
  window.squadStream = {
    on(name, fn) {
      (listeners[name] = listeners[name] || []).push(fn);
      if (!wanted.has(name)) { wanted.add(name); delete held[name]; reopen(); }
    },
    live: () => es !== null && Date.now() - lastAt < 20000,
  };
  open();
})();
