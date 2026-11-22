# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
MOVES = STATE / "board-moves.json"   # the door's record of every move it proved (#649), keyed by the Project item id
MOVES_LOCK = threading.Lock()        # one writer in this process: two HTTP threads never drop the same entry twice
def read_moves():
    """The door's record of proved moves: {item id: {"column", "at"}}. A record that is missing, unreadable or not an
    object is no record: the board is drawn from the read alone, and nothing is written."""
    try: d = json.loads(MOVES.read_text())
    except (OSError, ValueError): return {}
    return d if isinstance(d, dict) else {}
def move_time(entry):
    """The instant the door proved a move, as whole epoch seconds; None when the entry carries no readable instant.
    `_utc` returns an aware UTC datetime, so its timestamp is the door's own instant, and both sides of the comparison
    below are whole seconds. A record without one is never taken as agreed: the board keeps its mark rather than lose
    a proved move."""
    stamp = entry.get("at") if isinstance(entry, dict) else None
    if not isinstance(stamp, str): return None
    try: return int(_utc(stamp).timestamp())
    except (ValueError, OverflowError, OSError): return None
def read_carried(move_at, read_at):
    """Whether a list read that began at `read_at` can have carried a move proved at `move_at`, both whole epoch
    seconds. The read must have begun in a later second: in the same second nothing tells which came first, so the
    mark stays rather than lose a proved move (a later read drops it)."""
    return move_at is not None and read_at is not None and move_at < read_at
MOVE_CONTEXTS = ("owner", "loop")   # the dispatch contexts board.py records with a move (`SG_MOVE_BY`)
def has_context(entry):
    """Whether the door recorded a dispatch context for the move: a move with none does not outrank a read that began after it (#861)."""
    return entry.get("by") in MOVE_CONTEXTS
PENDING_WINDOW_DEFAULT = 120.0   # seconds a proved move may stand with no list read agreeing (#651)
PENDING_WARNED = set()           # bad window values already named on stderr, so a page reload never repeats the line
def pending_window():
    """The window a proved move may stand without a list read agreeing (`SG_PENDING_WINDOW_SECONDS`, 120 s by default).
    A value that is not a number, or is negative, is the default, and one stderr line names the value and the default:
    a setting the hub ignored never passes silently."""
    raw = os.environ.get("SG_PENDING_WINDOW_SECONDS")
    if not raw: return PENDING_WINDOW_DEFAULT
    try: w = float(raw)
    except ValueError: w = None
    if w is None or w < 0:
        if raw not in PENDING_WARNED:
            PENDING_WARNED.add(raw)
            print(f"hub: SG_PENDING_WINDOW_SECONDS={raw!r} is not a non-negative number: the pending window is "
                  f"{PENDING_WINDOW_DEFAULT:g} s", file=sys.stderr)
        return PENDING_WINDOW_DEFAULT
    return w
def overlay_on():
    """Whether the served board applies the door's record (`SG_PENDING_OVERLAY`, on by default). `off`, `0`, `false`
    or `no` serves the list read alone, so a suspected overlay defect is ruled out without a code change (#651)."""
    return (os.environ.get("SG_PENDING_OVERLAY") or "").strip().lower() not in ("0", "off", "false", "no")
def report_expired(n, item, recorded, read, window, context):
    """One line in the loop's log (`$SG_STATE/cycle.log`) for an entry that outlived its window with no list read
    agreeing (#651): the item, the column the door recorded and the column the read holds. A move with a dispatch context
    was drawn pending for the window; one without was drawn at the read's column at once (#861). It is called only for an
    entry the drop really removed, so the report is idempotent without process memory: the retry that succeeds reports
    once, and every later serve finds no entry to report."""
    if context:
        line = (f"{time.strftime('%T')} hub PENDING-EXPIRED #{n} (item {item}): the door recorded {recorded}, "
                f"the board read holds {read}; no read agreed within {window:g} s, so the entry was dropped")
    else:
        line = (f"{time.strftime('%T')} hub PENDING-EXPIRED #{n} (item {item}): the door recorded {recorded} with no dispatch "
                f"context, so the board drew the read's {read} at once; no read agreed within {window:g} s, so the entry was dropped")
    try:
        with (STATE / "cycle.log").open("a") as f: f.write(line + "\n")
    except OSError:
        pass   # the report is bookkeeping: the served board never fails for it
