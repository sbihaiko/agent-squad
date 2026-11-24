# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
# ---------------------------------------------------------------- the trip of one issue (#1769)
# The five stages of a delivery, folded by the rule layer from the run records and the gate history: the wait for a
# dispatch, coding, review, gates and landing. It reads files only, so it shows with the CPU sampling switch off. A
# record's stages are cached by its own signature, so a poll parses only a record that changed; the window is decided by
# the event times the fold reads, never by a write time.
TRIP_SPANS = {}       # record path -> (mtime, size, the rule layer's stages): a poll parses only a changed record
TRIP_SPANS_MAX = 4096 # a bound, never a ledger: a cleared entry is read again on the next poll
TRIP_CALLS = {}       # record path -> (mtime, size, the record's model calls): the trip's second read, cached the same way
_TRIP_EVENTS = frozenset(("run_start", "resume", "call_start", "spawn_accepted", "outcome", "review_round", "gate_start",
                          "gate_result", "land", "verdict"))   # the events the trip's stages are read from
_EVENT_NAME = re.compile(r'"event"\s*:\s*"([a-z_]+)"')

def trip_events(path):
    """One run record's events, read tolerantly: a line whose event is not one the trip reads, or that is not JSON, is
    skipped, so a half-written record never ends the fold and a long progress log costs no parse. A run's identity stays
    `scan`'s; this is the trip's own read of its events."""
    try: text = path.read_text(errors="replace")
    except OSError: return []
    out = []
    for line in text.splitlines():
        m = _EVENT_NAME.search(line)
        if not m or m.group(1) not in _TRIP_EVENTS: continue
        try: e = json.loads(line)
        except ValueError: continue
        if isinstance(e, dict) and e.get("event") == m.group(1): out.append(e)
    return out

def card_waits(store, numbers):
    """issue number -> `{"todo", "closed", "landing", "rework"}` from the board's transitions (#1894, #2292): the epoch of
    the card's first entry into Todo, the card's Batching stint that went on to Closed in seconds (`landing`) with its
    start (`landing_from`) and that Closed move (`closed`), and the stints that went back to work (`rework`). A card with no delivered stint answers its
    first Closed move. Only the issues a run of the window names are folded: the store holds every card of the board. A
    card the record never saw in a column answers None for it."""
    out = {}
    for n in numbers:
        rec = store.get(n)
        if rec is None: continue
        runs = _hist.intervals(rec.get("events") or [])
        todo = next((_epoch(r["from"]) for r in runs if r["column"] == LEAD_START), None)
        closed = next((_epoch(r["from"]) for r in runs if r["column"] == COLUMN["issue_closed"]), None)
        stints = hub_profiler.landing_stints([{"column": r["column"], "start": _epoch(r["from"]),
                                               "end": _epoch(r["to"]) if r.get("to") else None} for r in runs])
        if stints["closed"] is not None: closed = stints["closed"]
        if todo is not None or closed is not None or stints["rework"]:
            out[n] = {"todo": todo, "closed": closed, "landing": stints["delivered"],
                      "landing_from": stints["delivered_from"], "rework": stints["rework"]}
    return out

def card_column_runs(store, numbers, since, now):
    """issue number -> one `{"column", "start", "end"}` per column the card stood in over the window, from the board's
    transitions record (#2227). A run opens at the card's move into the column and closes at the next move; the last
    run is open to `now`. Runs outside the window are clipped to it and a run the clip empties is dropped. A card the
    record never saw answers no entry, so its samples land in no column."""
    out = {}
    for n in numbers:
        rec = store.get(n)
        if rec is None: continue
        runs = []
        for r in _hist.intervals(rec.get("events") or []):
            if not r.get("column"): continue
            a = _epoch(r["from"])
            b = _epoch(r["to"]) if r.get("to") else now
            if a is None or b is None: continue
            a, b = max(a, since), min(b, now)
            if b > a: runs.append({"column": r["column"], "start": a, "end": b})
        if runs: out[n] = runs
    return out

def card_wait(card, carried=None):
    """The card's Wait: the value the loop wrote to the field when there is one, else the loop's rule (board_wait.wait_of).
    A card a dispatch asked for answers through `board_wait.dispatch_state` (#2128): Doing with `run start` from the
    dispatch's settled write until the run's first call, and Doing with no reason after it. An unread input stays unknown
    and names what is missing; a live run keeps its name beside the Wait."""
    dispatch = board_wait.dispatch_of(card)
    answer = dispatch[1] if dispatch else board_wait.wait_of(card)
    wait, missing = (answer.name, answer.missing) if answer.name == "unknown" else (carried or answer.name, None)
    return {"wait": wait, "missing": missing, "run": card.get("run") if card.get("run_live") else None}

def trip_issues(path):
    """The issues one run names, from the request beside its record: the run's own request, never a second reader."""
    try: text = (pathlib.Path(path).parent / "request.txt").read_text(errors="replace")
    except OSError: return []
    return sorted((int(n) for n in request_issues(text)))

def trip_stages(path, card=None):
    """The rule layer's stages of one record, or None when it is no delivery, cached by the file's own signature and the
    card's moves and Batching stint: a transition alone changes the answer, so it is part of the key."""
    try: st = path.stat()
    except OSError: return None
    key = str(path)
    card = card or {}
    sig = (st.st_mtime, st.st_size, card.get("todo"), card.get("closed"), card.get("landing"))
    hit = TRIP_SPANS.get(key)
    if hit and hit[:5] == sig: return hit[5]
    spans = hub_profiler.trip_spans(trip_events(path), card)
    if len(TRIP_SPANS) >= TRIP_SPANS_MAX: TRIP_SPANS.clear()
    TRIP_SPANS[key] = (*sig, spans)
    return spans

# A gate-history row's fields, in the order `gate.sh` writes them; the one grammar lives in the rule layer (ADR-0137).
GATE_HISTORY_FIELDS = hub_profiler.GATE_HISTORY_FIELDS

