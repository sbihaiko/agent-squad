# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
# ---------------------------------------------------------------- the Loop tab (#1743)
# The loop of this profile as a hot-spot tree, folded from the process table by hub_profiler (the rule layer, ADR-0137).
# The tab has its own switch, `CPU sampling`, and off is the default: off, no sampler thread runs and nothing here reads
# the process table. The switch is a file in the state folder, so it survives a hub restart. Its audit is its own file:
# the loop reads its state from the last line of the loop audit, so a profiler line there would change what the loop
# switch shows.
PROFILER_SWITCH = STATE / "profiler.json"
PROFILER_AUDIT = STATE / "profiler-audit.jsonl"
PROFILER_SECS = 1.0   # the wait after each read of the process table, so two readings are never closer than this: a second resolves a fork storm, and the read runs only while the switch is on
PAGE_PROF_FLOOR_S = 2.0   # the Loop page's own cadence floor: a page tick a second buys nothing, so the page never polls
                         # the profiler faster than this, whatever the sampler interval (#2848)
def prof_poll_secs(sampler_secs):
    """The Loop page's profiler cadence for a sampler interval: the sampler's own interval, but never under the page floor.
    The page polls no faster than the sampler, so a sampler faster than the floor still gets one page read per floor."""
    return max(PAGE_PROF_FLOOR_S, sampler_secs)
PROF_POLL_SECS = prof_poll_secs(PROFILER_SECS)   # the Loop page asks the view on this cadence, never faster than the sampler's own interval (#2848)
PROFILER_READ_ONLY = "read-only from the tailnet: SQUAD_TAILNET_PROFILER_WRITE is not on (tailnet.profiler_write)"
PROFILER_JOIN_S = 10.0   # how long an off write waits for the sampler's last pass
PROFILER_STILL_RUNNING = "still running: the sampler ends after its current read of the process table"
PROFILER = {"window": hub_profiler.Window(), "thread": None, "stop": None}
PROFILER_LOCK = threading.RLock()   # it starts and ends the sampler, and the switch is read and written under it
# The other profiles of this hub, each with its supervisor pid and the folders only its work names (#1753). The seam
# answers them, so they are read once a minute, never on the sampler's tick.
PROFILER_OTHERS = {"at": None, "rows": ()}
PROFILER_OTHERS_SECS = 60.0

def profiler_on():
    v = _json(PROFILER_SWITCH)
    return isinstance(v, dict) and v.get("on") is True

def profiler_running():
    t = PROFILER["thread"]
    return t is not None and t.is_alive()

def profiler_scope():
    """The loop of this profile for the fold: the supervisor's pid (watchd.sh writes $SG_STATE/watchd.pid), the folders
    only this profile's work names, its run folders and the scripts the loop runs, and the other profiles' loops."""
    try: pid = int(_read(STATE / "watchd.pid").split()[0])
    except (ValueError, IndexError, OSError): pid = None
    co = pathlib.Path(CHECKOUT)
    live = pathlib.Path.home() / ".claude" / "skills" / "squad-goal" / "scripts"
    marks = dict.fromkeys(f"{d}/" for d in (co / "runs", co / "worktrees" / "run", co / "worktrees" / "child", SCRIPTS, live))
    return hub_profiler.Scope(pid, tuple(marks), profiler_others())

def profiler_others(now=None):
    """(name, supervisor pid, marks) for every other profile this hub serves, so the context row tells one loop from
    another: the pid from that profile's own state folder, the folders its work names. The marks are that profile's
    checkout and the folders under it, so two profiles that run the same scripts from their own checkouts are told apart.
    Off a page's read the list is a minute old at most; a seam that refuses keeps the rows it last read, since an empty
    answer would read as a machine with one profile."""
    now = time.time() if now is None else now
    if PROFILER_OTHERS["at"] is not None and 0 <= now - PROFILER_OTHERS["at"] < PROFILER_OTHERS_SECS:
        return PROFILER_OTHERS["rows"]
    rows = []
    try:
        for r in project_contexts():
            if r.get("own") or not r.get("checkout"): continue
            st = r.get("state")
            try: pid = int(_read(pathlib.Path(st) / "watchd.pid").split()[0]) if st else None
            except (ValueError, IndexError, OSError): pid = None
            co = pathlib.Path(r["checkout"])
            rows.append((r.get("slug") or "a profile", pid,
                         tuple(f"{d}/" for d in (co, co / "runs", co / "worktrees" / "run", co / "worktrees" / "child",
                                                 co / "contrib" / "squad-goal" / "scripts"))))
    except Exception:
        rows = list(PROFILER_OTHERS["rows"])
    PROFILER_OTHERS.update(at=now, rows=tuple(rows))
    return PROFILER_OTHERS["rows"]