DROP_FAILURES = 0   # record drops this process whose write failed: the entry stays, and the next serve retries it
def drop_moves(ids):
    """Drop the entries the board read agreed with; every other entry stays (ADR-0030: formed whole beside the record,
    then one rename). The hub writes the record for nothing else (#650). An entry the door replaced while the board was
    read is kept: this pass drops nothing, and the next one drops what its own read agrees with. Returns the ids the
    record no longer holds: None when this pass wrote nothing, so an expiry is reported only for an entry really gone."""
    if not ids: return set()
    with MOVES_LOCK:
        try: before = MOVES.read_text(); moves = json.loads(before)
        except (OSError, ValueError): return None
        if not isinstance(moves, dict): return None
        left = {k: v for k, v in moves.items() if k not in ids}
        if len(left) == len(moves): return set()
        try:
            if MOVES.read_text() != before: return None   # the door wrote meanwhile: never lose a move this pass did not read
        except OSError: return None
        _tmp = _temp_name(MOVES)   # the temp name is the write's own, as save_board's is (#512)
        try:
            _tmp.write_text(json.dumps(left, sort_keys=True) + "\n"); os.replace(_tmp, MOVES)
        except OSError:
            global DROP_FAILURES
            DROP_FAILURES += 1   # the entry stays; the failure is counted, and the next serve retries (dropping is bookkeeping, never the served board)
            try: _tmp.unlink()   # a rename removes the temp; a failed write leaves it behind, and it is never the record
            except OSError: pass
            return None
        return set(moves) - set(left)
def _band_extents(bands):
    """Each band over one day, stacked from the last band of FLOW_BANDS at the foot: lower is the cards in the bands
    after it, upper adds its own."""
    extents, run = {}, 0
    for name in reversed(FLOW_BANDS):
        extents[name] = (run, run + bands[name])
        run += bands[name]
    return extents