def gate_row(line):
    """One gate-history line as named fields, or None when it is too short to hold a finish and a start. The reason is
    free text and may hold a tab, so on a whole row the field is rejoined; the fold itself lives in the rule layer."""
    return hub_profiler.gate_row(line)

def gate_attempt_links():
    """The gate-attempt journal's links (#1255), `(tip sha, finish) -> {"wave", "card"}`: the one record that names the
    wave or the card a gate-history row ran for. An unread journal links no row."""
    try: text = _flow.history_path(BOARD, REPO).read_text(errors="replace")
    except OSError: return {}
    recs = []
    for line in text.splitlines():
        if '"gate_attempt"' not in line: continue
        try: rec = json.loads(line)
        except ValueError: continue
        if isinstance(rec, dict): recs.append(rec)
    return hub_profiler.gate_links(recs)

def trip_gates(since, links=None):
    """The gate-history rows of the window, `start` and `finish` in seconds beside their kind, outcome, caller, slot
    wait, the instants it asked for its slots and held them (`slot_ask`, `slot_got`, None when the record states none),
    and wave link (`hub_profiler.gate_row_link`, None when the journal names no wave or card for the row). A row
    that ran no battery (#1798, mode `reused` or `skipped`) is no gate observation: its start is its finish."""
    def slot(name):
        try: v = float(row.get(name))
        except (TypeError, ValueError): return None
        return v if v >= 0 else None
    out = []
    try: lines = (STATE / "gate-history").read_text(errors="replace").splitlines()
    except OSError: return out
    links = gate_attempt_links() if links is None else links
    for line in lines:
        row = gate_row(line)
        if row is None or row.get("mode") in hub_profiler.NO_BATTERY_MODES: continue
        try: finish, start = float(row["finish"]), float(row["start"])
        except (KeyError, TypeError, ValueError): continue
        if finish < since: continue
        try: wait = float(row.get("wait"))
        except (TypeError, ValueError): wait = None
        out.append({"finish": finish, "start": start, "kind": row.get("kind", ""),
                    "outcome": row.get("outcome", ""), "caller": row.get("caller", ""), "wait": wait,
                    "slot_ask": slot("slot_ask"), "slot_got": slot("slot_got"),
                    "link": hub_profiler.gate_row_link(row, links)})
    return out

def trip_calls_of(path):
    """One record's model calls, cached by the file's own signature: the trip reads a record's events once a poll, as it
    does for the stages."""
    try: st = path.stat()
    except OSError: return []
    key = str(path)
    sig = (st.st_mtime, st.st_size)
    hit = TRIP_CALLS.get(key)
    if hit and hit[:2] == sig: return hit[2]
    calls = hub_profiler.trip_calls(trip_events(path))
    if len(TRIP_CALLS) >= TRIP_SPANS_MAX: TRIP_CALLS.clear()
    TRIP_CALLS[key] = (*sig, calls)
    return calls

def trip_review_calls(since):
    """The review token ledger's calls of the window as `{at, tokens}` (#2227): review.sh writes one line per review call
    at the time it writes it, and that instant is the call's end. A line with no usage count carries no tokens, so it is
    no sample of the Tokens line."""
    return [{"at": c["t0"], "tokens": c["in"] + c["out"]} for c in ledger_calls()
            if c["t0"] >= since and not c.get("uncounted")]

def mem_total_gb():
    """The machine's memory in gibibytes, the size the reader's own system shows, and the number the RAM line's hover
    reads: the kernel's own figure, else psutil, else the POSIX page count, else None. None is no guess: a machine that answers neither source
    carries no memory figure, and the hover names no size."""
    total = _sysctl_u64("hw.memsize")
    if not total:
        try:
            import psutil; total = psutil.virtual_memory().total
        except Exception: total = 0
    if not total:   # #2240: a Linux runner has no Mach sysctl and may have no psutil; the POSIX page count needs neither
        try: total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except (ValueError, OSError, AttributeError): total = 0
    return round(total / 1024 ** 3, 1) if total > 0 else None

def load_rows_now():
    """The minute record's rows, re-read only when the file's identity moved: the one cache `load_view` keeps, so the
    load line and the trip's CPU and RAM read one copy of the record."""
    stamp = load_record_stamp()
    if LOAD_STATE.get("record_rows") is None or stamp != LOAD_STATE.get("record_stamp"):
        LOAD_STATE["record_rows"], LOAD_STATE["record_stamp"] = load_record_rows(), stamp
    return LOAD_STATE["record_rows"] or []

TRIP_MEMO_SECS = 30.0   # the bar's own cadence: the Loop tab's file views are read every 30 s with the switch off
_TRIP_KEPT = {}   # (the window, the refusal, the half minute, the inputs' identity) -> the trip view

def trip_stamp(allowed=True):
    """The identity of the inputs the trip bar folds (#2582): every run's record and request, the board's history stores,
    the gate history, the review ledger and, for a caller the hub allowed, the minute record. A poll that finds them all
    unchanged serves the view it built, so the fold runs when a record moves and not on every read of the tab."""
    files = trip_record_files()
    return (tuple(sorted((str(p), _pair(p), _pair(p.parent / "request.txt")) for p in files)),
            _pair(_hist.history_path(BOARD, REPO)), _pair(_flow.history_path(BOARD, REPO)),
            _pair(STATE / "gate-history"), _pair(STATE / "token-usage.tsv"), load_record_stamp() if allowed else None)

