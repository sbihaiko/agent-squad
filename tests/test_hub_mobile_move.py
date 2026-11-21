"""The owner's move from the phone (#829): a drag of a promotable Backlog card past the marked threshold, the Undo that
delays the write, and the cancellation of a pending move when the app is hidden or the profile changes.

What may move and where it goes are the hub's facts on the row (`pm`, `pmt`): the page names no column of its own, and
the server side of the rule is proved beside the promote route (`test_hub_promote.py`).

The rules are one client module, run under node alone with no DOM and no server. The page is composed from its files the
way the hub composes it, and it is then booted under node over a stub DOM whose `fetch` answers the board and the promote
route: a refusal is proved on the card itself, after the board reads again, never by a substring of the page's text.
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
MOVE_JS = SCRIPTS / "hub-mobile-move.js"
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
  getComputedStyle: () => ({ getPropertyValue: () => "" }),
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


@pytest.mark.node_bound
def test_a_release_before_the_threshold_does_nothing():
    src = MOVE_JS.read_text()
    assert run_js(src, "moveDrag({promotable:true,dx:MOVE_MIN-1,dy:2})") is None
    assert run_js(src, "moveDrag({promotable:true,dx:MOVE_MIN,dy:2})") == {"act": "stage"}


@pytest.mark.node_bound
def test_only_a_card_the_hub_marked_promotable_can_be_staged():
    src = MOVE_JS.read_text()
    assert run_js(src, "moveDrag({promotable:false,dx:500,dy:2})") is None
    assert run_js(src, "moveDrag({promotable:true,dx:-500,dy:2})") is None, "a drag left is no move forward"
    assert run_js(src, "moveDrag({promotable:true,dx:10,dy:200})") is None, "a vertical move is the list scrolling"


@pytest.mark.node_bound
def test_the_undo_delays_the_write():
    src = MOVE_JS.read_text()
    assert run_js(src, "moveAct(moveStage(7,'the row names it',1000),'write',1000+UNDO_MS-1).why") == ""
    assert run_js(src, "moveAct(moveStage(7,'the row names it',1000),'write',1000+UNDO_MS).why") == "written"
    assert run_js(src, "moveStage(7,'the row names it',1000).to") == "the row names it", \
        "the module names no column: the destination is the row's own fact"


@pytest.mark.node_bound
def test_an_undone_move_is_cancelled_and_never_written_later():
    src = MOVE_JS.read_text()
    prelude = "const p=moveAct(moveStage(7,'a column',1000),'undo',2000),w=moveAct(p,'write',99999);"
    assert run_js(src, "[w.why, w.live, w === p]", prelude) == ["undo", False, True]


@pytest.mark.node_bound
@pytest.mark.parametrize("act", ["hidden", "profile", "superseded"])
def test_a_hidden_app_a_profile_change_or_a_second_move_cancels_the_move(act):
    src = MOVE_JS.read_text()
    prelude = f"const p=moveAct(moveStage(7,'a column',1000),{json.dumps(act)},2000),w=moveAct(p,'write',99999);"
    assert run_js(src, "[w.why, w.live]", prelude) == [act, False]


def test_the_move_module_names_no_column_and_makes_no_network_call():
    text = MOVE_JS.read_text()
    table = _module("board_columns_move", COLUMNS_PY)
    for name in [c["name"] for c in table.shipped_columns()] + list(table.ROLE.values()):
        assert name not in text, f"the move module names the column {name!r}: the row names it"
    for banned in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "import "):
        assert banned not in text, f"the move module holds {banned!r}"


def test_the_move_rules_are_inlined_not_fetched(hub):
    assert MOVE_JS.read_text() in hub.MOBILE_PAGE
    assert "/*MOVE*/" not in hub.MOBILE_PAGE
    assert "<script src=" not in hub.MOBILE_PAGE


def test_the_page_posts_the_promote_route_with_the_desktop_body(hub):
    page = hub.MOBILE_PAGE
    assert 'fetch("/api/promote"' in page, "the page does not call the promote route"
    assert 'method: "POST"' in page and '"Content-Type": "application/json"' in page, "the write is not a JSON POST"
    assert "JSON.stringify({ issue:" in page, "the body is not the desktop's `{issue: n}`"


def test_the_page_cancels_a_pending_move_on_hidden_and_on_a_profile_change(hub):
    page = hub.MOBILE_PAGE
    assert 'addEventListener("visibilitychange"' in page and 'if (document.hidden) cancelMove("hidden")' in page
    assert 'cancelMove("profile")' in page, "a profile change does not cancel a pending move"
    assert 'cancelMove("undo")' in page and 'move cancelled' in page, "the Undo does not cancel, or says nothing"


@pytest.mark.node_bound
def test_a_refusal_stays_on_the_card_after_the_board_reads_again(hub):
    """AC4: the promote route's refusal shows its reason on the card, and the reason survives the board read that follows
    the write and every 10 s poll after it. The card is drawn again on every read, so the reason is page state the card
    reads, never a span the next render drops (#829)."""
    err = "the card is a ticket of a spec: it moves with its spec, never alone (ADR-0142)"
    out = run_page(hub.MOBILE_PAGE, """
      await loadBoard();
      const card = $("cards").querySelector('[data-role="card"][data-n="7"]');
      stageMove({ n: 7, pmt: "Todo" }, card);
      __skip(6000);
      __fire();
      await __tick(30);
      const after = $("cards").querySelector('[data-role="card"][data-n="7"]');
      return { reason: after ? (after.querySelector(".creason") || { textContent: null }).textContent : "(no card)",
               pending: pending && pending.n, reads: __state.reads };
    """, promote={"status": 400, "ct": "application/json", "body": json.dumps({"err": err})})
    assert out["pending"] is None, "a written move is no longer pending"
    assert out["reason"] == err, f"the refusal is not on the card after the board read: {out}"
    assert out["reads"] >= 2, "the board did not read again after the write"


@pytest.mark.node_bound
def test_a_refusal_the_hub_answers_as_plain_text_shows_that_text(hub):
    """A refusal that is not JSON (the seam's own 403) still names its reason on the card: 'the hub did not answer' is
    kept for a failed request, never for an answer (#829)."""
    out = run_page(hub.MOBILE_PAGE, """
      await loadBoard();
      const card = $("cards").querySelector('[data-role="card"][data-n="7"]');
      stageMove({ n: 7, pmt: "Todo" }, card);
      __skip(6000);
      __fire();
      await __tick(30);
      const after = $("cards").querySelector('[data-role="card"][data-n="7"]');
      return { reason: after ? (after.querySelector(".creason") || { textContent: null }).textContent : "(no card)" };
    """, promote={"status": 403, "ct": "text/plain; charset=utf-8", "body": "forbidden host"})
    assert out["reason"] == "forbidden host", out


@pytest.mark.node_bound
def test_a_second_staged_move_cancels_the_first_and_says_so(hub):
    """Staging a second move never drops the first without notice: the first is cancelled, and the Undo line names it.
    Only the second move holds a timer, so the first is never written later (#829)."""
    out = run_page(hub.MOBILE_PAGE, """
      await loadBoard();
      const a = $("cards").querySelector('[data-role="card"][data-n="7"]');
      stageMove({ n: 7, pmt: "Todo" }, a);
      stageMove({ n: 8, pmt: "Todo" }, null);
      return { text: $("undo-text").textContent, pending: pending && pending.n, timers: Object.keys(__timers).length };
    """, board=[{"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None,
                 "groups": [{"name": "Backlog", "n": 2, "items": [
                     {"n": 7, "t": "first", "l": [], "pm": True, "pmt": "Todo"},
                     {"n": 8, "t": "second", "l": [], "pm": True, "pmt": "Todo"}]}, {"name": "Todo", "n": 0, "items": []}]}])
    assert out["pending"] == 8, out
    assert "#7: move cancelled" in out["text"], f"the first move was dropped without notice: {out}"
    assert "8" in out["text"], out
    assert out["timers"] == 1, f"the first move keeps a timer: {out}"


@pytest.mark.node_bound
def test_a_mid_drag_board_read_never_stages_a_different_card(hub):
    """The release resolves the dragged card by its number against the board as it now stands. A 10 s read that reorders
    the column during the drag must not stage the card that took the index (#829)."""
    first = {"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None,
             "groups": [{"name": "Backlog", "n": 2, "items": [
                 {"n": 7, "t": "dragged", "l": ["wave:2"], "pm": True, "pmt": "Todo"},
                 {"n": 8, "t": "other", "l": ["wave:1"], "pm": True, "pmt": "Todo"}]}, {"name": "Todo", "n": 0, "items": []}]}
    second = {"t": 1, "err": None, "labels": [], "lc": {}, "cases": [], "flow": None,
              "groups": [{"name": "Backlog", "n": 2, "items": [
                  {"n": 8, "t": "other", "l": ["wave:1"], "pm": True, "pmt": "Todo"},
                  {"n": 7, "t": "dragged", "l": ["wave:2"], "pm": True, "pmt": "Todo"}]}, {"name": "Todo", "n": 0, "items": []}]}
    out = run_page(hub.MOBILE_PAGE, """
      await loadBoard();
      const card = $("cards").querySelector('[data-role="card"][data-n="7"]');
      __dispatch("touchstart", { target: card, touches: [{ clientX: 0, clientY: 700 }] });
      await loadBoard();
      __dispatch("touchend", { changedTouches: [{ clientX: 100, clientY: 700 }] });
      return { pending: pending && pending.n };
    """, board=[first, second])
    assert out["pending"] == 7, out


def test_the_card_takes_its_promotable_facts_from_the_row(hub):
    page = hub.MOBILE_PAGE
    assert "x.pm" in page and "x.pmt" in page, "the card does not read the row's own facts"
    assert "dataset.pm" in page, "the drag is not bound to the row's promotable fact"


def test_the_mobile_page_holds_no_column_list(hub):
    table = _module("board_columns_page", COLUMNS_PY)
    for name in [c["name"] for c in table.shipped_columns()] + list(table.ROLE.values()):
        assert name not in hub.MOBILE_PAGE, f"the mobile page names the column {name!r}: the rows name them"


@pytest.mark.node_bound
def test_the_phone_page_carries_no_session_overlay(hub):
    """The phone page shows no chat, so the session overlay's script left it with the chat (#2490)."""
    out = run_page(hub.MOBILE_PAGE, "return { overlay: window.__sqOverlay === undefined, context: typeof window.squadPageContext };")
    assert out == {"overlay": True, "context": "undefined"}
