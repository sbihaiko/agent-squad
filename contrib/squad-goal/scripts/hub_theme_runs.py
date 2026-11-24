# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
# ---------------------------------------------------------------- the childcc pass (#598)
# The pass writes auto/childcc-now.json (childcc.py): its pid and start, and one entry per child it holds, with the step it is
# at (review, enter, deferred) and since when it waits. The hub reads that file and nothing else of the pass, and shows it as
# one row of the WORKERS group: the pid and age, one line per child, and the deferred count with the oldest wait.
def _pid_alive(pid):
    try: os.kill(int(pid), 0)
    except (TypeError, ValueError, ProcessLookupError, OverflowError): return False
    except PermissionError: return True
    return True

def _childcc_line(c):
    who = f"{c.get('run')} {c.get('child')} #{c.get('issue')}"
    if c.get("step") == "review":
        model = _read(STATE / f"review-{c['rk']}.model").strip() if c.get("rk") else ""
        return f"{who} · in review" + (f" with {model}" if model else "")
    return f"{who} · " + ("entering wave" if c.get("step") == "enter" else "deferred")

def childcc_row(now=None):
    """The one WORKERS row of the childcc pass, or None when no pass is live and no child waits. With no live pass every
    child the last pass left still waits, whatever step it was at."""
    now = time.time() if now is None else now
    d = _json(STATE / "auto" / "childcc-now.json")
    if not isinstance(d, dict): return None
    kids = [c for c in d.get("children") or [] if isinstance(c, dict)]
    live = bool(d.get("pid")) and _pid_alive(d["pid"])
    waits = [c for c in kids if c.get("step") == "deferred" or not live]
    if not live and not waits: return None
    oldest = int(max(now - float(c.get("since") or now) for c in waits)) if waits else 0
    sub = f"CHILDCC DEFERRED {len(waits)} · oldest wait {_age(oldest)}" if waits else ""
    age = int(now - float(d.get("t0") or now)) if live else oldest
    return {"id": "childcc", "port": "", "kind": "childcc", "live": live, "rank": 0, "state": "running" if live else "waiting",
            "badge": "childcc" if live else "waiting", "tip": "the childcc pass: one review and one wave entry at a time",
            "label": f"childcc pass · pid {d['pid']}" if live else f"CHILDCC DEFERRED {len(waits)}", "issues": [], "pid": d["pid"] if live else None,
            "age": age, "dur": round(age / 60, 1), "spend": 0, "agents": 0, "project": "childcc", "sub": sub,
            "lines": [_childcc_line(c) for c in kids] if live else [], "deferred": len(waits), "oldest_wait": oldest,
            "held": _held_now(kids)}

def _held_now(kids):
    """The run the pass holds now: the child at review or wave entry, else the first child it holds. A click on the row opens
    that run (#689); the row itself is never a pane (#598)."""
    now = [c for c in kids if c.get("step") in ("review", "enter")] or kids
    return next((str(c["run"]) for c in now if c.get("run")), None)

def active_entries():
    """The runs the loop registered this pass, in the file's order, each once: what the Runs tab lists beside a run with a
    server and a run the childcc pass holds (#1649)."""
    try: rows = [r.strip() for r in (STATE / "active-runs.txt").read_text().splitlines() if r.strip()]
    except OSError: return []
    return list(dict.fromkeys(rows))

def active_folder(entry):
    """The folder of one active-runs entry: an absolute run folder, or a name under the checkout's runs (the one reader's
    rule, squad/run_liveness.py record_path). None when the entry does not resolve to a record."""
    try: folder = pathlib.Path(liveness().record_path(entry, CHECKOUT).parent)
    except Exception: return None
    return str(folder) if (folder / "record.jsonl").is_file() else None

