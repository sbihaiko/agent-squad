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
_rev[0]["hrs"] = _rev[0].get("hrs") or "19-07"      # merged: the shipped windows stay
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

def models_json():
    """GET /api/models as the panel reads it, never the file the test holds: the value a panel check compares to."""
    return json.loads(req("/api/models")[1])

print(f"artifacts: {S}")
print("== 1. boot an isolated hub (throwaway copy of the skill, port %d)" % PORT)
# The repository this throwaway hub reads. The fixture rows name no `r` of their own, so section 6 checks the hub fills
# this name on a row that carries none (hub_theme_board.py:66), never a string the fixture sets and nothing reads (#3048).
HREPO = "squad-hub-e2e/none"
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
     "prs": [{"n": 1491, "t": "Hub: the OPEN PRS card says why a review is late", "d": False, "ci": [], "ms": "clean",
              "u": "2026-10-11T09:00:00Z", "h": PR_SHA, "o": "2026-10-11T08:00:00Z"},
             {"n": 1492, "t": "Hub: the card reports the loop's own reason", "d": False, "ci": [], "ms": "clean",
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
                              SG_REPO=HREPO, SG_OWNER="squad-hub-e2e", SG_PROJECT="0", SG_CHECKOUT=REPO,
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
    # the header carries the project's own name, never a "Squad hub" title (2c34d1e44): the page's own title is its identity
    ck(st == 200 and "<!doctype html" in html.lower() and "<title>Hub</title>" in html, "GET / -> the page")
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
    # #3048: the panel reads a row by its chain and its id (`data-row="<chain>|<id>"`), so a model two chains share is
    # never ambiguous: section 5 reads the coding section's own row. A duplicate inside one chain would be, and it would
    # also make the row this case writes ambiguous, so the fixture's own reading must name the head once.
    ck(chain_rows("coding").count(code) == 1, f"the coding chain names its head {bare(code)} once", chain_rows("coding"))
    # A window unlike the coding head's own, so a page that ignores the stored window cannot show it by accident.
    WIN = "23-06"
    # a Save carries the version it read (#688): one GET before each POST, as the page does
    ver = lambda: json.loads(req("/api/models")[1])["version"]
    st, b = req("/api/models", {"version": ver(), "hours": {"coding_models": {code: WIN}}}, origin=BASE)
    ck(st == 200 and "err" not in json.loads(b), "a valid window is accepted", f"{st} {b[:120]}")
    # #3048: the value the panel check compares to comes back from GET /api/models, never the literal this case posted.
    # A literal passed on any page that printed it; the stored value goes red when the page ignores the store.
    api_win = (models_json().get("hours", {}).get("coding_models") or {}).get(bare(code))
    ck(api_win == WIN, "GET /api/models serves the window the Save stored", api_win)
    ck(window_of("coding", code) == api_win, "models.json holds the window the API serves", window_of("coding", code))
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
    SNAP = MODELS.read_text()   # every write below lands on the throwaway copy; the finally restores this
    # #3048: the panel check compares to the value GET /api/models serves, never a literal. Put the coding head's window
    # in the copy first, read it back, and hold that value: a page that ignored the stored window shows the file's own
    # window and the check goes red.
    req("/api/models", {"version": ver(), "hours": {"coding_models": {code: WIN}}}, origin=BASE)
    panel_want = (models_json().get("hours", {}).get("coding_models") or {}).get(bare(code))
    ck(panel_want == WIN, "the coding head's window the panel must show", panel_want)
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
        try: pg.wait_for_selector('#bbody .col[data-g="Open PRs"] .it .pst', timeout=20000)
        except Exception: pass
        pv = pg.eval_on_selector_all('#bbody .col[data-g="Open PRs"] .it', """els=>els.map(e=>{const s=e.querySelector('.pst'),r=e.querySelector('.prr');
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
        # the panes are tabs and the themes are one at a time: Settings and then Models & chains, or the panel is not on
        # screen (#3048). A test that clicked a hidden summary could never read the row it claims to check.
        pg.click("button[data-t=set]")
        pg.click(".snav button[data-sp=mdl]")
        pg.wait_for_selector(f'#mbody input[data-hr="coding_models|{bare(code)}"]', timeout=15000)
        # every chain draws the same row, the router's included (SPEC 750): each carries the hrs input and the effort select
        rows = pg.eval_on_selector_all("#mbody .mr:has(input.hr)", "els=>els.map(e=>({m:e.querySelector('.mn').textContent,hr:e.querySelector('input.hr').value,ph:e.querySelector('input.hr').placeholder}))")
        ck(len(rows) >= 6, f"panel renders the chains' rows ({len(rows)})", rows)
        rev = {bare(m): m for m in chain_rows("review")}   # the panel's id -> the spelling the file stores
        head_raw = chain_rows("review")[0]                 # the chain's head, the review row this copy gave a window to
        head, head_hr = bare(head_raw), window_of("review", head_raw)
        # #3048: read the row through its own `data-row` (chain|id), the key the page drew it with, so a label or a
        # same-named row in another chain can never answer. The coding section draws exactly one row for the head.
        ck(pg.eval_on_selector_all(f'#mbody .mr[data-row="coding_models|{bare(code)}"]', "e=>e.length") == 1, "the coding section draws one row for the head")
        coding_hr = pg.input_value(f'#mbody .mr[data-row="coding_models|{bare(code)}"] input.hr')
        ck(coding_hr == panel_want, f"coding {bare(code)} shows the window GET /api/models serves", coding_hr)
        # the head's window comes from the file, never a literal: the shipped chain follows the owner's tuning
        ck(pg.input_value(f'#mbody .mr[data-row="review_models|{head}"] input.hr') == head_hr, f"review {head} shows its stored window", head_hr)
        idle = next(b for b, m in rev.items() if b != head and not window_of("review", m))   # a chain member with no window
        idle_hr = pg.input_value(f'#mbody .mr[data-row="review_models|{idle}"] input.hr')
        ck(idle_hr == "", f"{idle} has no window (all day)", idle_hr)
        ck(all(r["ph"] == "always" for r in rows), "empty rows read 'always'", [r for r in rows if r['ph']!='always'])
        # the one Save writes the theme behind its confirm dialog (#1278): `#ssave` wakes on a dirty theme, `#sdok` writes it,
        # and the bar's `#schg` says what happened. The old `#msv`/`.msave .st` controls are gone from the page (#3048).
        ck(pg.is_disabled("#ssave"), "Save starts disabled (nothing dirty)")
        cur = window_of("review", rev[idle])
        want = "21-05" if cur != "21-05" else "20-04"
        pg.fill(f'input[data-hr="review_models|{idle}"]', want)
        pg.dispatch_event(f'input[data-hr="review_models|{idle}"]', "change")
        pg.wait_for_timeout(300)
        ck(not pg.is_disabled("#ssave"), "Save wakes up on an edit")
        pg.click("#ssave")
        pg.wait_for_selector("#sdlg[open]", timeout=15000)
        ck(not pg.is_disabled("#sdok"), "the confirm dialog offers the save", pg.inner_text("#sdpre")[:120])
        pg.click("#sdok")
        pg.wait_for_timeout(900)
        saved = window_of("review", rev[idle])
        ck(saved == want, f"the browser's Save wrote {want} to models.json", saved)
        ck("saved" in pg.inner_text("#schg").lower(), "the bar says saved", pg.inner_text("#schg")[:60])
        pg.reload(wait_until="domcontentloaded"); pg.wait_for_timeout(1200)
        pg.click("button[data-t=set]")
        pg.click(".snav button[data-sp=mdl]")
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
        other = pg.eval_on_selector_all(".snav button, #side details.sp > summary", "els=>els.map(e=>e.textContent.trim())")
        ck(any("spend" in o.lower() for o in other) and any("subscription" in o.lower() for o in other), "the other panels are on the page", other[:8])
        if not pg.eval_on_selector("details#stats", "e=>e.open"): pg.click("details#stats > summary"); pg.wait_for_timeout(1500)
        ck(pg.eval_on_selector("#stats", "e=>e.open") is True, "cost & usage opens")
        stxt = pg.inner_text("#stats")
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
    # #3048: a fixture row that names no `r` is this hub's own repository, filled on the read (hub_theme_board.py:66). The
    # assertion pins the absent-`r` path the fixture now takes; a string set in the fixture and read by nothing is gone.
    served = json.loads(req("/api/board")[1])["prs"]["items"]
    ck(served and all(x.get("r") == HREPO for x in served), "every served open-PR row carries the hub's repository", [(x.get("n"), x.get("r")) for x in served])
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
