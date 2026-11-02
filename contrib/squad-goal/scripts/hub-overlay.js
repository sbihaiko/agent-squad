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
// The build route sits beside the session route the page names: its base holds the same profile prefix.
SQ.versionRoute = api => api.slice(0, api.lastIndexOf("/") + 1) + "version";
SQ.buildStep = (seen, v) => ({ seen: seen === null ? v : seen, reload: seen !== null && v !== seen });
// The feed reads like a terminal: the latest turns stand, older ones fold into one line, and a long answer is clamped with
// "Show more" (owner, 2026-11-01; the terminal reads clearer because it summarizes age and length, not because it says less).
SQ.KEEP = 12;
SQ.splitEarlier = (turns, keep = SQ.KEEP) => turns.length <= keep ? { earlier: [], shown: turns }
  : { earlier: turns.slice(0, turns.length - keep), shown: turns.slice(turns.length - keep) };
// The push stream sends the turns as lines, one JSON object each; a turn already drawn is never drawn again.
SQ.streamTurns = text => String(text || "").split("\n").filter(Boolean).map(line => JSON.parse(line));
SQ.mergeTurns = (drawn, incoming) => {
  const seen = new Set(drawn.map(t => t.k)), out = drawn.slice();
  for (const t of incoming) if (!seen.has(t.k)) { seen.add(t.k); out.push(t); }
  return out;
};
// The talk comes from the stream only while the stream is live and this session is the one it watches; else the poll reads it.
SQ.feedFrom = (live, watching) => live && watching ? "push" : "poll";
// A repaint that lands above the newest line keeps the line the reader reads where it was on the screen.
SQ.viewAfter = (before, after, scrollTop) => before == null || after == null ? scrollTop : scrollTop + (after - before);

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
SQ.supervisorLine = info => info && info.state === "refused"
  ? `The terminal supervisor is not running: ${String(info.why || "no reason was recorded").replace(/\.$/, "")}.` : "";
// The sentence keeps its own row in the Terminal panel: the shared status line is written by every send and every poll (#1160).
SQ.supervisorView = info => { const text = SQ.supervisorLine(info); return { hidden: !text, text }; };
SQ.draft = ctx => {
  const list = v => (Array.isArray(v) ? v : v ? [v] : []).map(String).filter(Boolean);
  const lines = ["Page context", `Screen: ${ctx.screen || ""}`, `Route: ${ctx.route || ""}`];
  for (const [name, v] of [["Selection", ctx.selection], ["Filters", ctx.filters], ["Visible errors", ctx.errors]]) {
    const items = list(v);
    lines.push(`${name}: ${items.length ? items.join("; ") : "none"}`);
  }
  return (lines.join("\n") + "\n\n").slice(0, SQ.TEXT_MAX);
};

