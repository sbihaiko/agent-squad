// hub-overlay.js (#898): the session overlay. One source: hub.py serves it on the profile hub, the mobile page and a run
// dashboard reached through the hub; hubmux.py on the index. The button opens an overlay on the project's session (the one
// whose working directory is the profile's checkout), read through the Terminals routes and polled as the Terminals tab
// polls. It starts no session. A page names its session routes in window.SQUAD_OV_SOURCES: never a quoted API path here,
// because the mux gives every quoted one the profile prefix. The rules come first, as pure functions node runs; then the DOM.
(function () {
const SQ = {};
SQ.MIN_W = 360; SQ.MIN_BOARD = 480; SQ.MAX_FRAC = 0.7; SQ.STEP = 24; SQ.DEFAULT_W = 440; SQ.SHEET_W = 420; SQ.PHONE_W = 600;
SQ.IDLE_MS = 3000; SQ.LENS_R = 5; SQ.REACH = 240; SQ.POLL_MS=3000; SQ.TEXT_MAX = 4000;
SQ.SEND_SETTING = "SQUAD_TAILNET_TERMINALS_SEND";
// The feed reads like a terminal: the latest turns stand, older ones fold into one line, and a long answer is clamped with
// "Show more" (owner, 2026-11-01; the terminal reads clearer because it summarizes age and length, not because it says less).
SQ.KEEP = 12;
SQ.splitEarlier = (turns, keep = SQ.KEEP) => turns.length <= keep ? { earlier: [], shown: turns }
  : { earlier: turns.slice(0, turns.length - keep), shown: turns.slice(turns.length - keep) };

// The docked width: at least 360 px, at most 70% of the viewport, and the board keeps 480 px.
SQ.clampWidth = (w, vw) => {
  const max = Math.max(SQ.MIN_W, Math.min(Math.floor(vw * SQ.MAX_FRAC), vw - SQ.MIN_BOARD));
  return Math.round(Math.min(max, Math.max(SQ.MIN_W, w)));
};
// The handle sits on the panel's left edge: the left arrow widens the panel, the right arrow narrows it.
SQ.keyWidth = (w, key, vw) => SQ.clampWidth(w + (key === "ArrowLeft" ? SQ.STEP : key === "ArrowRight" ? -SQ.STEP : 0), vw);
SQ.canDock = vw => vw >= SQ.MIN_W + SQ.MIN_BOARD;
SQ.layout = (vw, docked) => vw <= SQ.PHONE_W ? "phone" : docked && SQ.canDock(vw) ? "docked" : "float";
// A ticket or run sheet stacks left of the docked panel while the board keeps 480 px; else it opens over the panel.
SQ.sheetMode = (vw, dock, sheet) => vw - dock - sheet >= SQ.MIN_BOARD ? "stack" : "over";

// The lens: a damped spring, one step per frame, toward a target clamped inside the lens.
SQ.springStep = (s, t, dt) => {
  const k = 170, c = 2 * 0.85 * Math.sqrt(k), h = Math.min(Math.max(dt, 0), 1 / 30);
  const vx = s.vx + (k * (t.x - s.x) - c * s.vx) * h, vy = s.vy + (k * (t.y - s.y) - c * s.vy) * h;
  return { x: s.x + vx * h, y: s.y + vy * h, vx, vy };
};
SQ.clampToLens = (x, y) => {
  const d = Math.hypot(x, y);
  return d <= SQ.LENS_R ? { x, y } : { x: x / d * SQ.LENS_R, y: y / d * SQ.LENS_R };
};
SQ.lensTarget = (p, c, now, lastMove) => {
  if (!p || now - lastMove > SQ.IDLE_MS) return { x: 0, y: 0 };
  const dx = p.x - c.x, dy = p.y - c.y, d = Math.hypot(dx, dy);
  if (!d) return { x: 0, y: 0 };
  const m = SQ.LENS_R * Math.min(1, d / SQ.REACH);
  return { x: dx / d * m, y: dy / d * m };
};
SQ.motionOn = (reduced, hidden) => !reduced && !hidden;

// The session and the composer.
SQ.pickNeeded = list => list.length > 1;
SQ.composerKey = (key, shift, composing) => key !== "Enter" || composing ? "none" : shift ? "newline" : "send";
SQ.canSend = (text, allowed) => !!allowed && String(text || "").trim() !== "";
SQ.sendState = info => info && info.send ? { disabled: false, note: "" }
  : { disabled: true, note: (info && info.why) || `Sending is off. The owner sets ${SQ.SEND_SETTING} to send.` };
SQ.draft = ctx => {
  const list = v => (Array.isArray(v) ? v : v ? [v] : []).map(String).filter(Boolean);
  const lines = ["Page context", `Screen: ${ctx.screen || ""}`, `Route: ${ctx.route || ""}`];
  for (const [name, v] of [["Selection", ctx.selection], ["Filters", ctx.filters], ["Visible errors", ctx.errors]]) {
    const items = list(v);
    lines.push(`${name}: ${items.length ? items.join("; ") : "none"}`);
  }
  return (lines.join("\n") + "\n\n").slice(0, SQ.TEXT_MAX);
};

SQ.MARKUP = `<style>
#sq-ov-btn{position:fixed;right:16px;bottom:var(--sq-ov-bottom,16px);z-index:999;width:52px;height:52px;padding:0;border:0;border-radius:50%;background:none;cursor:pointer;filter:drop-shadow(0 4px 10px #000b)}
#sq-ov-btn svg{display:block;width:100%;height:100%}
#sq-ov-btn:focus-visible{outline:2px solid #7aa2ff;outline-offset:3px}
html.sq-docked #sq-ov-btn{display:none}
html.sq-docked body{margin-right:var(--sq-dock)}
#sq-ov-lens{will-change:transform}
#sq-ov{position:fixed;z-index:1000;right:16px;bottom:calc(var(--sq-ov-bottom,16px) + 64px);width:min(440px,calc(100vw - 32px));height:min(640px,calc(100vh - 120px));display:flex;flex-direction:column;box-sizing:border-box;background:#11151c;color:#e6e8ee;border:1px solid #353d4b;border-radius:13px;box-shadow:0 18px 50px #000c;font:14px/1.45 system-ui,-apple-system,sans-serif;text-align:left}
#sq-ov[hidden]{display:none}
#sq-ov *{box-sizing:border-box}
#sq-ov[data-mode=docked]{top:0;right:0;bottom:0;height:auto;width:var(--sq-dock);border-radius:0;border-width:0 0 0 1px;box-shadow:none}
#sq-ov[data-mode=phone]{top:0;right:0;bottom:0;left:0;width:auto;height:auto;border:0;border-radius:0;padding:env(safe-area-inset-top) 0 env(safe-area-inset-bottom)}
#sq-ov header{display:flex;align-items:center;gap:2px;min-width:0;padding:6px 6px 6px 14px;border-bottom:1px solid #353d4b}
#sq-ov-title{flex:1;min-width:0;margin:0;font-size:15px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#sq-ov header button{flex:none;display:inline-flex;align-items:center;justify-content:center;width:44px;height:44px;padding:0;border:0;border-radius:8px;background:none;color:#b8c0cf;cursor:pointer}
#sq-ov header button:hover{background:#1c212b;color:#e6e8ee}
#sq-ov header button:disabled{opacity:.35;cursor:default;background:none}
#sq-ov header svg{width:18px;height:18px}
#sq-ov[data-mode=phone] .sq-ov-dockctl{display:none}
#sq-ov-handle{position:absolute;left:-5px;top:0;bottom:0;width:10px;cursor:ew-resize;display:none;touch-action:none}
#sq-ov[data-mode=docked] #sq-ov-handle{display:block}
#sq-ov-handle:hover{background:#7aa2ff55}
#sq-ov-handle:focus-visible{background:#7aa2ff55;outline:2px solid #7aa2ff;outline-offset:-2px}
#sq-ov-pick{display:flex;flex-wrap:wrap;gap:6px;padding:8px 14px;border-bottom:1px solid #353d4b}
#sq-ov-pick[hidden]{display:none}
#sq-ov-pick p{margin:0 0 2px;width:100%;color:#b8c0cf;font-size:13px}
#sq-ov-pick button{min-height:44px;padding:4px 10px;border:1px solid #353d4b;border-radius:8px;background:#1c212b;color:#e6e8ee;cursor:pointer;font:inherit;font-size:13px}
#sq-ov-pick button[aria-pressed=true]{border-color:#7aa2ff}
#sq-ov-log{flex:1;min-height:0;overflow:auto;padding:13px 14px}
#sq-ov-log .sq-empty{color:#b8c0cf}
.sq-turn{margin:0 0 4px;padding-left:8px;border-left:2px solid transparent;line-height:1.45}
.sq-who{display:block;font-size:10px;line-height:1.2;color:#707988;margin-bottom:2px;letter-spacing:.02em}
.sq-txt{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;color:#d8dee9}
.sq-p{margin:0 0 5px}.sq-p:last-child{margin-bottom:0}
.sq-owner{border-left:0;background:rgba(122,162,255,.08);border-radius:6px;padding:5px 9px}
.sq-owner .sq-who{color:#7aa2ff}
.sq-answer{border-left-color:#3ecf8e}.sq-answer .sq-who{color:#3ecf8e}
.sq-h{font-weight:600;font-size:13px;color:#eef1f5;margin:6px 0 3px}
.sq-ul{margin:3px 0;padding-left:16px}.sq-ul li{margin:0 0 2px}
.sq-code{margin:4px 0}.sq-code summary{font-size:11px;color:#7aa2ff;cursor:pointer;list-style:none}
.sq-code pre{margin:3px 0 0;padding:5px 8px;background:#0d1117;border:1px solid #232a36;border-radius:6px;overflow:auto;font:12px/1.4 ui-monospace,SFMono-Regular,monospace;color:#b8c0cf;white-space:pre}
.sq-more{margin:1px 0 4px;padding:1px 0;border:0;background:none;color:#7aa2ff;font-size:11px;cursor:pointer}
.sq-earlier{margin:0 0 6px}.sq-earlier summary{font-size:11px;color:#707988;cursor:pointer}
.sq-tool{border-left-color:#f5b041}.sq-tool .sq-who{color:#f5b041}
.sq-tool .sq-txt{color:#9aa3b2;font:12.5px/1.35 ui-monospace,SFMono-Regular,monospace}
#sq-ov-form{position:relative;display:flex;align-items:flex-end;gap:6px;padding:8px;border-top:1px solid #353d4b}
#sq-ov-plus{flex:none;width:44px;height:44px;border:1px solid #353d4b;border-radius:50%;background:#1c212b;color:#e6e8ee;font:22px/1 system-ui;cursor:pointer}
#sq-ov-menu{position:absolute;left:8px;bottom:60px;z-index:2;display:flex;flex-direction:column;min-width:170px;padding:4px;background:#1c212b;border:1px solid #353d4b;border-radius:10px;box-shadow:0 10px 30px #000a}
#sq-ov-menu[hidden]{display:none}
#sq-ov-menu button{min-height:44px;padding:6px 10px;border:0;border-radius:6px;background:none;color:#e6e8ee;text-align:left;font:inherit;cursor:pointer}
#sq-ov-menu button:hover{background:#2a3140}
#sq-ov-menu button:focus-visible{background:#2a3140;outline:2px solid #7aa2ff;outline-offset:-2px}
#sq-ov-text{flex:1;min-width:0;min-height:44px;max-height:200px;resize:none;padding:8px 10px;border:1px solid #353d4b;border-radius:10px;background:#0d1117;color:#e6e8ee;font:14px/1.4 system-ui,-apple-system,sans-serif}
#sq-ov-text:disabled{opacity:.6}
#sq-ov-send{flex:none;display:inline-flex;align-items:center;justify-content:center;width:44px;height:44px;padding:0;border:0;border-radius:50%;background:#7aa2ff;color:#0d1117;cursor:pointer}
#sq-ov-send:disabled{opacity:.35;cursor:default}
#sq-ov-send svg{width:20px;height:20px}
#sq-ov-note{margin:0;padding:0 13px 8px;font-size:13px;color:#b8c0cf}
#sq-ov-note:empty{display:none}
#sq-ov-status{margin:0;padding:0 13px 8px;font-size:13px;color:#e6e8ee}
#sq-ov-status:empty{display:none}
.sq-ov-back{display:none}
html.sq-over .sq-ov-back{display:inline-flex;align-items:center;min-height:44px;padding:3px 10px;border:1px solid #353d4b;border-radius:6px;background:none;color:inherit;cursor:pointer}
</style>
<button id="sq-ov-btn" type="button" aria-label="Chat with the AI" title="Chat with the AI" aria-haspopup="dialog" aria-expanded="false" aria-controls="sq-ov"><svg viewBox="0 0 64 64" aria-hidden="true" focusable="false"><defs>
<radialGradient id="sq-ov-g-ring" cx="38%" cy="28%" r="80%"><stop offset="0" stop-color="#eef1f5"/><stop offset=".3" stop-color="#9aa1ab"/><stop offset=".65" stop-color="#40454d"/><stop offset="1" stop-color="#121417"/></radialGradient>
<radialGradient id="sq-ov-g-lens" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="#fff7dc"/><stop offset=".09" stop-color="#ffc640"/><stop offset=".26" stop-color="#ff3a10"/><stop offset=".58" stop-color="#a50000"/><stop offset="1" stop-color="#2a0000" stop-opacity="0"/></radialGradient>
<clipPath id="sq-ov-clip"><circle cx="32" cy="32" r="21"/></clipPath></defs>
<circle class="sq-ov-ring" cx="32" cy="32" r="31" fill="url(#sq-ov-g-ring)"/>
<circle cx="32" cy="32" r="26.5" fill="none" stroke="#0a0b0d" stroke-width="2"/>
<circle cx="32" cy="32" r="24.5" fill="#060607"/>
<circle cx="32" cy="32" r="21" fill="#1f0000"/>
<g clip-path="url(#sq-ov-clip)"><g id="sq-ov-lens"><circle cx="32" cy="32" r="17" fill="url(#sq-ov-g-lens)"/></g></g>
<ellipse cx="25" cy="23" rx="6.5" ry="3" fill="#ffffff" opacity=".2" transform="rotate(-32 25 23)"/>
</svg></button>
<div id="sq-ov" role="dialog" aria-labelledby="sq-ov-title" hidden>
<div id="sq-ov-handle" role="separator" aria-orientation="vertical" aria-label="Resize the session panel" tabindex="0"></div>
<header><h2 id="sq-ov-title">Chat with the AI</h2>
<button type="button" class="sq-ov-dockctl" id="sq-ov-dock" aria-label="Dock right" title="Dock right"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><rect x="13" y="5" width="7" height="14" fill="currentColor"/></svg></button>
<button type="button" class="sq-ov-dockctl" id="sq-ov-undock" aria-label="Undock" title="Undock"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="8" width="13" height="12" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M11 4h9v9M20 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></button>
<button type="button" id="sq-ov-close" aria-label="Close" title="Close"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg></button>
</header>
<div id="sq-ov-pick" hidden></div>
<div id="sq-ov-log" role="log" aria-live="polite" aria-label="Session conversation"></div>
<div id="sq-ov-form">
<button type="button" id="sq-ov-plus" aria-label="Add to the message" aria-haspopup="menu" aria-expanded="false" aria-controls="sq-ov-menu">+</button>
<div id="sq-ov-menu" role="menu" hidden><button type="button" role="menuitem" data-act="shot">Attach screenshot</button><button type="button" role="menuitem" data-act="ctx">Page context</button></div>
<textarea id="sq-ov-text" rows="1" maxlength="4000" aria-label="Message to the session" placeholder="Message the session"></textarea>
<button id="sq-ov-send" type="button" aria-label="Send" title="Send" disabled><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
</div>
<p id="sq-ov-status" role="status"></p>
<p id="sq-ov-note"></p>
</div>`;

if (typeof module !== "undefined" && module.exports) module.exports = SQ;
// dom:begin
if (typeof document === "undefined" || !document.body || !document.createRange || window.__sqOverlay) return;   // no DOM: the rules only
window.__sqOverlay = true;
const STORE = "sq-ov", NOTE = "It arrives as a message from another session, not as your typed input or approval.";
const SHEETS = window.SQUAD_OV_SHEETS || "#tk,#rk";
const store = (() => { try { return JSON.parse(localStorage.getItem(STORE) || "{}") || {}; } catch (e) { return {}; } })();
const save = () => { try { localStorage.setItem(STORE, JSON.stringify(store)); } catch (e) { /* private mode: the width lasts the visit */ } };
document.body.appendChild(document.createRange().createContextualFragment(SQ.MARKUP));
const $ = id => document.getElementById(id);
const btn = $("sq-ov-btn"), ov = $("sq-ov"), title = $("sq-ov-title"), pick = $("sq-ov-pick"), log = $("sq-ov-log"),
      text = $("sq-ov-text"), sendBtn = $("sq-ov-send"), plus = $("sq-ov-plus"), menu = $("sq-ov-menu"), note = $("sq-ov-note"),
      stat = $("sq-ov-status"), handle = $("sq-ov-handle"), dockBtn = $("sq-ov-dock"), undockBtn = $("sq-ov-undock"), lens = $("sq-ov-lens");
const de = document.documentElement;
let sessions = [], cur = null, rule = null, timer = 0, lastSig = "";

// ---- the page context: each page exposes one function; a page without its own gets this one
const shown = e => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length)) && !e.closest("#sq-ov");
const words = e => (e.textContent || "").replace(/\s+/g, " ").trim().slice(0, 160);
function defaultContext() {
  const all = s => [...document.querySelectorAll(s)].filter(shown);
  const tabs = all('[aria-current="page"],[aria-selected="true"],[role=tab][aria-pressed="true"]').map(words).filter(Boolean);
  const sheets = all("[role=dialog]").map(e => {
    const head = e.querySelector("h1,h2,h3,header");
    return [e.getAttribute("aria-label"), head ? words(head) : ""].filter(Boolean).join(": ");
  }).filter(Boolean);
  const filters = all("select,input[type=search],input[type=checkbox]:checked,input[type=radio]:checked,[role=search] input")
    .map(e => `${e.getAttribute("aria-label") || e.name || e.id || e.type}=${e.type === "checkbox" || e.type === "radio" ? "on" : e.value}`)
    .filter(f => !/=$/.test(f));
  const errors = all('[role=alert],.err,.error,.broken').map(words).filter(Boolean);
  return { screen: [document.title, ...tabs.slice(0, 2)].filter(Boolean).join(" / "), route: location.pathname + location.search + location.hash,
           selection: sheets.slice(0, 6), filters: filters.slice(0, 12), errors: errors.slice(0, 8) };
}
if (typeof window.squadPageContext !== "function") window.squadPageContext = defaultContext;
function draftContext() {
  let ctx;
  try { ctx = window.squadPageContext(); } catch (e) { ctx = defaultContext(); }
  text.value = SQ.draft(ctx || defaultContext());
  grow(); syncSend();
}
function addLine(line) {
  text.value = (text.value && !text.value.endsWith("\n") ? text.value + "\n" : text.value) + line + "\n";
  grow(); syncSend(); text.focus();
}