def held_runs():
    """The run names the childcc pass holds, in the file's order, each once."""
    d = _json(STATE / "auto" / "childcc-now.json")
    kids = d.get("children") or [] if isinstance(d, dict) else []
    return list(dict.fromkeys(str(c["run"]) for c in kids if isinstance(c, dict) and re.fullmatch(r"run-[\w-]+", str(c.get("run") or ""))))

def held_folder(name):
    """The folder of a run the childcc pass holds, from the runs directories the hub knows (project_dirs), or None. Only a
    held run resolves: the name is never a path the page chose."""
    if name not in held_runs(): return None
    return next((str(d / name) for d in sorted(project_dirs()) if (d / name / "record.jsonl").is_file()), None)

def open_held(name, start=None):
    """The port of a dashboard server for a held run, started when none runs (dashboard_server.start), or None. A held run
    orphaned from active-runs has no server, so the hub starts one when the owner opens it (#689)."""
    return _open_run(name, held_folder(name), start)

def ended_folders():
    """(folder, row) of each run folder whose record ended on the machine's local day, latest end first. A run that ended today
    wrote its record today, so a record last written before midnight is not read."""
    today = dt.date.today(); now = time.time(); midnight = dt.datetime.combine(today, dt.time()).timestamp(); out = []
    for d in sorted(project_dirs()):
        for rd in d.glob("run-*"):
            try:
                if (rd / "record.jsonl").stat().st_mtime < midnight: continue
            except OSError: continue
            r = scan(rd / "record.jsonl")
            if not r or r.get("verdict") is None or not r.get("t1"): continue
            ended = float(r["t1"])
            if dt.date.fromtimestamp(ended) != today: continue
            row, _ = run_row(str(rd), "", [])
            out.append((str(rd), {**row, "live": False, "running": False, "ended_at": ended, "age": max(0, int(now - ended))}))
    return sorted(out, key=lambda x: -x[1]["ended_at"])

def ended_today():
    """The runs that ended on the machine's local day, latest end first, each with its end time."""
    return [row for _, row in ended_folders()]

def issue_url(n, repo=None):
    """The plain issue address on github.com, the form iOS opens in the GitHub app: no query string."""
    return f"https://github.com/{repo or REPO}/issues/{int(n)}"

_PENDING_WINDOW = [0]    # int: one walk of the pending read is one poll window, and its issue-state memory is keyed on it (#2661)
_ISSUE_STATE_KEPT = {}   # (repo, n) -> (window id, the issue's state or None): the pending read's memory for one window (#2661)

def github_issue_state(n, repo=None, window=None):
    """The issue's own state from one GitHub REST read: `closed`, `open`, or None when the read did not answer. The answer
    belongs to one pending poll window: a later window never serves it, and one window reads each issue at most once, so
    the served answer is the issue's state as of this window (#2661)."""
    repo = repo or REPO
    key = (repo.lower(), int(n)); window = _PENDING_WINDOW[0] if window is None else window
    hit = _ISSUE_STATE_KEPT.get(key)
    if hit is not None and hit[0] == window: return hit[1]
    try:
        r = ghread.read(f"repos/{repo}/issues/{int(n)}",
                        lambda route: ghread.gh_transport(route, timeout=REST_TIMEOUT))
        state = str((r.answer or {}).get("state") or "").lower() if r.is_answer and isinstance(r.answer, dict) else None
    except Exception as e:                       # a read that does not answer is no closed state: the decision stays pending
        log_once(f"hub: issue {int(n)} state read failed: {e}")
        state = None
    state = state if state in ("open", "closed") else None
    _ISSUE_STATE_KEPT[key] = (window, state)
    return state

