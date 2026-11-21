"""The stub DOM every phone-page test boots the served page on (SPEC 2498, #2501).

The page is composed from its files the way the hub composes it, then booted under node over a stub DOM whose `fetch`
answers the board and the promote route: a screen is read as elements, hrefs, chips and text, never by a substring of the
page's own source. This module holds no test; the suites for the Work, Specs, Runs and Now screens use it.
"""
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
COLUMNS_PY = SCRIPTS / "board_columns.py"
NODE = shutil.which("node")

# The page as the hub serves it, booted under node over a stub DOM: the elements come from the page's own markup, `fetch`
# answers the routes below, and timers are recorded, never waited for. The driver reads and sets the page's own names.
DOM_HARNESS = r"""
const vm = require("vm");
const hostSetTimeout = setTimeout;
const PAGE = __PAGE__;
const VOID = new Set(["meta", "link", "br", "hr", "img", "input", "source", "area", "base", "col", "embed", "param", "track", "wbr"]);
class ClassList {
  constructor(el) { this.el = el; }
  add(...cs) { cs.forEach(c => this.el.classes.add(c)); }
  remove(...cs) { cs.forEach(c => this.el.classes.delete(c)); }
  contains(c) { return this.el.classes.has(c); }
  toggle(c, force) { const on = force === undefined ? !this.el.classes.has(c) : !!force; on ? this.add(c) : this.remove(c); return on; }
}
class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = []; this.parentElement = null; this.attrs = {}; this.dataset = {};
    this.style = {}; this.classes = new Set(); this.listeners = {};
    this.id = ""; this.hidden = false; this.disabled = false; this._text = "";
    this.type = ""; this.value = ""; this.checked = false; this.href = ""; this.src = "";
    this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 0; this.tabIndex = 0;
    this.classList = new ClassList(this);
  }
  get className() { return [...this.classes].join(" "); }
  set className(v) { this.classes = new Set(String(v || "").split(/\s+/).filter(Boolean)); }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(""); }
  set textContent(v) { this.children = []; this._text = String(v); }
  // A markup write becomes real children, as a browser makes it, so a test reads the color of the element the page drew
  // from `switcherPaint` or a chart draw, not only the markup string it wrote. The getter returns the string the page
  // wrote when it wrote one, so a test that reads the markup still reads it whole.
  get innerHTML() { return this._html !== undefined ? this._html : this.children.map(c => c.outer()).join(""); }
  set innerHTML(v) {
    this._html = String(v); this._text = ""; this.children = [];
    for (const k of parseHtml(this._html).children.slice()) { k.parentElement = this; this.children.push(k); }
  }
  outer() {
    const at = Object.entries(this.attrs).map(([k, v]) => ` ${k}="${v}"`).join("");
    const tag = this.tagName.toLowerCase();
    return `<${tag}${this.id ? ` id="${this.id}"` : ""}${this.classes.size ? ` class="${this.className}"` : ""}${at}>`
      + this._text + this.children.map(c => c.outer()).join("") + `</${tag}>`;
  }
  append(...kids) { for (const k of kids) { if (k && k.tagName) { k.parentElement = this; this.children.push(k); } } }
  appendChild(k) { this.append(...(k.tagName === "#ROOT" ? k.children : [k])); return k; }
  prepend(...kids) { const ok = kids.filter(k => k && k.tagName); for (const k of ok) k.parentElement = this; this.children.unshift(...ok); }
  replaceChildren(...kids) { this.children = []; this._text = ""; this.append(...kids); }
  setAttribute(name, v) {
    if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = String(v);
    else if (name === "id") this.id = String(v);
    else if (name === "class") this.className = v;
    else this.attrs[name] = String(v);
  }
  removeAttribute(name) { delete this.attrs[name]; }
  scrollIntoView() {}
  getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
  remove() { const p = this.parentElement; if (!p) return; p.children = p.children.filter(c => c !== this); this.parentElement = null; }
  click() { if (typeof this.onclick === "function") this.onclick({ target: this }); for (const fn of this.listeners.click || []) fn({ target: this }); }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  descendants() { const out = []; const walk = e => { for (const c of e.children) { out.push(c); walk(c); } }; walk(this); return out; }
  querySelectorAll(sel) { return qsa(this, sel); }
  querySelector(sel) { return qsa(this, sel)[0] || null; }
  closest(sel) { for (let e = this; e; e = e.parentElement) if (matches(e, sel)) return e; return null; }
}
function attrValue(el, name) {
  if (name.startsWith("data-")) return el.dataset[name.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())];
  if (name === "id") return el.id;
  if (name === "class") return el.className;
  return el.attrs[name];
}
function matches(el, sel) {
  const re = /([#.]?[\w-]+)|\[([\w-]+)(?:=["']?([^\]"']*)["']?)?\]/g;
  let m, any = false;
  while ((m = re.exec(sel))) {
    any = true;
    if (m[1]) {
      if (m[1][0] === "#") { if (el.id !== m[1].slice(1)) return false; }
      else if (m[1][0] === ".") { if (!el.classes.has(m[1].slice(1))) return false; }
      else if (el.tagName !== m[1].toUpperCase()) return false;
    } else if (m[2]) {
      const v = attrValue(el, m[2]);
      if (m[3] === undefined) { if (v === undefined) return false; }
      else if (String(v) !== m[3]) return false;
    }
  }
  return any;
}
function qsa(root, sel) {
  const parts = String(sel).trim().split(/\s+/);
  let hits = [root];
  for (const part of parts) {
    const next = [];
    for (const base of hits) for (const e of base.descendants()) if (matches(e, part) && !next.includes(e)) next.push(e);
    hits = next;
  }
  return hits;
}
// The page's own stylesheet, resolved the way a browser applies it: a rule whose selector matches an element paints
// that element, the more specific selector wins and then the later rule, and var(--x) answers through :root. A test
// that reads a computed color therefore reads what the page paints, not the token block it happens to declare. The
// harness skips a state or pseudo-element selector and an at-rule (a `@media` rule the base layer does not hold), so a
// rule it cannot evaluate never paints here, and the read is conservative.
const CSS = (PAGE.match(/<style[^>]*>([\s\S]*?)<\/style>/gi) || [])
  .map(s => s.replace(/^<style[^>]*>/i, "").replace(/<\/style>$/i, "")).join("\n").replace(/\/\*[\s\S]*?\*\//g, "");
function stripAtRules(css) {
  let out = "", i = 0;
  while (i < css.length) {
    const at = css.indexOf("@", i);
    if (at < 0) { out += css.slice(i); break; }
    out += css.slice(i, at);
    const open = css.indexOf("{", at);
    if (open < 0) break;
    let depth = 0, j = open;
    for (; j < css.length; j++) { if (css[j] === "{") depth++; else if (css[j] === "}" && --depth === 0) { j++; break; } }
    i = j;
  }
  return out;
}
const RULES = [];
for (const m of stripAtRules(CSS).matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
  const decls = {};
  for (const d of m[2].matchAll(/([-\w]+)\s*:\s*([^;]+)/g)) decls[d[1]] = d[2].trim();
  for (const sel of m[1].split(",").map(s => s.trim()).filter(Boolean)) RULES.push({ sel, decls });
}
const TOKENS = {};
for (const r of RULES) if (r.sel === ":root") Object.assign(TOKENS, r.decls);
const INHERITED = new Set(["color", "font-size", "font-family", "font-weight", "line-height", "text-align", "visibility"]);
const selectorOK = s => s === ":root" || !s.includes(":");
function specificity(sel) {
  const ids = (sel.match(/#[\w-]+/g) || []).length;
  const cls = (sel.match(/\.[\w-]+|\[[^\]]*\]/g) || []).length;
  const tags = (sel.match(/(^|[\s>+~])[a-zA-Z][\w-]*/g) || []).length;
  return ids * 10000 + cls * 100 + tags;
}
function complexMatch(el, sel) {
  const parts = [];
  let child = false;
  for (const t of sel.trim().replace(/\s*>\s*/g, " > ").split(/\s+/)) {
    if (t === ">") { child = true; continue; }
    parts.push({ t, child }); child = false;
  }
  const at = (e, i) => {
    if (!e || !matches(e, parts[i].t)) return false;
    if (i === 0) return true;
    if (parts[i].child) return at(e.parentElement, i - 1);
    for (let p = e.parentElement; p; p = p.parentElement) if (at(p, i - 1)) return true;
    return false;
  };
  return parts.length > 0 && at(el, parts.length - 1);
}
function resolveVars(v, depth) {
  if ((depth || 0) > 8) return String(v);
  return String(v).replace(/var\((--[\w-]+)(?:,\s*([^)]*))?\)/g, (_m, name, fb) => {
    if (TOKENS[name] !== undefined) return resolveVars(TOKENS[name], (depth || 0) + 1);
    return fb !== undefined ? resolveVars(fb, (depth || 0) + 1) : "";
  }).trim();
}
function declared(el, prop) {
  let win = null;
  for (const r of RULES) {
    if (!(prop in r.decls) || !selectorOK(r.sel) || !complexMatch(el, r.sel)) continue;
    const sp = specificity(r.sel);
    if (!win || sp >= win.sp) win = { sp, v: r.decls[prop] };   // a later rule wins at equal specificity
  }
  return win ? resolveVars(win.v) : null;
}
const asRgb = v => {
  const m = /^#([0-9a-f]{3,8})$/i.exec(v);
  if (!m) return v;
  let h = m[1];
  if (h.length === 3) h = h.split("").map(c => c + c).join("");
  const [r, g, b] = [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16));
  return h.length === 8 ? "rgba(" + r + ", " + g + ", " + b + ", " + (parseInt(h.slice(6, 8), 16) / 255).toFixed(3) + ")"
                        : "rgb(" + r + ", " + g + ", " + b + ")";
};
function propValue(el, prop) {
  if (!el) return "";
  const inline = el.style && (el.style[prop] || (el.style.getPropertyValue ? el.style.getPropertyValue(prop) : ""));
  if (inline) return prop === "color" ? asRgb(resolveVars(inline)) : resolveVars(inline);   // an inline style wins
  if (String(prop).startsWith("--")) return TOKENS[String(prop).slice(2)] === undefined ? "" : resolveVars(TOKENS[String(prop).slice(2)]);
  const own = declared(el, prop);
  if (own !== null) return prop === "color" ? asRgb(own) : own;
  return INHERITED.has(prop) && el.parentElement ? propValue(el.parentElement, prop) : "";
}
function getComputedStyle(el) {
  return { getPropertyValue: p => propValue(el, p), get color() { return propValue(el, "color"); } };
}
function parseAttrs(s) {
  const out = {}, re = /([\w:-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;
  let m;
  while ((m = re.exec(s || ""))) out[m[1]] = m[2] !== undefined ? m[2] : m[3] !== undefined ? m[3] : m[4] !== undefined ? m[4] : "";
  return out;
}
function parseHtml(html) {
  const src = html.replace(/<style[\s\S]*?<\/style>/gi, "").replace(/<script[\s\S]*?<\/script>/gi, "");
  const root = new El("#root");
  const stack = [root], re = /<(\/?)([a-zA-Z][\w:-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>/g;
  let m;
  while ((m = re.exec(src))) {
    const closing = m[1] === "/", tag = m[2], rest = m[3] || "";
    if (closing) {
      for (let i = stack.length - 1; i > 0; i--) if (stack[i].tagName === tag.toUpperCase()) { stack.length = i; break; }
      continue;
    }
    const el = new El(tag), at = parseAttrs(rest);
    for (const [k, v] of Object.entries(at)) {
      if (k === "hidden") { el.hidden = true; continue; }
      el.setAttribute(k, v);
    }
    stack[stack.length - 1].append(el);
    if (!VOID.has(tag.toLowerCase()) && !/\/\s*$/.test(rest)) stack.push(el);
  }
  return root;
}
const root = parseHtml(PAGE);
const body = new El("body");
root.append(body);
const docListeners = {};
const reply = (body, status, ct) => ({
  ok: status >= 200 && status < 300, status,
  headers: { get: n => (String(n).toLowerCase() === "content-type" ? ct : null) },
  json: async () => JSON.parse(body), text: async () => body,
});
const jsonReply = (o, status) => reply(JSON.stringify(o), status || 200, "application/json");
const boot = {
  __state: { board: __BOARD__, promote: __PROMOTE__, reads: 0, routes: __ROUTES__, posts: [], gets: {}, streams: [] },
  __skew: 0,
  fetch: async (path, opts) => {
    const name = String(path).split("?")[0];            // a route named here answers as the hub would; a write is recorded
    if (!(opts && opts.method === "POST")) boot.__state.gets[name] = (boot.__state.gets[name] || 0) + 1;
    if (boot.__state.routes[name]) {
      if (opts && opts.method === "POST") boot.__state.posts.push({ path: name, url: String(path), opts });   // the raw URL keeps the query a write carries (#1155)
      const q = boot.__state.routes[name];
      const row = q.length > 1 ? q.shift() : q[0];
      return reply(row.body, row.status || 200, row.ct || "application/json");
    }
    if (String(path).startsWith("/api/promote")) {
      if (opts && opts.method === "POST") boot.__state.posts.push({ path: name, url: String(path), opts });   // a lost reply still counts the send
      const p = boot.__state.promote || { status: 200, ct: "application/json", body: "{}" };
      if (p.throw) throw new Error(p.throw === true ? "Failed to fetch" : String(p.throw));   // no answer at all, as a dropped network gives
      return reply(p.body, p.status, p.ct);
    }
    if (String(path).startsWith("/api/board")) {
      const q = boot.__state.board;
      boot.__state.reads++;
      const b = q.length > 1 ? q.shift() : q[0];
      return jsonReply(b);
    }
    if (String(path).startsWith("/api/status")) return jsonReply({ loop: { on: false, since: "1h" }, live: 0, spend_today: 0, open_bugs: 0, warnings: [], load: { line: "" } });
    if (String(path).startsWith("/api/hub")) return jsonReply({ repos: [] });
    if (String(path).startsWith("/api/issue")) return jsonReply({ err: "not read here" });
    return jsonReply({});
  },
};
const timers = {};
const intervals = [];
let timerSeq = 0;
const RealDate = Date;
class FakeDate extends RealDate {
  constructor(...a) { if (a.length === 0) super(RealDate.now() + boot.__skew); else super(...a); }
  static now() { return RealDate.now() + boot.__skew; }
}
class FakeEventSource {
  constructor(url) { this.url = url; this.handlers = {}; boot.__state.streams.push(this); }
  addEventListener(t, fn) { (this.handlers[t] = this.handlers[t] || []).push(fn); }
  close() { this.closed = true; }
}
const box = {
  console, JSON, Math, Date: FakeDate, Object, Array, String, Number, Promise, Set, Map, Error, RegExp, parseInt, parseFloat,
  isNaN, encodeURIComponent, decodeURIComponent, Boolean, Symbol, TypeError,
  fetch: boot.fetch, EventSource: FakeEventSource,
  performance: { now: () => RealDate.now() + boot.__skew },   // a browser has one; the overlay's lens reads it on a move
  scrollY: 0,
  innerHeight: 800,   // one phone screen: the thumb zone is its bottom 40% (#1142)
  innerWidth: 800,
  matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  requestAnimationFrame: fn => box.setTimeout(fn, 0),
  getComputedStyle,
  MutationObserver: class { observe() {} disconnect() {} },
  scrollTo: (_x, y) => { box.scrollY = y; },
  __skip: ms => { boot.__skew += ms; return boot.__skew; },
  __state: boot.__state,
  Option: class { constructor(text, value) { this.text = text; this.value = value; } },
  setTimeout: (fn, ms) => { const id = ++timerSeq; timers[id] = { fn, ms }; return id; },
  clearTimeout: id => { delete timers[id]; },
  setInterval: (fn, ms) => { intervals.push({ fn, ms }); return 0; },
  clearInterval: () => {},
  __timers: timers,
  __intervals: intervals,
  __fire: () => { const ids = Object.keys(timers); for (const id of ids) { const t = timers[id]; delete timers[id]; t.fn(); } return ids.length; },
  __tick: ms => new Promise(r => hostSetTimeout(r, ms || 0)),
  addEventListener: (t, fn) => { (docListeners[t] = docListeners[t] || []).push(fn); },
  __dispatch: (t, ev) => { for (const fn of docListeners[t] || []) fn(ev); },
  localStorage: { _m: new Map(), getItem(k) { return this._m.has(k) ? this._m.get(k) : null; }, setItem(k, v) { this._m.set(k, String(v)); } },
  navigator: { serviceWorker: { register: () => Promise.resolve() } },
  location: { pathname: "/x/y/mobile", href: "/x/y/mobile", hash: __HASH__ },   // the hash the address holds before the page loads (#1459)
  sessionStorage: { _m: new Map(), getItem(k) { return this._m.has(k) ? this._m.get(k) : null; }, setItem(k, v) { this._m.set(k, String(v)); } },
};
box.sessionStorage = box.sessionStorage;
box.window = box;
const popQueue = [];
// An entry keeps the address it names, as the browser's does: a push or a replace sets location.hash, and Back or Forward
// restores the hash of the entry it lands on.
box.history = {
  entries: __ENTRIES__, index: __INDEX__,
  get state() { return this.entries[this.index].state; },
  go(url) { if (typeof url === "string" && url.startsWith("#")) box.location.hash = url; },
  pushState(s, _t, url) {
    this.entries.length = this.index + 1;
    this.entries.push({ state: s, url: url || "" }); this.index++; this.go(url);
  },
  replaceState(s, _t, url) { this.entries[this.index] = { state: s, url: url || "" }; this.go(url); },
  back() { if (this.index > 0) { this.index--; this.go(this.entries[this.index].url); popQueue.push(1); } },
  forward() {
    if (this.index < this.entries.length - 1) { this.index++; this.go(this.entries[this.index].url); popQueue.push(1); }
  },
};
box.__popOne = () => { if (popQueue.length) { popQueue.shift(); box.__dispatch("popstate", {}); } };
box.__popAll = () => { while (popQueue.length) box.__popOne(); };
box.__pending = () => popQueue.length;
box.document = {
  hidden: false,
  getElementById: id => { const hit = root.descendants().find(e => e.id === id); return hit || null; },
  createElement: tag => new El(tag),
  createElementNS: (_ns, tag) => new El(tag),
  body,
  documentElement: Object.assign(new El("html"), { style: { setProperty() {}, removeProperty() {} } }),
  createRange: () => ({ createContextualFragment: html => parseHtml(html) }),
  querySelector: sel => qsa(root, sel)[0] || null,
  querySelectorAll: sel => qsa(root, sel),
  addEventListener: (t, fn) => { (docListeners[t] = docListeners[t] || []).push(fn); },
};
const ctx = vm.createContext(box);
for (const m of PAGE.matchAll(/<script>([\s\S]*?)<\/script>/g)) vm.runInContext(m[1], ctx);
const DRIVER = __DRIVER__;
(async () => {
  await box.__tick(10);                       // the page's own status and profile reads settle
  const out = await vm.runInContext("(async () => {" + DRIVER + "\n})()", ctx);
  console.log("__RESULT__" + JSON.stringify(out));
})().catch(e => { console.log("__ERROR__" + (e && e.stack ? e.stack : String(e))); process.exit(1); });
"""