// ---- the session
function say(msg) { stat.textContent = msg || ""; }
function syncSend() {
  const st = SQ.sendState(cur || rule);
  text.disabled = !!(rule || cur) && st.disabled;
  note.textContent = st.disabled && (rule || cur) ? st.note : NOTE;
  sendBtn.disabled = !(cur && SQ.canSend(text.value, cur.send));
}
function empty(msg) {
  const p = document.createElement("p"); p.className = "sq-empty"; p.textContent = msg;
  log.replaceChildren(p); lastSig = "";
}
function paintPick() {
  pick.hidden = !SQ.pickNeeded(sessions);
  if (pick.hidden) { pick.replaceChildren(); return; }
  const p = document.createElement("p");
  p.textContent = cur ? "Sessions in this project:" : `${sessions.length} sessions run in this project. Pick one.`;
  const items = sessions.map(s => {
    const b = document.createElement("button"); b.type = "button";
    b.textContent = [s.src.label, s.name || s.sid.slice(0, 8), s.status].filter(Boolean).join(" · ");
    b.setAttribute("aria-pressed", String(!!cur && cur.sid === s.sid && cur.src.api === s.src.api));
    b.onclick = () => { cur = s; try { sessionStorage.setItem(STORE + "-sid", s.src.api + " " + s.sid); } catch (e) { /* keep it for this view */ }
                        lastSig = ""; paintPick(); paintHead(); loadConversation(); };
    return b;
  });
  pick.replaceChildren(p, ...items);
}
function paintHead() {
  title.textContent = cur ? `Chat with the AI · ${cur.name || cur.sid.slice(0, 8)}` : "Chat with the AI";
  syncSend();
}
async function loadSessions() {
  let srcs = [];
  try { srcs = await window.SQUAD_OV_SOURCES(); } catch (e) { srcs = []; }
  const found = []; rule = null;
  for (const src of srcs || []) {
    try {
      const r = await fetch(src.api + "/project", { cache: "no-store" });
      if (!r.ok) continue;
      const d = await r.json();
      rule = rule || { send: d.send, why: d.why };
      for (const s of d.sessions || []) found.push({ ...s, src, send: d.send, why: d.why });
    } catch (e) { /* that repository's hub did not answer: its sessions are left out */ }
  }
  sessions = found;
  let kept = "";
  try { kept = sessionStorage.getItem(STORE + "-sid") || ""; } catch (e) { kept = ""; }
  cur = (cur && found.find(s => s.sid === cur.sid && s.src.api === cur.src.api))
     || found.find(s => s.src.api + " " + s.sid === kept) || (found.length === 1 ? found[0] : null);
  paintPick(); paintHead();
  if (cur) loadConversation();
  else if (!found.length) empty("No session runs in this project's checkout. Start Claude Code there, then open this again.");
  else empty("Pick a session above.");
}
// Only the structure that carries meaning in a 400 px panel: headings, bullets, a fence. Everything else stays text.
function richText(text) {
  const box = document.createElement("div");
  box.className = "sq-txt";
  const lines = String(text || "").split("\n");
  let buf = [], ul = null, fence = null;
  const para = () => { if (!buf.length) return; const p = document.createElement("div"); p.className = "sq-p"; p.textContent = buf.join("\n"); box.append(p); buf = []; };
  const list = () => { if (ul) { box.append(ul); ul = null; } };
  const code = () => { if (!fence) return; const d = document.createElement("details"); d.className = "sq-code";
    const s = document.createElement("summary"); s.textContent = "Code · " + fence.length + " line" + (fence.length === 1 ? "" : "s");
    const pre = document.createElement("pre"); pre.textContent = fence.join("\n");
    d.append(s, pre); if (fence.length <= 8) d.open = true; box.append(d); fence = null; };
  for (const raw of lines) {
    if (raw.trim().startsWith("```")) { if (fence) code(); else { para(); list(); fence = []; } continue; }
    if (fence) { fence.push(raw); continue; }
    const h = /^#{1,6}\s+(.*)$/.exec(raw);
    if (h) { para(); list(); const d = document.createElement("div"); d.className = "sq-h"; d.textContent = h[1]; box.append(d); continue; }
    const li = /^\s*[-*]\s+(.*)$/.exec(raw);
    if (li) { para(); if (!ul) { ul = document.createElement("ul"); ul.className = "sq-ul"; } const it = document.createElement("li"); it.textContent = li[1]; ul.append(it); continue; }
    if (!raw.trim()) { para(); list(); continue; }
    list(); buf.push(raw);
  }
  if (fence) code();
  para(); list();
  return box;
}
const CLAMP = { owner: 60, answer: 116, tool: 60 };
function clamp(body, px) {
  if (body.scrollHeight <= px + 6) return;
  body.style.maxHeight = px + "px"; body.style.overflow = "hidden";
  const b = document.createElement("button"); b.type = "button"; b.className = "sq-more"; b.textContent = "Show more";
  b.onclick = () => { const on = body.style.maxHeight !== ""; body.style.maxHeight = on ? "" : px + "px";
                      body.style.overflow = on ? "" : "hidden"; b.textContent = on ? "Show less" : "Show more"; };
  body.after(b);
}
function turnNode(t) {
  const d = document.createElement("div"), who = document.createElement("span");
  d.className = "sq-turn sq-" + t.role;
  who.className = "sq-who"; who.textContent = t.role === "owner" ? "You" : t.role === "tool" ? "Tool" : "AI";
  const body = richText(t.text);
  d.append(who, body);
  clamp(body, CLAMP[t.role] || 116);
  return d;
}
function feed(turns) {
  const { earlier, shown } = SQ.splitEarlier(turns), nodes = [];
  if (earlier.length) {
    const d = document.createElement("details"); d.className = "sq-earlier";
    const s = document.createElement("summary"); s.textContent = "Earlier activity · " + earlier.length + " turns";
    d.append(s, ...earlier.map(turnNode)); nodes.push(d);
  }
  nodes.push(...shown.map(turnNode));
  return nodes;
}
async function loadConversation() {
  const s = cur;
  if (!s || ov.hidden) return;
  try {
    const r = await fetch(s.src.api + "/conversation?sid=" + encodeURIComponent(s.sid), { cache: "no-store" });
    if (cur !== s) return;
    if (!r.ok) { if (r.status === 404) { cur = null; loadSessions(); } return; }
    const turns = (await r.json()).turns || [], last = turns[turns.length - 1];
    const sig = turns.length + ":" + (last ? last.id + ":" + last.text.length : "");
    if (sig === lastSig) return;
    const atEnd = !lastSig || log.scrollHeight - log.scrollTop - log.clientHeight < 48;
    lastSig = sig;
    if (!turns.length) { empty("No turns yet."); lastSig = sig; return; }
    log.replaceChildren(...feed(turns));
    if (atEnd) log.scrollTop = log.scrollHeight;
  } catch (e) { /* the next poll tries again */ }
}
const uuid = () => (crypto.randomUUID ? crypto.randomUUID()
  : "10000000-1000-4000-8000-100000000000".replace(/[018]/g, c => (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16)));
