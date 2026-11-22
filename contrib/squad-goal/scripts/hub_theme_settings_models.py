# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
def usable(ids, chain):
    """The ids one chain keeps: an unregistered id stays, so the panel shows it as unregistered; a registered id stays
    only when the registry says the chain can run it (ADR-0147, #545)."""
    return [m for m in ids if modelreg.model(m) is None or modelreg.compatible(m, chain)[0]]
def models_doc():
    """models.json as the panel reads it: a v2 file's chains under the v1 keys the page draws (v1_view)."""
    return v1_view(json.loads(MODELS.read_text()))
def v1_view(d):
    """The panel's reading of models.json: a v2 file's chains as the resolver reads them, under the v1 keys the panel draws (ADR-0147)."""
    if d.get("schema_version") != 2: return d
    view = {k: v for k, v in d.items() if k not in ("schema_version", "chains")}
    rows = {k: [e for e in modelreg.chain(d, k.removesuffix("_models")).entries if usable([e.raw], k.removesuffix("_models"))] for k in CHAINS}
    view.update({k: [e.raw for e in rows[k]] for k in CHAINS})
    view["off"] = {k: {e.raw: True for e in rows[k] if not e.on} for k in CHAINS}
    view["hours"] = {k: {e.raw: e.hrs for e in rows[k] if e.hrs} for k in CHAINS}
    view["efforts"] = {k: {e.raw: e.effort for e in rows[k] if e.effort} for k in CHAINS}
    return view
READONLY = ("known", "router_known", "suggest", "version", "labels", "boxes", "repo_diff", "last_coding", "chain", "models", "outside", "file", "counts")   # fields models_get() adds for the panel: posted back, never stored
# One model-id shape, in one place (#221): a bare id, and the context suffix the gateway serves beside it
# (`claude-deepseek-v4-flash[1m]`). The save validates with MODEL_ID and the page's Add box tests the same source,
# substituted when the page is served, so a second spelling cannot refuse an id the save takes -- the shape without the
# bracket refused every Save of the Router queue, because the queue always carries the `[1m]` id.
MODEL_ID_SRC = r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,79}(\[[A-Za-z0-9]{1,8}\])?"
MODEL_ID = re.compile("^" + MODEL_ID_SRC + "$")
# What /model offers that no file lists: the Claude 5 family from the session, the review (codex) ids and the Antigravity ids from `agy models`.
# The add box carries the provider catalogues as they are, minus the two things that made it unreadable: the
# workflow graph's prose (see known_models) and the same model under two spellings (the alias fold below). The
# gemini/gpt-oss ids are the Antigravity catalogue; refresh them from `agy models` when it moves.
EXTRA_MODELS = ["claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5",
    "claude:claude-opus-5-5", "claude:claude-sonnet-5-5", "claude:claude-fable-5-2",
    "claude-deepseek-v4-flash", "claude-deepseek-flash", "gpt-5.6-terra", "gpt-6.1-sol", "claude-session",
    "gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low",
    "gemini-3.7-flash-high", "gemini-3.7-flash-medium", "gemini-3.7-flash-low",
    "gemini-3.6-flash-high", "gemini-3.6-flash-medium", "gemini-3.6-flash-low",
    "gemini-3.1-pro-high", "gemini-3.1-pro-low",
    "claude-sonnet-4-6", "claude-opus-4-6-thinking", "gpt-oss-120b-medium",
    "grok-4.5", "grok-4.6"]
ALIAS = re.compile(r"^claude-(grok|gpt|gemini)-")
# The gateway's own tier suffix, which no chain consumer serves: review.sh hands a bare id to `codex exec`, never
# to the gateway. Folded onto the bare id (`gpt-5.6-terra-fast`) it reads as a codex model, so an entry picked
# from the box failed every call and fell through the chain (#1564).
GATEWAY_ONLY = re.compile(r"-fast$")
RUNNER = "claude:"          # review.sh's prefix for "run this one through the claude CLI"
def bare_id(mid):
    """The model's own name, without the review-path prefix. This is what the panel shows and what the box offers."""
    return mid[len(RUNNER):] if mid.startswith(RUNNER) else mid
def chain_id(chain, mid):
    """The spelling one chain needs in models.json. review.sh routes by prefix (claude:* -> the claude CLI,
    gemini-*/grok-* -> agy/grok, anything else -> codex), so an Anthropic id in review_models has to carry
    `claude:`; every other chain names the bare id the squad hands to the gateway. The panel works in bare ids
    and never sees the prefix: which spelling a role needs is this file's business, not the reader's."""
    b = bare_id(mid)
    return RUNNER + b if chain == "review_models" and b.startswith("claude-") else b
def last_coding_model(limit=15):
    """The model the newest run that got far enough to name one actually coded with.

    The chains say what the NEXT child will be given; that is not the same claim, and it read as one while the
    router was pinned to a model whose provider had stopped serving (2026-10-11). A child's outcome event carries
    its model (`route.router.N`), so the last one is read from the records, newest run first.
    """
    try: ds = sorted((d for runs in run_roots(CHECKOUT) for d in runs.glob("run-*")), key=lambda d: d.stat().st_mtime, reverse=True)[:limit]
    except OSError: return ""
    for d in ds:
        rec = d / "record.jsonl"
        if not rec.exists(): continue
        best = None
        for line in rec.read_text(errors="replace").splitlines():
            if '"outcome"' not in line or '"route.' not in line: continue
            try: e = json.loads(line)
            except Exception: continue
            if e.get("event") == "outcome" and (e.get("node") or "").startswith("route.") and e.get("model"):
                if best is None or (e.get("ts") or 0) > (best.get("ts") or 0): best = e
        if best: return best["model"]
    return ""