def _y_ticks(top):
    """The numbered ticks of the y axis: from zero to the first step of 1, 2 or 5 times a power of ten that covers the
    tallest stack in at most four steps."""
    step = next(s for e in range(7) for s in (10 ** e, 2 * 10 ** e, 5 * 10 ** e) if -(-top // s) <= 4)
    return [k * step for k in range(-(-top // step) + 1)]

def _x_ticks(days):
    """Up to five date ticks, evenly spaced over the history's calendar days."""
    return [{"i": i, "day": days[i]} for i in sorted({round(k * (len(days) - 1) / 4) for k in range(5)})] if days else []

FLOW_MOVED = "moved"   # the label a card carries once it left for another board: no band of this board counts it (#1663)
FLOW_EXCLUDED = _leadtime.CHART_EXCLUDE   # the owner's label: a card it marks stays out of every flow chart (#2743)
CFD_RANGES = [("7", 7), ("30", 30), ("90", 90), ("all", None)]   # the cumulative flow's range views, in days (#901)
LEAD_TIMEFRAMES = [("12h", 0.5), ("1", 1), ("2", 2), ("3", 3), ("7", 7), ("14", 14), ("30", 30), ("all", None)]   # the control chart presets (#900); the four short ones count hours on the page (owner, 2026-11-01); 12h is the default (#2029)
LEAD_START = COLUMN["issue_todo"]   # the Lead Time view opens measured from Todo (#1247): the payload the page reads carries it

def bar_keys(i):
    """The values the board's bar filters one item by: its spec, its Waves key `spec/wave` (#980), its kind and its type (#904, #985)."""
    return {"spec": _flow.spec_key(i), "wave": f'{_flow.spec_key(i)}/{_flow.wave_key(i.get("l"))}', "kind": _flow.kind_key(i),
            "type": _flow.type_key(i)}

def lead_rows(a=None, b=None, now=None):
    """The rows the lead-time arithmetic reads, one per board item (#900). A row's hours are the flowing and held time of its
    column intervals in the history store, from its FIRST entry into `a` to its last entry into `b` (#1024, #1248). A closed
    card the store has not read is not_read, one that never entered `a` is no_start, and one with no entry into `b` is
    no_transition. A closed card the store read that never entered the view's DEFAULT start column (LEAD_START, Todo),
    whatever its Type, is measured from its first time on the board instead, because such a card can close without ever
    reaching the work column: a spec is a document and moves with its tickets (ADR-0142), and a bug or a task can be born
    or moved past it. The row carries `fallback` when it does (#1354), and only an entry into the row's end column counts:
    the fallback replaces the start the reader never reached, never the end they chose. The fallback belongs to the
    default start alone: a card that entered a user-chosen start column keeps its no_start exclusion, and so does a card
    the chosen start column never saw, because that choice is never silently replaced (#1248). No `a` is Created, the
    card's first time on the board; an empty `a` is that same selection, never a column named empty. Each read card
    carries its observation `obs` for the survival reading: an open card is observed to `now`, censored (#1250). A
    delivered row also carries `columns`, the hours it spent in each column, and `gate`, its Testing hours, for the
    chart's stack (#1256). The row's bar keys filter it (#904). A card carrying the owner's `chart:exclude` label, in
    its board read or in its history, is no dot and no observation: the chart excludes it under that reason (#2743)."""
    a, b = a or None, b or None   # a selection cleared to empty is the default, Created or closed, never a column name
    store = _hist.load_store(_hist.history_path(BOARD, REPO))
    now = now or dt.datetime.now(dt.UTC)
    rows = []
    for i in board_cache["items"]:
        closed = str(i["s"]).lower() == "closed"
        excluded = FLOW_EXCLUDED in (i.get("l") or [])   # #2743: the owner's label keeps the card out of every chart
        read = store.get(i["n"])
        if not excluded and read:   # the history half mirrors the cumulative flow's moved filter (#1663)
            excluded = FLOW_EXCLUDED in _hist.labels_now(read["events"])
        runs = _hist.intervals(read["events"]) if read else []
        obs = None if excluded else (_hist.lead_observation(runs, start=a, end=b, complete=read["complete"], now=None if closed else now) if read else None)
        span = obs if closed and obs and not obs["censored"] else None
        entered = None
        if not excluded and closed and read and span is None:   # the only rows whose reason and fallback read it: lazily, never per card
            entered = any((r["on_board"] if a is None else r["column"] == a) for r in runs)
        # #1354: the default view measures from Todo, but a card can close without ever entering it. Measure such a card
        # from its first time on the board, the Backlog wait included, and say so. The end column is the reader's, so the
        # card must have entered it: never measure a span the chart does not ask for. A user-chosen start keeps its
        # exclusion, and a chosen span is never silently replaced.
        fallback = False
        if not excluded and closed and read and obs is None and not entered and a in (None, LEAD_START):
            end = b or COLUMN["issue_closed"]
            if any(r["column"] == end for r in runs):
                on_board = _hist.board_time(runs, end=b, complete=read["complete"])
                if on_board is not None: obs, span, fallback = on_board, on_board, True
        if excluded: reason = FLOW_EXCLUDED
        elif _columns_module.not_planned(i.get("cr")): reason = _leadtime.NOT_DELIVERED
        elif closed and read is None: reason = _leadtime.NOT_READ
        elif closed and span is None: reason = _leadtime.NO_TRANSITION if entered else _leadtime.NO_START
        else: reason = None
        row = {"n": i["n"], "column": _columns_module.view_column(i["st"], closed, i.get("l"), i.get("cr"))[0], "reason": reason,
               "c": i.get("c") if closed else None, "o": i.get("o"), "t": i.get("t", ""), **bar_keys(i),
               "hours": 0.0, "flowing": 0.0, "held": 0.0, "approx": False, "unknown_first": False,
               "columns": None, "gate": 0.0, "fallback": fallback,
               "obs": None if obs is None else {"start_lo": obs["start_lo"], "start_hi": obs["start_hi"],
                                                "hours": obs["flowing"] + obs["held"], "censored": obs["censored"],
                                                "unknown_start": obs["unknown_first"]}}
        if span:
            row.update(c=span["end"], o=span["start"], hours=span["flowing"] + span["held"], flowing=span["flowing"],
                       held=span["held"], approx=span["approx"], unknown_first=span["unknown_first"],
                       columns=span["columns"], gate=span["gate"])
        rows.append(row)
    return rows

def board_filters(query):
    """The bar's Waves, Specs and Kinds values and the chart's Type from a query string, `fw=5/4,none/none&fs=5&fk=bug&ty=bug`
    (#904, #985): (fw, fs, fk, ty). An absent value is an empty list: all. An absent or unknown type is the default, tasks."""
    q = urllib.parse.parse_qs(query or "")
    def values(key): return [v for v in (q.get(key, [""])[0]).split(",") if v]
    ty = q.get("ty", [""])[0]
    return values("fw"), values("fs"), values("fk"), ty if ty in _flow.TYPES else _flow.DEFAULT_TYPE

def cfd_filters(query):
    """The cumulative flow's route values (#1356): the bar's (fw, fs, fk, ty), and no type at all when the extra `every=1`
    says every card. The `ty` parameter keeps its accepted values and its tasks default, so a reader that knows only the old
    parameter reads the chart it always read; All is the extra value beside it."""
    fw, fs, fk, ty = board_filters(query)
    return fw, fs, fk, None if urllib.parse.parse_qs(query or "").get("every", [""])[0] == "1" else ty

def _in_bar(row, fw, fs, fk=(), ty=None):
    """A row the bar keeps: its Waves key is among the waves named, its spec among the specs named, its kind among the kinds
    named, and its type is the chart's Type (#985). Empty lists keep all; no type keeps all."""
    return ((not fw or row["wave"] in fw) and (not fs or row["spec"] in fs) and (not fk or row["kind"] in fk)
            and (ty is None or row["type"] == ty))

def _lead_plot(query=None):
    """The plot frame the lead chart clusters in: the page names its rendered plot size in pixels (`pw` x `ph`) on a
    resize, and the default frame serves the first draw. Every view recomputes its clusters from this one size (#1331)."""
    default = {"width": _leadtime.PLOT_WIDTH, "height": _leadtime.PLOT_HEIGHT}
    if not query or "pw" not in query or "ph" not in query: return default
    try: width, height = round(float(query["pw"][0])), round(float(query["ph"][0]))
    except (ValueError, OverflowError): return default
    return {"width": width, "height": height} if 100 <= width <= 4000 and 60 <= height <= 4000 else default

def lead_zoom(query):
    """The control chart over a dragged range (`from` and `to`, ISO datetimes) or a timeframe preset (`tf`), measured
    from the start column `a` to the end column `b` (#905); the y axis is always log (#1355). An old `scale` value in the
    query changes nothing: the route takes no scale option and serves the log axis (#1355). No `a` is the trip from `Todo`,
    the column a card starts its work in; an empty `a` is the card's opened instant. No `b`, or the closed column, is its
    closed instant. The bar's values in the query filter its rows (#904)."""
    q = urllib.parse.parse_qs(query, keep_blank_values=True)   # an empty `a` is the reader's own "Created", never the default
    q.pop("scale", None)   # #1355: an old bookmark's `scale=jira` is a documented no-op; every chart is log, and no `scale` key stays in the payload
    a = (q.get("a") or [None])[0]
    if a is None: a = LEAD_START   # the page opens on the trip from Todo, the column the payload already measures from
    b = (q.get("b") or [""])[0] or None
    a, b = _columns_module.recorded_column(a), _columns_module.recorded_column(b)   # a bookmark keeps the name it recorded, so a version 1 name reads its column (#2151, #2190)
    if b == COLUMN["issue_closed"]: b = None
    if any(c is not None and c not in ORDER for c in (a, b)): return {"error": "bad column: a and b name a board column"}
    fw, fs, fk, ty = board_filters(query)
    now = dt.datetime.now(dt.UTC)
    rows = [r for r in lead_rows(a, b, now) if _in_bar(r, fw, fs, fk, ty)]
    measure = f"{a or 'opened'} -> {b or 'closed'}"
    if "from" not in q and "to" not in q and "tf" in q:
        days = dict(LEAD_TIMEFRAMES).get(q["tf"][0], 0)
        if days == 0: return {"error": "bad timeframe"}
        return _leadtime.control_chart(rows, timeframe=days, now=now, measure=measure, plot=_lead_plot(q))
    try:
        start, end = (dt.datetime.fromisoformat(q[k][0]) for k in ("from", "to"))
    except (KeyError, IndexError, ValueError):
        return {"error": "bad range: from and to are ISO datetimes"}
    start, end = (m if m.tzinfo else m.replace(tzinfo=dt.UTC) for m in (start, end))
    if end <= start: return {"error": "bad range: to is not after from"}
    return _leadtime.control_chart(rows, now=now, start=start, end=end, measure=measure, plot=_lead_plot(q))

CHARTS = {}   # each chart route's last answer, with the key of the inputs it read (#971)

def chart(name, key, build):
    hit = CHARTS.get(name)
    if hit and hit[0] == key: return hit[1]
    t = time.time()   # the instant the chart reads its data: a cached answer keeps it, however late it is served
    answer = {"t": t, **build()}
    if not _read_failed(answer): CHARTS[name] = (key, answer)
    return answer

def _read_failed(answer):
    """True when a chart answer names a failed read, so the next request reads anew (#1072)."""
    return "err" in answer or "err" in (answer.get("burn") or {})

def _file_key(path):
    try: st = path.stat(); return (str(path), st.st_mtime_ns, st.st_size)
    except OSError: return (str(path), None)

def history_key():
    return _file_key(_hist.history_path(BOARD, REPO))

def _coverage_payload(store):
    """How much of the board the history store has read: the board's cards it holds, their complete timelines, and the last refresh."""
    numbers = {i["n"] for i in board_cache["items"]}
    c = _hist.coverage({n: r for n, r in store.items() if n in numbers})
    return {"read": c["read"], "complete": c["complete"], "total": len(board_cache["items"]),
            "last": _hist._iso(c["last"]) if c["last"] is not None else None}

def _cfd_payload(fw=(), fs=(), fk=(), ty=_flow.DEFAULT_TYPE):
    """The cumulative flow's numbers, counted per day by board_cfd from the history store's column intervals; the page draws
    them and computes none. `fw`, `fs`, `fk` and `ty` are the bar's values: the chart keeps only the cards the bar keeps (#904, #1024)."""
    store = _hist.load_store(_hist.history_path(BOARD, REPO))
    columns_key = _file_key(pathlib.Path(__file__).resolve().parent / "board_columns.py")   # the column rules each fold reads
    cards = [_hist.intervals(rec.get("events") or [], (rec.get("n"), rec.get("t"), columns_key)) for rec in store.values()
             if (rec.get("keys") is None or _in_bar(rec["keys"], fw, fs, fk, ty))
             and not (_hist.labels_now(rec.get("events") or []) & {FLOW_MOVED, FLOW_EXCLUDED})]   # a moved card is another board's work (#1663); the owner's label is no band either (#2743)
    zone = day_zone()
    cfd = _cfd.cfd(cards, ORDER, zone=zone, now=dt.datetime.now(dt.UTC))
    extents = {p["day"]: _band_extents(p["bands"]) for p in cfd["points"]}
    days = [p["day"] for p in cfd["points"]]
    points = {p["day"]: p for p in cfd["points"]}
    coverage = _coverage_payload(store)
    def values(name, k): return [extents[d][name][k] for d in days]
    def view(width):
        """The chart over the last `width` calendar days (all of them when None): the same arrays, cut to the window (#901)."""
        window = days if width is None else days[-width:]
        top = max((extents[d][FLOW_BANDS[0]][1] for d in window), default=0)
        ticks = _y_ticks(top)
        def cut(arr): return [arr[days.index(d)] for d in window]
        def band(name):
            lower, held = cut(values(name, 0)), [points[d]["held"][name] for d in window]
            return {"name": name, "color": BOARD_COLORS.get(name, "var(--chart-series-fallback)"),
                    "lower": lower, "upper": cut(values(name, 1)),
                    "count": [extents[d][name][1] - extents[d][name][0] for d in window], "held": held,
                    "held_upper": [lo + h for lo, h in zip(lower, held, strict=True)]}   # the role rides with the band, so the phone names no column (#2504)
        def read(d):
            """One day's reading, whole: the page prints it on that day's hover and indexes no series for it (#1149)."""
            return {"day": d, "counts": [{"name": n, "count": extents[d][n][1] - extents[d][n][0]} for n in FLOW_BANDS],
                    "wip": points[d]["wip"], "throughput": points[d]["throughput"]}
        last = window[-1] if window else None   # the hub chooses the last day; the page prints its two figures (#1149)
        return {"start": dt.datetime.fromtimestamp(cfd["start"], dt.UTC).date().isoformat() if cfd["start"] is not None else None,
                "days": window, "ymax": ticks[-1], "yticks": ticks, "xticks": _x_ticks(window), "coverage": coverage, "zone": zone,
                "reads": [read(d) for d in window],
                "last": None if last is None else {"day": last, "wip": points[last]["wip"], "throughput": points[last]["throughput"]},
                "bands": [band(n) for n in FLOW_BANDS]}
    return {"cfd": {**view(None), "zone": zone, "ranges": {key: view(width) for key, width in CFD_RANGES}}}

def _lead_payload(fw=(), fs=(), fk=(), ty=_flow.DEFAULT_TYPE):
    """The lead-time numbers of the board read, measured from LEAD_START, the view's default start (#1247), computed here;
    the page draws them and computes none. The bar's values `fw`, `fs` and `fk` filter the rows (#904)."""
    now = dt.datetime.now(dt.UTC)
    rows = [r for r in lead_rows(LEAD_START, None, now) if _in_bar(r, fw, fs, fk, ty)]
    measure = f"{LEAD_START} -> closed"
    lead = _leadtime.lead_time(rows, now=now, measure=measure)
    plot = _lead_plot()
    control = {key: _leadtime.control_chart(rows, timeframe=days, now=now, measure=measure, plot=plot)
               for key, days in LEAD_TIMEFRAMES}   # the control chart, one per timeframe, always on the log axis (#900, #1355)
    coverage = _coverage_payload(_hist.load_store(_hist.history_path(BOARD, REPO)))
    return {"plot_frame": plot,
          "lead": {"measure": lead["measure"], "type": ty, "window": lead["window"], "start": LEAD_START,
                     "excluded": [{"reason": r, "count": c} for r, c in lead["excluded"].items()],
                     "excluded_cards": lead["excluded_cards"],
                     "excluded_text": _leadtime.excluded_text(lead["excluded"]),
                     "excluded_reasons": _leadtime.exclusion_words(lead["excluded"]),
                     "excluded_total": sum(lead["excluded"].values()), "series": lead["series"], "summary": lead["summary"],
                     "control": control, "columns": ORDER, "coverage": coverage}}

def flow_cfd(fw=(), fs=(), fk=(), ty=_flow.DEFAULT_TYPE):
    """The cumulative flow for the bar's values and the Type; the cache key holds them, so a change reads anew (#904, #985)."""
    return chart("cfd", (history_key(), tuple(fw), tuple(fs), tuple(fk), ty), lambda: _cfd_payload(fw, fs, fk, ty))

def flow_lead(fw=(), fs=(), fk=(), ty=_flow.DEFAULT_TYPE):
    return chart("lead", (board_cache.get("t"), history_key(), tuple(fw), tuple(fs), tuple(fk), ty), lambda: _lead_payload(fw, fs, fk, ty))

HISTORY_EVERY = 60   # seconds between history passes; a pass reads only the timelines that are due (#1024)
HISTORY_TIMELINE = ("query($owner:String!,$name:String!,$number:Int!,$after:String){rateLimit{cost}"
                    "repository(owner:$owner,name:$name){issue(number:$number){number updatedAt "
                    "projectItems(first:20){nodes{project{number} fieldValueByName(name:\"Status\"){... on ProjectV2ItemFieldSingleSelectValue{name updatedAt}}}} "
                    "timelineItems(first:100,after:$after){pageInfo{hasNextPage endCursor} nodes{__typename "
                    "... on ProjectV2ItemStatusChangedEvent{createdAt status} "
                    "... on LabeledEvent{createdAt label{name}} ... on UnlabeledEvent{createdAt label{name}} "
                    "... on ClosedEvent{createdAt} ... on ReopenedEvent{createdAt} "
                    "... on AddedToProjectV2Event{createdAt project{number}} ... on RemovedFromProjectV2Event{createdAt project{number}} "
                    "... on ParentIssueAddedEvent{createdAt parent{... on Issue{number}}} ... on ParentIssueRemovedEvent{createdAt}}}}}}")
HISTORY_SEARCH = ("query($q:String!,$after:String){search(query:$q,type:ISSUE,first:100,after:$after){issueCount "
                  "pageInfo{hasNextPage endCursor} nodes{... on Issue{number}}}}")

def day_zone():
    """The zone a chart day ends in: the TZ the hub runs under, else the machine's zone, else UTC (#1024)."""
    name = os.environ.get("TZ") or os.path.realpath("/etc/localtime").partition("zoneinfo/")[2] or "UTC"
    try: return zoneinfo.ZoneInfo(name).key
    except (zoneinfo.ZoneInfoNotFoundError, ValueError): return "UTC"

def _history_timeline(n, cursor):
    owner, _, name = REPO.partition("/")
    return gh_graphql(HISTORY_TIMELINE, owner=owner, name=name, number=int(n), **({"after": cursor} if cursor else {}))

def _history_search():
    """Every issue number the repository holds, page by page. None when a page answered nothing, or when the pages fall short
    of the repository's count: GitHub caps a search at 1000 results, and a capped search reads no absence (#1024)."""
    found, after = [], None
    while True:
        search = (((gh_graphql(HISTORY_SEARCH, q=f"repo:{REPO} is:issue", **({"after": after} if after else {})) or {})
                   .get("data") or {}).get("search"))
        if search is None: return None
        found += [node["number"] for node in search["nodes"] if node.get("number")]
        if not search["pageInfo"]["hasNextPage"]:
            return found if len(found) == search["issueCount"] else None
        after = search["pageInfo"]["endCursor"]

def _history_due(store, items):
    """The cards a pass reads: no timeline yet, a partial one, one whose Status moved past its record, or one whose
    closed state disagrees with its record (#1024, #1419). The rows carry the issue's own state (`s`), never the card's
    Status (`st`), so a close through the API is a read reason even when the card never moved."""
    return _hist.due_items(items, store)

def _read_timeline(n, prior, keys, read_issue, path, recs, now):
    """Read one card's timeline into the store. A partial timeline resumes from its cursor; a complete one is read from the
    start and replaces its record only once read whole, so a cut-short read never truncates it. False when the budget ran
    out or a read failed, which ends the pass."""
    work = {n: prior} if prior and not prior.get("complete") else {}
    ok = True
    while not (n in work and work[n]["complete"]):
        if not _hist.allow(recs, now, 1)[0]:
            ok = False
            break
        payload = read_issue(n, work[n]["cursor"] if n in work else None)
        if payload is None:
            ok = False
            break
        data = payload.get("data") or {}
        recs.append(_hist.record_points(path, (data.get("rateLimit") or {}).get("cost", 1), now))
        issue = (data.get("repository") or {}).get("issue")
        if issue is None: return True
        facts = _hist.item_facts(issue, int(PROJECT_NO))
        _hist.record_page(work, n, issue["timelineItems"], facts["issue_updated"], facts["status_updated"],
                         project=int(PROJECT_NO))
    rec = work.get(n)
    if rec and (prior is None or not prior.get("complete") or rec["complete"]):
        recs.append(_hist.record_issue(path, n, rec["issue_updated"], rec["status_updated"], rec["events"], rec["complete"], now,
                                       rec["cursor"], keys))
    return ok

def history_pass(read_issue=None, read_search=None, now=None):
    """Read the timelines due and the hourly search into the history store. A read the budget refuses ends the pass; the next
    pass resumes a partial timeline from its cursor (#1024)."""
    read_issue, read_search = read_issue or _history_timeline, read_search or _history_search
    path = _hist.history_path(BOARD, REPO)
    now = time.time() if now is None else now
    store, recs, items = _hist.load_store(path), _hist.read_records(path), board_cache.get("items") or []
    keys = {i["n"]: bar_keys(i) for i in items}
    try:
        for n in _history_due(store, items):
            if not _read_timeline(n, store.get(n), keys[n], read_issue, path, recs, now): return
        if _hist.search_due(recs, now):
            found = read_search()
            _hist.record_search(path, found, now)
            # #1193: the search names every number the repository holds, and the board cache does not. A card the board read
            # missed -- the window in which the frontier logged "the board read carries no card for it" -- still has a
            # timeline, and that timeline carries the closed event the throughput counts. So a number the search returned
            # and the store holds no complete record of is due in this pass. The points budget bounds the sweep as it bounds
            # a pass, and a partial record resumes from its cursor.
            if found:
                stored, on_board = _hist.load_store(path), {i["n"] for i in items}
                for n in found:
                    rec = stored.get(n)
                    if n in on_board or (rec or {}).get("complete"): continue
                    if not _read_timeline(n, rec, None, read_issue, path, recs, now): return
    finally:
        _hist.compact(path, now)

def refresh_history():
    """history_pass() every HISTORY_EVERY seconds while a page reads this profile (#1024, #1790); one failed pass never
    ends the loop."""
    while True:
        if read_wanted():
            try: history_pass()
            except Exception as e: print(f"history pass failed: {e}", file=sys.stderr)
        read_pause(HISTORY_EVERY)

PREDICT_DEFECT = "BOARD-PENDING-OLD"   # the name a prediction past its bound is reported as (#1841)
PREDICTED_SEEN = STATE / "board-predictions.json"   # issue -> the instant of the prediction the loop's log already named
def predictions():
    """The card writes the issue ledger holds pending for this profile's repository, by issue number (#1841): each one
    carries its column, the instant the writer recorded it, and the writer's name. This is a read of the writer's own fact,
    never a second writer (ADR-0149): only that writer's `boardw` or `boardx` ends a prediction. A state directory the
    ledger cannot read predicts nothing, and one stderr line names it: a missing reading is not a reading of nothing."""
    try: rows = issue_ledger.predictions(REPO)
    except (RuntimeError, OSError, ValueError) as e:
        print(f"hub: the ledger's predictions do not read ({e}): the board is the list read alone", file=sys.stderr)
        return {}
    return {int(r["n"]): r for r in rows}

def predicted_draw(pred, read_column, read_at, window, closed, labels, cr):
    """The draw of one prediction (#1841): the column the writer named, the freeze that column carries, the pending mark,
    and the named defect when no read carried the write within its bound. None when the column is not one this board
    ships, or when the read began after the write and already carries its column: then the board draws the read.
    `read_at` is the instant the read that observed the card began; for a card the cache lacks it is the pass's own
    instant, because the whole read is what failed to carry it. The bound is `window`, the setting's own seconds, measured
    on the read's clock as the proved moves' window is, so a cache that stopped updating never expires a prediction."""
    recorded = _columns_module.canonical(_columns_module.SHIPPED_NAME.get(pred["column"], pred["column"]))   # any spelling reads as its column (#2315)
    if recorded not in ORDER: return None   # a column this board does not ship is never drawn
    at = pred.get("instant")
    carried = isinstance(at, (int, float)) and read_at is not None and at < read_at
    if carried and recorded == read_column: return None   # the read proves the write, however old it is
    defect = PREDICT_DEFECT if carried and read_at - at >= window else None
    return {"st": recorded, "fz": _columns_module.view_column(recorded, closed, labels, cr)[1], "state": "pending",
            "recorded": recorded, "entry": None, "defect": defect, "at": at}

def report_predictions(defects):
    """One line in the loop's log for each prediction that outlived its bound (#1841): the issue, the writer, the column
    it recorded and the bound. It is written once per issue and instant, so a page reload names it no second time; the
    report is bookkeeping, and a state directory that refuses it never costs the served board."""
    if not defects: return
    with MOVES_LOCK:
        try: seen = json.loads(PREDICTED_SEEN.read_text())
        except (OSError, ValueError): seen = {}
        if not isinstance(seen, dict): seen = {}
        fresh = [d for d in defects if seen.get(str(d["n"])) != d["at"]]
        if not fresh: return
        for d in fresh: seen[str(d["n"])] = d["at"]
        _tmp = _temp_name(PREDICTED_SEEN)
        try:
            _tmp.write_text(json.dumps(seen, sort_keys=True) + "\n"); os.replace(_tmp, PREDICTED_SEEN)
        except OSError:
            try: _tmp.unlink()
            except OSError: pass
            return
    try:
        with (STATE / "cycle.log").open("a") as f:
            for d in fresh:
                f.write(f"{time.strftime('%T')} hub {d['defect']} #{d['n']}: {d['by'] or 'a writer'} recorded {d['column']} "
                        f"and no read carried it within {d['window']:g} s; the card stays drawn pending\n")
    except OSError:
        pass   # the report is bookkeeping: the served board never fails for it

def drawn_column(i, moves, window):
    """The column the served board draws for one row, the freeze that column carries, and what the serve acts on:
    `read` (no entry names a column this board ships, or the read's column stands), `agree` (the read began after the
    move and carries its column), `expired` (the window passed with no read agreeing), `settled` (the writer proved the
    move the hub sent: drawn at once with no mark, the entry kept as the column's guard, #1300) or `pending` (the move is
    drawn at once and wears the mark). The board and the promote route ask this one function, so a card is promotable by the
    column both of them draw, never by two different columns (#829, ADR-0144)."""
    read_at = i.get("ot")   # the instant the read that observed this card began: the only stamp the overlay compares a move against
    closed = str(i["s"]).lower() == "closed"   # the row's own closedness: the freeze below is the card's, not the read's
    st, fz = _columns_module.view_column(i["st"], closed, i.get("l"), i.get("cr"))   # the column the table names, and the freeze over it
    entry = moves.get(str(i["i"])) if i.get("i") else None   # the door keys an entry by the Project item id
    if not (isinstance(entry, dict) and entry.get("column")):
        return {"st": st, "fz": fz, "state": "read", "recorded": None, "entry": None}
    recorded = _columns_module.canonical(_columns_module.SHIPPED_NAME.get(entry["column"], entry["column"]))   # an old Status name reads as its shipped column (#2315)
    if recorded not in ORDER:   # no column this board ships: the entry is never drawn, and the read says nothing about it, so the hub leaves it (never writes the record for it)
        return {"st": st, "fz": fz, "state": "read", "recorded": recorded, "entry": entry}
    at = move_time(entry)   # the instant the door proved the move, in whole epoch seconds
    if recorded == st and read_carried(at, read_at):   # the read began after the move and carries its column: the entry's work is done, however old it is
        return {"st": st, "fz": fz, "state": "agree", "recorded": recorded, "entry": entry}
    if at is not None and read_at is not None and read_at - at >= window:
        # The move is past its window at the read's own instant with no agreement: the read's column stands and the entry
        # is dropped. The window is the read's clock, never the wall clock, so a cache that stopped updating never expires
        # a move no read has had a window to agree with (#651).
        return {"st": st, "fz": fz, "state": "expired", "recorded": recorded, "entry": entry}
    if read_carried(at, read_at) and not has_context(entry):
        return {"st": st, "fz": fz, "state": "read", "recorded": recorded, "entry": entry}   # a move with no dispatch context stands aside for a read that began after it
    # The writer proved the move the hub sent (#1300): the column stands with no mark, and the entry stays as the column's
    # guard against a lagging read until a read agrees or the window passes.
    if closed and recorded not in (COLUMN["issue_closed"], COLUMN["abandoned"]) and read_carried(at, read_at):   # the latest read closed the card after the move: the closure wins (#1661)
        return {"st": st, "fz": fz, "state": "read", "recorded": recorded, "entry": entry}
    state = "settled" if proof_confirmed(i["i"], recorded, entry) else "pending"
    # Draw the move at once and keep the mark until a read that began after it agrees (#650). A move with a dispatch
    # context keeps the mark through a contradicting read too, for the whole window. A column that matches by coincidence is no proof.
    return {"st": recorded, "fz": _columns_module.view_column(recorded, closed, i.get("l"), i.get("cr"))[1], "state": state,
            "recorded": recorded, "entry": entry}

def flight_draw(i):
    """The draw of a card whose write is in flight: the column the write goes to, marked pending until the writer answers
    (#1300). The freeze is the column's, as a pending move's is (ADR-0144)."""
    closed = str(i["s"]).lower() == "closed"
    return {"st": PROMOTE_TO, "fz": _columns_module.view_column(PROMOTE_TO, closed, i.get("l"), i.get("cr"))[1], "state": "pending",
            "recorded": PROMOTE_TO, "entry": None}

def live_hand_holds(state=None, now=None):
    """The issue numbers a live hand hold holds, read once per board serve (#2533, #2501). The phone's `hand hold` chip is
    this fact, so the page reads it off the board row it already has, never a second route."""
    base = pathlib.Path(state) if state else STATE
    closed = {i["n"] for i in board_cache.get("items", []) if i.get("s") == "closed"}
    try:
        return {h["issue"] for h in hand_holds(base, closed=closed, now=now) if h["live"]}
    except Exception as e:
        log_once(f"hub: the hand holds are unknown: {type(e).__name__}: {' '.join(str(e).split())}")
        return set()

def spec_plan_parents(items):
    """The spec numbers whose batch plan holds a ticket: an open sub-issue the board cache holds that is no spec and no held
    ticket (#1128). The board draws its start control from this cache fact, so a render never reads the sub-issues."""
    return {i["pa"] for i in items if isinstance(i.get("pa"), int) and str(i.get("s") or "").lower() != "closed"
            and _columns_module.SPEC_LABEL not in (i.get("l") or []) and not set(i.get("l") or []) & set(TICKET_HOLD_LABELS)}
