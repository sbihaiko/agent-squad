"""The overlay's screenshot route (#903): a PNG of the page beneath the overlay is written under the project's hub state
folder and the session is told its path. The send rule and the origin rule admit it; the PNG check and the 5 MB cap refuse
what they must. The route is driven over a loopback server, and the send is the one seam stubbed.
"""
import http.client
import importlib.util
import json
import pathlib
import shutil
import subprocess
import threading
import urllib.parse
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
OVERLAY = SCRIPTS / "hub-overlay.js"
NODE = shutil.which("node")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SID = "11111111-1111-1111-1111-111111111111"
CID = "22222222-2222-2222-2222-222222222222"
ROUTE = f"/api/sessions/screenshot?sid={SID}&client_id={CID}"
FIVE_MB = 5 * 1024 * 1024


@pytest.fixture
def hub(monkeypatch, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("HOME", str(tmp_path)),
                 ("SG_CHECKOUT", str(tmp_path)), ("SG_HUB_DIR", str(tmp_path / "hub")), ("SG_STATE", str(state))):
        monkeypatch.setenv(k, v)
    for k in ("SG_STATE_BASE", "SG_SHARED", "SG_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("hub_screenshot", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, state


@pytest.fixture
def served(hub, monkeypatch):
    mod, state = hub
    sent = []

    def fake_send(profile, sid, text, cid, **kw):
        sent.append((sid, text, cid))
        return {"status": "sent"}

    monkeypatch.setattr(mod.session_send, "send", fake_send)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, state, srv.server_port, sent
    srv.shutdown()
    srv.server_close()


def post(port, path, body, ctype="image/png", origin=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    headers = {"Content-Type": ctype, "Origin": origin or f"http://127.0.0.1:{port}"}
    conn.request("POST", path, body=body, headers=headers)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data


def written(state):
    return state / "sessions" / SID / "images" / f"{CID}.png"


def stored(state):
    return sorted((state / "sessions").rglob("*.png")) if (state / "sessions").exists() else []


def test_a_png_is_written_under_the_session_images_folder_and_its_path_is_sent(served):
    mod, state, port, sent = served
    status, _ = post(port, ROUTE, PNG)
    f = written(state)
    assert status == 200
    assert f.read_bytes() == PNG
    assert sent == [(SID, f"[Image: source: {f}]", CID)]


def test_the_path_the_session_is_told_is_served_for_its_session_and_refused_for_another(served):
    mod, state, port, sent = served
    other = "0" * 8 + "-0000-0000-0000-" + "0" * 12
    assert post(port, ROUTE, PNG)[0] == 200
    (sid, line, cid), = sent
    assert sid == SID and cid == CID
    assert line.startswith("[Image: source: ") and line.endswith("]")
    path = line[len("[Image: source: "):-1]
    assert pathlib.Path(path).read_bytes() == PNG
    assert mod.image_payload(urllib.parse.urlencode({"sid": SID, "p": path})) == (PNG, "image/png")
    assert mod.image_payload(urllib.parse.urlencode({"sid": other, "p": path})) is None


def test_a_non_png_is_refused_and_nothing_is_written(served):
    mod, state, port, sent = served
    assert post(port, ROUTE, PNG, ctype="image/jpeg")[0] == 415
    assert post(port, ROUTE, b"GIF89a" + b"\x00" * 64)[0] == 415
    assert stored(state) == [] and sent == []


def test_a_body_over_five_megabytes_is_refused_and_nothing_is_written(served):
    mod, state, port, sent = served
    status, _ = post(port, ROUTE, PNG + b"\x00" * FIVE_MB)
    assert status == 413
    assert stored(state) == [] and sent == []


def test_a_body_of_exactly_five_megabytes_is_admitted(served):
    mod, state, port, sent = served
    status, _ = post(port, ROUTE, PNG + b"\x00" * (FIVE_MB - len(PNG)))
    assert status == 200
    assert written(state).stat().st_size == FIVE_MB


def test_a_request_the_send_rule_refuses_is_refused(served, monkeypatch):
    mod, state, port, sent = served
    monkeypatch.setattr(mod, "send_rule", lambda a: (False, "Sending from the tailnet is off."))
    assert post(port, ROUTE, PNG)[0] == 403
    assert stored(state) == [] and sent == []


def test_a_foreign_origin_is_refused_and_nothing_is_written(served):
    mod, state, port, sent = served
    assert post(port, ROUTE, PNG, origin="http://evil.example")[0] == 403
    assert stored(state) == [] and sent == []


def test_a_malformed_session_id_is_refused_before_anything_is_written(served):
    mod, state, port, sent = served
    assert post(port, f"/api/sessions/screenshot?sid=bad%20id&client_id={CID}", PNG)[0] == 400
    assert stored(state) == [] and sent == []


# ---------------------------------------------------------------- the overlay's capture, under node over a stub DOM

OVERLAY_RUN = r"""
const vm = require("vm"), fs = require("fs");
const W = __WIDTH__, H = 800;
const els = new Map();
function el(id) {
  if (els.has(id)) return els.get(id);
  const e = {
    id, hidden: false, value: "", textContent: "", innerHTML: "", dataset: {}, style: { setProperty() {} },
    listeners: {}, children: [], classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
    setAttribute() {}, getAttribute() { return null; }, removeAttribute() {}, appendChild(c) { return c; },
    remove() {}, focus() {}, blur() {}, getClientRects() { return []; }, closest() { return null; },
    querySelector() { return el("child"); }, querySelectorAll() { return []; }, contains() { return false; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0 }; },
    setPointerCapture() {}, releasePointerCapture() {}, scrollTo() {}, scrollIntoView() {}, prepend() {}, append() {},
    replaceChildren() {}, select() {}, click() {}, cloneNode() { return el("clone"); },
    offsetWidth: 1, offsetHeight: 1, scrollHeight: 0, clientHeight: 0, clientWidth: 0,
  };
  els.set(id, e);
  return e;
}
el("sq-ov").hidden = true;
const rec = { capture: null, fetches: [] };
const doc = {
  documentElement: el("html"), body: el("body"), getElementById: id => el(id), createElement: () => el("made"),
  createRange: () => ({ createContextualFragment: () => el("frag") }), addEventListener() {}, removeEventListener() {},
  querySelectorAll: () => [], querySelector: () => el("child"),
};
const sandbox = {
  document: doc, innerWidth: W, innerHeight: H, scrollX: 0, scrollY: 0,
  localStorage: { getItem: () => null, setItem() {} }, sessionStorage: { getItem: () => null, setItem() {} },
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  requestAnimationFrame: f => setTimeout(f, 0), setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {},
  crypto: { randomUUID: () => "11111111-1111-4111-8111-111111111111" },
  Blob, URL, console, navigator: {}, addEventListener() {}, location: { pathname: "/", search: "", hash: "" },
  ResizeObserver: class { observe() {} disconnect() {} },
  MutationObserver: class { observe() {} disconnect() {} },
  SQUAD_OV_SOURCES: async () => [{ label: "", api: "/api/sessions" }],
  html2canvas: async (target, opts) => {
    rec.capture = { target: target === doc.documentElement, hidden: [el("sq-ov-btn").hidden, el("sq-ov").hidden],
                    width: opts.width, height: opts.height };
    return { toBlob: (cb, type) => cb(new Blob(["png"], { type })) };
  },
  fetch: async (url, opts = {}) => {
    const h = opts.headers || {};
    rec.fetches.push({ url, method: opts.method || "GET", type: h["Content-Type"] || "", body: opts.body && opts.body.type });
    if (url.endsWith("/project")) return { ok: true, json: async () => ({ sessions: [{ sid: "s1", name: "", status: "idle" }], send: true, why: "" }) };
    if (url.includes("/screenshot")) return { ok: true, json: async () => ({ status: "sent" }) };
    return { ok: true, json: async () => ({ turns: [], next: null }) };
  },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(__FILE__, "utf8"), sandbox);
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  await sleep(30);
  el("sq-ov-btn").listeners.click[0]();
  await sleep(20);
  const opened = el("sq-ov").hidden === false;
  el("sq-ov-menu").listeners.click[0]({ target: { closest: () => ({ dataset: { act: "shot" } }) } });
  for (let i = 0; i < 100 && !rec.fetches.some(f => f.url.includes("/screenshot")); i++) await sleep(10);
  await sleep(20);
  console.log(JSON.stringify({ opened, capture: rec.capture, after: { ovHidden: el("sq-ov").hidden, btnHidden: el("sq-ov-btn").hidden },
                              fetches: rec.fetches, status: el("sq-ov-status").textContent }));
})();
"""


def overlay_run(width):
    script = OVERLAY_RUN.replace("__WIDTH__", str(width)).replace("__FILE__", json.dumps(str(OVERLAY)))
    p = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.mark.parametrize("width", [1280, 390], ids=["desktop", "phone"])
def test_the_overlay_steps_aside_for_the_capture_and_comes_back(width):
    run = overlay_run(width)
    assert run["opened"] is True
    assert run["capture"] == {"target": True, "hidden": [True, True], "width": width, "height": 800}
    assert run["after"] == {"ovHidden": False, "btnHidden": False}
    assert [f for f in run["fetches"] if "/screenshot" in f["url"]] == [{
        "url": "/api/sessions/screenshot?sid=s1&client_id=11111111-1111-4111-8111-111111111111",
        "method": "POST", "type": "image/png", "body": "image/png"}]
    assert run["status"] == "Attached: the session's inbox accepted the screenshot."


def test_the_mux_routes_the_screenshot_to_its_repositorys_hub(hub, monkeypatch):
    mod, state = hub
    sent = []
    monkeypatch.setattr(mod.session_send, "send", lambda profile, sid, text, cid, **kw: sent.append(text) or {"status": "sent"})
    spec = importlib.util.spec_from_file_location("hubmux_screenshot", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    srv = mux.server(0, {"x": {"repo": "x/y", "state": str(state), "mod": mod}})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, _ = post(srv.server_port, f"/x/api/sessions/screenshot?sid={SID}&client_id={CID}", PNG)
    finally:
        srv.shutdown()
        srv.server_close()
    assert status == 200
    assert written(state).read_bytes() == PNG
    assert sent == [f"[Image: source: {written(state)}]"]