// The live activity line (#1082): busy or waiting shows a spinner, the verb, the time and the output tokens since the owner's
// last turn; idle shows nothing. The spinner turns about eight times a second; a count the transcript does not show reads as a dash.
SQ.SPIN = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
SQ.fmtElapsed = s => s == null ? "—" : s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}m`;
SQ.fmtTokens = n => n == null ? "—" : n < 1000 ? String(n) : `${(n / 1000).toFixed(1)}k`;
SQ.liveText = (a, now) => {
  if (!a || (a.status !== "busy" && a.status !== "waiting")) return "";
  const secs = a.elapsed == null ? null : a.elapsed + Math.floor((now - a.got) / 1000);
  const verb = a.status === "busy" ? "Working" : "Waiting for you";
  return [`${SQ.SPIN[Math.floor(now / 120) % SQ.SPIN.length]} ${verb}`, SQ.fmtElapsed(secs), `↓ ${SQ.fmtTokens(a.tokens)} tokens`].join(" · ");
};

SQ.MARKUP = `<style>
#sq-ov-btn{position:fixed;right:16px;bottom:var(--sq-ov-bottom,16px);z-index:999;width:52px;height:52px;padding:0;border:0;border-radius:50%;background:none;cursor:pointer;filter:drop-shadow(0 4px 10px #000b)}
#sq-ov-btn svg{display:block;width:100%;height:100%}
#sq-ov-btn:focus-visible{outline:2px solid #7aa2ff;outline-offset:3px}
html.sq-docked #sq-ov-btn{display:none}
html.sq-docked body{margin-right:var(--sq-dock)}
#sq-ov-lens{will-change:transform}
#sq-ov{position:fixed;z-index:1000;right:16px;bottom:calc(var(--sq-ov-bottom,16px) + 64px);width:min(440px,calc(100vw - 32px));height:min(640px,calc(100vh - 120px));display:flex;flex-direction:column;box-sizing:border-box;background:#11151c;color:#e6e8ee;border:1px solid #353d4b;border-radius:13px;box-shadow:0 18px 50px #000c;--sq-fs:14px;--sq-mono:ui-monospace,SFMono-Regular,SF Mono,Menlo,Consolas,monospace;font:14px/1.55 var(--sq-mono);text-align:left}
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
.sq-ov-fsb{font:600 13px/1 system-ui;min-width:44px;height:44px;padding:0 8px}   /* one control size for the desktop and the phone floor (owner, 2026-11-01) */
#sq-ov-log{flex:1;min-height:0;overflow:auto;padding:13px 14px}
#sq-ov-log .sq-empty{color:#b8c0cf}
.sq-turn{margin:0 0 12px;padding:0;border-left:0;line-height:1.55;text-indent:0}
.sq-who{display:block;font-family:var(--sq-mono);font-size:13px;line-height:1.55;color:#8b949e;margin-bottom:2px;letter-spacing:.02em}
.sq-txt{white-space:pre-wrap;overflow-wrap:anywhere;font-family:var(--sq-mono);font-size:var(--sq-fs);color:#e6e8ee}
.sq-p{margin:0 0 1.55em}.sq-txt>:last-child{margin-bottom:0}
.sq-owner{margin:0 0 10px auto;width:fit-content;max-width:86%;border:1px solid #2e6b4f;border-radius:14px 14px 4px 14px;padding:8px 12px;background:#12302a}
.sq-owner .sq-who{color:#8b949e;font-size:13px}.sq-owner .sq-txt{color:#e6edf3}
.sq-system .sq-who{color:#8b949e;font-size:13px}.sq-system .sq-txt{color:#8b949e;font-style:italic}
.sq-fold{border-left:0;margin:0 0 8px}.sq-fold summary{font-size:13px;color:#8b949e;cursor:pointer;list-style:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:flex;align-items:center}
.sq-fold summary::-webkit-details-marker{display:none}.sq-fold[open] summary{color:#8b949e}
.sq-answer{margin:0 auto 10px 0;width:fit-content;max-width:100%;border:1px solid #26303c;border-radius:14px 14px 14px 4px;padding:8px 12px;background:#161c24}
.sq-answer .sq-who{color:#8b949e;font-size:13px}
/* One header line per bubble: the owner's label at the left, the clock at the right corner, as WhatsApp draws it (owner,
   2026-11-01). Floated alone, the clock hung above the bubble and pushed the first line down (#1099). No tick. */
.sq-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin:0 0 3px}
.sq-time{margin-left:auto;color:#8b949e;font-size:13px;line-height:1.4;white-space:nowrap;user-select:none}
.sq-h{font-family:var(--sq-mono);font-weight:600;font-size:var(--sq-fs);color:#eef1f5;margin:1.55em 0 0}
.sq-tbl{width:100%;border-collapse:collapse;margin:0 0 1.55em;font-size:var(--sq-fs)}
.sq-tbl th,.sq-tbl td{border:1px solid #353d4b;padding:4px 8px;text-align:left;vertical-align:top}
.sq-tbl th{background:#101a16;color:#e6e8ee;font-weight:600}
.sq-ul{margin:0 0 1.55em;padding-left:18px}.sq-ul li{margin:0}.sq-ol{list-style:decimal}
.sq-ic{font-family:var(--sq-mono);color:#e6c07b;font-size:var(--sq-fs)}
.sq-txt b{color:#ffffff;font-weight:650}.sq-txt i{color:inherit}
.sq-txt a{color:#7aa2ff;text-decoration:underline}.sq-txt code{color:#e6c07b}
.sq-code{margin:0 0 1.55em}.sq-code summary{font-size:13px;color:#7aa2ff;cursor:pointer;list-style:none;display:flex;align-items:center}
.sq-code pre{margin:0 0 1.55em;padding:6px 9px;background:#0d1117;border:1px solid #232a36;border-radius:6px;overflow:auto;font-family:var(--sq-mono);font-size:var(--sq-fs);line-height:1.4;color:#b8c0cf;white-space:pre}
.sq-img{display:block;margin:0 0 1.55em}.sq-img img{max-width:100%;max-height:180px;border:1px solid #232a36;border-radius:6px;display:block}
.sq-onlyimg{max-width:min(86%,280px)}.sq-onlyimg .sq-img img{width:100%;max-height:220px;object-fit:contain}
.sq-more{margin:0 0 4px;padding:0;border:0;background:none;color:#7aa2ff;font-size:13px;cursor:pointer}
.sq-earlier{margin:0 0 6px}.sq-earlier summary{font-size:13px;color:#8b949e;cursor:pointer;display:flex;align-items:center}
.sq-tool{}.sq-tool .sq-who{color:var(--warn)}
.sq-tool .sq-txt{color:#9aa3b2;font-family:var(--sq-mono);font-size:var(--sq-fs);line-height:1.55}
#sq-ov-form{position:relative;display:flex;align-items:flex-end;gap:6px;padding:8px;border-top:1px solid #353d4b}
#sq-ov-plus{flex:none;width:44px;height:44px;border:1px solid #353d4b;border-radius:50%;background:#1c212b;color:#e6e8ee;font:22px/1 system-ui;cursor:pointer}
#sq-ov-menu{position:absolute;left:8px;bottom:60px;z-index:2;display:flex;flex-direction:column;min-width:170px;padding:4px;background:#1c212b;border:1px solid #353d4b;border-radius:10px;box-shadow:0 10px 30px #000a}
#sq-ov-menu[hidden]{display:none}
#sq-ov-menu button{min-height:44px;padding:6px 10px;border:0;border-radius:6px;background:none;color:#e6e8ee;text-align:left;font:inherit;cursor:pointer}
#sq-ov-menu button:hover{background:#2a3140}
#sq-ov-menu button:focus-visible{background:#2a3140;outline:2px solid #7aa2ff;outline-offset:-2px}
#sq-ov-text{flex:1;min-width:0;min-height:44px;max-height:200px;resize:none;padding:8px 10px;border:1px solid #353d4b;border-radius:10px;background:#0d1117;color:#e6e8ee;font-family:var(--sq-mono);font-size:var(--sq-fs);line-height:1.55;font:var(--sq-fs)/1.55 var(--sq-mono);font-ui,-apple-system,sans-serif}
#sq-ov-text:disabled{opacity:.6}
#sq-ov-send{flex:none;display:inline-flex;align-items:center;justify-content:center;width:44px;height:44px;padding:0;border:0;border-radius:50%;background:#7aa2ff;color:#0d1117;cursor:pointer}
#sq-ov-send:disabled{opacity:.35;cursor:default}
#sq-ov-send svg{width:20px;height:20px}
#sq-ov-note{margin:0;padding:0 13px 8px;font-size:13px;color:#b8c0cf}
#sq-ov-note:empty{display:none}
#sq-ov-status{margin:0;padding:0 13px 8px;font-size:13px;color:#e6e8ee}
#sq-ov-status:empty{display:none}
#sq-ov-supervisor{margin:0;padding:0 13px 8px;font-size:13px;color:var(--warn)}
#sq-ov-supervisor[hidden]{display:none}
#sq-ov-live{margin:0;padding:0 14px 6px;font-family:var(--sq-mono);font-size:var(--sq-fs);line-height:1.55;color:#b8c0cf;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-variant-numeric:tabular-nums}
#sq-ov-live[hidden]{display:none}
.sq-ov-back{display:none}
html.sq-over .sq-ov-back{display:inline-flex;align-items:center;min-height:44px;padding:3px 10px;border:1px solid #353d4b;border-radius:6px;background:none;color:inherit;cursor:pointer}
</style>
<button id="sq-ov-btn" type="button" aria-label="Terminal" title="Terminal" aria-haspopup="dialog" aria-expanded="false" aria-controls="sq-ov"><svg viewBox="0 0 64 64" aria-hidden="true" focusable="false"><defs>
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
<header><h2 id="sq-ov-title">Terminal</h2>
<button type="button" class="sq-ov-dockctl sq-ov-fsb" id="sq-ov-fsdown" aria-label="Smaller text" title="Smaller text">A−</button>
<button type="button" class="sq-ov-dockctl sq-ov-fsb" id="sq-ov-fsup" aria-label="Larger text" title="Larger text">A+</button>
<button type="button" class="sq-ov-dockctl" id="sq-ov-dock" aria-label="Dock right" title="Dock right"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><rect x="13" y="5" width="7" height="14" fill="currentColor"/></svg></button>
<button type="button" class="sq-ov-dockctl" id="sq-ov-undock" aria-label="Undock" title="Undock"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="8" width="13" height="12" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M11 4h9v9M20 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></button>
<button type="button" id="sq-ov-close" aria-label="Close" title="Close"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg></button>
</header>
<div id="sq-ov-pick" hidden></div>
<p id="sq-ov-supervisor" role="alert" hidden></p>
<div id="sq-ov-log" role="log" aria-live="polite" aria-label="Session conversation"></div>
<p id="sq-ov-live" aria-live="off" hidden></p>
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
      stat = $("sq-ov-status"), supLine = $("sq-ov-supervisor"), handle = $("sq-ov-handle"), dockBtn = $("sq-ov-dock"),
      undockBtn = $("sq-ov-undock"), lens = $("sq-ov-lens"), live = $("sq-ov-live");
const de = document.documentElement;
let sessions = [], cur = null, rule = null, timer = 0, lastSig = "", liveAct = null, liveTick = 0;

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
function addLine(line) {
  text.value = (text.value && !text.value.endsWith("\n") ? text.value + "\n" : text.value) + line + "\n";
  grow(); syncSend(); text.focus();
}

// ---- the session
function say(msg) { stat.textContent = msg || ""; }
// The supervisor's absence gets its own row, so a status message never overwrites it and the panel states it once (#1160).
function paintSupervisor(info) {
  const view = SQ.supervisorView(info);
  supLine.hidden = view.hidden;
  supLine.textContent = view.text;
}
// The live line keeps its own row: the spinner turns about eight times a second from the last read, and a read sets the count (#1082).
function setActivity(a) { liveAct = a ? { ...a, got: Date.now() } : null; paintLive(); }
function paintLive(){
  const t = SQ.liveText(liveAct, Date.now());
  live.hidden = !t; live.textContent = t;
}
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
  title.textContent = "Terminal";   // the title names the panel, not the session (owner, 2026-11-01)
  syncSend();
}
// A read that fails says nothing about the sessions (owner, 2026-11-01): it used to blank the panel and drop the reader's
// session, so a reload that landed during a hub restart read as "no session runs here". The panel keeps what it shows and
// asks again, and only a read that answered may say a session is gone.
let retryT = 0, retryN = 0, missT = 0, emptyN = 0;
function retrySoon() {
  if (retryT || retryN >= 20) return;
  retryT = setTimeout(() => { retryT = 0; retryN++; loadSessions(); }, 1500);
}
async function loadSessions() {
  let srcs = [];
  try { srcs = await window.SQUAD_OV_SOURCES(); } catch (e) { srcs = []; }
  const found = []; let rl = null, sup = null, answered = 0;
  for (const src of srcs || []) {
    try {
      const r = await fetch(src.api + "/project", { cache: "no-store" });
      if (!r.ok) continue;
      const d = await r.json();
      answered++;
      rl = rl || { send: d.send, why: d.why };
      sup = sup || d.supervisor || null;
      for (const s of d.sessions || []) found.push({ ...s, src, send: d.send, why: d.why });
    } catch (e) { /* that repository's hub did not answer: its sessions are left out */ }
  }
  if (!answered) {
    say("Reading the sessions…");
    retrySoon();
    if (!cur) empty("Reading the sessions…");
    return;   // nothing is dropped on a read that did not answer
  }
  retryN = 0;
  // One empty answer never takes away a talk the reader is reading (owner, 2026-11-01): the registry scan passes through
  // moments with no session. Only a repeated empty answer may blank it.
  emptyN = found.length ? 0 : emptyN + 1;
  if (cur && !found.length && emptyN < 3) {
    say("the session list came back empty · asking again");
    retrySoon();
    return;
  }
  rule = rl;
  sessions = found;
  let kept = "";
  try { kept = sessionStorage.getItem(STORE + "-sid") || ""; } catch (e) { kept = ""; }
  cur = (cur && found.find(s => s.sid === cur.sid && s.src.api === cur.src.api))
     || found.find(s => s.src.api + " " + s.sid === kept) || (found.length === 1 ? found[0] : null);
  paintPick(); paintHead(); paintSupervisor(sup);
  if (cur) loadConversation();
  else if (!found.length) empty("No session runs in this project's checkout. Start Claude Code there, then open this again.");
  else empty("Pick a session above.");
}
// Only the structure that carries meaning in a 400 px panel: headings, bullets, a fence. Everything else stays text.
// A transcript names a screenshot by the path it was written to: the hub serves that file and the panel draws a thumbnail
// (owner, 2026-11-01). "[Image #7]" alone names no file, so it stays text.
const IMG_RE = /^\s*\[Image: source: ([^\]\n]+)\](?:\s.*)?$/;
function imageNode(path) {
  const t = cur, a = document.createElement("a"), i = document.createElement("img");
  a.className = "sq-img"; a.href = t.src.api + "/image?sid=" + encodeURIComponent(t.sid) + "&p=" + encodeURIComponent(path);
  a.target = "_blank"; a.rel = "noopener";
  i.loading = "lazy"; i.alt = "screenshot"; i.src = a.href;
  i.onerror = () => { a.replaceWith(document.createTextNode("[Image: " + path + "]")); };
  a.append(i);
  return a;
}
// The talk is markdown (owner, 2026-11-01): bold, italic, inline code and a link, built as DOM nodes so the text is never
// parsed as HTML. A fence, a heading and a list keep their block rules in richText.
const INLINE_RE = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\n]+\*|\[[^\]\n]+\]\([^)\s]+\))/g;
function inline(el, text) {
  let last = 0, m;
  INLINE_RE.lastIndex = 0;
  while ((m = INLINE_RE.exec(text))) {
    if (m.index > last) el.append(document.createTextNode(text.slice(last, m.index)));
    const tok = m[0];
    if (tok.startsWith("**")) { const b = document.createElement("b"); b.textContent = tok.slice(2, -2); el.append(b); }
    else if (tok.startsWith("`")) { const c = document.createElement("code"); c.className = "sq-ic"; c.textContent = tok.slice(1, -1); el.append(c); }
    else if (tok.startsWith("[")) {
      const k = tok.indexOf("]("), a = document.createElement("a");
      a.textContent = tok.slice(1, k); a.href = tok.slice(k + 2, -1); a.target = "_blank"; a.rel = "noopener"; el.append(a);
    } else { const it = document.createElement("i"); it.textContent = tok.slice(1, -1); el.append(it); }
    last = m.index + tok.length;
  }
  if (last < text.length) el.append(document.createTextNode(text.slice(last)));
  return el;
}
// A markdown table (owner, 2026-11-01): a header row of pipes, a row of dashes, then the body. Built as a table element, so
// every cell is text and a cell never carries markup of its own.
const TBL_ROW = /^\s*\|(.+)\|\s*$/, TBL_SEP = /^\s*\|?\s*:?-{2,}[\s:|-]*$/;
const tblCells = row => row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim());
function tableNode(head, rows) {
  const t = document.createElement("table"); t.className = "sq-tbl";
  const hr = document.createElement("tr");
  head.forEach(c => { const th = document.createElement("th"); inline(th, c); hr.append(th); });
  const thead = document.createElement("thead"); thead.append(hr); t.append(thead);
  const tb = document.createElement("tbody");
  rows.forEach(r => { const tr = document.createElement("tr");
    head.forEach((_, i) => { const td = document.createElement("td"); inline(td, r[i] == null ? "" : r[i]); tr.append(td); });
    tb.append(tr); });
  t.append(tb);
  return t;
}
function richText(text) {
  const box = document.createElement("div");
  box.className = "sq-txt";
  const lines = String(text || "").split("\n");
  let buf = [], ul = null, ol_ = null, fence = null;
  const para = () => { if (!buf.length) return; const p = document.createElement("div"); p.className = "sq-p";
    buf.forEach((ln, i) => { if (i) p.append(document.createElement("br")); inline(p, ln); }); box.append(p); buf = []; };
  const list = () => { if (ul) { box.append(ul); ul = null; } if (ol_) { box.append(ol_); ol_ = null; } };
  const code = () => { if (!fence) return; const d = document.createElement("details"); d.className = "sq-code";
    const s = document.createElement("summary"); s.textContent = "Code · " + fence.length + " line" + (fence.length === 1 ? "" : "s");
    const pre = document.createElement("pre"); pre.textContent = fence.join("\n");
    d.append(s, pre); if (fence.length <= 8) d.open = true; box.append(d); fence = null; };
  for (let li_ = 0; li_ < lines.length; li_++) {
    const raw = lines[li_];
    if (TBL_ROW.test(raw) && TBL_SEP.test(lines[li_ + 1] || "")) {
      para(); list();
      const head = tblCells(raw), rows = [];
      let k = li_ + 2;
      while (k < lines.length && TBL_ROW.test(lines[k])) { rows.push(tblCells(lines[k])); k++; }
      box.append(tableNode(head, rows)); li_ = k - 1; continue;
    }
    if (raw.trim().startsWith("```")) { if (fence) code(); else { para(); list(); fence = []; } continue; }
    if (fence) { fence.push(raw); continue; }
    const h = /^#{1,6}\s+(.*)$/.exec(raw);
    if (h) { para(); list(); const d = document.createElement("div"); d.className = "sq-h"; inline(d, h[1]); box.append(d); continue; }
    const li = /^\s*[-*]\s+(.*)$/.exec(raw);
    if (li) { para(); if (!ul) { ul = document.createElement("ul"); ul.className = "sq-ul"; } const it = document.createElement("li"); inline(it, li[1]); ul.append(it); continue; }
    const ol = /^\s*(\d+)[.)]\s+(.*)$/.exec(raw);
    if (ol) { para(); if (!ol_) { ol_ = document.createElement("ol"); ol_.className = "sq-ul sq-ol"; } const it = document.createElement("li"); inline(it, ol[2]); ol_.append(it); continue; }
    if (!raw.trim()) { para(); list(); continue; }
    const img = IMG_RE.exec(raw);
    if (img && cur && cur.src) { para(); list(); box.append(imageNode(img[1])); continue; }
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
const CLOCK = typeof Intl === "undefined" ? null
  : new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
function clock(iso) {
  if (!iso || !CLOCK) return "";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : CLOCK.format(d);
}
function turnNode(t) {
  // A system turn folds to one line (owner, 2026-11-01): a reminder or a hook's note is worth one sentence, not a wall.
  const onlyImage = IMG_RE.test(String(t.text || ""));
  if ((t.role === "system" || t.role === "tool") && !onlyImage) {
    const d = document.createElement("details"), sum = document.createElement("summary");
    d.className = "sq-turn sq-" + t.role + " sq-fold";
    const one = String(t.text || "").replace(/\s+/g, " ").trim();
    sum.textContent = (t.role === "system" ? "system" : "tool") + " \u00b7 " + (one.length > 72 ? one.slice(0, 72) + "\u2026" : one);
    d.append(sum, richText(t.text));
    return d;
  }
  const d = document.createElement("div"), who = document.createElement("span"), head = document.createElement("div");
  // A screenshot is the owner's, whatever the harness calls the turn: it is a bubble of its own on the owner's side
  // (owner, 2026-11-01).
  const role = onlyImage ? "owner" : t.role;
  d.className = "sq-turn sq-" + role + (onlyImage ? " sq-onlyimg" : "");
  who.className = "sq-who";
  // The owner's bubble says "you"; the answer's says the model that wrote it, as the transcript names it (owner, 2026-11-02).
  who.textContent = onlyImage ? "" : t.role === "owner" ? "\u203A you" : String(t.m || "");
  const body = richText(t.text);
  // The clock rides the bubble's own header line, at the right corner, level with the owner's label: floated alone it hung
  // above the bubble and pushed its first line down (owner, 2026-11-02, #1099).
  head.className = "sq-head";
  if (who.textContent) head.append(who);
  const at = clock(t.at);
  if (at) {
    const tm = document.createElement("span");
    tm.className = "sq-time";
    tm.textContent = at;
    head.append(tm);
  }
  if (head.childElementCount) d.append(head);
  d.append(body);
  clamp(body, CLAMP[role] || 116);
  return d;
}
// An image the owner sent belongs to the message it came with (owner, 2026-11-01): the harness records it as a turn of its
// own, so it would draw as a second bubble under the first. A turn that is only an image joins the owner turn just above it.
function stitch(turns) {
  const out = [];
  for (const t of turns || []) {
    const prev = out[out.length - 1], mine = IMG_RE.test(String(t.text || ""));
    const join = mine && prev && (prev.role === "owner" || IMG_RE.test(String(prev.text || "")));
    if (join) out[out.length - 1] = { ...prev, text: String(prev.text || "").replace(/\s+$/, "") + "\n" + String(t.text) };
    else out.push(t);
  }
  return out;
}
function feed(turns) {
  const { earlier, shown } = SQ.splitEarlier(turns), nodes = [], said = stitch(shown);
  if (earlier.length) {
    const d = document.createElement("details"); d.className = "sq-earlier";
    const s = document.createElement("summary"); s.textContent = "Earlier activity · " + earlier.length + " turns";
    d.append(s, ...stitch(earlier).map(turnNode)); nodes.push(d);
  }
  nodes.push(...said.map(t => { const n = turnNode(t); if (t.k) n.dataset.k = t.k; return n; }));
  return nodes;
}
// The feed stays at its newest line (owner, 2026-11-01): a scroll away from the end stops that, and the bottom is re-pinned
// after the frame in which images and open blocks settle their height.
let stick = true;
function pinBottom() { log.scrollTop = log.scrollHeight; requestAnimationFrame(() => { log.scrollTop = log.scrollHeight; }); }
log.addEventListener("scroll", () => { stick = log.scrollHeight - log.scrollTop - log.clientHeight < 48; });
log.addEventListener("load", () => { if (stick) pinBottom(); }, true);   // an image lands after the paint
// The turns on the screen. With the push stream on, the first read names the session's offset, and each pushed turn merges
// into these turns as it arrives; the poll only merges what the stream has not sent (SPEC 1085, #1097).
let talk = [], watched = null, hooked = false;
const sigOf = list => { const last = list[list.length - 1]; return list.length + ":" + (last ? last.id + ":" + last.text.length : ""); };
function repaint(pin) {
  const edge = () => log.getBoundingClientRect().top;
  const top = pin ? null : [...log.children].find(e => e.dataset.k && e.getBoundingClientRect().bottom > edge());
  const key = top ? top.dataset.k : null, was = top ? top.getBoundingClientRect().top : null;
  log.replaceChildren(...feed(talk));
  if (pin) return pinBottom();
  const again = key == null ? null : [...log.children].find(e => e.dataset.k === key);
  log.scrollTop = SQ.viewAfter(was, again ? again.getBoundingClientRect().top : null, log.scrollTop);
}
function paintTalk(pin) {
  if (talk.length) repaint(pin); else empty("No turns yet.");
  lastSig = sigOf(talk);
}
function watchTalk(s, epoch, end) {
  if (watched && watched !== s.sid) window.squadStream.unwatch(watched);
  watched = s.sid; talk = [];
  window.squadStream.watch(s.sid, epoch, end);
}
function hookStream() {
  if (hooked || !window.squadStream) return;
  hooked = true;
  window.squadStream.on("conversation", d => {
    if (!cur || d.sid !== cur.sid || d.sid !== watched) return;
    talk = SQ.mergeTurns(talk, SQ.streamTurns(d.lines));
    if (!ov.hidden) paintTalk(stick);
  });
  window.squadStream.on("gap", d => {
    if (!cur || d.sid !== cur.sid || d.sid !== watched) return;
    talk = [];
    if (!ov.hidden) paintTalk(true);
  });
}
async function loadConversation() {
  const s = cur;
  if (!s || ov.hidden) return;
  try {
    const r = await fetch(s.src.api + "/conversation?sid=" + encodeURIComponent(s.sid), { cache: "no-store" });
    if (cur !== s) return;
    // A 404 is a question for the session list, never a verdict of its own: only the list read may drop the session, and
    // one miss is asked about at most every five seconds (#1102, owner, 2026-11-01).
    if (!r.ok) {
      console.warn("conversation:", r.status);
      if (r.status === 404 && Date.now() - missT > 5000) { missT = Date.now(); loadSessions(); }
      return;
    }
    const page = await r.json(), turns = page.turns || [];
    setActivity(page.activity);
    hookStream();
    const streamOn = !!window.squadStream, fresh = streamOn && watched !== s.sid;
    if (fresh) watchTalk(s, page.epoch, page.end);
    talk = streamOn ? SQ.mergeTurns(talk, turns) : turns;
    if (!fresh && SQ.feedFrom(streamOn && window.squadStream.live(), watched === s.sid) === "push") return;
    const sig = sigOf(talk);
    if (sig === lastSig) return;
    paintTalk(!lastSig || stick);   // the talk opens at its newest line, until the reader scrolls up (owner, 2026-11-01)
  } catch (e) { console.warn("conversation:", e); }   // the next poll tries again; a swallowed failure froze the feed silently (#1079)
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
// The text size is the reader's (owner, 2026-11-01): two buttons in the header step it, and the choice outlives the page.
const FS_KEY = "sq-ov-fs", FS_MIN = 13, FS_MAX = 22, FS_STEP = 1;   // 13 px is the served floor: the reader never shrinks below it
let fsNow = 14;
function fsSet(px) {
  fsNow = Math.min(FS_MAX, Math.max(FS_MIN, Math.round(px)));
  ov.style.setProperty("--sq-fs", fsNow + "px");
  const down = $("sq-ov-fsdown"), up = $("sq-ov-fsup");
  if (down) down.disabled = fsNow <= FS_MIN;
  if (up) up.disabled = fsNow >= FS_MAX;
  try { localStorage.setItem(FS_KEY, String(fsNow)); } catch (e) {}
}
function fsInit() {
  let v = 14;
  try { v = Number(localStorage.getItem(FS_KEY)) || 14; } catch (e) {}
  fsSet(v);
  const down = $("sq-ov-fsdown"), up = $("sq-ov-fsup");
  if (down) down.onclick = () => fsSet(fsNow - FS_STEP);
  if (up) up.onclick = () => fsSet(fsNow + FS_STEP);
}
const OPEN_KEY = "sq-ov-open";
function wantOpen(on) { try { localStorage.setItem(OPEN_KEY, on ? "1" : "0"); } catch (e) {} }
function wasOpen() { try { return localStorage.getItem(OPEN_KEY) === "1"; } catch (e) { return false; } }
function openOverlay(){
  ov.hidden = false; btn.setAttribute("aria-expanded", "true"); wantOpen(true);
  applyLayout();
  // The field opens empty (owner, 2026-11-01): the page context is one click away in the + menu, never typed for you.
  fsInit();
  if (cur && watched === cur.sid && talk.length) paintTalk(true);   // turns the stream pushed while the panel was shut
  loadSessions();
  clearInterval(timer); timer = setInterval(loadConversation, SQ.POLL_MS);
  clearInterval(liveTick); liveTick = setInterval(paintLive, 120);
  text.focus();
}
function closeOverlay() {
  ov.hidden = true; btn.setAttribute("aria-expanded", "false"); setMenu(false);
  clearInterval(timer); timer = 0;
  clearInterval(liveTick); liveTick = 0;
  wantOpen(false);
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
// A background tab throttles timers to about once a minute, so a reader who works in another window waits (#1079):
// the talk is read again the moment this one is looked at or focused. Registered once, never per open.
const reread = () => { if (!ov.hidden && !document.hidden) loadConversation(); };
document.addEventListener("visibilitychange", () => { lastT = 0; kick(); reread(); });
addEventListener("focus", reread);
if (reduced.addEventListener) reduced.addEventListener("change", kick);
// A new hub build reloads the open page, so no tab keeps an overlay the hub no longer serves (owner, 2026-11-01).
let build = null;
(async () => {
  let srcs = [];
  try { srcs = await window.SQUAD_OV_SOURCES(); } catch (e) { return; }
  const src = (srcs || [])[0];
  if (!src) return;
  const route = SQ.versionRoute(src.api);
  setInterval(async () => {
    try {
      const s = SQ.buildStep(build, (await (await fetch(route, { cache: "no-store" })).json()).v);
      build = s.seen;
      if (s.reload) location.reload();
    } catch (e) { /* the hub did not answer: the page keeps what it shows */ }
  }, SQ.POLL_MS);
})();
applyLayout(); syncSend();
// A reload puts the panel back as it was left (owner, 2026-11-01): the page reloads when the hub build changes, and the
// terminal closing on that reload loses the reader's place.
if (wasOpen()) openOverlay();
})();