def trip_view(window_s, now=None, allowed=True):
    """The Loop tab's trip bar (#1769, #2152): the board's six columns of a delivery over the chosen window, folded by the
    rule layer from the run records under this profile's run roots, the gate history, and the board transitions of the
    card each run works (#1894). A run that names no card reads no column the card alone fills. It reads files only, so
    it shows with the CPU sampling switch off. Beside the bar it carries what each column consumes (#2227): the run
    records' call outcomes and the review token ledger as Tokens, and the minute record's whole-machine CPU and RAM, each
    sample placed in the column the card stood in at its time. `allowed` False is a caller the hub refused: then the
    minute record is not stat, the CPU and RAM lines read no row, and the payload asserts neither a peak nor a count of
    silent minutes, as the load chart reads none. The view is kept while its inputs and the half minute its `since` stands
    in are unchanged (#2582): the Loop tab's sampler asks it every second, and the fold reads every record."""
    now = time.time() if now is None else now
    key = (window_s, allowed, int(now // TRIP_MEMO_SECS), trip_stamp(allowed))
    kept = _TRIP_KEPT.get(key)
    if kept is not None: return kept
    since = now - window_s
    files = trip_record_files()
    issues = {p: trip_issues(p) for p in files}
    numbers = {n for ns in issues.values() for n in ns}
    store = _hist.load_store(_hist.history_path(BOARD, REPO))
    waits = card_waits(store, numbers)
    columns = card_column_runs(store, numbers, since, now)
    spans = [trip_stages(p, next((waits[n] for n in issues[p] if n in waits), None)) for p in files]
    # #2292: a run whose issue another run delivered is not a delivery of that issue.
    keep = hub_profiler.trip_deliveries([s["end"] if s else None for s in spans], [issues[p] for p in files],
                                        {n: w["closed"] for n, w in waits.items() if w.get("closed") is not None})
    spans = [s if k else None for s, k in zip(spans, keep, strict=True)]
    rework = [r for w in waits.values() for r in w.get("rework") or ()]
    if len(_TRIP_KEPT) >= 16: _TRIP_KEPT.clear()   # a long-lived process may stand in many windows: the old ones go first
    _TRIP_KEPT[key] = out = {"window_s": window_s, "since": since,
            **hub_profiler.trip(spans, trip_gates(since), since,
                                cards=[next((columns[n] for n in issues[p] if n in columns), None) for p in files],
                                calls=[trip_calls_of(p) for p in files], review_calls=trip_review_calls(since),
                                loads=load_rows_now() if allowed else None, now=now, cores=os.cpu_count(),
                                mem_gb=mem_total_gb(), rework=rework,
                                waves=[{"wave": p.parent.name, "issues": issues[p]} for p in files])}
    return out

PROMOTED_STG = re.compile(r"promote-pass:\s+(\S+)\s+promoted:")        # promote-pass.sh's dated success line
PROMOTED_MAIN = re.compile(r"promote-main-pass:\s+(\S+)\s+promoted:")   # promote-main-pass.sh's own
PROMOTE_ISO = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:[.,]\d+)?(?:Z|[+-]\d\d:?\d\d)?")   # the date the writer puts before its marker

def strip_audit():
    """The switch audit's lines, each with its epoch, action, writer, origin and the issue a loop drain names."""
    out = []
    for raw in _read(LOOP_AUDIT).splitlines():
        try: e = json.loads(raw)
        except ValueError: continue
        at = _epoch(e.get("at"))
        if at is not None:
            out.append({"at": at, "action": e.get("action"), "who": e.get("who"), "from": e.get("from"), "issue": e.get("issue")})
    return out

def strip_steps():
    """The dated step records, folded by the one reader #1750 ships: this tab adds no second reader of the file. None when
    the file is absent, so the strip draws a gap with its reason rather than reading an absent source as a dead loop."""
    path = STATE / "steps.tsv"
    if not path.is_file():
        return None
    return steprec.fold(_read(path).splitlines())

def strip_landings(base=None):
    """The landing ledger's rows, `<epoch> TAB <branch> TAB <sha> TAB <issue>`: a row this reader cannot read is skipped.
    `base` is another project's state directory (#2507 reads a hold's own stage from it)."""
    out = []
    for raw in _read((pathlib.Path(base) if base else STATE) / "landed.tsv").splitlines():
        f = raw.split("\t")
        if len(f) < 4: continue
        try: at = int(f[0])
        except ValueError: continue
        try: issue = int(f[3])
        except ValueError: issue = None
        out.append({"at": at, "branch": f[1], "sha": f[2], "issue": issue})
    return out

def strip_promotions(base=None):
    """The promotion log's success lines: a line that says `promoted:` after its marker, never a refusal's line. The mark
    takes the nearest date in front of the marker, so a writer that wraps or prefixes its line still names its delivery.
    `base` is another project's state directory (#2507 reads a hold's own stage from it)."""
    stg = os.environ.get("SG_STG_BRANCH") or "stg"
    main = os.environ.get("SG_MAIN_BRANCH") or "main"
    out = []
    for raw in _read((pathlib.Path(base) if base else STATE) / "promote.log").splitlines():
        for rx, branch in ((PROMOTED_MAIN, main), (PROMOTED_STG, stg)):
            m = rx.search(raw)
            if not m: continue
            dates = PROMOTE_ISO.findall(raw[:m.start()])
            at = _epoch(dates[-1]) if dates else None
            if at is not None:
                out.append({"at": at, "branch": branch, "sha": m.group(1)})
    return out

def strip_gates(start, end):
    """The gate-history lines of the strip's window, folded by the rule layer: one mark per finished gate."""
    try: lines = (STATE / "gate-history").read_text(errors="replace").splitlines()
    except OSError: lines = []
    return hub_profiler.gate_marks(lines, start, end)

def strip_view(w=None, now=None):
    """The loop state strip of the window `w` the page chose, from the records the loop already writes: the switch audit,
    the dated step records, the landing ledger, the promotion log and the gate history. No GitHub read, and no process
    table."""
    out = hub_profiler.strip(strip_audit(), strip_steps(), strip_landings(), strip_promotions(), w,
                             time.time() if now is None else now)
    out["gates"] = strip_gates(out["start"], out["end"])   # the gates under the bar share the strip's own window
    return out

WALL_TAIL_BYTES = 4 * 1024 * 1024   # the newest bytes of each file the wall view reads: the step file is cut at 256 KiB, the history only grows

def _tail(path, limit=None):
    """The file's newest `limit` bytes as whole lines: a tail that starts mid-line drops its first line."""
    limit = WALL_TAIL_BYTES if limit is None else limit
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - limit))
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if size > limit else lines

