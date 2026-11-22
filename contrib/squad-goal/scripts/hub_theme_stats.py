# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
def working(rows):
    """The ids of the runs that work: a run folder with no verdict and a dashboard that answers, as the list above groups them. Test passes are not runs."""
    return [r["id"] for r in rows if r.get("kind") not in ("test", "childcc") and group_of(r) == "running"]

def canon_model(raw):
    """The canonical id of one model spelling, through the one registry (ADR-0147): `claude:claude-opus-5-5` and
    `claude-opus-5-5[1m]` name one model. A spelling the registry does not know keeps its raw form."""
    ref = modelreg.normalize(raw or "unknown")
    return ref.id if ref.known else (raw or "unknown")

CACHE_READ_RATIO = modelreg.CACHE_READ_RATIO   # the one shared ratio (#495, #791); a registry cache_read overrides it per model

def _usage_spend(model, fresh, out, cr):
    """USD of one count of tokens: the PRICES table first, else the registry's display_price, whose cache reads take its cache
    rate or else the shared ratio of the input rate (#789, #791, #795). Returns (spend, unpriced); a model neither names counts 0.0.
    `fresh` is the input that missed the cache, `cr` the cache reads."""
    spend = priced({"model": model, "tokens_in": fresh + cr, "cache_read": cr, "tokens_out": out, "spend": None})   # None: no PRICES match
    if spend is not None: return spend, False
    rate = modelreg.display_price(model) or {}
    rin, rout = rate.get("input"), rate.get("output")
    if not (isinstance(rin, (int, float)) and isinstance(rout, (int, float))): return 0.0, True
    rcr = rate.get("cache_read")   # a null is unknown, priced at the shared ratio: only a number is a rate (#795)
    if not isinstance(rcr, (int, float)): rcr = rin * CACHE_READ_RATIO
    return ((fresh * rin) + (cr * rcr) + out * rout) / 1e6, False

def ledger_calls():
    """The review calls of $SG_STATE/token-usage.tsv (review.sh), one dict per line: t0, model (canonical), in (cache reads
    included, as an outcome's tokens_in is), out, cache and spend. Only review.sh writes the ledger, so every line is a
    review call. A call the PRICES table does not match is priced from the registry's display_price, its cache reads at the
    display_price cache_read, else at the shared CACHE_READ_RATIO share of the input price. A missing or unreadable ledger is no calls."""
    out = []
    try: lines = (STATE / "token-usage.tsv").read_text(errors="replace").splitlines()
    except OSError: return out
    for l in lines:
        p = l.split("\t")
        if len(p) < 6: continue
        try:
            t0 = time.mktime(time.strptime(p[0], "%Y-%m-%dT%H:%M:%S"))
            i, o, cr = int(float(p[3])), int(float(p[4])), int(float(p[5]))
        except (ValueError, OverflowError): continue
        m = canon_model(p[2])
        spend, unpriced = _usage_spend(m, i, o, cr)
        if unpriced: log_once(f"hub: no price for {m}, its review calls count 0.0 in the Spend totals")
        # a 7th field marks a call with no usage count (tokens.py): it is counted as uncounted, never as a measure (#789)
        uncounted = len(p) > 6 and bool(p[6].strip())
        out.append({"t0": t0, "model": m, "in": i + cr, "out": o, "cache": cr, "spend": round(spend, 4), "unpriced": unpriced, "uncounted": uncounted})
    return out

OPERATOR = None   # squad.operator_usage.Cache: the operator transcripts' fold, kept between polls (#1756)
OPERATOR_WINDOW_S = 48 * 3600   # the Spend panel reads two days: a row older than this is never a candidate (#1897)