BOARD_ONE = {"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None,
             "groups": [{"name": "Backlog", "n": 1, "items": [{"n": 7, "t": "a ticket", "l": [], "pm": True, "pmt": "Todo"}]},
                        {"name": "Todo", "n": 0, "items": []}]}


def run_page(page, driver, board=None, promote=None, routes=None, history_state=None, history_below=False, hash=None):
    """The served mobile page booted under node over the stub DOM, then one driver run inside it. The driver is one async
    body: it reads and sets the page's own names (`pending`, `$`, `stageMove`) and returns the facts the test asserts.
    `routes` answers any other path the driver's screen asks, and records every POST it takes. `history_state` is the entry
    the page boots on; `history_below` puts a page entry under it, as a reload of a layer's entry has. `hash` is the hash
    the address holds before the page loads (#1459)."""
    entries = [{"state": {}, "url": hash or ""},
               {"state": history_state, "url": ""}] if history_below else [{"state": history_state, "url": hash or ""}]
    script = (DOM_HARNESS.replace("__PAGE__", json.dumps(page))
                         .replace("__DRIVER__", json.dumps(driver))
                         .replace("__BOARD__", json.dumps(board or [BOARD_ONE]))
                         .replace("__ROUTES__", json.dumps(routes or {}))
                         .replace("__PROMOTE__", json.dumps(promote))
                         .replace("__HASH__", json.dumps(hash or ""))
                         .replace("__ENTRIES__", json.dumps(entries))
                         .replace("__INDEX__", str(len(entries) - 1)))
    path = pathlib.Path(tempfile.mkdtemp()) / "page.js"
    path.write_text(script)
    p = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=60)
    shutil.rmtree(path.parent, ignore_errors=True)
    assert p.returncode == 0, p.stderr or p.stdout
    line = [row for row in p.stdout.splitlines() if row.startswith("__RESULT__")]
    assert line, p.stdout
    return json.loads(line[0][len("__RESULT__"):])


def route_tabs(page):
    """The tabs the page's own route table names, in table order, each as (tab, label). One reader for every test that
    names the five tabs, so the list never drifts from the page it checks (SPEC 2498, #2499)."""
    body = page[page.index("const ROUTE = {"):]
    body = body[:body.index("\n};")]
    return re.findall(r'^\s{2}(\w+):\s*\{\s*panel:\s*"\w+"\s*,\s*label:\s*"(\w+)"', body, re.M)


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_js(src, expr, prelude=""):
    script = f"{src}\n{prelude}\nconsole.log(JSON.stringify({expr}));"
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    return _module("hub_mobile_move", HUB_PY)