def wall_view(ww=None, now=None):
    """The Loop tab's wall-time view of the window `ww` the page chose: the bounded tails of the dated step records and the gate
    history, folded by the rule layer. It reads files only, so it shows with the CPU sampling switch off."""
    now = time.time() if now is None else now
    return hub_profiler.wall(steprec.fold(_tail(STATE / "steps.tsv")), _tail(STATE / "gate-history"),
                             hub_profiler.wall_window(ww), now)

def load_record_rows():
    """The minute record's lines as the chart reads them: `{at, load, cpu}`, a field the writer could not read as None.
    A line that names no time is no record: it never draws."""
    out = []
    for raw in _read(load_record_path()).splitlines():
        row = hub_profiler.load_row(raw)
        if row is not None: out.append(row)
    return out

def load_record_stamp():
    """The record file's identity for the row cache: any write changes it, so the view reads the file only when it did."""
    try:
        s = load_record_path().stat()
    except OSError:
        return None
    return (s.st_mtime_ns, s.st_size)

def load_view(st):
    """The machine load over the strip's window: one stat a poll, and a re-read only when the record's identity moved."""
    stamp = load_record_stamp()
    if LOAD_STATE.get("record_rows") is None or stamp != LOAD_STATE.get("record_stamp"):
        LOAD_STATE["record_rows"], LOAD_STATE["record_stamp"] = load_record_rows(), stamp
    return hub_profiler.load_chart(LOAD_STATE["record_rows"], st["start"], st["end"], os.cpu_count() or 1)

# The run slots in use (#1766): the step line of the Loop tab, folded by the rule layer from the run records. One record
# is read tolerantly: an event that is not JSON is skipped, and a record that is there and cannot be opened is a gap with
# its reason, never a run of zero slots. The cap's marks are the caps each `run_start` recorded, so a change shows at the
# time a record names it; the cap in force now heads a window no earlier record speaks for, and no file's mtime is a cap change.
NEVER_STARTED_S = 300   # squad/caps.py LAUNCH_GRACE, the janitor's rule-J bound: past it a folder with no record is lost, not a launch
def run_invocations(path):
    """One run's invocations and the caps its starts recorded: each invocation is `{"start", "end"}` from a `run_start` or
    `resume` to the verdict after it (`end` None while it has none), and each mark `{"at", "runs"}` the cap that start
    recorded. None when the record cannot be read. A verdict before a later start is history (ADR-0167)."""
    try: text = path.read_text(errors="replace")
    except OSError: return None
    out, caps, cur = [], [], None
    for line in text.splitlines():
        if '"ts"' not in line: continue
        try: e = json.loads(line)
        except ValueError: continue
        if not isinstance(e, dict): continue
        ts, name = e.get("ts"), e.get("event")
        if not isinstance(ts, (int, float)) or isinstance(ts, bool): continue
        if name in ("run_start", "resume"):
            if cur is not None:
                if cur["end"] is None: cur["end"] = ts
                out.append(cur)
            cur = {"start": ts, "end": None}
            c = e.get("caps") if isinstance(e.get("caps"), dict) else None
            if name == "run_start" and isinstance((c or {}).get("runs"), int) and not isinstance(c["runs"], bool):
                caps.append({"at": ts, "runs": c["runs"]})
        elif name == "verdict" and cur is not None:
            cur["end"] = ts
    if cur is not None: out.append(cur)
    return out, caps

def run_dir_time(d):
    """A run folder's own write time, or None when the folder is gone: the best evidence of its start when its record
    cannot be read, and never a reason to fail the read."""
    try: return d.stat().st_mtime
    except OSError: return None

def slot_view(window_s, now=None, cap=None):
    """The run slots in use of the window `window_s` and the instant `now`: the records of every run under this profile's
    run roots, folded by the rule layer. `cap` is the run cap in force now, read once by the caller. A folder past its
    launch window with no record, and a record that exists but cannot be read, are gaps with their reason: the chart says
    what it cannot see, never a silent zero. With no readable record at all, one gap covers the whole window, so the
    stretch a lost folder names never stands in for the rest. No GitHub read, and no process table, so it shows with the
    CPU switch off."""
    now = time.time() if now is None else now
    invs, caps, bad, readable = [], [], [], 0
    for root in run_roots(CHECKOUT):
        try: ds = sorted(root.glob("run-*"))
        except OSError: continue
        for d in ds:
            rec = d / "record.jsonl"
            got = run_invocations(rec)
            if got is None:
                at = run_dir_time(d)
                if rec.exists():
                    bad.append({"start": at, "reason": f"{d.name}: record unreadable"})
                elif at is not None and now - at > NEVER_STARTED_S:
                    bad.append({"start": at, "reason": f"{d.name}: no record"})
                continue   # a run still launching has no record yet: no slot drawn, never a gap
            readable += 1
            inv, marks = got
            invs.extend(inv); caps.extend(marks)
    return hub_profiler.run_slots(None if readable == 0 else invs, caps, window_s, now, cap=cap, unreadable=bad)

def profiler_view(a, w=None, sw=None, ww=None):
    """What the Loop tab reads: the switch, the window the tree covers, the tree of the loop, the context row apart from
    it, the facts of the reading, the loop state strip of its own window `sw`, the run slots in use and the machine load
    under it, the trip bar and the time in failed runs table of the window `w` the page chose, and the wall time of the
    steps and gates of the window `ww`; no tree while the switch is off, and the file views read files only, so they show
    with the switch off. The strip, the slots and the load are folded from one `now`, so a column of the page is the same
    instant in each. All of them are null for a caller the hub refused, as the records are not read for one; the cap is
    read once, so the chart's ceiling and the rest of the page name the same value."""
PROF_ON_SECS = PROF_POLL_SECS   # with the switch on the view is kept for the page's own poll cadence, which is never faster than the sampler's interval, so a read always serves an answer the poll would not have refreshed sooner (#2848)
PROF_OFF_SECS = 30.0  # the file views' cadence with the switch off (`profFileDue`), so one build serves every page in a window
_PROF_KEPT = {}   # (w, sw, ww, on, tailnet) -> (at, answer): the loaded module's own, so one profile never serves another's
_PROF_LOCK = threading.Lock()

