# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
def _newest_mtime(*paths):
    ts = []
    for p in paths:
        try: ts.append(pathlib.Path(p).stat().st_mtime)
        except OSError: pass
    return max(ts) if ts else None

def _loop_on(pid_file=None, state=None):
    """True while the supervisor's pid file (watchd.sh writes $SG_STATE/watchd.pid) names a live process: a signal 0, never a `ps`.
    `auto/off` pauses dispatch, so it reads off even while that pid is live (#788). `state` names another project's state
    directory, so one project's loop is read from its own files and never from the hub's (#1599)."""
    base = pathlib.Path(state) if state else STATE
    pf = pathlib.Path(pid_file) if pid_file else base / "watchd.pid"
    if (pf.parent / "auto" / "off").exists(): return False
    if next((pf.parent / "auto").glob("loop-stop-*"), None) is not None: return False   # #956: an open loop-bug holds dispatch
    try: pid = int(_read(pf).split()[0])
    except (ValueError, IndexError): return False
    try: os.kill(pid, 0)
    except ProcessLookupError: return False
    except PermissionError: return True   # alive, owned by another user
    except OSError: return False
    return True

LOOP_STAMP = HUBDIR / "loop-stamp.json"   # each on/off change the sampler saw, with the machine's local time of it (#826)

def loop_stamp_next(prev, on, when):
    """The stamp after one sighting: a state never seen before has no time, and a change takes the time it was seen."""
    if not prev or "on" not in prev: return {"on": on, "since": None}
    if bool(prev["on"]) == on: return prev
    return {"on": on, "since": when}