async function send() {
  const s = cur, body = text.value;
  if (!s || !SQ.canSend(body, s.send)) return;
  sendBtn.disabled = true; say("Sending");
  try {
    const r = await fetch(s.src.api + "/send", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sid: s.sid, text: body, client_id: uuid() }) });
    const d = await r.json().catch(() => ({}));
    const st = r.ok ? d.status : "refused";
    say(st === "sent" ? "Sent: the session's inbox accepted the message." : st === "refused" ? "Refused" + (d.reason ? ": " + d.reason : ": the hub did not send it.") : "Uncertain: the send was interrupted.");
    if (st === "sent") { text.value = ""; grow(); }
  } catch (e) { say("Uncertain: no answer from the hub."); }
  syncSend(); loadConversation();
}

// ---- the composer
function grow() { text.style.height = "auto"; text.style.height = Math.min(text.scrollHeight + 2, 200) + "px"; }
text.addEventListener("input", () => { grow(); syncSend(); });
text.addEventListener("keydown", e => {
  if (SQ.composerKey(e.key, e.shiftKey, e.isComposing) === "send") { e.preventDefault(); send(); }
});
sendBtn.addEventListener("click", send);
function setMenu(open) { menu.hidden = !open; plus.setAttribute("aria-expanded", String(open)); if (open) menu.querySelector("button").focus(); }
plus.addEventListener("click", () => setMenu(menu.hidden));
menu.addEventListener("click", e => {
  const act = e.target.closest("[data-act]");
  if (!act) return;
  setMenu(false);
  if (act.dataset.act === "ctx") { let c; try { c = window.squadPageContext(); } catch (x) { c = defaultContext(); } addLine(SQ.draft(c || defaultContext()).trim()); }
  else screenshot();
});
// The page beneath the overlay: the overlay steps aside, the vendored library captures the viewport, the overlay comes back,
// and the PNG goes to the session's route. The hub admits it by the send's rule and refuses a PNG over 5 MB.
const SHOT_MAX = 5 * 1024 * 1024;
async function screenshot() {
  const s = cur;
  if (!s) { say("Choose a session first."); return; }
  if (!s.send) { say((rule && rule.why) || "Sending is off here."); return; }
  const shown = [btn.hidden, ov.hidden];
  let blob = null;
  btn.hidden = true; ov.hidden = true; applyLayout();
  try {
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    const c = await html2canvas(de, { x: scrollX, y: scrollY, width: innerWidth, height: innerHeight, windowWidth: innerWidth, windowHeight: innerHeight, scale: 1, logging: false, useCORS: true });
    blob = await new Promise(r => c.toBlob(r, "image/png"));
  } catch (e) { blob = null; }
  finally { btn.hidden = shown[0]; ov.hidden = shown[1]; applyLayout(); }
  if (!blob) { say("No screenshot: the capture failed."); return; }
  if (blob.size > SHOT_MAX) { say("No screenshot: the image is over 5 MB."); return; }
  say("Attaching the screenshot");
  try {
    const r = await fetch(s.src.api + "/screenshot?sid=" + encodeURIComponent(s.sid) + "&client_id=" + uuid(), { method: "POST", headers: { "Content-Type": "image/png" }, body: blob });
    const d = await r.json().catch(() => ({}));
    const st = r.ok ? d.status : "refused";
    say(st === "sent" ? "Attached: the session's inbox accepted the screenshot." : st === "refused" ? "Refused" + (d.reason || d.err ? ": " + (d.reason || d.err) : ": the hub did not attach it.") : "Uncertain: the attach was interrupted.");
  } catch (e) { say("Uncertain: no answer from the hub."); }
  loadConversation();
}