def prof_kept_clear():
    """Clear the kept Loop view. A switch write is the page's own change, so the read after it shows the write and never
    a view built before it."""
    with _PROF_LOCK: _PROF_KEPT.clear()

def profiler_served(a, w=None, sw=None, ww=None, now=None):
    """The Loop view, built at most once per the page's own poll window (#2440).

    The build reads the process table, the trip fold, the run table and the machine load, and it cost 0.8 to 1.8 seconds
    of CPU on a request that the page makes every two seconds while it samples and every thirty with the switch off. The
    window follows the switch: the page's own poll cadence, which is never faster than the sampler's interval, or the file
    views' own thirty. A caller the hub refused is built
    for and never kept, so a refusal is never served from a kept view; the tailnet flag is part of the key, since it
    decides the write the answer names."""
    if not a.allowed or a.tailnet: return profiler_view(a, w, sw, ww)   # a refusal is never kept, and a tailnet caller's
    on = profiler_on()                                                  # write follows a grant that can move inside a window
    window = PROF_ON_SECS if on else PROF_OFF_SECS
    key = (w, sw, ww, on)
    clock = POLL_CLOCK if now is None else (lambda: now)   # one clock for the stamp and its comparison (#2848)
    with _PROF_LOCK:
        at, answer = _PROF_KEPT.get(key, (None, None))
        if at is not None and clock() - at < window: return answer
        answer = profiler_view(a, w, sw, ww)
        _PROF_KEPT[key] = (clock(), answer)   # the stamp is the instant the answer existed: a build longer than the window would otherwise let the read that waited for it build again (#2848)
        return answer

def profiler_view(a, w=None, sw=None, ww=None):
    write = a.allowed and (not a.tailnet or tailnet_block().get("profiler_write") is True)
    window_s = hub_profiler.run_window(w)
    now = time.time()
    st = strip_view(sw, now) if a.allowed else None   # no loop record read for a caller the hub already refused
    # The load chart is drawn only with the strip, and the page draws it with the switch off as it draws the strip, so
    # the one stat of the record is the cost of a drawn chart: a refused poll stats nothing (#1768).
    load = load_view(st) if st else None
    out = {"on": profiler_on(), "since": None, "window_s": 0, "span_s": hub_profiler.SPAN_S, "secs": PROFILER_SECS, "tree": None,
           "context": None, "last_reading": None, "interval_s": None, "failed_readings": 0,
           "processes_read": 0, "processes_seen": 0, "coverage": None,
           "trip": trip_view(window_s, now, allowed=a.allowed), "runs": profile_run_table(window_s), "runs_window_s": window_s,
           "strip": st, "slots": slot_view(sw, now, (loop_caps() or {}).get("runs")) if a.allowed else None, "load": load,
           "runs_windows": [{"s": s, "label": hub_profiler.RUN_WINDOW_LABELS[s]} for s in hub_profiler.RUN_WINDOWS],
           "wall": wall_view(ww, now) if a.allowed else None,
           "wall_windows": [{"s": s, "label": hub_profiler.WALL_WINDOW_LABELS[s]} for s in hub_profiler.WALL_WINDOWS],
           "write": write, "reason": "" if write else (a.reason if not a.allowed else PROFILER_READ_ONLY)}
    if not out["on"]: return out
    profiler_ensure()
    with PROFILER_LOCK:
        w = PROFILER["window"]
        out.update(since=w.since, window_s=round(w.window_s(time.time()), 1), tree=hub_profiler.trim(w.tree()),
                   context=w.context(), **w.stats())
    return out

def profiler_switch(a, body, origin_ok):
    """The profiler switch's write, shaped as the loop switch's (ADR-0162): the access rule, its own tailnet grant, the
    origin and a JSON body `{"on": true|false}`. The answer carries `sampler: "running"|"ended"`, and on a sampler that
    outlived the off write's wait, its words beside it in `sampler_text`."""
    why = profiler_refusal(a, origin_ok, tailnet_block())
    if why: return 403, {"err": "forbidden", "reason": why}
    if not isinstance(body, dict) or not isinstance(body.get("on"), bool): return 400, {"err": "bad request"}
    client = body.get("client") if body.get("client") in LOOP_CLIENTS else None
    ended = profiler_set(body["on"], a.login or "owner", "tailnet" if a.tailnet else "loopback", client=client)
    prof_kept_clear()   # the page reads its own switch at once, never a view built before the write (#2440)
    out = {"ok": True, "on": body["on"], "sampler": "ended" if ended else "running"}   # a word a page tests, not prose
    if not ended: out["sampler_text"] = PROFILER_STILL_RUNNING   # the off answer tells the truth about the sampler it left
    return 200, out

def foot_sources(now=None):
    """One reading of every source the line draws on. A source that is not there reads as `None` for its own fact only,
    so one unreadable file costs one `?` and never the rest of the line.
    The queue is one pass over the issue ledger's open hold episodes (#1173), taken here once per refresh: the page then
    draws the whole queue from that one reading, with no request per waiting child and no count of the hub's own."""
    now = time.time() if now is None else now
    out = {"landing": _last_landing(), "gate": _gate_status(), "queue": issue_ledger.open_episodes(STATE, now), "nightly": _nightly_status(),
           "quota": {"codex": limited_until(_read(SHARED / "codex-limited"), now),
                     "agy": limited_until(_read(SHARED / "agy-limited"), now)}}
    plans = plans_get()   # the hand-entered percentage, for a probe that did not answer: the two things #1839's tokens read
    out["burn"] = burn_cached(now)
    out["cycle"] = _cycle_state(now)
    out["loop"] = loop_seen(_loop_on(), now)
    out["subs"] = {k: {"probe": quota_row(k, now), "quota": (plans.get(k) or {}).get("quota")} for k in SUBS}
    return out

