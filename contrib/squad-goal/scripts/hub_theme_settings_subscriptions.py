# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
PLANS = pathlib.Path(__file__).resolve().parent / "plans.json"
SUBS = ("claude", "grok", "gpt", "gemini")
SUB_ACR = dict(zip(SUBS, ("CL", "GK", "GPT", "GEM")))   # the footer's token for each subscription (#1839): 2-3 letters, spelled here and nowhere else
def provider(m):
    m = m or ""
    if re.search(r"grok", m): return "grok"
    if re.search(r"gpt", m): return "gpt"
    if re.search(r"gemini", m): return "gemini"
    if re.match(r"^(claude-)?(sonnet|opus|fable|haiku)\b", m): return "claude"
    return None
# Monthly list prices in USD, as published (they differ by country: pick "Custom amount" to enter what you really pay, in any currency).
PRESETS = {
    "claude": {"pro": ("Pro", 20), "max5": ("Max 5x", 100), "max20": ("Max 20x", 200), "team": ("Team standard seat", 30), "teamp": ("Team premium seat", 150)},
    "gpt": {"plus": ("Plus", 20), "pro": ("Pro", 200), "business": ("Business seat", 30)},
    "grok": {"super": ("SuperGrok", 30), "heavy": ("SuperGrok Heavy", 300)},
    "gemini": {"plus": ("AI Plus", 7.99), "pro": ("AI Pro", 19.99), "ultra": ("AI Ultra", 249.99)},
}
RATES = HUBDIR / "hub-rates.json"
def rate(cur):
    """Units of `cur` per 1 USD, from open.er-api.com (no key, only the currency code leaves this machine); cached 12 h, a stale cache beats no rate."""
    if cur == "USD": return 1.0
    try: c = json.loads(RATES.read_text())
    except Exception: c = {"t": 0, "r": {}}
    if time.time() - c["t"] > 43200:
        try:
            r = json.loads(subprocess.run(["curl", "-s", "-m", "10", "https://open.er-api.com/v6/latest/USD"], capture_output=True, text=True, timeout=15).stdout)
            if r.get("result") == "success": c = {"t": time.time(), "r": r["rates"]}; RATES.write_text(json.dumps(c))
        except Exception: pass
    return c["r"].get(cur)
def plans_get():
    try: d = json.loads(PLANS.read_text())
    except Exception: d = {}
    out = {}
    for k in SUBS:
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool): v = {"plan": "custom", "amount": v, "currency": "USD"}   # the first format: a bare USD number
        out[k] = v if isinstance(v, dict) else None
    return out
def plan_label(k, v):
    """What one plan reads as in a diff line: its preset name and price, its custom amount and currency, or no plan,
    plus the hand-entered quota when the owner set one."""
    if not v: return "no plan"
    p = v.get("plan")
    if p == "custom": base = f"{v.get('amount')} {v.get('currency')} a month"
    else:
        n = PRESETS.get(k, {}).get(p)
        base = f"{n[0]} · US${n[1]}" if n else str(p)
    return base + (f" · quota {v['quota']}%" if v.get("quota") is not None else "")
def plans_diff(old, new):
    """The diff a Save shows before it writes: one line per changed plan, in the order the theme reads them."""
    return [f"Subscriptions · {sub_name(k, new.get(k), None)}: {plan_label(k, old.get(k))} -> {plan_label(k, new.get(k))}"
            for k in SUBS if old.get(k) != new.get(k)]