GATEWAY = (os.environ.get("ANTHROPIC_BASE_URL") or "http://localhost:8016").rstrip("/")
_gw = {"t": 0.0, "ids": []}
def gateway_models(ttl=120):
    """The ids the gateway itself serves, read from its own `/v1/models` -- the same list Claude Code's `/model` picker
    shows as "From gateway". Live, because the catalogue changes on the gateway's side and a hardcoded copy went stale
    (the GPT family was missing from the add box, 2026-10-11); cached briefly, because the panel asks on every load.
    Empty when the gateway is down, which leaves the other sources to fill the box."""
    if time.time() - _gw["t"] < ttl: return _gw["ids"]
    ids = []
    try:
        with urllib.request.urlopen(GATEWAY + "/v1/models", timeout=3) as r:
            d = json.loads(r.read().decode("utf8", "replace"))
        ids = [m["id"] for m in (d.get("data") or d.get("models") or []) if isinstance(m, dict) and m.get("id")]
    except Exception: pass
    _gw.update(t=time.time(), ids=ids)
    return ids

def runnable_models(ids):
    """The ids a chain consumer can actually run, which the sources behind the box are not all of.

    No chain consumer goes through the gateway: review.sh sends a bare id to `codex exec`, a `claude:`-prefixed one
    to `claude -p --model`, `grok-*` to the grok CLI and `gemini-*` to agy. The gateway's `-fast` tier answers only
    on the gateway route -- the alias that serves it lives in the interactive shell wrapper, which review.sh does
    not go through -- and under its folded id it reached `codex exec` as a model codex does not have: the 400
    "The 'gpt-5.6-terra-fast' model is not supported when using Codex with a ChatGPT account" (#1564). Both sources
    spell it: the gateway's own catalogue and the Claude Code settings, which register the same wrapper aliases.
    """
    return [m for m in ids if not GATEWAY_ONLY.search(m)]

def router_known():
    """The Router queue's add box: the ids the gateway serves, in its own spelling (#225).

    A queue entry is handed to the engine, which runs every call through the gateway, so the box offers only what the
    gateway's catalogue holds. The other CLIs' ids (codex, agy, grok) stay in `known`, for the role chains.

    The gateway's catalogue lists no Anthropic id, and it passes one through, so the box also offers every live
    registry id that matches ANTHROPIC_ID (#687)."""
    reg = modelreg.default_registry()
    anthropic = {m for m, e in reg.entries.items() if ANTHROPIC_ID.match(m) and not e.retired}
    return sorted({m for m in set(gateway_models()) | anthropic if MODEL_ID.match(m) and not provkey.closed(m)})   # no key, never offered (#482)

def model_labels(ids):
    """{id: registry label} for the ids the one registry knows (ADR-0147); an unknown id has no row (#672).

    Display only: the add box matches a typed "4.1" against the label, and the value it saves stays the id."""
    out = {}
    for m in ids:
        e = modelreg.model(m)
        if e is not None and e.label: out[m] = e.label
    return out

def model_suggestions(ids):
    """The add dropdown's options (#687): [{id, text}], the registry label as the text and the id beside it.

    An id no registry entry knows is left out (a gateway-only id such as claude-gpt-5.2): display never guesses a
    name, and a raw id can still be typed. An effort spelling adds its effort word ("Gemini 3.8 Flash high"); two ids
    that still share a text add their route; a last tie adds the id. So every text in one list is unique, and the
    page maps a picked text back to its id."""
    reg = modelreg
    rows = []
    for m in sorted(set(ids)):
        e = reg.model(m)
        if e is None or not e.label: continue
        eff = next((w for w in EFFORTS if m.endswith("-" + w) and reg.model(m[:-len(w) - 1]) is e), None)
        rows.append({"id": m, "text": f"{e.label} {eff}" if eff else e.label})
    for key in (lambda r: f"{r['text']} · {model_box(r['id'])['route']}", lambda r: f"{r['text']} · {r['id']}"):
        counts = {}
        for r in rows: counts[r["text"]] = counts.get(r["text"], 0) + 1
        for r in rows:
            if counts[r["text"]] > 1: r["text"] = key(r)
    return sorted(rows, key=lambda r: (r["text"], r["id"]))

ROUTE_TEXT = {"gateway": "gateway", "direct": "direct API"}   # a cli route reads as its own CLI, named by the registry's argv

def model_box(raw, chain=None):
    """What one hub box that stands for a model shows (#677): the registry label beside the route, or "unregistered".

    The rule lives here, never in the page (ADR-0137). `chain` picks the spelling that chain stores (chain_id), so a
    review row of an Anthropic id reads as the claude CLI it runs through. A cli route is named by its argv's program
    (claude, codex, grok, agy)."""
    reg = modelreg
    spelling = chain_id(chain, raw) if chain else raw
    e, ref = reg.model(spelling), reg.normalize(spelling)
    if e is None or not e.label:
        return {"id": raw, "llm": None, "route": None, "registered": False, "text": "unregistered"}
    argv = ((e.routes.get("cli") or {}).get("argv") or [None]) if ref.route == "cli" else [None]
    route = f"{pathlib.Path(argv[0]).name} CLI" if argv[0] else ROUTE_TEXT.get(ref.route or "", ref.route)
    return {"id": raw, "llm": e.label, "route": route, "registered": True, "text": f"{e.label} · {route}" if route else e.label}

ANTHROPIC_ID = re.compile(r"^claude-(opus|sonnet|haiku|fable)-[0-9][0-9a-z.\-]*$")

def known_models(d):
    """Suggestions for the add box: the models the chains already use, the Claude Code settings, plus EXTRA_MODELS.

    The workflow graph is no longer read: it names a dozen ids in its own prose (the ADR-0113 history of this
    flow), so scraping it filled the box with models that appear nowhere else.

    One model, one row. A non-Anthropic provider is reachable as `grok-4.6` and as `claude-grok-4.6` and both
    spellings were listed: the alias is folded onto the bare id, which is the form the chains name and the
    bridge answers.

    An id offered here is one a chain consumer runs: the assembled list goes through runnable_models, so the
    gateway's `-fast` tier never reaches the box, where it costs a whole run to discover.
    """
    out = list(EXTRA_MODELS) + gateway_models()
    for k in CHAINS: out += d.get(k, [])
    try: out += list(json.loads((pathlib.Path.home() / ".claude" / "settings.json").read_text()).get("modelSettings", {}))
    except Exception: pass
    return sorted({bare_id(ALIAS.sub(r"\1-", m)) for m in runnable_models(out) if MODEL_ID.match(m) and not provkey.closed(m)})   # no key, never offered (#482)