def foot_refresh(now=None):
    """One pass of the footer's sources, kept where the page serves it from: the sampler thread calls this, so the line
    can never be written from a figure the loop has stopped refreshing. A failed pass keeps the last good facts."""
    src = foot_sources(now)
    LOAD_STATE["facts"] = foot_items(src, now)
    return LOAD_STATE["facts"]

SPEC_TIP_LINES = 8   # the spec tile's tooltip names at most this many specs, then "+k more"
NOSPEC_HOURS = 4     # the No spec group counts the cards closed in this many hours (owner, 2026-11-02)

def nospec_count(items, moves=None, window=None, now=None):
    """The No spec group (bugs and single tickets): `total` is its open cards plus its closed cards closed in the last
    NOSPEC_HOURS hours by the read's own closed-at (`c`); `closed` is those closed ones. The window reads the closing, never the
    opening: the work takes hours, so a card opened in the window is rarely closed in it, and the group answered zero (owner,
    2026-11-02). A card is in the group as the board's No spec
    lane holds it: no spec label, no loop-bug label, and no parent spec, or a parent spec the board draws closed (an open
    card only: a closed card of a closed spec is history). An abandoned card, or a closed card with no opened-at, never
    counts. The column is `drawn_column`, with the door's overlay, so the lane, the summary and the tile agree (ADR-0137)."""
    if moves is None: moves = read_moves() if overlay_on() else {}
    if window is None: window = pending_window()
    since = (time.time() if now is None else now) - NOSPEC_HOURS * 3600
    ends, closed_col = (COLUMN["issue_closed"], COLUMN["abandoned"]), COLUMN["issue_closed"]
    cols = [(i, cache_row_column(i, moves, window)) for i in items or []]
    gone = {i.get("n") for i, st in cols if _columns_module.SPEC_LABEL in (i.get("l") or []) and st in ends}
    done = total = 0
    for i, st in cols:
        labels = i.get("l") or []
        if _columns_module.SPEC_LABEL in labels or _loop_defect(i) or st == COLUMN["abandoned"]: continue
        pa = i.get("pa") if isinstance(i.get("pa"), int) else None
        if pa is not None and (pa not in gone or st == closed_col): continue
        if st != closed_col: total += 1; continue
        try: at = _utc(i["c"]).timestamp() if i.get("c") else None
        except Exception: at = None
        if at is not None and at >= since: done, total = done + 1, total + 1
    return {"closed": done, "total": total}

def spec_progress(items, moves=None, window=None, now=None):
    """The Throughput card's last row (#1424): closed over total sub-issues, summed over every open spec on the board cache. An open
    spec is a card with the spec label that the board draws in neither Closed nor Abandoned: its column comes from
    `drawn_column`, with the door's overlay, as the board draws it (#829). The count is the one `task_count` reads, so
    no new GitHub read. The percent rounds down, so 100% means every sub-issue is closed; `frac` is the exact fraction
    for the bar width. The page only draws these values (ADR-0137)."""
    if moves is None: moves = read_moves() if overlay_on() else {}
    if window is None: window = pending_window()
    gone = (COLUMN["issue_closed"], COLUMN["abandoned"])
    done = total = 0
    rows = []   # (fraction, number, tip line) per open spec
    # A ticket's close instant stands for its landing: the board cache keeps no dev commit time.
    landed = {i["n"]: (t, t) for i in items or [] if isinstance(i.get("n"), int) and (t := _close_instant(i.get("c"))) is not None}
    kids = {}
    for i in items or []:
        if isinstance(i.get("pa"), int) and isinstance(i.get("n"), int): kids.setdefault(i["pa"], []).append(i["n"])
    ages = specstall.ages(kids, landed, time.time() if now is None else now)
    for i in items or []:
        if _columns_module.SPEC_LABEL not in (i.get("l") or []): continue
        if cache_row_column(i, moves, window) in gone: continue
        sc = task_count(i)
        age = f" · open {int(ages[i['n']] // 86400)} d" if i.get("n") in ages else ""
        if sc:
            c, t = (int(x) for x in sc.split("/"))
            done, total = done + c, total + t
            rows.append((c / t, i.get("n") or 0, f"#{i.get('n')} {i.get('t') or ''} — {sc}{age}"))
        else:
            rows.append((0.0, i.get("n") or 0, f"#{i.get('n')} {i.get('t') or ''} — no sub-issue{age}"))
    rows.sort(key=lambda r: (r[0], r[1]))
    tips = [r[2] for r in rows[:SPEC_TIP_LINES]] + ([f"+{len(rows) - SPEC_TIP_LINES} more"] if len(rows) > SPEC_TIP_LINES else [])
    n = len(rows)
    out = {"open": n, "done": done, "total": total, "tip": "\n".join(tips)}
    if not n: return dict(out, pct=0, frac=0, value="no open spec", state="none", bar=False)
    if not total: return dict(out, pct=0, frac=0, value="0 of 0 closed", state="info", bar=True)
    pct = done * 100 // total
    return dict(out, pct=pct, frac=done / total, value=f"{done} of {total} closed - {pct}%",
                state="ok" if done == total else "info", bar=True)

SPEED_SLOTS = 6                 # the rolling slots the window is compared with, the window itself being the newest
SPEED_WINDOW_SECS = 4 * 3600    # the window's first value; SG_SPEED_WINDOW_SECS moves it without a code change
LABELS_STALE_SECS = 120         # the board cache's label read may trail the card this long before the card names it (#1424)

def speed_window_secs():
    """The window in seconds: the constant, or SG_SPEED_WINDOW_SECS when it names a positive whole number. Read at call
    time, so a changed setting moves the next fact and needs no restart."""
    try: v = int(os.environ.get("SG_SPEED_WINDOW_SECS") or 0)
    except ValueError: return SPEED_WINDOW_SECS
    return v if v > 0 else SPEED_WINDOW_SECS

def _close_instant(value):
    """One close stamp as an epoch, or None when the record carries no readable stamp."""
    try: return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError): return None