def settled_on_board(r, window=None):
    """True when the board settles the run's decision: its work landed (handled) or every issue it names is closed
    (verified by hand, evidence on the issue). The pending read asks this itself, so a row leaves Needs you even when
    the run row's own summary cannot see the board. The board cache holds only the items the REST items route returns, so
    an issue it does not hold is read from GitHub, once per poll window (#2661)."""
    landed = {p["n"] for p in board_cache.get("landed") or [] if p.get("m")}
    if r.get("fix") in landed: return True
    issues = {int(n) for n in r.get("issues") or []}
    if not issues: return False
    items = {i["n"]: i for i in board_cache.get("items") or [] if isinstance(i.get("n"), int)}
    closed = {n for n, i in items.items() if i.get("s") == "closed"}
    for n in sorted(issues - set(items)):             # off the cache (another project, or a missed item): ask GitHub
        if github_issue_state(n, window=window) == "closed": closed.add(n)
    return issues <= closed or bool(issues & landed)

def pending_decisions():
    """Every run that ended needs-human and still waits for the owner, across every date, the latest end first (SPEC 2498).

    The ended-runs read covers the local day only, so a decision the owner did not answer yesterday waits here today. The
    record's final verdict is the last invocation's: a run resumed after its verdict has no verdict and is live again, and
    one reclassified by a later verdict ended another way, so neither reaches this read. A run whose verdict was later
    handled (its work landed) or verified (every issue was closed afterwards) is not pending. One run gives one row."""
    _PENDING_WINDOW[0] += 1                         # one walk is one poll window: an issue-state answer never leaves its walk (#2661)
    _ISSUE_STATE_KEPT.clear()
    window, now, out, seen = _PENDING_WINDOW[0], time.time(), [], set()
    for d in sorted(project_dirs()):
        for rd in sorted(d.glob("run-*")):
            if rd.name in seen or not (rd / "record.jsonl").is_file(): continue
            r = scan(rd / "record.jsonl")
            if not r or r.get("verdict") != "needs-human": continue
            row, _ = run_row(str(rd), "", [])
            if row.get("state") != "needs-human": continue
            if settled_on_board(r, window): continue
            seen.add(rd.name)
            n = next((int(x) for x in r.get("issues") or []), None)
            ended = float(r.get("t1") or now)
            out.append({"id": row["id"], "label": row.get("label") or row["id"], "chip": "needs a decision",
                        "issue": n, "url": issue_url(n) if n is not None else None,
                        "spend": row.get("spend") or 0, "age": max(0, int(now - ended)), "ended_at": ended})
    return sorted(out, key=lambda x: -x["ended_at"])

PENDING_POLL_SECS = 15.0   # seconds: every open page shares one walk of the run folders; a decision waits days, not seconds
_PENDING_KEPT = {"at": None, "answer": None}
_PENDING_LOCK = threading.Lock()

def pending_served():
    """The pending decisions, computed at most once per the page's poll window: any number of readers share one read."""
    now = time.monotonic()
    with _PENDING_LOCK:
        at = _PENDING_KEPT["at"]
        if at is not None and now - at < PENDING_POLL_SECS: return _PENDING_KEPT["answer"]
        answer = pending_decisions()
        _PENDING_KEPT.update(at=now, answer=answer)
        return answer

def open_ended(name, start=None):
    """The port of a dashboard server for a run that ended today, or for a registered run with no server of its own, or None:
    the page opens a serverless row through this route, and a live run is registered long before it ends (#1649).
    dashboard_server.start reuses a server that runs (ADR-0118)."""
    folder = next((folder for folder, row in ended_folders() if row["id"] == name), None)
    if folder is None and re.fullmatch(r"run-[\w-]+", name or ""):
        folder = next((folder for folder in map(active_folder, active_entries()) if folder and pathlib.Path(folder).name == name), None)   # a registered run has no history row until tomorrow (#1649)
    return _open_run(name, folder, start)

def _open_run(name, folder, start):
    if folder is None: return None
    if start is None: start = _start_dashboard
    try: return int(start(folder))
    except Exception as e:
        log_once(f"hub: no dashboard server for {name}: {e}"); return None