def plans_set(new):
    """One subscription = a preset plan, or a custom monthly amount in any 3-letter currency; null clears it. Atomic write.
    A body with no `confirm` writes, as it did before #1281, so an existing caller (a script, curl) keeps its contract.
    Save's preview is explicit: `confirm: false` answers the diff of every changed plan and writes nothing, which is what
    the dialog shows first, and `confirm: true` writes. A non-boolean `confirm` is refused, never read as either."""
    if not isinstance(new, dict) or set(new) - set(SUBS) - {"confirm"}: return {"err": "unknown subscription"}
    if "confirm" in new and not isinstance(new["confirm"], bool): return {"err": "confirm: true or false"}
    confirm = new.get("confirm", True) is True
    d = plans_get(); out = dict(d)
    for k, v in new.items():
        if k == "confirm": continue
        if v is None: out[k] = None; continue
        if not isinstance(v, dict): return {"err": f"{k}: bad plan"}
        if "plan" not in v:   # only the quota the owner reads off the provider's usage page changes
            q, w = v.get("quota"), v.get("qwin")
            if not out.get(k) or not v or not set(v) <= {"quota", "qwin"} or (q is not None and (isinstance(q, bool) or not isinstance(q, (int, float)) or not 0 <= q <= 100)) or (w is not None and not (isinstance(w, str) and len(w) <= 40)): return {"err": f"{k}: set a plan first; quota is 0-100, the window label up to 40 characters"}
            out[k] = {**out[k], **{x: v[x] for x in ("quota", "qwin") if x in v}}; continue
        plan = v.get("plan"); keep = {x: out[k][x] for x in ("quota", "qwin") if out.get(k) and out[k].get(x) is not None}
        if plan in PRESETS[k]: out[k] = {"plan": plan, "amount": PRESETS[k][plan][1], "currency": "USD", **keep}; continue
        amt, cur = v.get("amount"), str(v.get("currency") or "").upper()
        if plan != "custom" or isinstance(amt, bool) or not isinstance(amt, (int, float)) or not 0 <= amt <= 1e7 or not re.fullmatch(r"[A-Z]{3}", cur): return {"err": f"{k}: need a plan, or an amount and a 3-letter currency"}
        out[k] = {"plan": "custom", "amount": amt, "currency": cur, **keep}
    diff = plans_diff(d, out)
    if not confirm: return {"diff": diff}
    if diff:
        tmp = PLANS.with_suffix(".tmp"); tmp.write_text(json.dumps(out, indent=2) + "\n"); os.replace(tmp, PLANS)
    return {"diff": diff, "plans": out}
# ---- quota read from each provider's own CLI, so nobody has to type it. Every window is {label, pct, resets (epoch)}.
QUOTA = SHARED / "hub-quota.json"
try: quota = json.loads(QUOTA.read_text())
except Exception: quota = {}
def q_claude():
    """`claude -p` streams a rate_limit_event with the five-hour and seven-day utilisation of the subscription (one tiny haiku call, no session kept). --strict-mcp-config: the probe reads a rate limit and needs no MCP server; without it the machine's server set loads into the call and pays tens of thousands of input tokens of tool schemas before the one word it reads."""
    out = subprocess.run(["claude", "-p", "ok", "--strict-mcp-config", "--model", "haiku", "--output-format", "stream-json", "--verbose", "--max-turns", "1", "--no-session-persistence"],
                         capture_output=True, text=True, timeout=180, cwd=tempfile.gettempdir()).stdout
    w = None
    for l in out.splitlines():
        try: e = json.loads(l)
        except Exception: continue
        if e.get("type") == "rate_limit_event": w = (e.get("rate_limit_info") or {}).get("unifiedWindows") or w
    if not w: return None
    names = {"seven_day": "weekly", "five_hour": "5 h"}
    return {"windows": sorted(({"label": names.get(k, k.replace("_", " ")), "pct": round(v["utilization"] * 100, 1), "resets": v.get("resetsAt")} for k, v in w.items()), key=lambda x: x["label"] != "weekly")}
