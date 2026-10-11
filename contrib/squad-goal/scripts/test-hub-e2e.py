#!/usr/bin/env python3
"""Squad hub e2e: pins the hub's contract over HTTP and drives the real page in Chrome.

Usage: cd <checkout> && python3 <skill>/scripts/test-hub-e2e.py
Run it from the checkout whose profile the hub should read. It works on a throwaway
copy of the skill on its own port, so the live hub and the real models.json are never
touched. Needs playwright (python) and Chrome; without either it prints SKIP and exits 0.
Port: SG_HUB_E2E_PORT (7799). Artifacts: SG_HUB_E2E_ARTIFACTS (default a temp dir).
"""
import json, os, pathlib, shutil, signal, subprocess, sys, tempfile, time, urllib.error, urllib.request

SKILL = pathlib.Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("SG_HUB_E2E_PORT", "7799"))
BASE = f"http://127.0.0.1:{PORT}"
REPO = os.getcwd()
S = pathlib.Path(os.environ.get("SG_HUB_E2E_ARTIFACTS") or tempfile.mkdtemp(prefix="hub-e2e-"))
shutil.copytree(SKILL, S / "skill", ignore=shutil.ignore_patterns("__pycache__"))
MODELS = S / "skill/scripts/models.json"
def browser_ok():
    """Probe in a child process: a sync playwright session started here would keep its event loop alive."""
    code = "from playwright.sync_api import sync_playwright\nwith sync_playwright() as p: p.chromium.launch(channel='chrome').close()"
    return subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0

if not browser_ok():
    print("SKIP test-hub-e2e.py: needs playwright (python) plus a Chrome it can launch"); sys.exit(0)

fails, checks = [], 0

def ck(cond, label, extra=""):
    global checks
    checks += 1
    print(("  ok   " if cond else "  FAIL ") + label + (f"  [{extra}]" if extra and not cond else ""))
    if not cond: fails.append(label)

def req(path, data=None, origin=None, ct="application/json"):
    h = {}
    if origin: h["Origin"] = origin
    if data is not None: h["Content-Type"] = ct
    r = urllib.request.Request(BASE + path, data=json.dumps(data).encode() if data is not None else None, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=20) as x: return x.status, x.read()
    except urllib.error.HTTPError as e: return e.code, e.read()

def wait_up():
    for _ in range(60):
        try:
            with urllib.request.urlopen(BASE + "/api/version", timeout=2): return True
        except Exception: time.sleep(0.5)
    return False

print(f"artifacts: {S}")
print("== 1. boot an isolated hub (throwaway copy of the skill, port %d)" % PORT)
p = subprocess.Popen([sys.executable, str(S / "skill/scripts/hub.py"), str(PORT)], cwd=REPO,
                     stdout=open(S / "hub.log", "w"), stderr=subprocess.STDOUT, start_new_session=True)