def _start_dashboard(folder):
    """dashboard_server.start(folder) in a child interpreter, from the first root that holds the squad package."""
    roots = [r for r in squad_roots(SQUAD_ROOT, os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__) if (pathlib.Path(r) / "squad" / "dashboard_server.py").is_file()]
    if not roots: raise RuntimeError("no squad package")
    py = pathlib.Path(roots[0]) / ".venv" / "bin" / "python"
    p = subprocess.run([str(py) if py.exists() else sys.executable, "-c", "import sys; from squad import dashboard_server as d; print(d.start(sys.argv[1]))", folder],
                       cwd=roots[0], capture_output=True, text=True, timeout=30)
    if p.returncode != 0: raise RuntimeError((p.stderr or "").strip().splitlines()[-1:] or "exit " + str(p.returncode))
    return int(p.stdout.split()[-1])

UNREADABLE_TIP = "the record read empty on this poll: the figures are from the last read that had an event"
READ_FIGURES = {}   # run folder -> (the last scan that had an event, the record's write time then)

def run_row(d, port, procs):
    """One run folder's row of the list and its load-attribution entry (None while the record is empty). `port` is its
    dashboard server's, or "" when it has none. `procs` is the process reading (`read_procs`), or empty when there is none."""
    p = pathlib.Path(d) / "record.jsonl"
    r = scan(p)
    unreadable = r is None or r["t0"] is None
    if unreadable and d not in READ_FIGURES:
        b = badge_of("starting")
        return {"id": os.path.basename(d), "port": port, "state": "starting", "badge": b["text"], "tip": b["title"], "tone": b["color"], "mark": b["mark"], "label": "run " + os.path.basename(d)[-6:], "issues": [], "age": 0, "project": os.path.basename(os.path.dirname(os.path.dirname(d))), "rank": 0, "spend": 0, "dur": 0, "kind": "code", "live": True, "running": True, "agents": 0}, None
    if unreadable: r, written = READ_FIGURES[d]
    else: written = p.stat().st_mtime; READ_FIGURES[d] = (r, written)
    age = time.time() - written; st = state_of(r, age)
    gone = run_gone(r, age, procs if isinstance(procs, dict) else {})   # killed: no verdict will come, so it is not live whatever its age
    st = demoted(st, r, {i["n"] for i in board_cache.get("items", []) if i.get("s") == "closed"})
    merged = {p["n"] for p in board_cache.get("landed", []) if p.get("m")}
    st = handled_of(st, {**r, "merged": r.get("fix") in merged or any(int(n) in merged for n in r["issues"])})
    b = badge_of(st, r)
    if gone: b = gone_badge(b)
    rank = run_rank(st, live=not gone)
    if unreadable: st, b = "unreadable", {**badge_of("unreadable"), "title": UNREADABLE_TIP}
    t1 = r["t1"] or time.time()
    return {"id": os.path.basename(d), "port": port, "state": st, "badge": b["text"], "tip": b["title"], "tone": b["color"], "mark": b["mark"], "label": r.get("label"), "issues": r["issues"], "issue_src": r.get("issue_src"), "issue_why": r.get("issue_why"), "age": int(age), "project": os.path.basename(os.path.dirname(os.path.dirname(d))), "rank": rank, "spend": r["spend"], "dur": round((t1 - (r["t0"] or t1)) / 60, 1), "kind": r.get("kind", "code"), "fix": r.get("fix"), "stack": r.get("stack"), "bug": r.get("bug"), "merge": r.get("merge"), "titles": {i: TITLE.get(int(i), "") for i in r["issues"]}, "live": r["verdict"] is None and not gone, "running": r["verdict"] is None and not gone,   # a finished run is not live, however present its row is (#1649)
            "started": r["t0"], "ended": None if r["verdict"] is None and not gone else r["t1"], "verdict": r["verdict"], "agents": 0 if gone else r.get("agents") or 0, "share": r.get("share"), "stage": r.get("node"), "model": r.get("model"), "model_label": (model_labels({r["model"]}).get(r["model"]) if r.get("model") else None)}, {"id": os.path.basename(d), "dir": d, "wts": r.get("wts") or []}   # the row, and what the load attribution needs and nothing else