// ---- open, close, dock
function width() { return SQ.clampWidth(store.w || SQ.DEFAULT_W, innerWidth); }
function applyLayout() {
  const mode = ov.hidden ? "closed" : SQ.layout(innerWidth, !!store.docked), w = width();
  ov.dataset.mode = mode;
  de.classList.toggle("sq-docked", mode === "docked");
  de.style.setProperty("--sq-dock", mode === "docked" ? w + "px" : "0px");
  dockBtn.disabled = mode === "docked" || !SQ.canDock(innerWidth);
  undockBtn.disabled = mode !== "docked";
  handle.setAttribute("aria-valuenow", String(w));
  handle.setAttribute("aria-valuemin", String(SQ.MIN_W));
  handle.setAttribute("aria-valuemax", String(SQ.clampWidth(1e9, innerWidth)));
  placeSheets();
}
function placeSheets() {
  const docked = de.classList.contains("sq-docked"), w = width();
  let over = false;
  for (const el of document.querySelectorAll(SHEETS)) {
    const open = getComputedStyle(el).display !== "none";
    const mode = docked && open ? SQ.sheetMode(innerWidth, w, el.offsetWidth || SQ.SHEET_W) : "";
    if (mode === "over") over = true;
    el.style.right = mode === "stack" ? w + "px" : "";
    el.style.zIndex = mode === "over" ? "1001" : "";
    const head = el.querySelector("header");
    if (head && !head.querySelector(".sq-ov-back")) {
      const back = document.createElement("button");
      back.type = "button"; back.className = "sq-ov-back"; back.textContent = "← Session";
      back.setAttribute("aria-label", "Back to the session");
      back.onclick = () => { const x = el.querySelector('[aria-label="Close"]'); if (x) x.click(); else el.classList.remove("on"); };
      head.prepend(back);
    }
  }
  de.classList.toggle("sq-over", over);
}
for (const el of document.querySelectorAll(SHEETS)) new MutationObserver(placeSheets).observe(el, { attributes: true, attributeFilter: ["class", "style", "hidden"] });
function openOverlay(){
  ov.hidden = false; btn.setAttribute("aria-expanded", "true");
  applyLayout();
  if (!text.value.trim()) draftContext();
  loadSessions();
  clearInterval(timer); timer = setInterval(loadConversation, SQ.POLL_MS);
  text.focus();
}
function closeOverlay() {
  ov.hidden = true; btn.setAttribute("aria-expanded", "false"); setMenu(false);
  clearInterval(timer); timer = 0;
  applyLayout(); btn.focus();
}
btn.addEventListener("click", () => (ov.hidden ? openOverlay() : closeOverlay()));
$("sq-ov-close").addEventListener("click", closeOverlay);
dockBtn.addEventListener("click", () => { store.docked = true; save(); applyLayout(); });
undockBtn.addEventListener("click", () => { store.docked = false; save(); applyLayout(); });
ov.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  if (!menu.hidden) { setMenu(false); plus.focus(); } else closeOverlay();
});
addEventListener("resize", applyLayout);
handle.addEventListener("keydown", e => {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  e.preventDefault(); store.w = SQ.keyWidth(width(), e.key, innerWidth); save(); applyLayout();
});
handle.addEventListener("dblclick", () => { store.w = SQ.DEFAULT_W; save(); applyLayout(); });
handle.addEventListener("pointerdown", e => {
  handle.setPointerCapture(e.pointerId);
  const move = m => { store.w = SQ.clampWidth(innerWidth - m.clientX, innerWidth); applyLayout(); };
  const up = () => { handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", up); save(); };
  handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", up);
});

// ---- the lens: its center follows the pointer by a damped spring, one step per animation frame
const reduced = matchMedia("(prefers-reduced-motion: reduce)");
let ls = { x: 0, y: 0, vx: 0, vy: 0 }, ptr = null, lastMove = -1e9, raf = 0, lastT = 0;
const still = () => !SQ.motionOn(reduced.matches, document.hidden);
function kick() { if (!raf && !still()) raf = requestAnimationFrame(frame); }
function frame(t) {
  raf = 0;
  if (still()) { lastT = 0; return; }
  const dt = lastT ? (t - lastT) / 1000 : 1 / 60; lastT = t;
  const r = btn.getBoundingClientRect();
  const tg = SQ.lensTarget(ptr, { x: r.left + r.width / 2, y: r.top + r.height / 2 }, performance.now(), lastMove);
  ls = SQ.springStep(ls, tg, dt);
  const p = SQ.clampToLens(ls.x, ls.y);
  lens.setAttribute("transform", `translate(${p.x.toFixed(2)} ${p.y.toFixed(2)})`);
  const rest = Math.abs(ls.x - tg.x) < 0.01 && Math.abs(ls.y - tg.y) < 0.01 && Math.abs(ls.vx) < 0.01 && Math.abs(ls.vy) < 0.01;
  if (!rest || tg.x || tg.y) kick(); else lastT = 0;
}
addEventListener("pointermove", e => { ptr = { x: e.clientX, y: e.clientY }; lastMove = performance.now(); kick(); }, { passive: true });
document.addEventListener("visibilitychange", () => { lastT = 0; kick(); });
if (reduced.addEventListener) reduced.addEventListener("change", kick);
applyLayout(); syncSend();
})();