def close_stamps(store, items=None):
    """Every close stamp the two sources carry: the history store's `closed` events and the board cache's `c` stamps."""
    out = []
    for rec in store.values():
        for e in rec.get("events") or []:
            if e.get("type") != "closed": continue
            ts = _close_instant(e.get("t"))
            if ts is not None: out.append(ts)
    for i in items or []:
        if i.get("s") != "closed": continue
        ts = _close_instant(i.get("c"))
        if ts is not None: out.append(ts)
    return out

def closed_since(items, now_ts, window_secs):
    """How many cards the board cache shows closed inside the window, or None when it holds no card. A closed card names
    its own close stamp (`c`), so the window never waits for a timeline read (#1193)."""
    if not items: return None
    since, out = now_ts - window_secs, 0
    for i in items:
        if i.get("s") != "closed": continue
        ts = _close_instant(i.get("c"))
        if ts is not None and since <= ts <= now_ts: out += 1
    return out

def cache_row_column(i, moves, window):
    """The board's own column for one cache row. A row a test or an older read hands in with a key missing is never a
    KeyError: the missing key is one fact the board cannot draw, and the column reads from what the row carries."""
    return drawn_column({"s": i.get("s"), "st": i.get("st"), "l": i.get("l"), "cr": i.get("cr"),
                         "i": i.get("i"), "ot": i.get("ot")}, moves, window)["st"]

def _loop_defect(i): return any(str(x).startswith("loop-bug") for x in (i.get("l") or []))

def _defect(i): return "bug" in (i.get("l") or []) or _loop_defect(i)

def _task(i):
    labels = [str(x) for x in (i.get("l") or [])]
    return "spec" not in labels and "bug" not in labels and not _loop_defect(i)

def window_row(items, now_ts, window_secs, moves, window, kind):
    """One window row (#2264): `closed` counts the cards of the kind closed inside the window, by their close stamp; `total`
    is `closed` plus the cards of the kind the board draws open now, whatever their age. An abandoned card is in neither
    count, and a card closed before the window is in no count. The column is the kanban's own (ADR-0137)."""
    since, closed, total = now_ts - window_secs, 0, 0
    for i in items or []:
        if not kind(i): continue
        column = cache_row_column(i, moves, window)
        if column == COLUMN["abandoned"]: continue
        if column == COLUMN["issue_closed"]:
            at = _close_instant(i.get("c"))
            if at is None or not since <= at <= now_ts: continue
            closed += 1
        total += 1
    return {"closed": closed, "total": total, "value": f"{closed} of {total} closed" if total else "none",
            "frac": closed / total if total else 0}

def arrivals_fact(items, now_ts, window_secs, moves=None, window=None):
    """The Bugs and Loop-bugs rows under the hero figure, over the hero's window (#1424, #2264). Bugs counts `bug` or any
    label beginning with `loop-bug` (the prefix rule the loop dispatches by, ADR-0146); Loop-bugs counts the prefix alone,
    always a subset of Bugs."""
    if moves is None: moves = read_moves() if overlay_on() else {}
    if window is None: window = pending_window()
    return {"bugs": window_row(items, now_ts, window_secs, moves, window, _defect),
            "loop_bugs": window_row(items, now_ts, window_secs, moves, window, _loop_defect)}

def tasks_fact(items, now_ts, window_secs, moves=None, window=None):
    """The Tasks row under the hero figure, over the hero's window (#1362, #2264): a closed card with no spec, bug or
    loop-bug label. A spec is a document and never work, and a defect is a fix."""
    if moves is None: moves = read_moves() if overlay_on() else {}
    if window is None: window = pending_window()
    return window_row(items, now_ts, window_secs, moves, window, _task)

def throughput_fact(now=None):
    """How fast the board is closing cards over the last window: its close count, the six rolling slots it is compared
    with (the newest is the window), which finished slots are available, one median and the bar maximum over those, the
    trend word and the age of the oldest source read. The window is the last four hours back from the sampler's clock,
    never a calendar day. Under the hero figure the fact carries the Tasks, Bugs and Loop-bugs rows, each counting the
    cards closed in the same window over the cards open now plus those closed in it (#2264).

    A finished slot is unavailable when it ends before the earliest close stamp of the two sources; it enters neither the
    median nor the maximum. A slot with no close inside an available span is a measured zero. The window's own figure
    comes from the board cache, as the daily figure did, and falls back to the store when the hub holds no cache."""
    store = _hist.load_store(_hist.history_path(BOARD, REPO))
    items = board_cache.get("items") or []
    if not store and not items: return None
    now_ts = now.timestamp() if hasattr(now, "timestamp") else (time.time() if now is None else now)
    w = speed_window_secs()
    stamps = close_stamps(store, items)
    earliest = min(stamps) if stamps else None
    window_n = closed_since(items, now_ts, w)
    if window_n is None: window_n = sum(1 for ts in stamps if now_ts - w <= ts <= now_ts)
    slots = []
    for k in range(SPEED_SLOTS - 1, -1, -1):   # oldest first, the newest slot being the window
        end, start = now_ts - k * w, now_ts - (k + 1) * w
        if not k:
            slots.append({"start": start, "end": end, "n": window_n, "finished": False, "available": None})
            continue
        slots.append({"start": start, "end": end, "n": sum(1 for ts in stamps if start <= ts < end),
                      "finished": True, "available": earliest is not None and end >= earliest})
    avail = [s["n"] for s in slots if s["finished"] and s["available"]]
    median = round(st.median(avail), 1) if avail else None
    if median is None: trend = None
    elif median > 0: trend = "up" if window_n > median * 1.2 else "down" if window_n < median * 0.8 else "flat"
    else: trend = "up" if window_n > 0 else "flat"   # a ratio at a zero median has no meaning
    # Each age names its own read (#1661): the board item read (the cards, their labels and their closures) is the cache's
    # `t`; the history read is the newest timeline stamp in the store. Neither ages the other.
    board_t = board_cache.get("t") or 0
    board_age = now_ts - board_t if board_t else None
    hist_t = max((r.get("t") or 0 for r in store.values()), default=0) if store else 0
    hist_age = now_ts - hist_t if hist_t else None
    bound = board_stale_bound()
    moves = read_moves() if overlay_on() else {}
    window = pending_window()
    return {"window_secs": w, "window": window_n, "day": sum(s["n"] for s in slots),   # closes in the whole day: none means no history
            "arrivals": arrivals_fact(items, now_ts, w, moves, window),   # the Bugs and Loop-bugs rows, over the hero's window
            "tasks": tasks_fact(items, now_ts, w, moves, window),   # the Tasks row, over the hero's window
            "labels": {"age_secs": board_age, "stale": board_age is not None and board_age > LABELS_STALE_SECS},   # the card labels ride the item read
            "board": {"as_of": board_t or None, "age_secs": board_age, "stale": board_age is not None and board_age > bound,
                      "overdue": board_read_overdue(board_t, now_ts)},
            "history": {"as_of": hist_t or None, "age_secs": hist_age, "stale": hist_age is not None and hist_age > bound},
            "slots": slots, "available": avail, "median": median, "min": min(avail) if avail else None,
            "max": max(avail + [window_n]), "trend": trend}