PORT_MEMO = {}   # (pid, start) -> the listening port of a dashboard this profile owns

def dashboard_port(pid, start):
    """The port a dashboard listens on, asked of `lsof` once per process and kept by `(pid, start)`. A pid is reused only
    with a new start, so a kept answer can never describe another process. A miss is not kept: a dashboard still starting
    listens a moment later. `runs()` asks only for a dashboard this profile owns."""
    key = (pid, start)
    if key in PORT_MEMO: return PORT_MEMO[key]
    m = re.search(r"TCP 127\.0\.0\.1:(\d+)|n127\.0\.0\.1:(\d+)", sh(LSOF, "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", "-Fn"))
    if not m: return ""
    PORT_MEMO[key] = m.group(1) or m.group(2)
    return PORT_MEMO[key]

def runs():
    _titles()
    out = []; load_rows = []
    table = process_table()
    procs = read_procs()
    own = own_roots()
    pids = set()
    for p in table.values():
        pids.add(p.pid)
        m = re.match(r"\s*(\d+)\s+\S+\s+-m\s+squad\.dashboard_server\s+(\S+)", f"{p.pid} {p.command}")   # any interpreter: macOS runs the server as `.../MacOS/Python` (#1751)
        if not m: continue
        d = m.group(2)
        if not pathlib.Path(d).exists(): continue   # a dashboard server outliving its run folder (worktree removed) must not break the list
        if pathlib.Path(d).parent not in own: continue   # another profile's dashboard: the ps scan sees the whole machine
        if re.search(r"/pytest-\d+/|/pytest-of-", d): continue   # dashboards started by agent-squad's own test suite are fixtures, not runs
        port = dashboard_port(p.pid, p.start)   # lsof runs only for this profile's own dashboard, once per process
        if not port: continue
        row, load = run_row(d, port, procs)
        out.append(row)
        if load: load_rows.append(load)
    for k in [k for k in PORT_MEMO if k[0] not in pids]: PORT_MEMO.pop(k, None)   # a dashboard that ended leaves no entry behind
    listed = {r["id"] for r in out}
    for entry in active_entries():   # a registered run has no dashboard server until someone opens it: it is listed all the same (#1649)
        folder = active_folder(entry)
        if not folder: continue
        name = pathlib.Path(folder).name
        if name in listed: continue
        row, load = run_row(folder, "", procs)
        out.append({**row, "url": "/ended/" + name})
        listed.add(name)
        if load: load_rows.append(load)
    for name in held_runs():   # a run the childcc pass holds, orphaned from active-runs, has no dashboard server: it is listed all the same (#689)
        folder = None if name in listed else held_folder(name)
        if not folder or not (pathlib.Path(folder) / "record.jsonl").is_file(): continue
        row, load = run_row(folder, "", procs)
        out.append({**row, "url": "/held/" + name, "held": True})
        if load: load_rows.append(load)
    for f in sorted((STATE / "tests").glob("test-*.json")):   # coordinator test passes (test.sh)
        try: d = json.loads(f.read_text())
        except Exception: continue
        age = time.time() - f.stat().st_mtime
        raw = d.get("state") or "running"
        lost = raw == "running" and age > TEST_LOST_SECS   # the pass died before it could write its terminal state (#1783)
        if lost and age > TEST_LOST_DROP_SECS: continue    # a ghost nobody will retry stops being listed at all
        if raw != "running" and age > 4 * 3600: continue
        rec = {"head": d.get("head"), "merged": d.get("merged"), "exit_code": d.get("rc")}
        st = handled_of("stalled" if lost else raw, rec); t1 = d.get("t1") or time.time(); b = badge_of(st, rec)
        tip = LOST_NOTE if lost else b["title"]
        if lost: b = {**b, "text": b["text"] + " · lost"}   # the badge carries the note too: the state alone reads as a live stall
        out.append({"id": f.stem, "port": "", "url": "/test/" + f.stem, "state": st, "badge": b["text"], "tip": tip, "label": "test #" + str(d.get("issue")), "issues": [str(d.get("issue"))], "age": int(age), "project": "test", "rank": run_rank(st, live=not lost), "spend": 0, "dur": round((t1 - d.get("t0", t1)) / 60, 1), "kind": "test", "titles": {str(d.get("issue")): TITLE.get(int(d.get("issue") or 0), "")}, "live": not lost and st in LIVE_STATES, "running": not lost and st in LIVE_STATES, "agents": 0})
    cc = childcc_row()
    if cc: out.append(cc)   # the pass is a worker beside the runs, never one of them: working() and the header count skip its kind
    out.extend(hold_rows())   # the hand holds beside the runs: Live work lists both, so the page and the card agree (#1664)
    LOAD_RUNS[:] = load_rows   # the attribution works off the runs the hub just showed, so it never needs a query of its own
    attach_load(out, LOAD_STATE.get("runs") or {}, sustained(LOAD_STATE.get("cpu") or []))
    out.sort(key=lambda r: (r["rank"], r["age"]))   # live runs first, finished next; then most recently updated first
    return out