EFFORTS = ("low", "medium", "high", "max")
# the hours a model may run: "HH-HH" local, start inclusive, end exclusive, wrapping midnight (22-07), or clauses with
# days and a UTC suffix ("mon-fri 00-01,04-06,10-24 UTC; sat-sun 00-24 UTC"). Absent = always. The grammar is the
# resolver's (modelreg.in_window); HOURS is the one-range form a Save body has always been allowed to post.
HOURS = re.compile(r"^([01]\d|2[0-3])-([01]\d|2[0-3])$")
def model_hours(d, chain, mid):
    """The window for one model, or "" when it may always run."""
    h = d.get("hours") if isinstance(d.get("hours"), dict) else {}
    hk = h.get(chain) if isinstance(h.get(chain), dict) else {}
    v = hk.get(mid)
    return v if isinstance(v, str) and modelreg.window_valid(v) and mid in d.get(chain, []) else ""
def in_hours(d, chain, mid, when=None):
    """True when the model may run at `when` (a datetime; default: now) inside its own window."""
    w = model_hours(d, chain, mid)
    if not w: return True
    return modelreg.in_window(w, when or time.time())
def model_off_ids(d):
    """The models switched off at model level (SPEC 889 #920): normalized ids from models.json's top-level `model_off`.
    The one registry reads the same key (modelreg.chain), so the hub and every consumer agree on the spelling."""
    mo = d.get("model_off") if isinstance(d.get("model_off"), dict) else {}
    return {modelreg.normalize(m).id for m, v in mo.items() if v is True and isinstance(m, str)}
def model_off(d, chain, mid):
    """True when the model is switched off at model level: off in every chain it sits in (SPEC 889 #920)."""
    return modelreg.normalize(mid).id in model_off_ids(d) and mid in d.get(chain, [])
def chain_off(d, chain, mid):
    """True when a chain row already in the file carries the old chain-level switch (`off`). The hub writes no new one
    (SPEC 889 #920); a file that holds one keeps honoring it, and the panel shows it as "off (chain)"."""
    o = d.get("off") if isinstance(d.get("off"), dict) else {}
    ok = o.get(chain) if isinstance(o.get(chain), dict) else {}
    return bool(ok.get(mid)) and mid in d.get(chain, [])
def off_of(d):
    """Per-chain {model-id: True} from models.json's optional top-level `off`; only entries whose model is in that chain."""
    return {k: {m: True for m in d.get(k, []) if chain_off(d, k, m)} for k in CHAINS}
def may_run(d, chain, mid, when=None):
    """True when the model may run at `when`: a model switched off is skipped exactly like one outside its window.

    One predicate for every reason, because the consumers do not care which one it was -- the panel's row shows which."""
    return not (model_off(d, chain, mid) or chain_off(d, chain, mid)) and in_hours(d, chain, mid, when) and not provkey.closed(mid)   # a provider key that is absent closes it too (#482)
def hours_of(d):
    """Per-chain {model-id: "HH-HH"} from models.json's optional top-level `hours`; only entries whose model is in that chain."""
    h = d.get("hours") if isinstance(d.get("hours"), dict) else {}
    return {k: {m: v for m, v in (h.get(k) or {}).items() if model_hours(d, k, m) == v} for k in CHAINS}
def efforts_of(d):
    """Per-chain {model-id: effort} from models.json's optional top-level `efforts`; every chain present, only entries whose model is in that chain."""
    e = d.get("efforts") if isinstance(d.get("efforts"), dict) else {}
    return {k: {m: v for m, v in (e.get(k) or {}).items() if isinstance(e.get(k), dict) and m in d.get(k, []) and v in EFFORTS and v != "medium"} for k in CHAINS}
def router_get(doc=None):
    """The router's own chain (#95, SPEC 750): the models the router agent runs on, in rank order, read from
    models.json's `chains.router` like the three role chains. The panel shows the entries the chain can run (usable)."""
    d = models_doc() if doc is None else v1_view(doc)
    return [bare_id(m) for m in d.get(ROUTER_CHAIN, [])]

def subscription_states():
    """Each subscription's quota now, read once per page (#545): its marker, its quota cache, or neither."""
    reg = modelreg.default_registry()
    return {k: {"quota": modelreg.quota(k, shared=SHARED), "label": info.get("label", k)} for k, info in reg.subscriptions.items()}
def model_status(raw, states):
    """The subscription that bills one model, and its quota: spent when a marker or a window closes it (#545)."""
    e = modelreg.model(raw)
    if e is None: return {"sub": None, "quota": None}
    q = states[e.subscription]["quota"]
    return {"sub": {"key": e.subscription, "label": states[e.subscription]["label"]},
            "quota": {"spent": bool(q["limited"]), "reason": q["reason"], "until": q["until"],
                      "until_text": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(q["until"])) if q["until"] else None}}
def model_rows(d, states):
    """The models table (SPEC 889 #920): one row per (model, route) across the four chains, with its subscription and
    quota and its state. A model switched off at model level reads "off (model)"; a chain row the file still holds off
    reads "off (chain)"; a spent subscription reads "at limit"; otherwise "on". The table holds no hours (#913).

    The route is the one the chain row draws (model_box, ADR-0137): `claude-sonnet-5-5` in coding_models runs on the
    gateway while `claude:claude-sonnet-5-5` in review_models runs through the claude CLI, so those are two rows.
    Each row carries the label it draws (#1264): a model on one route shows the plain registry label, but two rows of
    one model would read the same, so every row of that model shows the label beside its route
    (`DeepSeek V4.1 Flash · gateway`, `… · claude CLI`); a label that still repeats takes the row's id."""
    entry = {}   # the first chain entry that makes each (bare id, route) row, kept for its box
    for k in CHAINS:
        for m in d.get(k, []): entry.setdefault((bare_id(m), model_box(m, k)["route"]), (k, m))
    chain_off_ids = {bare_id(m) for k in CHAINS for m in d.get(k, []) if chain_off(d, k, m)}
    off = model_off_ids(d)
    rows, groups = [], {}
    for (b, route), (chain, raw) in entry.items():
        st = model_status(b, states)
        is_off = modelreg.normalize(b).id in off
        spent = bool(st["quota"] and st["quota"]["spent"])
        state = "off (model)" if is_off else "off (chain)" if b in chain_off_ids else "at limit" if spent else "on"
        box = model_box(raw, chain)
        row = {"id": b, "off": is_off, "state": state, **st, "label": box["llm"] or b, "route": box["route"]}
        rows.append(row)
        groups.setdefault(modelreg.normalize(raw).id, []).append(row)
    for group in groups.values():
        if len(group) > 1:
            for r in group: r["label"] = f"{r['label']} · {r['route']}" if r["route"] else r["label"]
            labels = [r["label"] for r in group]
            for r in group:
                if labels.count(r["label"]) > 1: r["label"] = f"{r['label']} ({r['id']})"
    return rows