def operator_calls(runs=None, src="both"):
    """The operator session's model calls, its agents and workflows included (#1756): this checkout's Claude Code project
    folder (~/.claude/projects, subagent transcripts in subfolders), folded by squad.operator_usage. A call the token usage
    ledger or a run record already names is dropped, so a loop call is never counted twice. The match set follows the
    panel's billing filter: a loop call `src` drops is not on the chart, so it must not hide its transcript twin either.
    The read is bounded: only a transcript whose stamp changed is parsed again, the fold is kept, and an unchanged folder
    is not walked. This is the Spend panel's third source and no guard's: a missing package or folder is no call, never a
    guess. `runs` is the stats' own list, so one poll reads the run folders once; the operator rows are
    {id, model, in, out, cache, t0}."""
    global OPERATOR
    co = str(CHECKOUT) if CHECKOUT else ""
    folder = transcript_folder(os.path.realpath(co)) if co else None
    if folder is None:
        return []
    roots = squad_roots(os.environ.get("SG_SQUAD"), os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__)
    try:
        (OU,) = load_squad(roots, ("operator_usage",))
    except Exception as e:
        log_once(f"hub: operator usage unavailable, none is counted: {type(e).__name__}: {' '.join(str(e).split())}")
        return []
    if OPERATOR is None or not isinstance(OPERATOR, OU.Cache):
        OPERATOR = OU.Cache(STATE / "operator-usage.json")   # the fold outlives this process, so a restart re-parses nothing (#1888)
    rows = OPERATOR.read(folder)
    keep = lambda m: _src_keeps(src, m)   # noqa: E731  the panel's own filter, so the two readings never disagree
    since = time.time() - OPERATOR_WINDOW_S   # the panel reads a window, so the match never walks the history (#1897)
    loop = [c for c in ledger_calls() if isinstance(c, dict) and keep(c.get("model") or "")]
    for r in (all_runs() if runs is None else runs):
        loop.extend(c for c in (r.get("calls") or []) if isinstance(c, dict) and keep(c.get("model") or ""))
    return OU.calls(rows, loop, canon=canon_model, since=since)

def _billed(src, model, spend):
    """A call's spend under the stats' billing filter: both, the subscriptions only, or the API only (as `stats` filters runs)."""
    if src == "both": return spend
    return spend if bool(SUBSCRIPTION.match(model)) == (src == "sub") else 0.0

PACE_WINDOW_H = 24

def _src_keeps(src, model):
    """The stats' billing filter: both, the subscriptions only, or the API only, as `stats` and the pie above read it."""
    return src == "both" or bool(SUBSCRIPTION.match(canon_model(model))) == (src == "sub")

def _run_tokens(r, src="both"):
    """A run's tokens, in + out over the models the billing filter keeps, as the token ring counts them (#1073). A module
    record without token fields, or a record with no `mod`, counts 0: a legacy record never breaks the Spend box."""
    n = 0
    for raw, v in (r.get("mod") or {}).items():
        if not isinstance(v, dict) or not _src_keeps(src, raw): continue
        n += v.get("in", 0) + v.get("out", 0)
    return n