def loop_seen(on, now=None):
    now = time.time() if now is None else now
    prev = _json(LOOP_STAMP)
    nxt = loop_stamp_next(prev, on, dt.datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"))
    if nxt != prev:
        tmp = LOOP_STAMP.with_suffix(".tmp"); tmp.write_text(json.dumps(nxt)); os.replace(tmp, LOOP_STAMP)
    return on

def loop_status():
    on = _loop_on(); prev = _json(LOOP_STAMP) or {}
    return {"on": on, "since": prev.get("since") if bool(prev.get("on")) == on else None}

LOOP_AUDIT = STATE / "loop-audit.jsonl"   # one line per loop switch change: who, when, from where, and what a stop-now killed (#923)
LOOP_READ_ONLY = "read-only from the tailnet: SQUAD_TAILNET_LOOP_WRITE is not on (tailnet.loop_write)"
LOOP_ACTIONS = ("start", "drain", "stop-now")
LOOP_CLIENTS = ("phone", "desktop")   # the two pages that write a switch; the audit names the one that asked
WATCHD = pathlib.Path(__file__).resolve().parent / "watchd.sh"

def loop_watchd(*args, state=None, checkout=None, profile=None):
    # the mux pins a profile's env only while it imports this file: watchd gets this profile, its state and its checkout, never the hub's cwd
    env = {**_PROFILE_ENV, "SG_STATE": str(state or STATE), "SG_CHECKOUT": checkout or CHECKOUT}
    if profile:   # another project's supervisor: its own profile decides, never the hub's values inherited here (#1599)
        for k in ("SG_REPO", "SG_OWNER", "SG_PROJECT", "SG_PROFILE"):
            env.pop(k, None)
        env["SG_PROFILE"] = profile
        d = profiles_dir()
        if d: env["SG_PROFILES_DIR"] = str(d)
    return subprocess.run(["bash", str(WATCHD), *args], capture_output=True, text=True, timeout=60, env=env, cwd=checkout or CHECKOUT).returncode

def _pid_alive(pid):
    try: os.kill(pid, 0)
    except PermissionError: return True
    except OSError: return False
    return True

def loop_alive(state=None):
    base = pathlib.Path(state) if state else STATE
    try: pid = int(_read(base / "watchd.pid").split()[0])
    except (ValueError, IndexError): return False
    return _pid_alive(pid)

LOOP_RUN_CMD = os.environ.get("SG_RUN_CMD_RE") or r"[s]quad start( |$)"   # a squad run's command, as heartbeat.sh reads it (squad/run_liveness.py RUN_CMD)
LOOP_LAUNCH_S = 60 * float(os.environ.get("HB_DEAD_IDLE_MIN") or 1)   # a record with no call_start that moved this recently is a launch, not a death

def loop_record(run, checkout=None):
    return liveness().record_path(run, checkout or CHECKOUT)

def _loop_procs(RL, rows):
    """The process snapshot the loop switch reads: a fresh one when `rows` is None, a snapshot given as is, and a list of
    (pid, ppid, command) rows as a snapshot with no start, so each pid answers by itself. One read of the machine's table
    (`squad/procs.py`) is the same answer in its own shape, so it is converted and never read again (#2582)."""
    if rows is None: return RL.read_processes
    if isinstance(rows, dict):
        return {pid: p if hasattr(p, "cmd") else RL.Proc(p.start, p.command) for pid, p in rows.items()}
    return {pid: RL.Proc(None, cmd) for pid, _, cmd in rows}

def loop_run_pid(run, rows=None):
    """The pid of a live run's own process, by the one reader (squad/run_liveness.py run_pid): the engine pid of the last
    call_start or gate_start of its last invocation, while it runs as a squad run (SG_RUN_CMD_RE), did not start after the
    call named it, and is not this hub. None when a read fails: stop-now then ends nothing it cannot name. A run is started
    in its own session (start_new_session), so the supervisor's group stop never reaches it."""
    try:
        RL = liveness(); procs = _loop_procs(RL, rows)
        return RL.run_pid(RL.invocation(RL.read_text(loop_record(run))), procs() if callable(procs) else procs, LOOP_RUN_CMD, os.getpid())
    except Exception as e:
        log_once(f"hub: the run reader failed for {run}, stop-now ends no process of it: {type(e).__name__}: {' '.join(str(e).split())}")
        return None

def loop_run_live(run, rows=None, now=None):
    """A run is live while its last invocation has no verdict and its engine process runs, or, before its first call, its
    record moved within the launch window: the one reader (squad/run_liveness.py run_live_at). A run resumed after a verdict
    is live again (ADR-0167). Raises the reader's ReadError when a read fails."""
    RL = liveness()
    return RL.run_live_at(run, CHECKOUT, _loop_procs(RL, rows), time.time() if now is None else now, LOOP_LAUNCH_S, LOOP_RUN_CMD, os.getpid())

def loop_live(state=None, checkout=None, procs=None):
    """The live runs (an active-runs line whose run still runs, `loop_run_live`) and the held gate (the gate lock's pid file
    naming a live pid): what a stop kills, and what a drain waits for. A read that fails is named in `unknown`, never read as
    no run. `state` and `checkout` name another project's, so each project counts only the work it holds (#1599)."""
    base = pathlib.Path(state) if state else STATE
    checkout = checkout or CHECKOUT
    gate = _read(base / "locks" / "gate" / "pid").strip()
    gates = [f"gate pid {gate}"] if gate.isdigit() and _pid_alive(int(gate)) else []
    try:
        RL = liveness()
        table = _loop_procs(RL, procs if procs is not None else run_table())   # one read answers the rows; an empty table is no live run (#2582)
        runs = RL.live_runs(base / "active-runs.txt", checkout, table, time.time(), LOOP_LAUNCH_S, LOOP_RUN_CMD, os.getpid())
    except Exception as e:
        why = f"{type(e).__name__}: {' '.join(str(e).split())}"
        log_once(f"hub: the live runs are unknown: {why}")
        return {"runs": [], "gates": gates, "unknown": why}
    return {"runs": runs, "gates": gates}

def loop_audit_add(who, frm, action, killed=None, client=None, path=None, **extra):
    line = {"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "who": who, "from": frm, "action": action}
    if client: line["client"] = client   # which page made the change: the phone or the desktop, never guessed from the transport
    if killed is not None: line["killed"] = killed
    line.update(extra)   # a failed start's reason, or a stop-now's survivors
    p = pathlib.Path(path or LOOP_AUDIT)
    p.parent.mkdir(parents=True, exist_ok=True)
    old = p.read_bytes() if p.is_file() else b""   # atomic (ADR-0030): an interruption leaves the audit as it was
    tmp = _temp_name(p); tmp.write_bytes(old + (json.dumps(line) + "\n").encode()); os.replace(tmp, p)

def loop_audit_last(n, path=None):
    out = []
    for raw in _read(path or LOOP_AUDIT).splitlines()[-n:]:
        try: out.append(json.loads(raw))
        except ValueError: pass
    return out

LOOP_STARTING = STATE / "auto" / "starting"   # the hub's pending mark: written before watchd start runs, removed when it returns
LOOP_START_WINDOW = 90   # a mark older than this outlived its hub (the call times out at 60 s): it reads off

def loop_state(last, now=None, state=None):
    """on or draining while the supervisor is alive; starting only while a hub start is in progress; off otherwise. A start
    that failed reads off, and the view carries the failure, so no disabled button hides it. `state` is another project's
    state directory, read on its own files (#1599)."""
    base = pathlib.Path(state) if state else STATE
    if loop_alive(base): return "draining" if (base / "auto" / "drain").exists() else "on"
    now = time.time() if now is None else now
    try:
        if now - (base / "auto" / "starting").stat().st_mtime < LOOP_START_WINDOW: return "starting"
    except OSError: pass
    return "off"

def loop_cause(state=None):
    """Why the loop is off or draining, from the two marks watchd.sh writes (#1708, ADR-0146): the loop-bug the loop drained
    itself for, or the owner's own stop. Neither mark gives an empty answer."""
    auto = (pathlib.Path(state) if state else STATE) / "auto"
    if (auto / "owner-stop").exists(): return {"owner": True}
    n = _read(auto / "loop-bug-drain").strip()
    return {"issue": int(n)} if n.isdigit() else {}

LOOP_STATE_ACTIONS = {"off": ["start"], "on": ["drain", "stop-now"], "draining": ["start", "stop-now"], "starting": []}   # the hub's rule, so no page holds one

# One project is one profile (SPEC 1596, rule 8; #1599). The loop fact lists every project with its own state, live work,
# last landing, last change and open loop-bugs, read from that project's own state directory and board cache. The list is
# cached against the profiles directory's change time, so a steady machine spawns no shell per poll.
_PROFILES = {"key": None, "rows": []}
_CONTEXTS = {"key": None, "rows": []}

def _profiles_key():
    d = profiles_dir()
    try: return (str(d), tuple(sorted((f.name, f.stat().st_mtime_ns) for f in d.glob("*.sh"))))
    except (OSError, AttributeError, TypeError): return (str(d), None)

def _profile_slug(line):
    """The profile a `--list` line names: the seam prints `<profile>\\t<owner/repo>`, and a line's first field is the slug
    whatever separator the seam uses, so a change in that format reads as a slug, never as a whole line."""
    return line.strip().split("\t", 1)[0].split()[0] if line.strip() else ""

def profile_list():
    """Every profile the seam lists, in byte order: `address.sh --list`, read again for a profile added, removed or edited."""
    key = _profiles_key()
    if key == _PROFILES["key"]: return _PROFILES["rows"]
    rows = []
    try:
        p = subprocess.run(["bash", str(SCRIPTS / "address.sh"), "--list"], capture_output=True, text=True, timeout=20)
        rows = [s for s in (_profile_slug(ln) for ln in (p.stdout or "").splitlines()) if s]
    except Exception as e:
        log_once(f"hub: the profile list is unavailable: {type(e).__name__}: {' '.join(str(e).split())}")
    _PROFILES.update(key=key, rows=rows)
    return rows

def _ctx_value(raw):
    """One `KEY=%q` value, unquoted: the seam prints each through `printf %q`, so an escaped space, a quoted empty string
    or a backslash is one value. A value no shell quoting explains is read as printed."""
    try:
        parts = shlex.split(raw)
        if len(parts) == 1: return parts[0]
    except ValueError:
        pass
    return raw

def _profile_context(slug):
    """One project's context from the seam, `address.sh <slug>`: its repository, state directory, checkout and board cache.
    Each line is one `KEY=value`, so a value holding a space stays whole. Empty when the seam refuses it, so a broken
    profile is left out rather than read as another project's files."""
    try:
        p = subprocess.run(["bash", str(SCRIPTS / "address.sh"), slug], capture_output=True, text=True, timeout=20)
        if p.returncode != 0: return {}
        out = {}
        for ln in p.stdout.splitlines():
            k, sep, v = ln.partition("=")
            if sep and k: out[k] = _ctx_value(v)
        return out
    except Exception:
        return {}

def project_contexts():
    """Every project this hub serves, each with its own state directory, checkout and board cache. The profile in force is
    read from the environment the mux pinned for it, never from a shell; every other one from the seam. The profile in
    force is always in the list, so a hub with no profile list still answers for its own project."""
    key = _profiles_key()
    if key == _CONTEXTS["key"]: return _CONTEXTS["rows"]
    rows, seen = [], set()
    for slug in profile_list():
        if slug == PROFILE_NAME and PROFILE_NAME:
            row = {"slug": slug, "repo": REPO, "state": str(STATE), "checkout": CHECKOUT, "board": str(BOARD), "own": True}
        else:
            c = _profile_context(slug)
            if not c.get("SG_STATE"): continue
            row = {"slug": slug, "repo": c.get("SG_REPO", ""), "state": c.get("SG_STATE", ""),
                   "checkout": c.get("SG_CHECKOUT", ""), "board": c.get("SG_BOARD_CACHE", ""), "own": False}
        rows.append(row); seen.add(slug)
    if not PROFILE_NAME or PROFILE_NAME not in seen:
        rows.append({"slug": PROFILE_NAME, "repo": REPO, "state": str(STATE), "checkout": CHECKOUT,
                     "board": str(BOARD), "own": True})
    _CONTEXTS.update(key=key, rows=rows)
    return rows

def _cache_of(row):
    """A project's board rows: the live cache for the profile in force, the file it keeps for every other project."""
    if row.get("own"): return board_cache
    return _json(row["board"]) if row.get("board") else None

def _loop_bugs_open(cache, n=20):
    """The open loop-bug cards of one project: a card a label beginning with `loop-bug` marks and the board still draws
    open, so the refused start names exactly the bugs whose presence holds it (ADR-0146)."""
    out = []
    for i in (cache or {}).get("items") or []:
        if str(i.get("s")).upper() != "OPEN": continue
        if not any(str(x).startswith("loop-bug") for x in (i.get("l") or [])): continue
        out.append({"n": i.get("n"), "t": i.get("t") or ""})
        if len(out) >= n: break
    return out

def loop_project(row, a, procs=None, now=None):
    """One project's loop row: its state, live runs and gates, last landing, last change and open loop-bugs, plus whether
    this request may write it. Every fact comes from that project's own files, never the hub's."""
    st = pathlib.Path(row["state"])
    cache = _cache_of(row)
    change = (loop_audit_last(1, st / "loop-audit.jsonl") or [None])[-1]
    bugs = _loop_bugs_open(cache)
    tn = tailnet_block(row.get("checkout") or None)
    write = a.allowed and (not a.tailnet or tn.get("loop_write") is True)
    live = loop_live(state=st, checkout=row.get("checkout"), procs=procs)
    return {"slug": row["slug"], "repo": row.get("repo", ""), "own": bool(row.get("own")),
            "state": loop_state(change or {}, state=st), "live": live,
            "landing": _last_landing_of(cache), "change": change,
            "bugs": len(bugs), "bugs_open": bugs, "write": write,
            "reason": "" if write else (a.reason if not a.allowed else LOOP_READ_ONLY)}

def loop_projects(a, procs=None, now=None, rows=None):
    """Every project's loop row, in slug order, from one process snapshot: one read of the machine answers every project,
    so the list costs one `ps` and one small read per project. `rows` is the project list the caller already read, so the
    projects and the change log answer one request from the same list."""
    if rows is None: rows = project_contexts()
    if procs is None:
        try: procs = _loop_procs(liveness(), run_table())   # the run readers' one read, never the kill path's (#2582)
        except Exception: procs = None
    return sorted((loop_project(r, a, procs=procs, now=now) for r in rows), key=lambda p: str(p["slug"]))

def _change_key(e):
    try: return dt.datetime.fromisoformat(str(e.get("at"))).timestamp()
    except (TypeError, ValueError): return 0.0

def loop_changes(rows, n=20):
    """The change log across projects, newest first: one project's audit lines tagged with the project they belong to, so
    the Loops list audits a switch without asking which loop it was."""
    out = []
    for r in rows:
        if not r.get("state") or not pathlib.Path(r["state"]).is_dir(): continue   # a project whose state is gone names no change
        for e in loop_audit_last(n, pathlib.Path(r["state"]) / "loop-audit.jsonl"):
            out.append({"project": r["slug"], **e})
    out.sort(key=_change_key, reverse=True)
    return out[:n]

LOOP_STARTED = STATE / "watchd.started"   # every watchd start stamps it with its epoch second, from the hub or the terminal

def loop_started_after(at, path=None):
    """True when a watchd start came after the audit time `at`: an old hub failure then no longer describes the loop.
    `path` is another project's stamp, so one project's start never clears another's failure (#1599)."""
    try: when = dt.datetime.fromisoformat(at).timestamp()
    except (TypeError, ValueError): return False
    try: return int(_read(path or LOOP_STARTED).split()[0]) > int(when)
    except (ValueError, IndexError): return False

def loop_health(now=None):
    """The loop answer's health (#1280): how long a pending start has left, which the Monitor's switch states. The live
    counts, the cycle age and the unknown line left with the Loop theme's tiles (#2515): no page reads them, and the
    `live` block and the foot line already carry them. No window open is None, never zero."""
    now = time.time() if now is None else now
    starting = None
    try:
        since = now - LOOP_STARTING.stat().st_mtime
        if since < LOOP_START_WINDOW: starting = int(LOOP_START_WINDOW - since)
    except OSError: pass
    return {"starting_left": starting}

def loop_view(a):
    last = (loop_audit_last(1) or [{}])[-1]
    tn = tailnet_block()
    write = a.allowed and (not a.tailnet or tn.get("loop_write") is True)
    state = loop_state(last)
    try: procs = _loop_procs(liveness(), run_table())   # the run readers' one read, never the kill path's (#2582)
    except Exception: procs = None
    live = loop_live(procs=procs)
    rows = [r for r in project_contexts() if r.get("own") or (r.get("state") and pathlib.Path(r["state"]).is_dir())]
    return {"state": state, "actions": list(LOOP_STATE_ACTIONS.get(state, [])) if write else [], "who": last.get("who", ""), "when": last.get("at", ""), "live": live,
            "cause": loop_cause(),   # the loop-bug a drain waits on, or the owner's stop (#1708)
            "health": loop_health(),   # the start window the Monitor's switch states (#1280)
            "failed": last.get("failed", "") if state == "off" and not loop_started_after(last.get("at", "")) else "",
            "log": loop_audit_last(20)[::-1], "write": write, "reason": "" if write else LOOP_READ_ONLY,
            "projects": loop_projects(a, procs=procs, rows=rows),   # every project this hub serves, each on its own files (#1599)
            "changes": loop_changes(rows),   # one project list, so the strip and the change log never disagree in one answer
            "tailnet": a.tailnet, "access": {"loop_write": tn.get("loop_write") is True}}   # kept for every client that already reads the loop answer

def access_view(a):
    """Settings > Access reads the reach and the tailnet rule on their own route (#1285), an additive answer beside the
    loop route so the theme never waits on the loop answer. `reach` is this request's own access result; each rule says
    allowed or refused, and `file` names the file the rule is read from: the project file when it holds the block, else the
    project's personal `.env`, and none when no file holds one. A request the seam refuses gets its reach and no rule, login
    or file, marked `withheld` so the page says the values stay with the owner rather than reading as an empty rule."""
    if not a.allowed:
        return {"tailnet": a.tailnet, "login": "", "reach": False, "reason": a.reason, "withheld": True, "access": {}, "file": ""}
    tn, path = tailnet_source()
    return {"tailnet": a.tailnet, "login": a.login, "reach": a.allowed, "reason": "",
            "access": {"loop_write": tn.get("loop_write") is True},
            "file": path}

def loop_refusal(a, origin_ok, tn):
    """The reason a loop write is refused, or '' when it may run: the access rule, then the tailnet grant, then the origin and body."""
    if not a.allowed: return a.reason
    if a.tailnet and tn.get("loop_write") is not True: return LOOP_READ_ONLY
    return "" if origin_ok else "forbidden"

def _ps_rows():
    """Every process as (pid, ppid, command), from a FRESH read of the process table: the kill path never reads the
    one-second snapshot cache, so a run born inside a tick is always seen here (see `process_table`)."""
    return [(p.pid, p.ppid, p.command) for p in _machine_snapshot().values()]

def loop_kill(root):
    """End `root` and every process under it: TERM, then KILL what is left. True when `root` is gone after it."""
    rows = _ps_rows(); kids = {}
    for pid, ppid, _ in rows: kids.setdefault(ppid, []).append(pid)
    tree, stack = [], [root]
    while stack:
        pid = stack.pop(); tree.append(pid); stack.extend(kids.get(pid, ()))
    for sig in (15, 9):
        for pid in tree:
            try: os.kill(pid, sig)
            except OSError: pass
        for _ in range(30):
            if not any(_pid_alive(p) for p in tree): return True
            time.sleep(0.1)
    return not _pid_alive(root)

def loop_mark_killed(run, checkout=None):
    """Give a run stop-now ended its verdict at once, through squad.killed (the one writer), so it never reads live again.
    False when it could not be written: the run then still reads not live, since its process is gone."""
    try:
        (K,) = load_squad(squad_roots(os.environ.get("SG_SQUAD"), os.environ.get("SG_CHECKOUT"), checkout or CHECKOUT, __file__), ("killed",))
        K.mark_killed(loop_record(run, checkout).parent, "killed/stop-now")
        return True
    except Exception as e:
        log_once(f"hub: stop-now could not write the killed verdict of {run}: {type(e).__name__}: {' '.join(str(e).split())}")
        return False

def loop_stop_now(live, checkout=None):
    """End each listed run and gate; answer what died and what survived. A run that died gets its killed verdict."""
    killed, survived = {"runs": [], "gates": []}, {"runs": [], "gates": []}
    for r in live["runs"]:
        pid = loop_run_pid(r)
        if pid and loop_kill(pid):
            killed["runs"].append(r); loop_mark_killed(r, checkout)
        else: survived["runs"].append(r)
    for g in live["gates"]:
        pid = int(g.rsplit(" ", 1)[-1])
        (killed if pid != os.getpid() and loop_kill(pid) else survived)["gates"].append(g)
    return killed, survived

def _loop_targets(row=None):
    """The files a loop write acts on: one project's state directory, checkout, audit log, pending-start mark and start
    stamp. The hub's own project reads the module's files; another project reads only its own, from its context (#1599)."""
    if row is None or row.get("own"):
        return {"state": STATE, "checkout": CHECKOUT, "audit": LOOP_AUDIT, "starting": LOOP_STARTING, "profile": None}
    st = pathlib.Path(row["state"])
    return {"state": st, "checkout": row.get("checkout") or "", "audit": st / "loop-audit.jsonl",
            "starting": st / "auto" / "starting", "profile": row["slug"]}

def loop_switch(a, body, origin_ok, row=None):
    """One project's loop switch: the access rule, the tailnet grant of that project's own tree, the origin and body, then
    the action on that project's files. `row` names a project other than the hub's own (#1599, AC4)."""
    t = _loop_targets(row)
    why = loop_refusal(a, origin_ok, tailnet_block(t["checkout"] or None))
    if why: return 403, {"err": "forbidden", "reason": why}
    action = body.get("action") if isinstance(body, dict) else None
    if action not in LOOP_ACTIONS: return 400, {"err": "bad request"}
    state = loop_state((loop_audit_last(1, t["audit"]) or [{}])[-1], state=t["state"])
    if action not in LOOP_STATE_ACTIONS.get(state, []):   # the route applies the rule the view publishes
        return 409, {"err": "the loop is not running" if state == "off" else f"the loop is {state}: {action} does not apply"}
    who, frm = a.login or "owner", "tailnet" if a.tailnet else "loopback"
    client = body.get("client") if body.get("client") in LOOP_CLIENTS else None   # the page that asked, stated, never guessed
    def watchd(*verbs):   # the hub's own supervisor takes no target; another project's names its own files (#1599)
        if t["profile"] is None and t["state"] == STATE: return loop_watchd(*verbs)
        return loop_watchd(*verbs, state=t["state"], checkout=t["checkout"] or None, profile=t["profile"])
    if action == "start":
        t["starting"].parent.mkdir(parents=True, exist_ok=True); t["starting"].write_text(f"{time.time()}\n")
        try:
            try: rc = watchd("start")
            except subprocess.TimeoutExpired: rc = -1
            # The pause lifts only after the start was not refused (#1708): a start refused for an open loop-bug keeps the
            # drain and its mark, so the loop never runs on beside an open loop defect.
            if not rc:
                for f in ("off", "drain", "drain-pause"): (t["state"] / "auto" / f).unlink(missing_ok=True)
            failed = "watchd did not start" if rc else "" if loop_alive(t["state"]) else "the supervisor was not running after its start"
        finally: t["starting"].unlink(missing_ok=True)
        if failed:
            loop_audit_add(who, frm, action, client=client, path=t["audit"], failed=failed)
            return 500, {"err": failed}
        loop_audit_add(who, frm, action, client=client, path=t["audit"])
        return 200, {"ok": True, "action": action}
    if action == "drain":
        if watchd("drain"): return 500, {"err": "watchd did not drain"}
        loop_audit_add(who, frm, action, client=client, path=t["audit"])
        return 200, {"ok": True, "action": action}
    if body.get("confirm") is not True: return 400, {"err": "confirm the stop: it kills the live runs and gates listed"}
    # The kill path hands loop_live a fresh table (_ps_rows), so loop_live builds its run rows from that read and never the
    # run readers' 2.5 s window (RUN_TABLE_SECS, `loop_live` line "table = _loop_procs(... procs ...)"). A run born inside
    # that window would read not live, so stop-now would end fewer runs than it names and that run would survive the stop.
    live = loop_live(state=t["state"], checkout=t["checkout"] or None, procs=_ps_rows())
    if watchd("stop", "--force"): return 500, {"err": "watchd did not stop"}   # stop-now is the hard stop: the snapshot runs first (#1662)
    killed, survived = loop_stop_now(live, t["checkout"] or None)
    extra = {"survived": survived} if survived["runs"] or survived["gates"] else {}
    loop_audit_add(who, frm, action, killed, client=client, path=t["audit"], **extra)
    return 200, {"ok": True, "action": action, "killed": killed, **extra}

def loop_switch_project(a, slug, body, origin_ok):
    """A write from one project's loop page: `/slug/api/loop` resolves the slug through the projects this hub serves and
    applies the action to that project alone. A slug no profile names is refused and reads no state, so a page can never
    write another project's loop through the hub's own (AC4; #1599)."""
    row = next((r for r in project_contexts() if r.get("slug") == slug), None)
    if row is None: return 404, {"err": "unknown project", "reason": f"this hub serves no project named {slug}"}
    return loop_switch(a, body, origin_ok, row=row)

# ---------------------------------------------------------------- a read only while a page reads (#1790)
# A background read is a page's reading, not the machine's: with no page open, nobody reads what it publishes, and the
# hub still paid for it every few seconds -- the process-table scan, the board and history reads, the quota probes. The
# many-repository hub gives each profile a gate: a request to that profile's page touches it, and a read works only while
# the gate says a page read the profile recently. A hub served alone (hub.py's own `__main__`) sets no gate, and its
# reads run as they always did.
READ_IDLE_SECS = 60.0   # the pages poll every few seconds, so an open page keeps its profile's reads running

class ReadGate:
    """Whether a page reads this profile now, and the sleep between two reads: a read parked with no page ends that sleep
    at once when a page reads the profile, so the read that follows a page opening runs on the read, never on the end of
    the sleep before it. A read a page is keeping alive keeps its own period: a page read never shortens a cadence (#1790)."""
    def __init__(self, idle=None):
        self.idle = READ_IDLE_SECS if idle is None else idle
        self.at = 0.0
        self.wake = threading.Event()
    def touch(self):
        self.at = time.time(); self.wake.set()
    def wanted(self):
        return time.time() - self.at < self.idle
    def pause(self, secs):
        if self.wanted():
            time.sleep(secs)   # a page reads this profile: the cadence stands
        else:
            self.wake.wait(secs); self.wake.clear()

READ_GATE = None   # the many-repository hub sets one per profile (hubmux.start_profile)

def read_wanted():
    return READ_GATE is None or READ_GATE.wanted()

def read_pause(secs):
    """Sleep up to `secs`, and return at once when a page reads this profile."""
    if READ_GATE is None: time.sleep(secs)
    else: READ_GATE.pause(secs)