def not_switched_off(suggestions, off):
    """The add list never offers a model switched off at model level (SPEC 889 #920): it keeps its place in the chains."""
    return [s for s in suggestions if modelreg.normalize(s["id"]).id not in off]
def repo_chain_diff():
    """The chains where the repository copy of models.json differs from the one this hub reads: [{chain, repo, live}] (#1261).
    The page prints it and computes nothing (ADR-0137). Empty when the repository copy is missing or is the file itself."""
    repo = pathlib.Path(CHECKOUT) / "contrib" / "squad-goal" / "scripts" / "models.json"
    try:
        if not repo.is_file() or repo.resolve() == MODELS.resolve(): return []
    except OSError: return []
    return models_chains.diff_files(repo, MODELS)
def models_get():
    """The four chains as the panel reads them: bare ids, so one model is one row in every chain and in the box.

    The file keeps the spelling each role needs (chain_id); the panel never sees it, so a reader cannot pick the
    wrong one of two spellings of the same model."""
    d = models_doc()
    known, rknown, states = known_models(d), router_known(), subscription_states()
    off = model_off_ids(d)
    rows = model_rows(d, states)
    return {**{k: [bare_id(m) for m in d.get(k, [])] for k in CHAINS},
            "efforts": {k: {bare_id(m): v for m, v in ek.items()} for k, ek in efforts_of(d).items()},
            "hours": {k: {bare_id(m): v for m, v in hk.items()} for k, hk in hours_of(d).items()},
            "off": {k: {bare_id(m): True for m in ok} for k, ok in off_of(d).items()},
            "model_off": {r["id"]: True for r in rows if r["off"]},
            "models": rows,
            "outside": {k: {bare_id(m): model_hours(d, k, m) for m in d.get(k, []) if model_hours(d, k, m) and not in_hours(d, k, m)} for k in CHAINS},
            "known": known, "last_coding": last_coding_model(),
            "router_known": rknown, "version": models_version(), "repo_diff": repo_chain_diff(),
            # The router's add box offers what the gateway serves, never the other CLIs' ids (#225): the router row is a
            # chain like the other three, so its own catalogue answers for it (#687).
            "suggest": {**{k: not_switched_off(model_suggestions(usable(known, k.removesuffix("_models"))), off) for k in CHAINS if k != ROUTER_CHAIN},
                        ROUTER_CHAIN: not_switched_off(model_suggestions(usable(rknown, "router")), off)},
            "labels": model_labels(set(known) | set(rknown) | {bare_id(m) for k in CHAINS for m in d.get(k, [])}),
            "boxes": {**{k: {bare_id(m): {**model_box(bare_id(m), k), **model_status(bare_id(m), states)} for m in d.get(k, [])} for k in CHAINS},
                      "known": {m: model_box(m) for m in set(known) | set(rknown) | ({last_coding_model()} - {None, ""})}},
            "chain": chainhealth.chain_state(d, "review_models", SHARED),
            # #1278: the Save dialog names the file it writes, and the theme's chip reads the table's own count.
            "file": str(MODELS), "counts": {"on": sum(1 for r in rows if r["state"] == "on"),
                                            "at_limit": sum(1 for r in rows if r["state"] == "at limit")}}
def models_version():
    """What a Save must have read (#688): a hash of models.json, parsed, so a rewrite that keeps the same data keeps
    the version. An unreadable models.json hashes as its raw bytes. The four chains all live in that one file (SPEC
    750), so no run graph enters the hash: a graph edit no longer makes a panel tab stale."""
    try: m = json.loads(MODELS.read_text())
    except (OSError, ValueError):
        try: m = MODELS.read_bytes().hex()
        except OSError: m = None
    return hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()

def models_record(changes):
    """One line per model-level flip in the state directory's models switch ledger (SPEC 889 #920): the model, its new
    state, who and when. Whole lines, appended; a Save that flips nothing writes nothing."""
    if not changes: return
    who, at = os.environ.get("SG_OWNER") or "owner", time.strftime("%Y-%m-%d %H:%M:%S")
    STATE.mkdir(parents=True, exist_ok=True)   # a state directory the hub has not written yet: the first flip makes it
    with open(STATE / MODELS_LEDGER, "a", encoding="utf-8") as f:
        f.write("".join(json.dumps({"at": at, "model": m, "state": st, "who": who}, ensure_ascii=False) + "\n" for m, st in changes))

_SAVE = threading.Lock()   # one Save at a time, so the version check and the write see the same files

def models_set(new):
    """Replace the four chains in models.json; every other key is kept. Atomic write, so a reader never sees half a file.

    A body whose version is not the current one comes from a page that did not read the last Save (#688): it is
    refused whole, before any write, so an old tab never takes back what a newer Save wrote."""
    with _SAVE:
        if not isinstance(new, dict) or new.get("version") != models_version(): return {"err": "stale: reload the page"}
        return _models_set(new)