RUN_WATCH_SECS = 1.0   # one tick a second, so a changed record reads within the second. Measured at a fifth of a second:
                       # the roots are the checkout plus one per run and child worktree, 482 records in 16.6 ms a tick, 0.08
                       # core with no page open; a second a tick is under 0.02 core (#2440)
RUN_POLL_SECS = 3.0
RUN_LIST = []
RUN_LIST_AT = 0.0
RUN_LIST_LOCK = threading.Lock()

def run_watch_state(dirs=None):
    """The record files a watch pass sees: their nanosecond mtimes and sizes, so a new run and its verdict both differ."""
    out = {}
    for d in project_dirs() if dirs is None else dirs:
        try: records = pathlib.Path(d).glob("run-*/record.jsonl")
        except OSError: continue
        for p in records:
            try:
                s = p.stat(); out[str(p)] = (s.st_mtime_ns, s.st_size)
            except OSError: pass
    return out

def refresh_runs():
    """Refresh the displayed run list after a record change, or the fixed fallback poll."""
    global RUN_LIST_AT
    rows = runs()
    with RUN_LIST_LOCK:
        RUN_LIST[:] = rows
        RUN_LIST_AT = time.monotonic()
    return rows

def run_watch_tick(before, dirs=None):
    """Refresh when a watched record appeared, changed or left the runs directory."""
    after = run_watch_state(dirs)
    if after != before: refresh_runs()
    return after

def run_list():
    """The displayed list, with its fixed poll as a fallback for a missed watch event."""
    if time.monotonic() - RUN_LIST_AT >= RUN_POLL_SECS: refresh_runs()
    with RUN_LIST_LOCK: return list(RUN_LIST)

def runs_answer():
    """The opted-in runs answer (`/api/runs?groups=1`): the rows the page draws and the Live group's header, built here
    (#1277, #1778). The bare route keeps its array, so a script, a phone forward or any other client that reads it is
    untouched. The caps are read on every answer, so a changed ceiling shows after one poll and the page substitutes none at
    serve time."""
    rows = run_list()
    return {"runs": rows, "groups": run_groups(rows, loop_caps())}

def watch_runs(stop=None):
    """Watch the known runs directories without a platform-specific dependency; the API poll remains the fallback.
    The project directories are read on each tick, so a project added later is watched. `stop` (an Event) ends the loop: the test seam."""
    state = run_watch_state()
    while not (stop is not None and stop.is_set()):
        time.sleep(RUN_WATCH_SECS)
        try: state = run_watch_tick(state)
        except Exception: pass