def profiler_sampler(stop):
    """Read the process table, then wait PROFILER_SECS after the read ends, until `stop` is set, while a page reads this
    profile (#1790). A failed or empty read is counted and not folded: folded, it would count every process of the next
    reading as new. Each pass reads the sampler's own thread CPU time, so the fold shows it apart from the hub process
    that runs it, split into the table read and the window fold (#2581)."""
    while not stop.is_set():
        started = time.thread_time()
        try: reading = {(pid, p.start): (p.ppid, p.cpu if getattr(p, "cpu_known", True) else None, p.command)   # a refused CPU read is seen and not read (#1753)
                        for pid, p in profiling_table().items()}
        except Exception: reading = None
        try: scope = profiler_scope() if reading else None
        except Exception: reading = None
        read_s = time.thread_time() - started
        with PROFILER_LOCK:
            if stop.is_set() or not profiler_on() or not read_wanted(): break   # the switch file is the truth, whoever wrote it
            if reading: PROFILER["window"].add(time.time(), reading, scope, sampler=(os.getpid(), read_s))
            else: PROFILER["window"].fail()
        stop.wait(PROFILER_SECS)

def profiler_ensure():
    """Start the sampler when the switch is on and none runs. The hub calls this as it starts, and on each read, so a
    sampler that died is started again by the page that needs it. Off, it does nothing. The switch is read under the lock:
    an off write and this never interleave, so no sampler starts after the off."""
    with PROFILER_LOCK:
        if not profiler_on(): return
        if profiler_running() and PROFILER["stop"] is not None and not PROFILER["stop"].is_set(): return   # a sampler told to stop is none: it ends by its own event
        PROFILER["stop"] = threading.Event()
        PROFILER["window"].reset(time.time())   # a window starts at its switch-on: an older one would mix two profiles
        PROFILER["thread"] = threading.Thread(target=profiler_sampler, args=(PROFILER["stop"],), daemon=True, name="profiler")
        PROFILER["thread"].start()

def profiler_set(on, who, frm, client=None):
    """Write the switch and its audit line; a write that changes nothing writes neither. An off write returns when the
    sampler has ended, or the wait bound has passed: it is told to stop under the lock and joined outside it, since its
    last pass takes the lock. Returns whether the sampler has ended."""
    old, ended = None, True
    with PROFILER_LOCK:
        if on and PROFILER["thread"] is not None and PROFILER["stop"] is not None and PROFILER["stop"].is_set() and not PROFILER["thread"].is_alive():
            PROFILER["thread"] = None
        if on != profiler_on():
            PROFILER_SWITCH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _temp_name(PROFILER_SWITCH); tmp.write_text(json.dumps({"on": on, "at": time.time()}) + "\n"); os.replace(tmp, PROFILER_SWITCH)
            loop_audit_add(who, frm, "on" if on else "off", client=client, path=PROFILER_AUDIT)
        if not on:
            old = PROFILER["thread"]   # kept until it has ended: a second off finds it and waits for it too
            if PROFILER["stop"] is not None: PROFILER["stop"].set()
    if old is not None and old is not threading.current_thread():
        old.join(PROFILER_JOIN_S)   # bounded: a read of the process table that never returns must not hold the request for ever
        ended = not old.is_alive()
        with PROFILER_LOCK:
            if PROFILER["thread"] is old and ended: PROFILER["thread"] = None
    if on: profiler_ensure()
    return ended

def profiler_refusal(a, origin_ok, tn):
    """The reason a profiler switch write is refused, or '' when it may run: the access rule, then the switch's own
    tailnet grant, then the origin and body. The loop-write grant is not read here (ADR-0162)."""
    if not a.allowed: return a.reason
    if a.tailnet and tn.get("profiler_write") is not True: return PROFILER_READ_ONLY
    return "" if origin_ok else "forbidden"

def profile_run_rows(window_s, now=None):
    """The folded rows of this profile's runs the window can hold, from the one reader of a record (`scan`): `t0`, `t1`,
    the verdict and its reasons. The window is the fold's own rule, the run's end (`t1`, or `now` while a run has no
    verdict) at or after its start, so the write time of a record never decides: a run still going with no recent event
    is in the window, and a run that ended inside it stays there."""
    now = time.time() if now is None else now
    since = now - window_s
    out = []
    for root in run_roots(CHECKOUT):
        try: ds = sorted(root.glob("run-*"))
        except OSError: continue
        for d in ds:
            r = scan(d / "record.jsonl")
            if not r or not r.get("t0"): continue
            if (r.get("t1") if r.get("t1") is not None else now) < since: continue   # the same window the fold applies
            out.append({"t0": r["t0"], "t1": r["t1"], "verdict": r["verdict"], "reasons": r["reasons"]})
    return out

def profile_run_table(window_s, now=None):
    """The Loop tab's time in failed runs table for this profile, folded by the rule layer (ADR-0137)."""
    now = time.time() if now is None else now
    return hub_profiler.failed_run_table(profile_run_rows(window_s, now), window_s, now)