def _new_raw(k, mid, reg):
    """The spelling a new row takes in the file: the one the route its chain prefers serves, through chain_id's rule.

    The posting id is the owner's choice and stays when that route serves it, context suffix included. When it does
    not, the route's own spelling answers: the gateway names `claude-gpt-5.6-terra` for `gpt-5.6-terra`, while the
    bare spelling there is the codex CLI's. A spelling without an effort suffix wins, so a gemini row keeps the
    effort the panel chose."""
    e = modelreg.model(mid, reg)
    route = modelreg._default_route(k.removesuffix("_models"), e)
    served = ((e.routes.get(route) or {}).get("served") or []) if (e is not None and route) else []
    if not served or mid in served or mid.removeprefix(RUNNER) in served: return chain_id(k, mid)
    ref = modelreg.normalize(mid, reg)
    same = next((x for x in served if modelreg.normalize(x, reg).ctx == ref.ctx
                 and modelreg.normalize(x, reg).effort == ref.effort), None)
    plain = next((x for x in served if not modelreg.normalize(x, reg).effort), None)
    return chain_id(k, same or plain or mid)

_ABSENT = object()   # a setting section the Save body leaves out: the file's own value stays, never cleared

def _entry(k, mid, prev, effort, window):
    """One v2 chain entry for a panel row, in the shape `modelreg.migrate` writes: `id`, `raw` when the spelling
    differs, an explicit route, and the row's effort and window. The switch is per model now (SPEC 889 #920): the hub
    writes no chain-level `on`, and an `on: false` a file already holds stays as the file has it.

    `raw` is not the panel's id (ADR-0147): review's Anthropic row carries `claude:`, and a gateway row carries the
    `claude-` alias of its bare model. A row already in the file keeps the spelling and the route it holds; a new row
    takes the spelling its chain needs. A `-low`/`-medium` suffix fixes an effort, so a changed effort drops it.

    A setting the body leaves out (`_ABSENT`) keeps the value the file holds. A value the body names -- the empty
    string or the default effort included -- replaces it, so an omitted section never clears a row."""
    ref = modelreg.normalize(mid)
    want = chain_id(k, mid)
    raw = _new_raw(k, mid, None)
    route = modelreg._default_route(k.removesuffix("_models"), modelreg.model(mid))
    if effort is _ABSENT: effort = prev.effort if prev is not None else None
    if window is _ABSENT: window = prev.hrs if prev is not None else None
    if prev is not None and prev.raw == want:
        held = modelreg.normalize(prev.raw)
        if not held.effort or held.effort == (effort or "medium"):
            raw = prev.raw
        elif mid.endswith("-" + held.effort):   # the owner set the default effort: drop the suffix that fixed another
            raw = _new_raw(k, mid[: -len(held.effort) - 1], None)
        route = prev.route or route
    out = {"id": ref.id}
    if raw != ref.id: out["raw"] = raw
    if route: out["route"] = route
    if prev is not None:   # what the panel does not draw stays: review's timeout and tool rule, an explicit context
        if prev.ctx: out["ctx"] = prev.ctx
        if prev.timeout_s is not None: out["timeout_s"] = prev.timeout_s
        if prev.no_tools: out["no_tools"] = True
    if effort and effort != "medium": out["effort"] = effort
    if isinstance(window, str) and modelreg.window_valid(window): out["hrs"] = window
    if prev is not None and not prev.on: out["on"] = False   # honored, never written: a row the file already holds off
    return out

def _setting(sec, k, raw):
    """One row's setting from a posted section: `_ABSENT` when the body left the chain (or the whole section) out, so
    the file's value stays; the row's own value otherwise, `None` when the section names the chain but not this row.

    A row is looked up by the spelling the file stores, by the panel's bare id, or by the model id the resolver folds
    both onto, so a caller may key a section by either spelling of one model."""
    if sec is _ABSENT or not isinstance(sec, dict) or k not in sec: return _ABSENT
    rows, want = sec.get(k) or {}, modelreg.normalize(raw).id
    for name, value in rows.items():
        if name in (raw, bare_id(raw)) or modelreg.normalize(name).id == want: return value
    return None

def _v2_chain(k, posted, doc, eff, hrs):
    """One v2 chain for the Save, from the panel's list in rank order (or the file's own list when the body omits it).

    An entry the panel's view hid -- `usable()` drops a model its chain cannot run (ADR-0147) -- is not in the posted
    list; it stays where it was, else a Save of another chain would delete the owner's entry. An entry the owner
    removed with ✕ was shown, so it goes.

    One model is one row. Two posted spellings can fold onto one stored spelling (`gpt-5.6-terra` and
    `claude-gpt-5.6-terra` both store as the gateway alias), so the fold drops the later one here: a file with two
    identical entries cannot be read back or saved again."""
    canon = k.removesuffix("_models")
    old = list(modelreg.chain(doc, canon).entries)
    if posted is None:   # the body names a section but no list: apply the section, keep the file's rank and rows
        return [_entry(k, e.raw, e, _setting(eff, k, e.raw), _setting(hrs, k, e.raw)) for e in old]
    by_id = {modelreg.normalize(e.raw).id: e for e in old}
    out, seen = [], set()
    for m in posted:
        ref = modelreg.normalize(m)
        seen.add(ref.id)   # two spellings of one model may both be posted: the panel drew two rows, so both stay
        out.append(_entry(k, m, by_id.get(ref.id), _setting(eff, k, m), _setting(hrs, k, m)))
    shown = {modelreg.normalize(m).id for m in v1_view(doc).get(k, [])}
    for i, e in enumerate(old):
        ref = modelreg.normalize(e.raw)
        if ref.id in shown or ref.id in seen: continue
        out.insert(min(i, len(out)), _entry(k, e.raw, e, _ABSENT, _ABSENT))
        seen.add(ref.id)
    written, unique = set(), []
    for e in out:   # one stored spelling, one row: the fold above can land two posted ids on the same `raw`
        spelling = e.get("raw") or e["id"]
        if spelling in written: continue
        written.add(spelling); unique.append(e)
    return unique

