#!/usr/bin/env python3
"""Squad hub e2e: pins the hub's contract over HTTP and drives the real page in Chrome.

Usage: cd <checkout> && python3 <skill>/scripts/test-hub-e2e.py
Run it from the checkout whose profile the hub should read. It works on a throwaway
copy of the skill on its own port, so the live hub and the real models.json are never
touched. Needs playwright (python) and Chrome; without either it prints SKIP and exits 0.
Port: SG_HUB_E2E_PORT (7799). Artifacts: SG_HUB_E2E_ARTIFACTS (default a temp dir).
"""
import json, os, pathlib, re, shutil, signal, subprocess, sys, tempfile, time, urllib.error, urllib.request

SKILL = pathlib.Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("SG_HUB_E2E_PORT", "7799"))
BASE = f"http://127.0.0.1:{PORT}"
REPO = os.getcwd()
S = pathlib.Path(os.environ.get("SG_HUB_E2E_ARTIFACTS") or tempfile.mkdtemp(prefix="hub-e2e-"))
shutil.copytree(SKILL, S / "skill", ignore=shutil.ignore_patterns("__pycache__"))
MODELS = S / "skill/scripts/models.json"
# The panel checks below read one row that carries a window, so the copy's review chain head is given one: the shipped
# chain follows the owner's tuning, and a suite that pinned a model id went red the day that chain changed (#393).
_m = json.loads(MODELS.read_text())
_rev = _m["chains"]["review"]                       # v2: a window is the entry's own `hrs`, never a top-level map
_rev[0]["hrs"] = "19-07"                            # #3049: the shape is the case's, never the owner's tuning
MODELS.write_text(json.dumps(_m, indent=2) + "\n")

def chain_rows(kind):
    """The stored spelling of one chain's rows, in rank order: the panel shows `bare` of each."""
    return [e.get("raw") or e["id"] for e in json.loads(MODELS.read_text())["chains"][kind]]

def bare(raw):
    """The id the panel shows for a stored spelling: review.sh's `claude:` prefix is the file's, never the page's."""
    return raw[len("claude:"):] if raw.startswith("claude:") else raw

def window_of(kind, raw):
    """One entry's window, "" when it has none (all day)."""
    e = next(e for e in json.loads(MODELS.read_text())["chains"][kind] if (e.get("raw") or e["id"]) == raw)
    return e.get("hrs") or ""
def browser_ok():
    """Probe in a child process: a sync playwright session started here would keep its event loop alive."""
    code = "from playwright.sync_api import sync_playwright\nwith sync_playwright() as p: p.chromium.launch(channel='chrome').close()"
    return subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0

if not browser_ok():
    print("SKIP test-hub-e2e.py: needs playwright (python) plus a Chrome it can launch"); sys.exit(0)

fails, checks = [], 0

def open_models(pg):
    """Bring the models panel into view (#3049): the board pane opens first, then the Settings pane, then the panel
    group. The page shows one pane and one group at a time, so a reload lands back on the board."""
    pg.click('header button:text-is("Settings")')
    pg.wait_for_timeout(300)
    pg.click('#tab-set nav.snav button:has-text("Models & chains")')
    pg.wait_for_timeout(300)

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
# A PR whose head's review attempt timed out and was re-queued (#1502): the hub serves it as its last good board data, so the
# page renders the card through the real data path. A row older than the head's marker belongs to the head that moved.
PR_SHA, PR9 = "1e38e5a7b41c0d2e9f", "1e38e5a7b"
# A second open PR with a green review line and no loop reason of its own: the card used to derive `ready to merge` from that
# line even while the loop held the merge (#1771). The hub serves both cards from its last good board data.
GATE_SHA, GATE9, GATE_OLD = "abcdef0123456789ab", "abcdef012", "9c0ffee12"
ST = S / "squadstate"; (ST / "auto").mkdir(parents=True)
(S / "hubdir").mkdir()
NOW = time.time()
(S / "hubdir/hub-board.json").write_text(json.dumps(
    {"t": NOW, "items": [], "landed": [], "lc": {}, "err": None,
     "prs": [{"n": 1491, "r": "squad-hub-e2e/none", "t": "Hub: the OPEN PRS card says why a review is late", "d": False, "ci": [], "ms": "clean",
              "u": "2026-10-11T09:00:00Z", "h": PR_SHA, "o": "2026-10-11T08:00:00Z"},
             {"n": 1492, "r": "squad-hub-e2e/none", "t": "Hub: the card reports the loop's own reason", "d": False, "ci": [], "ms": "clean",
              "u": "2026-10-11T08:30:00Z", "h": GATE_SHA, "o": "2026-10-11T07:30:00Z"}]}))