try:
    ck(wait_up(), "hub answers /api/version")
    st, b = req("/api/version")
    ck(st == 200 and isinstance(json.loads(b).get("v"), int), "GET /api/version -> 200 + int v", f"{st} {b[:80]}")

    print("== 2. every GET endpoint the page calls")
    for path, key in [("/api/runs", None), ("/api/stats", None), ("/api/board", None),
                      ("/api/subs", None), ("/api/speed", None), ("/api/models", "coding_models"),
                      ("/api/issue?n=1", None)]:
        st, b = req(path)
        try: d = json.loads(b)
        except Exception: d = None
        ck(st == 200 and d is not None, f"GET {path} -> 200 + JSON", f"{st} {b[:80]}")
        if key and d: ck(key in d, f"{path} carries {key}", list(d)[:6])
    st, b = req("/")
    html = b.decode("utf-8", "replace")
    ck(st == 200 and "<!doctype html" in html.lower() and "Squad hub" in html, "GET / -> the page")
    ck('placeholder="always"' in html and "vazio" not in html, "page ships the current models row template")

    print("== 3. write protection")
    st, _ = req("/api/models", {"hours": {}}, origin=None)
    ck(st == 400, "POST with no Origin -> 400 forbidden", st)
    st, _ = req("/api/models", {"hours": {}}, origin="http://evil.example")
    ck(st == 400, "POST with a foreign Origin -> 400", st)
    st, _ = req("/api/plans", {"x": 1}, origin=BASE)
    ck(st in (200, 400), "POST /api/plans reachable from our origin", st)
    st, _ = req("/api/nope", {"x": 1}, origin=BASE)
    ck(st == 400, "POST to an unknown path -> 400", st)

    print("== 4. the hours round-trip through the API (isolated copy)")
    before = MODELS.read_text()
    st, b = req("/api/models", {"hours": {"coding_models": {"claude-sonnet-5-5": "23-06"}}}, origin=BASE)
    d = json.loads(b)
    ck(st == 200 and d.get("hours", {}).get("coding_models", {}).get("claude-sonnet-5-5") == "23-06", "valid window stored", f"{st} {b[:120]}")
    ck(json.loads(MODELS.read_text())["hours"]["coding_models"]["claude-sonnet-5-5"] == "23-06", "written to models.json")
    st, b = req("/api/models", {"hours": {"coding_models": {"claude-sonnet-5-5": "25-99"}}}, origin=BASE)
    d = json.loads(b)
    ck("err" in d, "bad window rejected", b[:120])
    st, b = req("/api/models", {"hours": {"coding_models": {"claude-sonnet-5-5": "07-07"}}}, origin=BASE)
    ck("err" in d or "coding_models" not in json.loads(b).get("hours", {}), "degenerate window (07-07) refused")
    st, b = req("/api/models", {"hours": {"not_a_chain": {"m": "01-02"}}}, origin=BASE)
    ck("err" in json.loads(b), "unknown chain refused", b[:120])
    st, b = req("/api/models", {"hours": {"coding_models": {"not-in-chain": "01-02"}}}, origin=BASE)
    ck("err" in json.loads(b), "model outside the chain refused", b[:120])
    MODELS.write_text(before)
    ck(MODELS.read_text() == before, "models.json restored byte for byte")

    print("== 5. the browser: the models panel a user actually sees")
    SNAP = MODELS.read_text()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        br = pw.chromium.launch(channel="chrome")
        pg = br.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.goto(BASE, wait_until="domcontentloaded")
        pg.wait_for_timeout(1200)
        ck(pg.title().strip() != "", "page loads", pg.title())
        if not pg.eval_on_selector("details#mdl", "e=>e.open"): pg.click("details#mdl > summary")
        pg.wait_for_selector('#mbody input[data-hr="coding_models|claude-sonnet-5-5"]', timeout=15000)
        rows = pg.eval_on_selector_all("#mbody .mr", "els=>els.map(e=>({m:e.querySelector('.mn').textContent,hr:e.querySelector('input.hr').value,ph:e.querySelector('input.hr').placeholder}))")
        ck(len(rows) >= 6, f"panel renders the chains' rows ({len(rows)})", rows)
        by = {r["m"]: r for r in rows}
        ck(by.get("claude-sonnet-5-5", {}).get("hr") == "19-07", "coding claude-sonnet-5-5 shows 19-07", by.get("claude-sonnet-5-5"))
        ck(by.get("claude-opus-5-5", {}).get("hr") == "19-07", "review claude-opus-5-5 shows 19-07", by.get("claude-opus-5-5"))   # the panel shows bare ids; review.sh's `claude:` prefix is added on the way into models.json
        ck(by.get("grok-4.5", {}).get("hr") == "", "grok-4.5 has no window (all day)", by.get("grok-4.5"))
        ck(all(r["ph"] == "always" for r in rows), "empty rows read 'always'", [r for r in rows if r['ph']!='always'])
        ck(pg.is_disabled("#msv"), "Save starts disabled (nothing dirty)")
        cur = json.loads(MODELS.read_text())["hours"].get("review_models", {}).get("grok-4.5", "")
        want = "21-05" if cur != "21-05" else "20-04"
        pg.fill('input[data-hr="review_models|grok-4.5"]', want)
        pg.dispatch_event('input[data-hr="review_models|grok-4.5"]', "change")
        pg.wait_for_timeout(300)
        ck(not pg.is_disabled("#msv"), "Save wakes up on an edit")
        pg.click("#msv")
        pg.wait_for_timeout(900)
        saved = json.loads(MODELS.read_text())["hours"].get("review_models", {}).get("grok-4.5")
        ck(saved == want, f"the browser's Save wrote {want} to models.json", saved)
        ck("saved" in pg.inner_text(".msave .st").lower(), "the panel says saved", pg.inner_text(".msave .st")[:60])
        pg.reload(wait_until="domcontentloaded"); pg.wait_for_timeout(1200)
        if not pg.eval_on_selector("details#mdl", "e=>e.open"): pg.click("details#mdl > summary")
        pg.wait_for_selector('input[data-hr="review_models|grok-4.5"]', timeout=15000)
        ck(pg.input_value('input[data-hr="review_models|grok-4.5"]') == want, "survives a reload", pg.input_value('input[data-hr="review_models|grok-4.5"]'))
        pg.fill('input[data-hr="review_models|grok-4.5"]', "99-99")
        pg.dispatch_event('input[data-hr="review_models|grok-4.5"]', "change")
        pg.wait_for_timeout(300)
        ck(pg.input_value('input[data-hr="review_models|grok-4.5"]') == "", "a garbage window never enters the field", pg.input_value('input[data-hr="review_models|grok-4.5"]'))
        ov = pg.eval_on_selector("#side", "e=>({sw:e.scrollWidth,cw:e.clientWidth})")
        ck(ov["sw"] <= ov["cw"] + 1, "the sidebar has no horizontal overflow", ov)
        wide = pg.eval_on_selector_all("#mbody .mr", "els=>els.map(e=>[e.querySelector('.mn').textContent, e.scrollWidth-e.clientWidth]).filter(x=>x[1]>1)")
        ck(not wide, "no model row overflows its box", wide)
        out = pg.eval_on_selector_all("#mbody .mr .mb button", "els=>els.map(e=>{const r=e.getBoundingClientRect(),b=e.closest('.mr').getBoundingClientRect();return Math.round(b.right-r.right)})")
        ck(all(c >= 0 for c in out), "every row's buttons sit inside its box", out)
        pg.locator("#side").screenshot(path=str(S / "side.png"))
        pg.screenshot(path=str(S / "panel.png"), full_page=False)
        pg.click("#mrv"); pg.wait_for_timeout(400)
        other = pg.eval_on_selector_all(".acc summary, details summary", "els=>els.map(e=>e.textContent.trim())")
        ck(any("cost" in o for o in other) and any("subscription" in o.lower() or "plans" in o.lower() for o in other), "the other panels are on the page", other[:8])
        if not pg.eval_on_selector("details#stats", "e=>e.open"): pg.click("details#stats > summary"); pg.wait_for_timeout(1500)
        ck(pg.eval_on_selector("#stats", "e=>e.open") is True, "cost & usage opens")
        stxt = pg.inner_text("#sbody") if pg.query_selector("#sbody") else pg.inner_text("#stats")
        ck(len(stxt) > 40, "cost & usage renders content", stxt[:60].replace("\n", " "))
        side = pg.eval_on_selector("#stats .sb", "e=>({sw:e.scrollWidth,cw:e.clientWidth})")
        ck(side["sw"] <= side["cw"] + 1, "cost & usage has no horizontal overflow", side)
        over = pg.eval_on_selector_all(
            "#stats .sb h4, #stats .sb h4 small, #stats .sb .tot>div, #stats .sb .cash, #stats .sb .kr, #stats .sb .pie .lg div",
            "els=>els.map(e=>[(e.textContent||'').trim().slice(0,30),e.scrollWidth-e.clientWidth]).filter(x=>x[1]>1)")
        ck(not over, "no cost & usage header or row overflows the 300 px sidebar", over)
        pg.screenshot(path=str(S / "cost.png"))
        br.close()
        ck(not errs, "no page errors", errs[:3])
finally:
    MODELS.write_text(SNAP) if "SNAP" in dir() else None
    p.send_signal(signal.SIGTERM); time.sleep(1)
    if p.poll() is None: p.kill()

print(f"\n{checks - len(fails)}/{checks} checks passed")
if fails:
    print("FAILED: " + "; ".join(fails)); sys.exit(1)
print(f"SQUAD HUB E2E: PASS (artifacts in {S})")