def _models_set(new):
    # The panel posts back whatever models_get() handed it, so every read-only field it carries comes back here: `known`
    # (the add box's list) and `last_coding` (the header's line). Dropping only `known` made the other one read as a chain
    # the panel invented -- "unknown chain" on every Save (2026-10-11).
    new = {k: v for k, v in new.items() if k not in READONLY} if isinstance(new, dict) else new
    if not isinstance(new, dict) or set(new) - set(CHAINS) - {"efforts", "hours", "off", "model_off"}: return {"err": "unknown chain"}
    d = json.loads(MODELS.read_text())
    eff, hrs = new.pop("efforts", _ABSENT), new.pop("hours", _ABSENT)
    new.pop("off", None)   # the chain-level switch is retired (SPEC 889 #920): an old page's `off` is read past, never written
    moff = new.pop("model_off", _ABSENT)   # the model-level switch (SPEC 889 #920): {model id: true|false}; absent keeps the file
    if moff is not _ABSENT:
        if not isinstance(moff, dict): return {"err": "model_off: must name models"}
        for m, v in moff.items():
            if not (isinstance(m, str) and MODEL_ID.match(m) and isinstance(v, bool)): return {"err": f"model_off: {m} must be true or false"}
    posted = {}
    for k, v in new.items():
        # An empty chain is a legal state: the reader tolerates it, and a file that carries one (the router's, until the
        # owner ranks it) must not refuse every Save. The panel's row controls keep at least one entry (SPEC 750).
        if not isinstance(v, list) or len(v) > 8 or not all(isinstance(m, str) and MODEL_ID.match(m) for m in v):
            return {"err": f"{k}: need at most 8 distinct model ids"}
        stored = [chain_id(k, m) for m in v]      # the panel works in bare ids; the file gets the spelling the role needs
        if len(set(stored)) != len(stored): return {"err": f"{k}: need at most 8 distinct model ids"}
        posted[k] = [bare_id(m) for m in v]
    # ✕ takes a row out of its chain, and the panel still posts that row's effort, hrs and switch back (#685): a setting
    # of a model no longer in its chain goes with the row, never refuses the Save. A bad value of a kept row still refuses,
    # and the sentence names the row and the rule, so a caller learns what to send.
    for name, sec, rule, ok in (("efforts", eff, "must name one of low|medium|high|max", lambda v: v in EFFORTS),
                                ("hours", hrs, "must be an hours window: HH-HH (00-23), or days, ranges and UTC as in modelreg.py",
                                 lambda v: isinstance(v, str) and (HOURS.match(v) or modelreg.window_valid(v)))):
        if sec is _ABSENT: continue
        if not isinstance(sec, dict) or set(sec) - set(CHAINS): return {"err": f"{name}: unknown chain"}
        for k, rows in sec.items():
            if not isinstance(rows, dict): return {"err": f"{name}.{k}: must name models"}
            for m, v in rows.items():
                if not (isinstance(m, str) and ok(v)): return {"err": f"{name}.{k}: {m} {rule}"}
    # The file is v2, and every chain writes there: the router row is a chain row like the other three (SPEC 750). The
    # graph and its record are never touched by a Save; a run reads the router chain from this file.
    changes = []
    if moff is not _ABSENT:
        was = model_off_ids(d)
        mo = dict(d.get("model_off") or {}) if isinstance(d.get("model_off"), dict) else {}
        for m, v in moff.items():
            key = modelreg.normalize(m).id
            if v: mo[key] = True
            else: mo.pop(key, None)
            if (key in was) != bool(v): changes.append((bare_id(m), "off" if v else "on"))   # one line per flip, never per read
        if mo: d["model_off"] = mo
        else: d.pop("model_off", None)
    chains = {k.removesuffix("_models"): _v2_chain(k, posted.get(k), d, eff, hrs) for k in CHAINS}
    # A chain the panel does not draw (editorial) is the file's own: a Save keeps it as it stands.
    kept = {k: v for k, v in (d.get("chains") or {}).items() if isinstance(d.get("chains"), dict) and k not in chains}
    modelreg.write_chains(MODELS, {**chains, **kept}, d)
    models_record(changes)
    return models_get()

# The phone's content-security line: the page loads no image from another origin.
MOBILE_CSP = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self'"

GESTURE_JS = (pathlib.Path(__file__).resolve().parent / "hub-mobile-gesture.js").read_text()
# The phone's lead-time drawing (#1333): the desktop payload's numbers, laid out for the phone. Rules stay out of the
# view (ADR-0137): the module computes no number the hub serves.
CHART_JS = (pathlib.Path(__file__).resolve().parent / "hub-mobile-chart.js").read_text()
# The loop rules of the phone page (SPEC 1596, rule 8; #1599): the aggregate, one row per project and the one primary of a
# loop page. A pure rule module, so the view binds and delegates and holds no rule of its own (ADR-0137).
LOOP_JS = (pathlib.Path(__file__).resolve().parent / "hub-mobile-loop.js").read_text()
# The phone's card link (#1459): `#issue=N` names one card, and one module names the result the board answers. The page
# binds the hash and opens the card, and holds no rule of its own (ADR-0137).
LINK_JS = (pathlib.Path(__file__).resolve().parent / "hub-mobile-link.js").read_text()
RUN_JS = (pathlib.Path(__file__).resolve().parent / "hub-mobile-run.js").read_text()
# Runs › Usage on the phone (SPEC 1596, #1604): the token bars and the model pie, shaped from the desktop's /api/stats.
USAGE_JS = (pathlib.Path(__file__).resolve().parent / "hub-mobile-usage.js").read_text()
# Specs on the phone (SPEC 1596, #1604): each open spec's tickets by column, from the board the page already reads.
SPECS_JS = (pathlib.Path(__file__).resolve().parent / "hub-mobile-specs.js").read_text()
# Truthful filters, the counted remainder and Search on the phone (SPEC 1596, rules 4, 5 and 6; #1600): the remainder line
# and the title/number/label match. A pure rule module, so the view binds and delegates and holds no rule of its own.
SEARCH_JS = (pathlib.Path(__file__).resolve().parent / "hub-mobile-search.js").read_text()
# The repository and its board on GitHub (SPEC 2498, #2501): a card, a spec row and the board link are plain anchors to
# these addresses, and the page names no repository of its own.
HUB_BOARD_URL = f"https://github.com/users/{OWNER}/projects/{PROJECT_NO}"
# The card rules of the desktop board (#917): spec color, one block of board-lanes.js, so the mobile page holds no
# rule of its own and never the board's column names (ADR-0137).
CARD_RULES_JS = re.search(r"// <card-rules>.*?// </card-rules>", (pathlib.Path(__file__).resolve().parent / "board-lanes.js").read_text(), re.S).group(0)
# The Monitor card (#983) on the phone's first tab: its markup, style and script are cut from the desktop page, never
# copied, so one change draws both. The cell helpers and the ceiling ride along; the repository and the ceiling are this hub's.
INST_CARD = re.search(r"<details id=inst\b.*?</details>", PAGE, re.S).group(0)
INST_CSS = re.search(r"^/\* INST-CSS\{ \*/$.*?^/\* \}INST-CSS \*/$", PAGE, re.M | re.S).group(0)
INST_JS = "\n".join([re.search(r"^const LOAD_ERR=.*$", PAGE, re.M).group(0),
                     re.search(r"^const fpct=.*$", PAGE, re.M).group(0),
                     *re.findall(r"^function ld(?:Num|Round|Cell|Cells)\(.*$", PAGE, re.M),
                     re.search(r"^// INST\{.*?^// \}INST$", PAGE, re.M | re.S).group(0)]).replace("__LOADERR__", str(LOAD_ERR_PCT)).replace("__REPO__", REPO)