def spend_pace(runs, calls, now, src="both", window_h=PACE_WINDOW_H):
    """The Spend box's two readings (#1251), in tokens (#1267), computed here so the page only prints them. The models run
    on subscriptions, so the summary counts tokens, never dollars: a run's tokens are its in + out over the models kept, a
    call's its in + out; `src` is the stats' billing filter, as the chart and pie above honor it. A finished run counts at
    the hour it ended, a review call at the hour it started. The window is the last `window_h` one-hour slots back from
    `now`; a slot with no run and no call is an idle hour, kept out of the median (never a zero). last60_tokens sums what
    ended or started in the last 60 minutes. `unpriced` counts the window's calls the price table cannot price: they carry
    no dollars and are named, never hidden, and their tokens are still counted, as every kept call's are."""
    slots, unpriced = {}, 0
    def _slot(t):
        age = now - t
        return int(age // 3600) if 0 <= age < window_h * 3600 else None
    for r in runs:
        k = _slot(r.get("t1", r["t0"])) if r.get("done") else None
        if k is not None: slots[k] = slots.get(k, 0) + _run_tokens(r, src)
    kept = [c for c in calls if _src_keeps(src, c["model"])]
    for c in kept:
        k = _slot(c["t0"])
        if k is None: continue
        slots[k] = slots.get(k, 0) + c["in"] + c["out"]; unpriced += bool(c["unpriced"])
    last60 = sum(_run_tokens(r, src) for r in runs if r.get("done") and 0 <= now - r.get("t1", r["t0"]) < 3600) + sum(c["in"] + c["out"] for c in kept if 0 <= now - c["t0"] < 3600)
    return {"window_hours": window_h, "active_hours": len(slots), "idle_hours": window_h - len(slots),
            "median_tokens": int(round(st.median(slots.values()))) if slots else None, "last60_tokens": int(last60), "unpriced": unpriced}

def stats(period, src="both"):
    fmt = {"hour": "%Y-%m-%d %H:00", "day": "%Y-%m-%d", "month": "%Y-%m"}[period]
    b = {}; amt = (lambda r: r["spend"]) if src == "both" else (lambda r: r["sub"] if src == "sub" else r["api"])
    allr = [dict(r, spend=amt(r)) for r in all_runs()]
    calls = ledger_calls()   # review calls: they carry spend and tokens, never a run
    op = operator_calls(allr, src)   # the operator session's calls, its own consumer: they ride beside the loop, never inside it (#1756)
    def _tok(r):   # a run's tokens: in + out over every model it called, the count the token ring draws (#1073)
        return sum(v["in"] + v["out"] for v in r["mod"].values())
    for r in allr:
        k = time.strftime(fmt, time.localtime(r["t0"])); x = b.setdefault(k, {"t": k, "spend": 0.0, "tokens": 0, "op": 0, "runs": 0, "durs": []})
        x["spend"] += r["spend"]; x["tokens"] += _tok(r); x["runs"] += 1
        if r["done"]: x["durs"].append(r["dur"])
    for c in calls:
        k = time.strftime(fmt, time.localtime(c["t0"])); x = b.setdefault(k, {"t": k, "spend": 0.0, "tokens": 0, "op": 0, "runs": 0, "durs": []})
        x["spend"] += _billed(src, c["model"], c["spend"]); x["tokens"] += c["in"] + c["out"]
    for c in op:
        if c["t0"] is None: continue
        k = time.strftime(fmt, time.localtime(c["t0"])); x = b.setdefault(k, {"t": k, "spend": 0.0, "tokens": 0, "op": 0, "runs": 0, "durs": []})
        x["op"] += c["in"] + c["out"]
    ks = sorted(b)[-48:]
    rows = [{"t": k, "spend": round(b[k]["spend"], 2), "tokens": int(b[k]["tokens"]), "op": int(b[k]["op"]), "runs": b[k]["runs"], "median_dur": round(st.median(b[k]["durs"]), 1) if b[k]["durs"] else None} for k in ks]
    now = time.localtime(); tot = {"today": 0.0, "month": 0.0, "all": 0.0, "unpriced": sum(1 for c in calls if c["unpriced"]),
           "uncounted": sum(1 for c in calls if c["uncounted"]),
           "tok_today": 0, "tok_month": 0, "tok_all": 0}
    def _add(lt, spend, tokens):
        tot["all"] += spend; tot["tok_all"] += tokens
        if lt[:2] == now[:2]: tot["month"] += spend; tot["tok_month"] += tokens
        if lt[:3] == now[:3]: tot["today"] += spend; tot["tok_today"] += tokens
    for r in allr: _add(time.localtime(r["t0"]), r["spend"], _tok(r))
    for c in calls: _add(time.localtime(c["t0"]), _billed(src, c["model"], c["spend"]), c["in"] + c["out"])
    mt = {}
    for r in allr:
        in_month = time.localtime(r["t0"])[:2] == now[:2]
        for raw, v in r["mod"].items():   # keyed on the canonical id, as the review calls are, so one model is one slice (#789)
            m = canon_model(raw)
            if src == "sub" and not SUBSCRIPTION.match(m) or src == "api" and SUBSCRIPTION.match(m): continue
            x = mt.setdefault(("loop", m), {"model": m, "consumer": "loop", "in": 0, "out": 0, "cache": 0, "spend": 0.0, "month_cost": 0.0, "month_tokens": 0, "calls": 0, "src": raw, "srcs": []})   # the first spelling names the box; every spelling's route is kept (#791)
            if raw not in x["srcs"]: x["srcs"].append(raw)
            for k in ("in", "out", "cache", "calls"): x[k] += v[k]
            x["spend"] += v["spend"]
            if in_month: x["month_cost"] += v["spend"]; x["month_tokens"] += v["in"] + v["out"]   # the pie counts tokens: dollars are a price list, tokens are the work (#1035)
    for c in calls:
        m = c["model"]
        if src == "sub" and not SUBSCRIPTION.match(m) or src == "api" and SUBSCRIPTION.match(m): continue
        x = mt.setdefault(("loop", m), {"model": m, "consumer": "loop", "in": 0, "out": 0, "cache": 0, "spend": 0.0, "month_cost": 0.0, "month_tokens": 0, "calls": 0, "src": m, "srcs": []})
        if m not in x["srcs"]: x["srcs"].append(m)
        x["in"] += c["in"]; x["out"] += c["out"]; x["cache"] += c["cache"]; x["calls"] += 1; x["spend"] += c["spend"]
        if time.localtime(c["t0"])[:2] == now[:2]: x["month_cost"] += c["spend"]; x["month_tokens"] += c["in"] + c["out"]
    for c in op:
        m = canon_model(c["model"])
        if src == "sub" and not SUBSCRIPTION.match(m) or src == "api" and SUBSCRIPTION.match(m): continue
        spend, _ = _usage_spend(m, c["in"], c["out"], c["cache"])
        x = mt.setdefault(("operator", m), {"model": m, "consumer": "operator", "in": 0, "out": 0, "cache": 0, "spend": 0.0, "month_cost": 0.0, "month_tokens": 0, "calls": 0, "src": m, "srcs": []})
        if m not in x["srcs"]: x["srcs"].append(m)
        x["in"] += c["in"]; x["out"] += c["out"]; x["cache"] += c["cache"]; x["calls"] += 1; x["spend"] += spend
        if c["t0"] is not None and time.localtime(c["t0"])[:2] == now[:2]: x["month_cost"] += spend; x["month_tokens"] += c["in"] + c["out"]
    def _box(x):
        box = model_box(x["src"])
        box["routes"] = sorted({model_box(s)["route"] for s in x["srcs"]} - {None})   # every route the model was called by (#791)
        return box
    models = sorted(({**{k: v for k, v in x.items() if k not in ("src", "srcs")}, "spend": round(x["spend"], 2), "box": _box(x)} for x in mt.values()),
                    key=lambda x: -(x["in"] + x["out"]))
    opt = {"calls": len(op), "models": len({canon_model(c["model"]) for c in op}), "unpriced": 0,
           "tokens_today": 0, "tokens_month": 0, "tokens_all": 0, "cost_today": 0.0, "cost_month": 0.0, "cost_all": 0.0}
    for c in op:
        n = c["in"] + c["out"]; opt["tokens_all"] += n
        spend, unpriced = _usage_spend(canon_model(c["model"]), c["in"], c["out"], c["cache"])
        opt["unpriced"] += bool(unpriced); opt["cost_all"] += spend   # a list price, never the owner's bill: the session runs on a subscription
        if c["t0"] is None: continue
        lt = time.localtime(c["t0"])
        if lt[:2] == now[:2]: opt["tokens_month"] += n; opt["cost_month"] += spend
        if lt[:3] == now[:3]: opt["tokens_today"] += n; opt["cost_today"] += spend
    for k in ("cost_today", "cost_month", "cost_all"): opt[k] = round(opt[k], 4)
    pace = spend_pace(allr, calls, time.time(), src)   # the token guard's readings: the loop sources only (#1756)
    return {"buckets": rows, "pace": pace, "totals": {**{k: round(v, 2) for k, v in tot.items() if not k.startswith("tok_")}, **{k: tot[k] for k in ("tok_today", "tok_month", "tok_all")}}, "models": models, "operator": opt}

POLL_CLOCK = time.monotonic   # the age reader of the kept poll answers, a duration and never an instant: a wall-clock jump
                              # moves the view's own timestamps and leaves the age right. A test injects its own clock.
_STATS_KEPT = {}   # (period, src) -> (the input stamp it was built from, answer). The key carries no profile because the
                   # module is one per profile: hubmux loads `hub_<slug>` per profile, and this cache lives in that module (#2440)
_STATS_LOCK = threading.Lock()

def _pair(path):
    """(mtime, size) of one input, or None when it is not there: its creation or its removal is a change of the state."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)

def stats_stamp():
    """The identity of the Spend answer's inputs (#2582): the review ledger, the operator fold, and every run record the
    answer reads. The local hour rides along, since the day and month totals and the pace window move without a file."""
    stamp = [time.strftime("%Y%m%d%H"), _pair(STATE / "token-usage.tsv"), _pair(STATE / "operator-usage.json"),
             (OPERATOR.parsed, OPERATOR.walks, len(OPERATOR.files)) if OPERATOR is not None else None]
    for d in sorted(project_dirs()):
        stamp.append((str(d), _pair(d)))
        for rd in sorted(d.glob("run-*")):
            stamp.append(_pair(rd / "record.jsonl"))
    return tuple(stamp)

def stats_served(period, src="both", now=None):
    """The Spend answer, built again only when one of its inputs moved (#2582).

    The build reads every run's record, the review ledger and the operator fold, and it cost 2.5 seconds of CPU. Every
    open page and every client asks the same question for the same profile, so the answer is kept while its inputs are
    unchanged: a state that has not moved serves every page and every client one build, and a new or changed record, a
    ledger line or an operator transcript builds again. The key is the period and the billing source, so one pair's
    answer never serves another pair. A build that raises keeps nothing, so the next read tries again."""
    key = (period, src)
    with _STATS_LOCK:
        if OPERATOR is None:
            operator_calls([])   # the fold's first read is no change of the state, so it happens before the stamp
        stamp = stats_stamp()
        got = _STATS_KEPT.get(key)
        if got is not None and got[0] == stamp: return got[1]
        answer = stats(period, src)
        _STATS_KEPT[key] = (stamp, answer)
        return answer

def spend_today(now=None):
    """Today's spend for the status tab, read without the history `stats` parses: a run's bucket is its start, and a run
    folder is named for that local date, so only today's and yesterday's folders are read (a run that starts before
    midnight and answers after it keeps yesterday's folder). The bucket rule is the day bucket of `stats`, and the two
    are asserted equal so the meter and the chart never disagree (#1696)."""
    at = time.time() if now is None else now
    today = time.localtime(at)[:3]
    days = [time.strftime("%Y%m%d", time.localtime(at - n * 86400)) for n in (0, 1)]
    spend = 0.0
    for d in project_dirs():
        for day in days:
            for rd in d.glob(f"run-{day}-*"):
                r = scan(rd / "record.jsonl")
                if r and r["t0"] and time.localtime(r["t0"])[:3] == today: spend += r["spend"]
    spend += sum(_billed("both", c["model"], c["spend"]) for c in ledger_calls() if time.localtime(c["t0"])[:3] == today)
    return round(spend, 2)

def adapter_errors():
    """Every `adapter_error` run verdict of the run folders still on disk, counted by (model, backend, cause) (#698).
    The classification is one pure module, `squad/adapter_classes.py`, resolved through the one model resolver (ADR-0147);
    this panel holds no model table and chooses no fix. It only reads: no run's record is opened for writing, and a folder
    that cannot be read is skipped. A missing squad package is reported here and the panel shows nothing, never a guess."""
    out = {"classes": [], "runs": 0, "err": None}
    folders = []
    for d in project_dirs():
        try: folders += [p for p in sorted(pathlib.Path(d).glob("run-*")) if p.is_dir()]
        except OSError: continue
    out["runs"] = len(folders)
    roots = squad_roots(os.environ.get("SG_SQUAD"), os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__)
    try:
        (AC,) = load_squad(roots, ("adapter_classes",))
        out["classes"] = AC.aggregate(folders)
    except Exception as e:
        out["err"] = f"{type(e).__name__}: {' '.join(str(e).split())}"
        log_once(f"hub: adapter errors unavailable, the panel shows nothing: {out['err']} (roots tried: {', '.join(roots)})")
    return out

def delivery():
    """The loop's delivery metrics, read from its own local records (#705): the issue ledgers, the landing ledger,
    the auto/ markers and the run folders. The computation is one pure module, `squad/delivery_metrics.py`; this
    binds it and gathers the run folders, as `adapter_errors` does. It only reads: no record is opened for writing,
    no work is started or retried, and the panel decides nothing. A missing squad package is reported here and the
    panel shows nothing, never a guess."""
    folders = []
    for d in project_dirs():
        try: folders += [p for p in sorted(pathlib.Path(d).glob("run-*")) if p.is_dir()]
        except OSError: continue
    out = {"err": None, "state": str(STATE), "runs": len(folders)}
    roots = squad_roots(os.environ.get("SG_SQUAD"), os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__)
    try:
        (DM,) = load_squad(roots, ("delivery_metrics",))
        out.update(DM.compute(STATE, folders))
    except Exception as e:
        out["err"] = f"{type(e).__name__}: {' '.join(str(e).split())}"
        log_once(f"hub: delivery metrics unavailable, the panel shows nothing: {out['err']} (roots tried: {', '.join(roots)})")
    return out