def q_codex():
    """`codex app-server` answers account/rateLimits/read over stdio (JSON-RPC)."""
    pr = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        def send(o): pr.stdin.write(json.dumps(o) + "\n"); pr.stdin.flush()
        def call(i, m, params):
            send({"id": i, "method": m, "params": params}); end = time.time() + 15
            while time.time() < end:
                if not select.select([pr.stdout], [], [], 0.5)[0]: continue
                l = pr.stdout.readline()
                if not l: return None
                try: e = json.loads(l)
                except Exception: continue
                if e.get("id") == i: return e
        call(1, "initialize", {"clientInfo": {"name": "squad-hub", "version": "1"}}); send({"method": "initialized"})
        r = ((call(2, "account/rateLimits/read", None) or {}).get("result") or {}).get("rateLimits")
    finally: pr.kill()
    if not r: return None
    ws = []
    for key, fallback in (("secondary", "weekly"), ("primary", "5 h")):
        x = r.get(key)
        if x: m = x.get("windowDurationMins") or 0; ws.append({"label": "weekly" if m >= 7 * 1440 else ("5 h" if m == 300 else fallback), "pct": x.get("usedPercent"), "resets": x.get("resetsAt")})
    return {"windows": ws, "plan": r.get("planType"), "reached": r.get("rateLimitReachedType")}
GROK_LOG = HOME / ".grok/logs/unified.jsonl"
GROK_SETTINGS = HOME / ".grok/settings_cache.json"   # the config the grok CLI caches at startup
GROK_SCAN_MAX = 64 * 1024 * 1024   # how far back the probe walks before calling the log blind
GROK_KEPT = HUBDIR / "hub-grok.json"   # the newest credit reading seen: the log rotates, and its one credit line with it (#553)
def grok_no_source(first):
    """The true reason for no reading: the grok CLI writes creditUsagePercent only when a TUI session starts."""
    since = f" since its first entry at {first}" if first else ""
    return f"no credit line in ~/.grok/logs/unified.jsonl{since}: open the grok TUI once to write one"
def grok_keep(k):
    """Keep reading k ({pct, start, end, asof}) when it is newer than the kept one. Atomic: tmp, then rename."""
    try: old = json.loads(GROK_KEPT.read_text())
    except Exception: old = None
    if isinstance(old, dict) and (old.get("asof") or 0) >= k["asof"]: return old
    tmp = _temp_name(GROK_KEPT)
    try: tmp.write_text(json.dumps(k)); os.replace(tmp, GROK_KEPT)
    except OSError: tmp.unlink(missing_ok=True)
    return k
def grok_plan():
    """The plan the grok CLI itself caches; it is also what proves the settings file is being read at all."""
    try: p = json.loads(json.loads(GROK_SETTINGS.read_text())["payload"])
    except Exception: return None
    return ((p.get("settings") or {}).get("subscription_tier_display")) or None
GROK_REFRESH = pathlib.Path(__file__).resolve().parent / "grokrefresh.sh"
GROK_REFRESH_TTL = 3600   # at most one TUI start an hour: a period's line survives the whole period (#1530)
def grok_refresh_due(kept, now=None):
    """True when the kept reading cannot answer: none at all, or a period that already ended. A reading inside its
    period is fresh, and the gauge needs no start."""
    now = time.time() if now is None else now
    if not isinstance(kept, dict) or kept.get("pct") is None: return True
    end = kept.get("end")
    return not end or now > end