BOOT = time.time_ns()   # the page's version is this process, not this file: a restart for a changed hubmux.py, run-sheet.js or address.sh leaves hub.py's mtime alone, and the open page must still reload
MOBILE_PAGE = ((pathlib.Path(__file__).resolve().parent / "hub-mobile.html").read_text().replace("<!--INST_CARD-->", INST_CARD).replace("/*INST_CSS*/", INST_CSS).replace("/*INST_JS*/", INST_JS).replace("/*SWITCHER_JS*/", SWITCHER_JS).replace("/*GESTURE*/", GESTURE_JS).replace("/*CHART*/", CHART_JS).replace("/*LINK*/", LINK_JS).replace("/*RUN*/", RUN_JS).replace("/*USAGE*/", USAGE_JS).replace("/*SPECS*/", SPECS_JS).replace("/*SEARCH*/", SEARCH_JS).replace('/*HUB_REPO*/"owner/repo"', json.dumps(REPO)).replace('/*HUB_BOARD_URL*/"https://github.com"', json.dumps(HUB_BOARD_URL)).replace("/*WEB_TOKENS*/", WEB_TOKENS).replace("/*CARD_RULES*/", CARD_RULES_JS).replace("/*LOOP_RULES*/", LOOP_JS).replace("/*PROFILER_JS*/", PROFILER_JS).replace("/*PROFILER_CSS*/", PROFILER_CSS).replace("/*RUN_STATE_BASE_CSS*/", RUN_STATE_BASE_CSS).replace("/*RUN_STATE_CSS*/", "".join(run_state_css(r) for r in RUN_STATES))).replace("__BUILD__", str(BOOT))
MOBILE_SHELL = ["/mobile/", "/mobile/manifest.webmanifest", "/mobile/icon.svg"]
SW_JS = (pathlib.Path(__file__).resolve().parent / "hub-mobile-sw.js").read_text().replace("/*SHELL*/[]", json.dumps(MOBILE_SHELL))
MANIFEST_JSON = json.dumps({"id": "/mobile/", "name": f"Squad hub {REPO}", "short_name": "Hub", "start_url": "/mobile/", "scope": "/mobile/", "display": "standalone", "background_color": BG_COLOR, "theme_color": BG_COLOR, "icons": [{"src": "/mobile/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]}).encode()
MOBILE_ICON = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192"><rect width="192" height="192" rx="36" fill="var(--bg)"/><circle cx="96" cy="96" r="52" fill="var(--chart-ok)"/></svg>'
MOBILE_ASSETS = {"/mobile/manifest.webmanifest": (MANIFEST_JSON, "application/manifest+json"), "/mobile/icon.svg": (MOBILE_ICON, "image/svg+xml")}

def mobile_sw():
    # The cache is named for this build, so a new build installs a new cache and drops the old one (#1455).
    return SW_JS.replace("__BUILD__", str(BOOT)).encode()

def test_log_page(d, lg):
    import html as _h
    ref = '<meta http-equiv="refresh" content="3">' if d.get("state") == "running" else ""
    return (f'<!doctype html><meta charset=utf-8><style>{WEB_TOKENS}</style>{ref}<body style="background:var(--well);color:var(--ink-body);font:var(--fs-body) var(--font-mono);margin:12px"><div style="color:var(--acc)">test #{d.get("issue")} · {d.get("state")}{" (exit "+str(d.get("rc"))+")" if "rc" in d else ""} · {_h.escape(d.get("dir",""))}</div><div style="color:var(--ink-muted)">$ {_h.escape(d.get("cmd",""))}</div><pre style="white-space:pre-wrap">{_h.escape(lg)}</pre><script>scrollTo(0,document.body.scrollHeight)</script>').encode()

def mobile_refusal(reason):
    return ('<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Hub</title>'
            f'<style>{WEB_TOKENS}</style><body style="font:var(--fs-body) system-ui;margin:24px"><h1>The hub refused this login</h1><p>{html.escape(reason)}</p>').encode()

HUB_STREAM_JS = (pathlib.Path(__file__).resolve().parent / "hub-stream.js").read_text()

def stream_tag():
    """The page's push stream: its path, then the script; the mux gives the path its profile prefix."""
    return f'<script>window.SQUAD_HUB_STREAM="{hubstream.PATH}";\n{HUB_STREAM_JS}</script>'

def mobile_html():
    """The phone page as the hub serves it: its push stream rides in the page only while the stream is on."""
    stream = f'window.SQUAD_HUB_STREAM="{hubstream.PATH}";\n{HUB_STREAM_JS}' if hubstream.enabled() else ""
    return MOBILE_PAGE.replace("/*STREAM*/", stream).encode()

RO_TAILNET = ("Read-only over the tailnet: this view may not write, and a write needs the owner's grant "
              "(ADR-0152, ADR-0162). Open the hub on the machine to change a value.")

def served_page(login="", allowed=True):
    """The page as the hub serves it, every placeholder substituted. The model-id shape replaces its quoted placeholder:
    json.dumps brings its own quotes, and a second pair is a syntax error that empties the page. The read-only flag follows
    the hub's own write rule: a request the server refuses to write gets the flag and its reason. An allowed login, loopback
    or tailnet, writes the settings routes, so its page stays live (#1278)."""
    who = f"signed in as {html.escape(login)}" if login else ""
    return (PAGE.replace("__WHO__", who).replace("__REPO__", REPO).replace("__LOADMS__", str(int(LOAD_SAMPLE_SECS * 1000)))
            .replace("__LOADERR__", str(LOAD_ERR_PCT)).replace("__BUILD__", str(BOOT))
            .replace("__READONLY__", "false" if allowed else "true").replace("__ROWHY__", "" if allowed else RO_TAILNET)
            + (stream_tag() if hubstream.enabled() else ""))

def own_host(h):
    """The one Host rule for GET and POST: the hub's own loopback name and port, so a page that rebinds its name to 127.0.0.1 is refused. `h` is any handler, the mux's too, which runs these routes."""
    return (h.headers.get("Host") or "") in (f"127.0.0.1:{h.server.server_port}", f"localhost:{h.server.server_port}")

PLUGIN_ID = "agent-squad@agent-squad"
def plugin_squad():
    """The agent-squad `squad` package a host profile's hub reads its settings through: SG_SQUAD when it names one, else the
    install path the plugin registry records, as env.sh and launch.py already resolve it (#871). Empty when neither answers,
    so a hub with no package refuses every tailnet request as before. Rule layer: one registry read, no hub state."""
    if os.environ.get("SG_SQUAD"): return os.environ["SG_SQUAD"]
    try:
        registry = pathlib.Path.home() / ".claude" / "plugins" / "installed_plugins.json"
        return json.loads(registry.read_text())["plugins"][PLUGIN_ID][0]["installPath"]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return ""

def tailnet_roots():
    """Where the tailnet rule reads its seam and settings reader: the tree this file sits in first, so a stale SG_SQUAD never
    answers for the hub's own code; then the profile's roots; then the agent-squad package the plugin install holds, so a host
    profile whose tree and checkout both hold no squad package still reads its own project file (#871)."""
    roots = [str(pathlib.Path(__file__).resolve().parents[3]),
             *squad_roots(os.environ.get("SG_SQUAD"), os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__), plugin_squad()]
    return [r for i, r in enumerate(roots) if r and r not in roots[:i]]

_SEAM = {}
def tailnet_seam():
    """The tailnet access seam (squad/tailnet_access.py), loaded by path from the first root that holds it; None when no root does."""
    if "seam" not in _SEAM:
        _SEAM["seam"] = None
        for root in tailnet_roots():
            path = pathlib.Path(root) / "squad" / "tailnet_access.py"
            if path.is_file():
                spec = importlib.util.spec_from_file_location("tailnet_access", path)
                seam = importlib.util.module_from_spec(spec); spec.loader.exec_module(seam)
                _SEAM["seam"] = seam
                break
        if _SEAM["seam"] is None:
            log_once("hub: no squad/tailnet_access.py, every tailnet request is refused")
    return _SEAM["seam"]

_PROJECT = {}
def _project_holds_tailnet(ps, path):
    """True when the tracked project file itself holds a `tailnet` block, so it wins the named home over the personal `.env`."""
    strip = getattr(ps, "without_tailnet", None)
    try:
        text = pathlib.Path(path).read_text()
    except OSError:
        return False
    return strip is not None and strip(text) != text

def _file_sig(path):
    """The stat signature of `path`, or None when it is absent: a moved mtime or size means the file changed, so a cached
    read is stale. The request path stats two small files and reads neither while the signature holds."""
    try:
        st = pathlib.Path(path).stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None

def tailnet_source(checkout=None):
    """The `tailnet` block and the file it is read from, from one cached load of `squad/project_settings.py`. The project
    route and the loop seam share the cache, so both read the same block within a request. Empty, with no named file, when
    the file or the squad package does not load or no file holds the block: the seam then refuses every tailnet request.
    The tracked project file wins the name; a block that lives only in `.env` names `.env`. A changed project file or
    `.env` signature re-reads; an unchanged one answers from the cache, keyed on both files and the checkout. `checkout`
    names another project's tree, so the per-project loop list states each project's own grant (#1599)."""
    checkout = checkout or CHECKOUT or "."
    proj = pathlib.Path(checkout) / "docs" / "squad" / "defaults.yaml"
    env = pathlib.Path(checkout) / ".env"
    sig = (str(pathlib.Path(checkout).resolve()), _file_sig(proj), _file_sig(env))
    sources = _PROJECT.setdefault("sources", {})
    hit = sources.get(sig)
    if hit is not None: return dict(hit[0]), hit[1]
    try:
        if "ps" not in _PROJECT:
            (_PROJECT["ps"],) = load_squad(tailnet_roots(), ("project_settings",))
        data, info = _PROJECT["ps"].load(checkout)
    except Exception as e:
        log_once(f"hub: tailnet settings unavailable, every tailnet request is refused: {type(e).__name__}: {' '.join(str(e).split())}")
        sources[sig] = ({}, "")
        return {}, ""
    block = (data or {}).get("tailnet") or {}
    path = (info or {}).get("path")
    if path and _project_holds_tailnet(_PROJECT["ps"], path): home = path          # the tracked file holds the block
    elif block and env.is_file(): home = str(env)                                 # only `.env` holds it
    else: home = ""                                                               # no file holds a block: name none
    sources[sig] = (block, home)
    return dict(block), home

def tailnet_block(checkout=None):
    """The `tailnet` block of the project file under the profile's checkout. Empty when the file or the squad package does
    not load, so the seam refuses every tailnet request."""
    return tailnet_source(checkout)[0]