def subs_gauges(now=None):
    """Each subscription's weekly window for the Monitor: its used share and when it resets, or why there is no reading.
    The weekly window is the one the reset matters for; a provider that reports only a short window shows that one."""
    now = time.time() if now is None else now
    plans = plans_get(); out = []
    for k in SUBS:
        q = quota_row(k, now)
        row = {"key": k, "label": sub_name(k, plans.get(k) or None, (quota.get(k) or {}).get("plan"))}
        ws = (q or {}).get("windows") or []
        if not ws:
            row.update({"pct": None, "resets": None, "why": (q or {}).get("err") or "no reading"})
        else:
            # The plan's own weekly window leads; a plan that reports no weekly one shows its monthly
            # share (the owner's choice); anything else shows the fullest window, never the one that merely resets last.
            w = (next((x for x in ws if str(x.get("label", "")).lower().startswith("weekly")), None)
                 or next((x for x in ws if "month" in str(x.get("label", "")).lower()), None)
                 or max(ws, key=lambda x: x.get("pct") or 0))
            row.update({"pct": w.get("pct"), "resets": w.get("resets"), "window": w.get("label")})
        out.append(row)
    return out

def load_payload():
    """What /api/load serves: the cached reading, never a fresh one, so three open tabs cost three JSON bodies and no
    sample. `cpu` is the newest reading, `warn` is the two-sample rule over the history, `runs` the shares the cards use,
    and `facts` the footer's own facts as the sampler last read them. The three numbers are the readings themselves, unrounded
    (#1816): the page builds the cells from them, so the text rounds for display while the ceiling rule judges the reading.
    `specs` is served on its own key, off the board cache alone, so the Throughput card draws it whatever the optional
    speed fact answers (#1424)."""
    hist = LOAD_STATE.get("cpu") or []
    cpu = hist[-1] if hist else None
    now = time.time()
    items = board_cache.get("items") or []
    moves = read_moves() if overlay_on() else {}
    return {"line": load_line(cpu, LOAD_STATE.get("ram"), LOAD_STATE.get("gpu")),
            "cpu": cpu,
            "ram": LOAD_STATE.get("ram"), "gpu": LOAD_STATE.get("gpu"),
            "warn": sustained(hist), "runs": dict(LOAD_STATE.get("runs") or {}),
            "facts": list(LOAD_STATE.get("facts") or []) + [_hist.budget_fact(_hist.history_path(BOARD, REPO), now)],
            "subs": subs_gauges(),   # the Monitor's subscription instruments: each weekly window and its reset, off the quota cache
            "specs": spec_progress(items, moves, pending_window(), now),   # the cumulative Specs row, never a window count
            "speed": throughput_fact(now),   # the Monitor's throughput card; the Specs row rides the key above (#1424)
            "secs": LOAD_SAMPLE_SECS, "asof": LOAD_STATE.get("asof", 0.0)}

def status_payload():
    """What the mobile Monitor tab reads below its card: the facts the hub computes, never a figure the page works out."""
    items = board_cache.get("items") or []
    return {"loop": loop_status(),
            "live": len(live_run_rows(run_list())),   # one count for the Now tile and the Runs heading (#2502)
            "spend_today": spend_today(),
            "open_bugs": sum(1 for i in items if str(i.get("s")).upper() == "OPEN" and "bug" in (i.get("l") or [])),
            "warnings": [{"text": f["text"], "tip": f.get("tip")} for f in (LOAD_STATE.get("facts") or []) if f.get("bad")],
            "load": load_payload(), "build": HUB_BUILD}

def worst_alert():
    """This project's worst alert as text: the first bad foot fact, in the order the line reads them; "" when none (#876)."""
    return next((f["text"] for f in (LOAD_STATE.get("facts") or []) if f.get("bad")), "")

def load_sampler():
    """The one sampler thread: a failed pass leaves the last reading in place rather than breaking the page. It reads
    while a page reads this profile, and does no work while none does (#1790)."""
    while True:
        if read_wanted():
            try: load_tick()
            except Exception: pass
            try: foot_refresh()
            except Exception: pass
        read_pause(LOAD_SAMPLE_SECS)

# A coordinator test pass writes `running` at its start and its terminal state at the end; killed in between, the file says
# `running` forever. Four hours without a touch is longer than any pass this coordinator runs, so an untouched file is not a
# running pass: the hub shows it as stalled with this note, past the RUNNING group, instead of counting a live run with no
# process behind it. The runner's file is never
# rewritten and the loop's own readers keep reading it as it is -- this is the hub's display bound, not a claim the test failed.
TEST_LOST_SECS = 4 * 3600
LOST_NOTE = "lost: no state written for over 4 h"
# A lost pass stays visible -- it is the one reading that says a pass died and nobody retried it -- but not forever: past a day
# the ghost of a pass nobody will retry outlives its message, and the left pane fills with entries that are older than the work
# around them. A day is past any pass a reader would still act on, and it is the same drop the finished ones get, so the list
# converges on what is current instead of growing by one row per killed pass.
TEST_LOST_DROP_SECS = 24 * 3600