def grok_refresh(now=None):
    """Start the grok TUI once through grokrefresh.sh, so the CLI writes a new credit line (#1530). The pty lives in
    that script, never here. Returns the script's one line, or None when it could not run."""
    try:
        r = subprocess.run(["bash", str(GROK_REFRESH), "--ttl", str(GROK_REFRESH_TTL)], capture_output=True,
                           text=True, timeout=120, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return (r.stdout or r.stderr or "").strip() or None
def _grok_kept():
    """The kept grok reading, read back after the log rotates or after a refresh."""
    try: return json.loads(GROK_KEPT.read_text())
    except Exception: return None
def q_grok():
    """Read the newest log line carrying a BillingConfig: `currentPeriod{start,end}`, and `creditUsagePercent`
    when the CLI writes it. The grok CLI pushes that payload in the line's `ctx.config`, and its message wording
    drifts between versions (`billing: fetched credits config` in older ones), so match the fields and never the
    text. A line whose `currentPeriod` carries an `end` is a reading; the 1.0.62 CLI omits `creditUsagePercent`
    when the period's usage is 0%, so a missing field on such a line reads as 0%, never as no reading.
    Finding none is a reading of its own — `unavailable` with the reason — so refresh_quota cannot keep showing
    yesterday's number. The newest reading is kept in GROK_KEPT and read back after the log rotates; `until` is
    its period end (quota_row)."""
    last = first = None
    try:
        with open(GROK_LOG, "rb") as fh:
            fh.seek(0, 2); size = fh.tell(); pos = size; floor = max(0, size - GROK_SCAN_MAX)
            while pos > floor and last is None:
                hi = pos; step = min(4_000_000, pos - floor); pos -= step
                if hi < size:   # the read would end inside a line: carry it to its newline, so this chunk holds that
                    fh.seek(hi)  # whole line and not its head alone, the tail of it being what the older chunk drops
                    while hi < size and hi < pos + 8_000_000:
                        b = fh.read(65_536); hi += len(b)
                        if b"\n" in b or not b: break
                fh.seek(pos); chunk = fh.read(hi - pos)
                if pos:   # read from the middle: the first line is half a line unless the byte before is the newline
                    fh.seek(pos - 1)
                    if fh.read(1) != b"\n": chunk = chunk.split(b"\n", 1)[-1]
                for l in chunk.decode("utf8", "ignore").splitlines():
                    try: d = json.loads(l)
                    except Exception: continue
                    if not isinstance(d, dict): continue
                    if first is None and d.get("ts"): first = d["ts"]   # the first line of the oldest chunk read
                    c = (d.get("ctx") or {}).get("config") or {}
                    if isinstance(c.get("currentPeriod"), dict) and c["currentPeriod"].get("end"): last = (d, c)
    except OSError: pass
    k = None
    if last:
        d, c = last; per = c.get("currentPeriod") or {}
        end = dt.datetime.fromisoformat(per["end"].replace("Z", "+00:00")).timestamp() if per.get("end") else None
        pct = c.get("creditUsagePercent")
        k = grok_keep({"pct": 0.0 if pct is None else pct, "end": end,
                       "asof": dt.datetime.fromisoformat(d["ts"].replace("Z", "+00:00")).timestamp()})
    else:
        k = _grok_kept()
    if grok_refresh_due(k):   # the CLI writes a credit line only on a TUI start: start one, then read again (#1530)
        grok_refresh()
        k = _grok_kept()
    if not isinstance(k, dict) or k.get("pct") is None:
        return {"windows": [], "unavailable": True, "err": grok_no_source(first), "plan": grok_plan()}
    return {"windows": [{"label": "weekly", "pct": k["pct"], "resets": k.get("end")}], "plan": grok_plan(),
            "asof": k.get("asof"), "until": k.get("end")}
NOBROWSE = None
def no_browser_dir():
    """A PATH entry whose `open` does nothing. A quota probe must never raise a login page: agy falls back to an
    interactive OAuth flow when the HOME it runs under carries no credential, and that flow opens a Google account
    page in the operator's browser. The probe runs with this entry first, so the page cannot open, whatever the CLI
    decides about its own auth."""
    global NOBROWSE
    if NOBROWSE is None:
        d = pathlib.Path(tempfile.mkdtemp(prefix="hub-nobrowse-"))
        for name in ("open", "xdg-open"):
            f = d / name
            f.write_text("#!/bin/sh\nexit 0\n")
            f.chmod(0o755)
        NOBROWSE = d
    return NOBROWSE
def q_agy():
    """`agy -p /quota` prints one line per model group (no model call): name, `Weekly Limit Remaining`, N%, reset ISO. The Gemini group is the Google plan's.

    A probe is not a login: it never opens a browser (see `no_browser_dir`), and a CLI that answers `not
    authenticated` reads as no quota at all."""
    env = {**os.environ, "PATH": f"{no_browser_dir()}:{os.environ.get('PATH', '')}", "BROWSER": "/usr/bin/true"}
    r = subprocess.run(["agy", "-p", "/quota"], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL, env=env)
    if "not authenticated" in (r.stdout + r.stderr).lower():
        return None
    w = agyquota.gemini_window(r.stdout)   # review.sh reads the agy-limited reset through the same module
    return {"windows": [{"label": "weekly", "pct": w[0], "resets": w[1]}]} if w else None
WEEKS = HUBDIR / "hub-weeks.json"
try: weeks = json.loads(WEEKS.read_text())
except Exception: weeks = {}
def note_week(k, r):
    """Remember the peak weekly % per window (keyed by its reset hour), so a month's quota value can sum the closed weeks too."""
    for w in r["windows"]:
        if w["label"] == "weekly" and w.get("resets") and w.get("pct") is not None:
            e = weeks.setdefault(k, {}).setdefault(str(round(w["resets"] / 3600)), {"end": w["resets"], "pct": 0})
            e["pct"] = max(e["pct"], w["pct"]); e["end"] = w["resets"]
    try: WEEKS.write_text(json.dumps(weeks))
    except OSError: pass
def week_value(k, usd, days, mstart):
    """Quota value this month = sum over weekly windows of peak % x (monthly price x 7 / days in month); a window counts when it started this month or is still open."""
    if usd is None or k not in weeks: return None
    tot = 0.0
    for e in weeks[k].values():
        if e["end"] - 7 * 86400 >= mstart or e["end"] > time.time(): tot += e["pct"] / 100 * usd * 7 / days
    return round(tot, 2)
def q_nvidia():
    """NVIDIA NIM's free tier publishes no quota endpoint: the plan is a rate limit (about 40 requests a minute), never a
    monthly share, so the row says so rather than drawing a guess."""
    return {"windows": [], "unavailable": True, "err": "free tier: a per-minute rate limit, no monthly reading"}
QUOTA_PROBES = {"claude": q_claude, "gpt": q_codex, "grok": q_grok, "gemini": q_agy, "nvidia": q_nvidia}
def quota_store(k, fn):
    """Run probe fn and store its answer for k. An answer with no windows replaces the row outright, so a reading that
    stopped tracking reality cannot pass as current, unless it says `keep` (no new reading, e.g. an expired token):
    then the last good windows stay with their own asof, and the answer's reason rides along. A probe that raises keeps
    the last good value."""
    try:
        r = fn()
        if not r: return
        old = quota.get(k) or {}
        if r.get("keep") and not r.get("windows") and old.get("windows"):
            old["err"] = r.get("err")
            return
        r.pop("keep", None)
        r.setdefault("asof", time.time())
        quota[k] = r
        if r.get("windows"): note_week(k, r)
    except Exception as e: quota.setdefault(k, {"windows": []})["err"] = str(e)[:120]
def refresh_quota_once():
    """One pass: ask each CLI for its quota (quota_store), then persist every row."""
    for k, fn in QUOTA_PROBES.items(): quota_store(k, fn)
    quota_write()
QUOTA_LOCK = threading.Lock()
def quota_write():
    """Persist the readings. One lock, so concurrent passes never drop each other's entry."""
    with QUOTA_LOCK:
        try: QUOTA.write_text(json.dumps(quota))
        except OSError: pass
def refresh_quota():
    """Every 20 min, one refresh_quota_once pass (the age of the last good value is shown on the row), and only while a
    page reads this profile (#1790): the probe starts a CLI, so a page read never cuts its sleep short and a page that
    opens and closes does not start one."""
    while True:
        if read_wanted(): refresh_quota_once()
        time.sleep(1200)
QUOTA_MAX_AGE = 3 * 3600   # a probe repolls every 20 min; a reading older than this stopped tracking the account
def quota_row(k, now=None):
    """What the cost panel and the footer show for provider k: the CLI's own reading, or the reason there is no reading.
    A reading past QUOTA_MAX_AGE is no reading, and says when the last one was taken, and why it did not move when the
    probe left a reason: a broken probe and a provider nobody called read the same without it. A reading with `until`
    (the kept grok reading: the CLI writes a credit line only when a TUI session starts) holds to its period end, then
    is stale."""
    r = quota.get(k); now = time.time() if now is None else now
    if not r: return None
    if r.get("windows") and r.get("until"):
        if now <= r["until"]: return r
        return {"unavailable": True, "stale": True, "asof": r.get("asof"), "plan": r.get("plan"),
                "err": f"stale: the last reading ({r['windows'][0].get('pct')}%) is for a period that ended "
                       f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(r['until']))}; open the grok TUI once to write a new one"}
    if r.get("windows"):
        age = now - r["asof"] if r.get("asof") else 0
        if age <= QUOTA_MAX_AGE: return r
        taken = time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(r['asof']))
        why = f"; {r['err']}" if r.get("err") else ""
        return {"unavailable": True, "asof": r["asof"], "plan": r.get("plan"),
                "err": f"no reading: the last one is {round(age / 3600)} h old (the limit is {QUOTA_MAX_AGE // 3600} h), taken {taken}{why}"}
    if r.get("err"): return {"unavailable": True, "err": r["err"], "asof": r.get("asof"), "plan": r.get("plan")}
    return None