(ST / "revci-1492.out").write_text(f"#1492 {GATE9} clean CI=success:2 VERDICT: APPROVE\n")
def gate(at, sha9, reason, text):
    """auto/gate-<pr>: `<head sha9><TAB><epoch><TAB><reason><TAB><what it means>`, the pass's latest decision (#1766)."""
    (ST / "auto/gate-1492").write_text(f"{sha9}\t{int(at)}\t{reason}\t{text}\n")
def stamp(at):
    """auto/.pass: the epoch of the loop's last pass."""
    (ST / "auto/.pass").write_text(f"{int(at)}\n")
REREVIEW_MK = NOW - 240   # the re-queue marker's age: the card's retry line counts down from it (#393)
REREVIEW_MIN = 20         # the hub's own window: the throwaway hub is started with no SG_REREVIEW_MINUTES of its own
for kind, at in (("launched", NOW - 1500), ("rereview", REREVIEW_MK)):
    mk = ST / f"auto/{kind}-1491-{PR9}"
    mk.write_text(""); os.utime(mk, (at, at))
with (ST / "review-times.tsv").open("a") as f:
    f.write("%s\t1491\tdeepseek-v4-flash\t120\t142\n" % time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - 30)))
p = subprocess.Popen([sys.executable, str(S / "skill/scripts/hub.py"), str(PORT)], cwd=REPO,
                     stdout=open(S / "hub.log", "w"), stderr=subprocess.STDOUT, start_new_session=True,
                     # the caller's SG_* never reaches the throwaway hub (#393): the loop's own shell exports SG_BOARD_CACHE
                     # (the live board), so the OPEN PRS lane served the live cards and held none of this fixture's. Every
                     # variable under test is pinned here, and nothing of the caller but the platform is passed on.
                     env=dict({k: v for k, v in os.environ.items() if not k.startswith("SG_")},
                              SG_HUB_DIR=str(S / "hubdir"), SG_STATE=str(ST),
                              SG_REPO="squad-hub-e2e/none", SG_OWNER="squad-hub-e2e", SG_PROJECT="0", SG_CHECKOUT=REPO,
                              SG_CLAUDE_PROJECTS=str(S / "projects")))   # the operator source reads this fixture folder, never the owner's transcripts (#1756)
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
    # #3049: the page's brand moved into the header's project switcher; the check reads the markup the page ships
    ck(st == 200 and "<!doctype html" in html.lower() and "id=pjn" in html, "GET / -> the page")
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
    # #3048: the row this case writes is the coding chain's head, read from the file, so a change to the owner's chain
    # never turns the e2e red. Pinning one model id is the defect the header names (#393), and it reached this case last.
    code = chain_rows("coding")[0]
    # a Save carries the version it read (#688): one GET before each POST, as the page does
    ver = lambda: json.loads(req("/api/models")[1])["version"]
    st, b = req("/api/models", {"version": ver(), "hours": {"coding_models": {code: "23-06"}}}, origin=BASE)
    d = json.loads(b)
    ck(st == 200 and d.get("hours", {}).get("coding_models", {}).get(code) == "23-06", "valid window stored", f"{st} {b[:120]}")
    ck(window_of("coding", code) == "23-06", "written to models.json")
    st, b = req("/api/models", {"version": ver(), "hours": {"coding_models": {code: "25-99"}}}, origin=BASE)
    ck("err" in json.loads(b), "bad window rejected", b[:120])
    st, b = req("/api/models", {"version": ver(), "hours": {"coding_models": {code: "07-07"}}}, origin=BASE)
    ck(window_of("coding", code) != "07-07", "degenerate window (07-07) never stored", b[:120])
    st, b = req("/api/models", {"version": ver(), "hours": {"not_a_chain": {"m": "01-02"}}}, origin=BASE)
    ck("err" in json.loads(b), "unknown chain refused", b[:120])
    st, b = req("/api/models", {"version": ver(), "hours": {"coding_models": {"not-in-chain": "01-02"}}}, origin=BASE)
    ck("err" not in json.loads(b), "a setting for a model outside the chain goes with the row, never refuses the Save", b[:120])
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
        # the OPEN PRS card of a head whose review timed out: the chip, its tooltip, the retry line, and the column's width (#1502)
        # #3049: the lane draws as `.col` or `.acc` (#1809), and only the name is fixed: `data-g` names the column.
        lane = '#bbody :is(.col,.acc)[data-g="Open PRs"]'
        try: pg.wait_for_selector(f'{lane} .it .pst', timeout=20000)
        except Exception: pass
        pv = pg.eval_on_selector_all(f'{lane} .it', """els=>els.map(e=>{const s=e.querySelector('.pst'),r=e.querySelector('.prr');
            return {n:e.dataset.n, chip:s&&s.textContent, line:r&&r.textContent, tip:(s&&s.title)||'', over:e.scrollWidth-e.clientWidth,
                    cover:s?s.scrollWidth-s.clientWidth:0, rover:r?r.scrollWidth-r.clientWidth:0}})""")
        card = next((x for x in pv if x["n"] == "1491"), None)
        ck(card is not None, "the OPEN PRS column renders the timed-out PR's card", pv)
        ck(card and card["chip"] == "review timed out · deepseek-v4-flash · 120 s", "the chip names the model and the seconds it burned", card)
        # the retry line counts down from the marker's age (#393), and the page is read seconds after the fixture built it,
        # so a hardcoded minute was a race that went red whenever the read crossed 60 s: the test recomputes the hub's own
        # countdown and allows the page the minute it was rendered on, or the one after it
        left = REREVIEW_MK + REREVIEW_MIN * 60 - time.time()
        want = int(-(-left // 60))
        retry = re.fullmatch(r"retry in (\d+) min", card["line"] if card else "")
        ck(bool(card) and retry is not None and want <= int(retry.group(1)) <= want + 1, "the muted line says when the loop retries", card)
        ck(card and "deepseek-v4-flash 120 s · timed out" in card["tip"], "the chip's tooltip lists the attempt for this head", card and card["tip"])
        ck(card and card["over"] <= 1 and card["cover"] <= 1 and card["rover"] <= 1, "the card does not overflow the column", card)
        open_models(pg)
        if not pg.eval_on_selector("details#mdl", "e=>e.open"): pg.click("details#mdl > summary")
        pg.wait_for_selector(f'#mbody input[data-hr="coding_models|{bare(code)}"]', timeout=15000)
        # every chain draws the same row, the router's included (SPEC 750): each carries the hrs input and the effort select
        rows = pg.eval_on_selector_all("#mbody .mr:has(input.hr)", "els=>els.map(e=>({m:e.querySelector('.mn').textContent,hr:e.querySelector('input.hr').value,ph:e.querySelector('input.hr').placeholder}))")
        ck(len(rows) >= 6, f"panel renders the chains' rows ({len(rows)})", rows)
        rev = {bare(m): m for m in chain_rows("review")}   # the panel's id -> the spelling the file stores
        head = bare(chain_rows("review")[0])               # the chain's head, the review row this copy gave a window to
        # #3049: the rows are read by their `data-hr` key, the one the panel edits. It names the chain and the model,
        # so a display name that changes with the registry never decides what this case proves.
        hrv = lambda kind, m: pg.input_value(f'#mbody input[data-hr="{kind}_models|{m}"]')
        # The panel shows what the API serves: section 4 already proved the API writes the file, and a hub that answers
        # per poll window may still serve the read it took before that write. This case pins the panel to the answer.
        served = json.loads(req("/api/models")[1]).get("hours", {}).get("coding_models", {})
        want_code = served.get(code) or served.get(bare(code)) or ""
        ck(hrv("coding", bare(code)) == want_code, f"coding {bare(code)} shows the window the API served", [hrv("coding", bare(code)), want_code])   # #3048: the row this run wrote
        ck(hrv("review", head) == "19-07", f"review {head} shows 19-07", hrv("review", head))
        idle = next(b for b, m in rev.items() if b != head and not window_of("review", m))   # a chain member with no window
        ck(hrv("review", idle) == "", f"{idle} has no window (all day)", hrv("review", idle))
        ck(all(r["ph"] == "always" for r in rows), "empty rows read 'always'", [r for r in rows if r['ph']!='always'])
        ck(pg.is_disabled("#ssave"), "Save starts disabled (nothing dirty)")   # #3049: the Settings pane carries one Save bar for its panels
        cur = window_of("review", rev[idle])
        want = "21-05" if cur != "21-05" else "20-04"
        pg.fill(f'input[data-hr="review_models|{idle}"]', want)
        pg.dispatch_event(f'input[data-hr="review_models|{idle}"]', "change")
        pg.wait_for_timeout(300)
        ck(not pg.is_disabled("#ssave"), "Save wakes up on an edit")
        # #3049: Save opens the diff dialog first, and the dialog's own button applies the write (#1278).
        pg.click("#ssave")
        pg.wait_for_selector("#sdlg[open]", timeout=15000)
        pg.click("#sdok")
        pg.wait_for_timeout(900)
        saved = window_of("review", rev[idle])
        ck(saved == want, f"the browser's Save wrote {want} to models.json", saved)
        ck(pg.inner_text("#schg").strip().lower().startswith("saved"), "the panel says saved", pg.inner_text("#schg")[:60])
        pg.reload(wait_until="domcontentloaded"); pg.wait_for_timeout(1200)
        open_models(pg)
        if not pg.eval_on_selector("details#mdl", "e=>e.open"): pg.click("details#mdl > summary")
        pg.wait_for_selector(f'input[data-hr="review_models|{idle}"]', timeout=15000)
        ck(pg.input_value(f'input[data-hr="review_models|{idle}"]') == want, "survives a reload", pg.input_value(f'input[data-hr="review_models|{idle}"]'))
        pg.fill(f'input[data-hr="review_models|{idle}"]', "99-99")
        pg.dispatch_event(f'input[data-hr="review_models|{idle}"]', "change")
        pg.wait_for_timeout(300)
        ck(pg.input_value(f'input[data-hr="review_models|{idle}"]') == "", "a garbage window never enters the field", pg.input_value(f'input[data-hr="review_models|{idle}"]'))
        ov = pg.eval_on_selector("#side", "e=>({sw:e.scrollWidth,cw:e.clientWidth})")
        ck(ov["sw"] <= ov["cw"] + 1, "the sidebar has no horizontal overflow", ov)
        wide = pg.eval_on_selector_all("#mbody .mr", "els=>els.map(e=>[e.querySelector('.mn').textContent, e.scrollWidth-e.clientWidth]).filter(x=>x[1]>1)")
        ck(not wide, "no model row overflows its box", wide)
        out = pg.eval_on_selector_all("#mbody .mr .mb button", "els=>els.map(e=>{const r=e.getBoundingClientRect(),b=e.closest('.mr').getBoundingClientRect();return Math.round(b.right-r.right)})")
        ck(all(c >= 0 for c in out), "every row's buttons sit inside its box", out)
        pg.locator("#side").screenshot(path=str(S / "side.png"))
        pg.screenshot(path=str(S / "panel.png"), full_page=False)
        pg.click('header button:text-is("Kanban")'); pg.wait_for_timeout(600)   # #3049: the panel list lives in the board pane, not behind a Review button
        other = pg.eval_on_selector_all(".acc summary, details summary", "els=>els.map(e=>e.textContent.trim())")
        # #3049: the panels the page ships now are Spend and Subscriptions; the check names them as the page names them
        ck(any("Spend" in o for o in other) and any("Subscriptions" in o for o in other), "the other panels are on the page", other[:8])
        if not pg.eval_on_selector("details#stats", "e=>e.open"): pg.click("details#stats > summary"); pg.wait_for_timeout(1500)
        ck(pg.eval_on_selector("#stats", "e=>e.open") is True, "cost & usage opens")
        stxt = pg.inner_text("#stats .sb")   # #3049: the panel's own body, never the settings pane's #sbody
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

    print("== 6. the OPEN PRS chip is the loop's own reason, never a derived one (#1771)")
    def chips():
        """The chip the hub serves for every open PR: [css class, text, tooltip], straight off /api/board."""
        st, b = req("/api/board")
        return {x["n"]: x.get("ps") or [] for x in json.loads(b)["prs"]["items"]}
    now = time.time()
    stamp(now - 30)
    gate(now - 20, GATE9, "hold-threads", "unresolved review threads opened after the cache")
    c = chips().get(1492)
    ck(c and c[1] == "held: threads", "a current gate that holds the merge is the chip", c)
    ck(c and c[1] != "ready to merge", "the held head is never `ready to merge`", c)
    ck(c and "unresolved review threads opened after the cache" in c[2] and "20 s ago" in c[2], "the tooltip carries the gate line and its age", c and c[2])
    gate(now - 20, GATE_OLD, "hold-threads", "unresolved review threads opened after the cache")
    ck(chips()[1492][1] == "new head, not seen yet", "a gate line for the head before this one is not a verdict", chips()[1492])
    gate(now - 20, GATE9, "merged", "merged as abc1234: APPROVE + CI green + clean + no open threads")
    ck(chips()[1492][1] == "ready to merge", "the gate saying the merge goes ahead is what `ready to merge` is for", chips()[1492])
    stamp(now - 600)
    ck(chips()[1492][1] == "loop idle", "no pass for three watch cycles reads `loop idle`", chips()[1492])
    stamp(now - 30)
    (ST / "auto/off").write_text("")
    off = chips()
    ck(all(v and v[1] == "loop off" for v in off.values()), "auto/off: every open-PR card says `loop off`", off)
    (ST / "auto/off").unlink()
    ck(chips()[1492][1] == "ready to merge", "lifting the kill switch gives the loop's reason back", chips()[1492])
finally:
    MODELS.write_text(SNAP) if "SNAP" in dir() else None
    p.send_signal(signal.SIGTERM); time.sleep(1)
    if p.poll() is None: p.kill()

print(f"\n{checks - len(fails)}/{checks} checks passed")
if fails:
    print("FAILED: " + "; ".join(fails)); sys.exit(1)
print(f"SQUAD HUB E2E: PASS (artifacts in {S})")