BRANDS = {"claude": "Claude", "grok": "Grok", "gpt": "ChatGPT", "gemini": "Google"}
def sub_name(k, plan, detected):
    """The card name comes from the account: the plan the probe detected, else the plan chosen in the card, else the provider.
    A plan of None (no plan, or a plan cleared) and a dict without a `plan` key both read as the provider's name."""
    name = detected or (PRESETS[k].get(plan.get("plan"), (None,))[0] if isinstance(plan, dict) else None)
    if not name: return BRANDS[k]
    name = str(name); name = name[:1].upper() + name[1:]
    return name if BRANDS[k].lower() in name.lower() else f"{BRANDS[k]} {name}"

def subs_month():
    """Per subscription, this month: API-equivalent used so far, the plan price prorated to today, and the saving."""
    now = time.localtime(); days = __import__("calendar").monthrange(now.tm_year, now.tm_mon)[1]; frac = (now.tm_mday - 1 + (now.tm_hour * 60 + now.tm_min) / 1440) / days
    used = {k: 0.0 for k in SUBS}; api = 0.0   # api = pay-per-use models (deepseek…): billed per token on top of the plans
    for r in all_runs():
        if time.localtime(r["t0"])[:2] != now[:2]: continue
        for m, v in r["mod"].items():
            p = provider(m)
            if p: used[p] += v["spend"]
            else: api += v["spend"]
    out = {}; mstart = time.mktime((now.tm_year, now.tm_mon, 1, 0, 0, 0, 0, 0, -1))
    for k, plan in plans_get().items():
        eq = round(used[k], 2); usd = None; rt = None
        if plan:
            rt = rate(plan["currency"]); usd = plan["amount"] / rt if rt else None
        pr = round(usd * frac, 2) if usd is not None else None
        out[k] = {"plan": plan, "usd": round(usd, 2) if usd is not None else None, "rate": rt, "noRate": bool(plan and not rt), "eq": eq, "qval": week_value(k, usd, days, mstart), "prorated": pr, "saved": round(eq - pr, 2) if pr is not None else None, "x": round(eq / pr, 1) if pr else None}
    plans = sum(v["usd"] for v in out.values() if v["usd"] is not None)
    for k in out:
        out[k]["quota"] = quota_row(k)
        out[k]["name"] = sub_name(k, out[k]["plan"], (quota.get(k) or {}).get("plan"))
    return {"subs": out, "frac": round(frac, 3), "plans": round(plans, 2), "api": round(api, 2), "total": round(plans + api, 2)}

MODELS = pathlib.Path(__file__).resolve().parent / "models.json"
CHAINS = ("coding_models", "review_models", "vision_models", "router_models")   # all four live in models.json (SPEC 750)
ROUTER_CHAIN = "router_models"   # the router's own chain: the models the router agent runs on, in rank order (#95)
