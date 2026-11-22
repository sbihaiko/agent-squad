# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
BOARD = pathlib.Path(os.environ.get("SG_BOARD_CACHE") or HUBDIR / "hub-board.json"); BOARD.parent.mkdir(parents=True, exist_ok=True)
_PROFILE_BOARD = None   # the profile's own board cache, resolved once (#1219); "" when the seam names none
def profile_board_cache():
    """The board cache the profile's own environment names (#1219): SG_BOARD_CACHE when the environment sets it (hubmux
    pins it for the live hub), else the seam's `sg_board_cache` for this hub's owner and project. A hub started before
    that pin still finds the live cache here, without reading GitHub or another repository's state."""
    global _PROFILE_BOARD
    if _PROFILE_BOARD is None:
        path = os.environ.get("SG_BOARD_CACHE")
        if not path:
            try:
                p = subprocess.run(["bash", "-c", '. "$1/address.sh" 2>/dev/null; sg_board_cache "$2" "$3"', "_",
                                    str(pathlib.Path(__file__).resolve().parent), OWNER, PROJECT_NO],
                                   capture_output=True, text=True, timeout=30)
                path = p.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                path = ""
        _PROFILE_BOARD = path or ""
    return pathlib.Path(_PROFILE_BOARD) if _PROFILE_BOARD else None

BOARD_STALE_DEFAULT = 600.0   # seconds the hub's cache may trail the profile's own before the page names it (#1219)
_BOARD_STALE_WARNED = set()
def board_stale_bound():
    """SG_HUB_BOARD_STALE_SECONDS: the gap past which a hub names its board cache as stale (#1219). A value that is not a
    non-negative number is the default, and one stderr line names it: a setting the hub ignored never passes silently."""
    raw = os.environ.get("SG_HUB_BOARD_STALE_SECONDS")
    if not raw: return BOARD_STALE_DEFAULT
    try: bound = float(raw)
    except ValueError: bound = None
    if bound is None or bound < 0:
        if raw not in _BOARD_STALE_WARNED:
            _BOARD_STALE_WARNED.add(raw)
            print(f"hub: SG_HUB_BOARD_STALE_SECONDS={raw!r} is not a non-negative number: the stale bound is "
                  f"{BOARD_STALE_DEFAULT:g} s", file=sys.stderr)
        return BOARD_STALE_DEFAULT
    return bound
def board_stale():
    """A message when this hub reads a board cache older than the profile's own, else None (#1219). The page shows it in
    the payload's one error key, as it shows a failed board read. Nothing to say when the two are one file, when either is
    absent, or when the gap is inside SG_HUB_BOARD_STALE_SECONDS."""
    own = profile_board_cache()
    if own is None or own.resolve() == BOARD.resolve(): return None
    try: gap = own.stat().st_mtime - BOARD.stat().st_mtime
    except OSError: return None
    if gap <= board_stale_bound(): return None
    return f"board cache is stale: {BOARD} is older than the profile's own {own}"
BOARD_READ = 2   # the version of the items read that wrote the cached rows: a cache that names another is read again (#921); version 2 carries each row's database id, the fast read's join key
try: board_cache = json.loads(BOARD.read_text())
except Exception: board_cache = {"t": 0, "items": [], "prs": [], "err": None}
if board_cache.get("rv") != BOARD_READ:
    # A cache written before the rows carried the close reason (#921) answers no `cr`, so a card closed as not planned
    # would draw Closed and count as delivered until the next whole items read. Its rows are dropped, and the pass reads
    # them again; the rows of a read this version wrote stay. A board with no row is safer than one that miscounts.
    board_cache.update(items=[], cases=[], t=0)
    board_cache["rv"] = BOARD_READ
if board_cache.get("repo") not in (None, REPO):
    # The cache file is one per owner and project (address.sh), so two repositories on one project share it, and the
    # file holds the rows of the hub that wrote last (#1015). Another repository's rows are never this board: they are
    # dropped, and this hub's first pass reads its own. A board with no row is safer than another repository's.
    board_cache.update(items=[], cases=[], prs=[], landed=[], errs=[], lc={}, lt=0, t=0, repo=REPO, fast_asked={}, fast_pending=[], fast_seen={})
# A cache written before the board read more than one repository (#1820) has rows with no repository on them: they are
# the profile repository's, which is the only one that pass read.
for _k in ("prs", "landed"):
    for _x in board_cache.get(_k) or []: _x.setdefault("r", REPO)
board_cache.setdefault("errs", [])
board_cache.setdefault("parts", {})
board_cache.setdefault("fast_asked", {})   # database id -> the full read it was asked under (#985): an ask a later read outran is asked again
board_cache.setdefault("fast_pending", [])   # the cards the fast read has seen and the cache does not hold (#985)
def fd_int(v):
    """A database id as an int, or None: a cache row may carry a spelling that is no number (a hand fixture, a corrupt
    file), and that row joins no card instead of blanking the whole board (#985)."""
    try: return int(v)
    except (TypeError, ValueError): return None
def apply_open_blockers(items, nodes):
    """Attach open native blockers (`Issue.blockedBy`) onto each item as `b`. A closed blocker drops off by itself."""
    by = {n.get("number"): n for n in nodes or []}
    for i in items:
        node = by.get(i.get("n"))
        if not node: continue
        open_ns = [x["number"] for x in (node.get("blockedBy") or {}).get("nodes") or [] if (x.get("state") or "").upper() == "OPEN"]
        if open_ns: i["b"] = open_ns
        else: i.pop("b", None)
    return items
def apply_parent_specs(items, nodes, complete=True, previous=None):
    """Attach the spec each ticket belongs to as `pa` (#829): the parent issue's number, when that parent carries the
    spec label. The relation read carries the parent with the blockers, so the board spends no read per card.
    A parent that is not a spec is no spec, and its children are ordinary issues that move on their own. A card the read
    names keeps exactly what that read says. A card a read that is not complete never reached keeps the `pa` the last
    good pass recorded, and a card with none is marked `pau`: an unread parent is never "no spec parent", so the rule
    fails closed, never open (ADR-0142)."""
    by = {n.get("number"): n for n in nodes or [] if isinstance(n, dict)}
    had = {i.get("n"): i for i in previous or [] if isinstance(i, dict)}
    for i in items:
        node = by.get(i.get("n"))
        if node is None and not complete:
            kept = (had.get(i.get("n")) or {}).get("pa")
            if isinstance(kept, int): i["pa"] = kept
            else: i["pau"] = True   # the parent is unknown, not absent: promotable() refuses it until a read names it
            continue
        i.pop("pau", None)
        parent = (node or {}).get("parent")
        num = parent.get("number") if isinstance(parent, dict) else None
        labels = {x.get("name") for x in ((parent.get("labels") or {}).get("nodes") or []) if isinstance(x, dict)} if isinstance(parent, dict) else set()
        if isinstance(num, int) and _columns_module.SPEC_LABEL in labels: i["pa"] = num
        else: i.pop("pa", None)
    return items
def apply_holders(items):
    """Name on each card the run that holds it, from the seam's one holder read (#241): `h` is {"run", "started"}, and a
    card no run holds carries none. A holder read that fails keeps the holder the last good pass read, never a blank."""
    try: held = board_seam().holders()
    except Exception:
        last = {i["n"]: i["h"] for i in board_cache.get("items") or [] if i.get("h")}
        held = {str(n): h for n, h in last.items()}
    for i in items:
        h = held.get(str(i["n"]))
        if h: i["h"] = h
        else: i.pop("h", None)
def holder_text(i):
    """The text a held card wears: `held by <run> since <start> UTC`, or `held by <run>` when no start was named."""
    h = i.get("h") or {}
    if not h.get("run"): return None
    t = str(h.get("started") or "")
    try: t = dt.datetime.fromtimestamp(float(t), dt.timezone.utc).strftime("%Y-%m-%d %H:%M")   # an epoch start
    except (ValueError, OverflowError, OSError): t = t[:16].replace("T", " ")   # an ISO start
    return f"held by {h['run']}" + (f" since {t} UTC" if t else "")
ROOTS_TTL = 5.0   # seconds a walk of the run roots answers for: a run in a new worktree shows up after this bound (#1771)
_roots_seen = {"t": None, "roots": ()}
_run_folders = {}
def run_folder(run):
    """The folder of one run the profile's roots hold a record for, or None when no root holds one (#1661). A found folder
    is read again with one stat, and the roots are walked again only after ROOTS_TTL: the board answer asks once for each
    held card, and a walk globs every worktree (#1771). A miss is never kept, and a change of the roots' sources (the
    checkout, SG_PROJECTS, SG_FORK_DIR or the reader) drops both caches."""
    key = (own_roots, CHECKOUT, PROJECTS, FORK_DIR)
    if _roots_seen.get("key") != key:
        _run_folders.clear(); _roots_seen.update(key=key, t=None, roots=())
    known = _run_folders.get(run)
    if known is not None and (known / "record.jsonl").is_file(): return known
    _run_folders.pop(run, None)
    now = time.monotonic()
    if _roots_seen["t"] is None or now - _roots_seen["t"] >= ROOTS_TTL:
        _roots_seen.update(t=now, roots=tuple(sorted(own_roots())))
    for root in _roots_seen["roots"]:
        folder = pathlib.Path(root) / run
        if (folder / "record.jsonl").is_file():
            if len(_run_folders) > 512: _run_folders.clear()   # a bound, never a ledger: a cleared entry is found again
            _run_folders[run] = folder
            return folder
    return None
HOLDER_CHECK_SECS = 5.0   # seconds a holder's fact answers for: a card says `running` or `ended` within this bound (#1790)
_HOLDER_CACHE = {}   # (run, start) -> (checked_at, fact)
def holder_fact(i, now=None):
    """(text, state) of the run that holds a card (#1661). `running` with `held by <run> since <start>` only while the run's
    process is live (squad/reap.py `run_live`, the one predicate). Otherwise `ended` with `held · run <run> ended <time>`, the
    record's last write, or `held · run <run>, no record` when no root holds that run. (None, None) when no run holds the card.
    The Project's Run field is the claim; it never proves that work is in flight."""
    h = i.get("h") or {}
    run = h.get("run")
    if not run: return None, None
    key = (run, str(h.get("started") or ""))   # the fact is the run's, never the card's: every card one run holds shares it
    if now is None:   # the board's key and its body both ask per card, and one board carries four hundred held cards over a handful of runs (#1790)
        clock = time.time()
        hit = _HOLDER_CACHE.get(key)
        if hit is not None and clock - hit[0] < HOLDER_CHECK_SECS: return hit[1]
        fact = _holder_fact_now(i, run, clock)
        _HOLDER_CACHE[key] = (clock, fact)
        return fact
    return _holder_fact_now(i, run, now)   # an explicit instant is asked by a test or a probe: it is answered exactly, never from a cache
_ACTIVE_RUNS = {}   # the active-runs list read once per board build: a card's run is looked up here before the process table is read for it
def active_run_names():
    """The runs the loop's own list names as active, read once per board build. A board carries four hundred held cards over
    four hundred runs, most of them long ended, and `run_live` reads a whole record and scans the process table for each: a
    run this list does not name has ended, so its record is never read (#1790). The list is the loop's own claim, and the
    process table still proves the ones it does name."""
    if "names" not in _ACTIVE_RUNS:
        try: names = {pathlib.Path(p).name for p in (STATE / "active-runs.txt").read_text().split()}
        except OSError: names = set()
        _ACTIVE_RUNS["names"] = names
    return _ACTIVE_RUNS["names"]
def _holder_fact_now(i, run, now):
    folder = run_folder(run)
    try: moved = (folder / "record.jsonl").stat().st_mtime if folder is not None else None
    except OSError: moved = None
    if moved is None: return f"held · run {run}, no record", "ended"
    # Only a run the loop's list names, or one still writing its record, is proven by the process table: a board carries four
    # hundred held cards over four hundred runs and most ended days ago, and `run_live` reads a whole record for each (#1790).
    try:
        live = (run in active_run_names() or now - moved < reaper().NO_PID_IDLE_S) and reaper().run_live(folder, now)
    except Exception as e:
        # The line names the reap module that answered, so the next miss records which root and which copy it read (#2358).
        src = None
        try: src = getattr(reaper(), "__file__", None)
        except Exception: pass
        try: when = dt.datetime.fromtimestamp(pathlib.Path(src).stat().st_mtime, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if src else "-"
        except OSError: when = "-"
        log_once(f"hub: the holder of run {run} is unknown: {type(e).__name__}: {' '.join(str(e).split())} (squad.reap {src or '-'} mtime {when})")
        return None, None
    if live: return holder_text(i), "running"
    return f"held · run {run} ended " + dt.datetime.fromtimestamp(moved, dt.timezone.utc).strftime("%Y-%m-%d %H:%M") + " UTC", "ended"
def board_read_overdue(t, now=None):
    """True when the board item read began more than BOARD_OVERDUE_SECS ago: a stalled pass is marked, never printed as normal
    (#1661). A board never read (t 0) is not overdue; the page says it is not read yet."""
    if not t: return False
    return (time.time() if now is None else now) - t > BOARD_OVERDUE_SECS
def taken_map():
    """{issue: takeover epoch} for every `auto/taken-<run>` marker: the issues a takeover left owed to a hand fix
    (ADR-0166). The marker's own write time is when it took over. An unreadable marker is skipped, never read as no
    takeover."""
    out = {}
    d = STATE / "auto"
    try: marks = sorted(d.glob("taken-*"))
    except OSError: return out
    for p in marks:
        try: text, when = p.read_text(), p.stat().st_mtime
        except OSError: continue
        for line in text.splitlines():
            if line.startswith("issues "):
                for n in line.split()[1:]:
                    if n.isdigit(): out.setdefault(n, when)
    return out
def holder_chip(i, taken=None, state=None):
    """The short text a held card wears: `Squad`, `Squad · running` while the run's own process lives, and
    `Squad · taken over <time>` once a takeover marker names it, so the chip names the holder on every card and says
    `running` only for a live one (#1661, #1664)."""
    h = i.get("h") or {}
    if not h.get("run"): return None
    when = (taken or {}).get(str(i["n"]))
    if when:
        t = dt.datetime.fromtimestamp(float(when), dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
        return f"Squad · taken over {t} UTC"
    return "Squad · running" if state == "running" else "Squad"
HAND_HOLD_ISO = "%Y-%m-%dT%H:%M:%SZ"   # the stamp `squad/handhold.py` writes `since` and `until` in
HAND_HOLD_CARRIERS = ("manual", "hal9k")
def _hand_hold(path):
    """One hold file as a dict, or None when the file is not there: `by`, `since` and `until`, on three lines. A
    malformed file raises ValueError, so the caller serves no hold rather than a guessed one. The format is
    `squad/handhold.py`'s and the hub reads it here (#2533)."""
    try: text = path.read_text()
    except FileNotFoundError: return None
    fields = {}
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        fields[key] = value
    try:
        if fields["by"] not in HAND_HOLD_CARRIERS: raise KeyError("by")
        since = dt.datetime.strptime(fields["since"], HAND_HOLD_ISO).replace(tzinfo=dt.timezone.utc)
        until = dt.datetime.strptime(fields["until"], HAND_HOLD_ISO).replace(tzinfo=dt.timezone.utc)
    except (KeyError, ValueError):
        raise ValueError(f"the hold on {path.name} is malformed") from None
    return {"by": fields["by"], "since": fields["since"], "until": fields["until"], "until_t": until.timestamp()}
def hand_holds(base, closed=(), now=None):
    """Every hand hold of this profile's state directory, read from the files the hub serves (#2533): a live hub serves
    a profile whose checkout and hub tree hold no `squad` package, so this read imports none. One file per issue,
    `auto/hold-<n>`, is the whole file face. `stale` is True once `until` has passed, `orphan` is True when the issue is
    closed, and `activity` is the seconds since the marker's own write, the folder-activity read."""
    now = time.time() if now is None else now
    d = pathlib.Path(base) / "auto"
    closed = {str(n) for n in closed}
    out = []
    for p in sorted(d.glob("hold-*")) if d.is_dir() else []:
        n = p.name[len("hold-"):]
        if not n.isdigit(): continue
        c = _hand_hold(p)
        if c is None: continue
        try: activity = now - p.stat().st_mtime
        except OSError: activity = None
        stale = now > c["until_t"]
        orphan = n in closed
        out.append({"issue": int(n), "carrier": c["by"], "since": c["since"], "until": c["until"],
                    "stale": stale, "orphan": orphan, "live": not stale and not orphan, "activity": activity})
    return sorted(out, key=lambda r: r["issue"])
def hand_hold_alerts(base, rows, now=None):
    """One `alerts.log` line for each stale and each orphan hold the last pass did not name: one line per episode, never
    one per poll, kept by the `auto/hold-alerts` file. The lines are `squad/handhold.py`'s, written here for the same
    reason the read is (#2533)."""
    now = time.time() if now is None else now
    stamp = time.strftime("%F %T", time.localtime(now))
    want = {}
    for r in rows:
        key = str(r["issue"])
        if r["orphan"]:
            want[f"orphan-{key}"] = (f"{stamp} HOLD ORPHAN #{key} {r['carrier']} since {r['since']}; "
                                     "the issue is closed or abandoned")
        elif r["stale"]:
            want[f"stale-{key}"] = (f"{stamp} HOLD STALE #{key} {r['carrier']} since {r['since']} until {r['until']}")
    base = pathlib.Path(base)
    seen_path = base / "auto" / "hold-alerts"
    try:
        seen = set(seen_path.read_text().split())
    except FileNotFoundError:
        seen = set()
    lines = [line for key, line in want.items() if key not in seen]
    if lines:
        with (base / "alerts.log").open("a") as f:
            for line in lines:
                f.write(line + "\n")
    if set(want) != seen:   # the dedupe file changes only when an episode starts or ends, never on every read
        _write_record(seen_path, "".join(f"{key}\n" for key in sorted(want)))
    return lines
def hold_rows(now=None, state=None):
    """The Live work rows for hand holds: a Manual or HAL9k hold proven by folder activity (the marker's own write time,
    under the one bound `SG_HAND_HOLD_MINUTES`), never stale and never an orphan. A Squad run is a run row, proven by its
    live process. The page holds no rule of its own (ADR-0137); the read is this module's on the profile's state
    directory, so it imports no `squad` package (#2533)."""
    now = time.time() if now is None else now
    base = pathlib.Path(state) if state else STATE
    closed = {i["n"] for i in board_cache.get("items", []) if i.get("s") == "closed"}
    try:
        rows = hand_holds(base, closed=closed, now=now)
        hand_hold_alerts(base, rows, now=now)   # one line per stale or orphan episode, never one per poll
    except Exception as e:
        log_once(f"hub: the hand holds are unknown: {type(e).__name__}: {' '.join(str(e).split())}"); return []
    out = []
    for h in rows:
        if not h["live"]: continue   # a stale hold is marked on the card; an orphan is alerted, never drawn as work
        carrier = "HAL9k" if h["carrier"] == "hal9k" else "Manual"
        out.append({"id": f"hold-{h['issue']}", "port": "", "state": "held", "badge": "held",
                    "label": f"{carrier} · #{h['issue']}", "carrier": h["carrier"], "issues": [str(h["issue"])],
                    "age": int(h["activity"] or 0), "rank": 0, "spend": 0, "dur": 0, "kind": "hold", "live": True,
                    "running": False, "titles": {str(h["issue"]): TITLE.get(h["issue"], "")}})
    return out
def _holder_argv(pid):
    """The command line of one live process, empty when the table cannot be read."""
    try: return next((p.command for p in process_table().values() if p.pid == pid), "")
    except Exception: return ""

def _gate_run(line):
    """The id of the run whose workspace a gate holder's command line runs from, None when it names no run."""
    m = re.search(r"run-\d{8}-\d{6}-[0-9a-f]+", line)
    return m.group(0) if m else None
def gate_lock_line(pid, line, issues_of):
    """One held gate lock, named by what its holder checks (#1695). The lock file names only `bash`, so the holder's
    command line answers: the run whose workspace the gate script runs from, else the gate script, else the pid alone."""
    run = _gate_run(line)
    if run: return f"gate: pid {pid} checks run {run}" + (f" · {issues_of[run]}" if issues_of.get(run) else "")
    scripts = [x for x in re.findall(r"([\w-]+\.(?:sh|py))\b", line) if x not in ("serial.sh", "gate.sh")]
    return f"gate: pid {pid} runs {scripts[0]}" if scripts else f"gate: pid {pid}"

# ---------------------------------------------------------------- one short live row (#2507)
# The Live work panel draws one short row per item: an icon, the issue or the item's name, its chips and its age. The pid,
# the script, the holder and the full title ride the row's tooltip, never its visible text.
DELIVERY_STAGES = ("gate", "dev", "stg", "main", "publish")   # the delivery pipeline a hold's chips follow, in order
LANES = (("watch", pathlib.Path("locks") / "watch" / "pid"), ("fastloop", pathlib.Path("fastloop.pid")),
         ("cqflush", pathlib.Path("cqflush.pid")))   # the loop's three lanes, each named by the pid file it writes

def delivery_stages(n, base=None, closed=False, gating=False):
    """The five delivery stages of one issue, from the loop's own records and no GitHub read (#2507): `gate` and `dev` are
    done once the issue's ledger holds a landing fact (ADR-0149, #2532); `stg` and `main` once a promotion of that branch
    came after the landing, since a later green tip carries the earlier work with it (ADR-0145); `publish` once the issue
    is closed, which is what a publish does to the issues it carries. The stage in hand is the first one not done, and
    every later one waits. Before a landing, `gate` is in hand only while a gate for the issue runs or waits for its
    slots (`gating`); with no landing and no gate the issue is still coded, so no stage is in hand (#2547)."""
    at = max(issue_ledger.landed_at(f"{REPO}#{n}", base), default=None)
    promos = strip_promotions(base)
    stg = os.environ.get("SG_STG_BRANCH") or "stg"
    main = os.environ.get("SG_MAIN_BRANCH") or "main"
    done = {"gate": at is not None, "dev": at is not None}
    done["stg"] = done["dev"] and any(p["at"] >= at for p in promos if p.get("branch") == stg)
    done["main"] = done["stg"] and any(p["at"] >= at for p in promos if p.get("branch") == main)
    done["publish"] = done["main"] and bool(closed)
    cur = next((s for s in DELIVERY_STAGES if not done[s]), None)
    if cur == "gate" and not gating: cur = None
    out = [{"name": s, "state": "done" if done[s] else ("current" if s == cur else "later")} for s in DELIVERY_STAGES]
    return [] if all(c["state"] == "later" for c in out) else out   # every stage waits: the chips say nothing (#2572)

def suite_name(line):
    """The short name a suite row shows (#2507): the pass or the gate its holder's command line names, never a path."""
    if "daysuites.sh" in line: return "Daily suite"
    if "child-gate.sh" in line: return "Child gate"
    if "shell-suites.sh" in line: return "Shell suites"
    return "Gate"

def caps_folder():
    """The machine's caps leases folder, the one squad/caps.py writes, or the folder a test points SQUAD_CAPS_DIR at."""
    return pathlib.Path(os.environ.get("SQUAD_CAPS_DIR") or (pathlib.Path.home() / ".cache" / "agent-squad" / "caps"))

def gate_slots(pid, procs=None, now=None):
    """The gate slots one lock holds and the instant its oldest lease began (#2507): the live caps.gates leases whose holder
    is the lock's own pid or a process under it, because a gate takes its slots from a child of the lock holder. The hub
    only reads: a lease whose pid is gone counts for nothing, and no caps folder answers `(0, None)`."""
    now = time.time() if now is None else now
    try: paths = sorted((caps_folder() / "gates").glob("*.json"))
    except OSError: return 0, None
    leases, table = [], procs if procs is not None else process_table(now)   # one table read for every lease
    for p in paths:
        try: lease = json.loads(p.read_text())
        except (OSError, ValueError): continue
        if not isinstance(lease, dict): continue
        lpid = lease.get("pid")
        if not isinstance(lpid, int) or not _pid_alive(lpid): continue
        q, depth = lpid, 0
        while q is not None and q != pid and depth < 8:   # the subtree the lock holder owns, never the whole machine
            q, depth = getattr(table.get(q), "ppid", None), depth + 1
        if q == pid: leases.append(lease)
    if not leases: return 0, None
    since = min((x["since"] for x in leases if isinstance(x.get("since"), (int, float))), default=None)
    return len(leases), since

LIVE_POLL_SECS = 3.0   # seconds: the page's own live poll (`poll()` reads /api/live every 3 s), never a guess (#1933)
LIVE_CLOCK = time.monotonic   # the age reader; a test injects its own, so freshness is proved by a clock and never by a sleep
_LIVE_KEPT = {"at": None, "answer": None}   # the kept Live work answer: one entry per loaded module, so one per profile
_LIVE_LOCK = threading.Lock()

def live_served(now=None):
    """The Live work answer, computed at most once per the page's own poll window (#1933).

    Every open page and every client asks this profile the same question, and one answer reads the process table, each
    live run's record and the board read, so a per-request answer took most of a core on the live hub. The window is the
    poll's own interval, so the answer a page sees is never older than the poll that would have refreshed it anyway, and
    any number of pages and clients inside one window share one compute. The lock is held across the compute, so
    concurrent readers wait for one answer instead of each paying for one. A request at or past the window computes
    again, never served as fresh. The cache is the loaded module's own, so one profile's answer is never another's."""
    now = LIVE_CLOCK() if now is None else now
    with _LIVE_LOCK:
        at = _LIVE_KEPT["at"]
        if at is not None and now - at < LIVE_POLL_SECS: return _LIVE_KEPT["answer"]
        answer = live_work_lines(board_cache.get("items", []), procs=run_table())   # one read answers every row of the answer
        _LIVE_KEPT.update(at=now, answer=answer)
        return answer

def live_recompute():
    """Clear the kept Live work answer: a loop write changes what the panel draws, so the page that acted reads its own
    change at once instead of waiting out the window."""
    with _LIVE_LOCK: _LIVE_KEPT.update(at=None, answer=None)

def live_work_lines(items, state=None, checkout=None, procs=None, now=None, argv=None):
    """The Live work panel's answer, all from local state and the board read the hub already holds:
    `{"loop": <on|off|draining|starting>, "lines": [<row>], "boardAge": <seconds or None>}`. A row is
    `{"kind", "id", "text", "icon", "title"}`: the kind names its source and the id names the thing it draws, so the card
    places a row by identity, never by a substring of its text (#1697). `text` is the short visible line and `title` the
    tooltip where the pid, the script, the holder and the full title live (#2507). The kinds, in order: `loop` (a stopped
    loop), `run` (each live run: its issue and node), `gate` (each gate lock: the suite's name, the gate slots it holds and
    its age, with `run` the id of the run it checks or None), `stopped` (a loop lane whose record names a dead process),
    `hold` (each hand hold: its issue as a link and the five delivery stage chips its own facts reach) and `hand` (each
    open Doing card no live run and no hold names: work by hand). The loop's state rides apart from the rows, so an open
    loop with nothing to show answers one `empty` row, `no live work`. A ready-for-human card waits on the owner and is no
    live work (#1701). Doing is the column the board draws, an old Status name and a recorded move included, never the
    row's raw Status (#1713). `boardAge` is how many seconds old the board read the `hand` rows came from is, so the panel
    never claims a freshness it does not have. `procs` is one process table (`run_table`'s shape), read here when the
    caller names none: every row of the answer judges that one read, so one poll costs one read of the machine (#2582)."""
    now = time.time() if now is None else now
    base = pathlib.Path(state) if state else STATE
    moves, window = (read_moves() if overlay_on() else {}), pending_window()   # the board's own column inputs, read once
    st = loop_state({}, now=now, state=base)
    rows, issues_of, held, gated = [], {}, set(), set()
    run_issues = {}   # the issues each live run names: a gate's issue is the issues of the run it checks (#2547)
    if st == "off": rows.append({"kind": "loop", "id": "loop", "text": "loop off"})
    table = procs if procs is not None else run_table()   # one read of the machine answers every row below (#2582)
    try: seen = _loop_procs(liveness(), table)
    except Exception: seen = {}
    starts = {pid: p.start for pid, p in table.items()}
    for entry in loop_live(state=base, checkout=checkout, procs=seen)["runs"]:
        folder = active_folder(entry)
        if not folder: continue
        row, _ = run_row(folder, "", starts)
        issues = " ".join(f"#{n}" for n in row["issues"]) or "no issue"
        rows.append({"kind": "run", "id": row["id"], "icon": "run", "age": int(row["age"]),
                     "text": f"{issues} · node {row.get('stage') or '-'}",
                     "title": f"run {row['id']} · {issues} · node {row.get('stage') or '-'} · age {int(row['age'] // 60)} min"})
        issues_of[row["id"]] = issues; held.update(str(n) for n in row["issues"])
        run_issues[row["id"]] = {str(n) for n in row["issues"]}
    for d in sorted((base / "locks").glob("*")):
        if not d.is_dir(): continue   # a stray file in locks/ is not a lock
        try:
            pid = _read(d / "pid").strip()
            if not (pid.isdigit() and _pid_alive(int(pid))): continue
            cmd = _read(d / "cmd").strip()
        except OSError: continue   # one unreadable lock is no lock: it never fails the panel
        if not cmd: continue   # a lock that names no command is a loop driver's own: no live work, and no row (#1692, #2507)
        line = (argv or _holder_argv)(int(pid))
        slots, since = gate_slots(int(pid), table, now)   # the slots the gate under this lock holds (#2507)
        gated.update(run_issues.get(_gate_run(line), ()))   # a gate holds its lock while it runs or waits for its slots
        rows.append({"kind": "gate", "id": f"gate-{d.name}", "run": _gate_run(line), "icon": "microscope",
                     "text": suite_name(line), "slots": slots, "age": int(now - since) if since else None, "red": False,
                     "title": gate_lock_line(pid, line, issues_of)})   # the pid and the script ride the tooltip only
    if st != "off":   # a lane whose own record names a dead process is one red row; a live lane is no row at all (#2507)
        for lane, f in LANES:
            pid = _read(base / f).strip()
            if not pid or (pid.isdigit() and _pid_alive(int(pid))): continue
            rows.append({"kind": "stopped", "id": f"stopped-{lane}", "icon": "run", "age": None, "red": True,
                         "text": f"{lane} stopped", "title": f"the loop's {lane}.sh lane names pid {pid}, which is not alive"})
    try:
        (HHg,) = load_squad(squad_roots(os.environ.get("SG_SQUAD"), os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__), ("handhold",))
        gated.update(HHg.gates(state=base))   # a hand gate in flight names its issue (#2563)
    except Exception as e:
        log_once(f"hub: the hand gates are unknown: {type(e).__name__}: {' '.join(str(e).split())}")
    holds = hold_rows(now=now, state=base)   # each live hand hold is one row, with its carrier (#1713)
    titles = {str(i.get("n")): (i.get("t") or "") for i in items}
    for r in holds:
        n = int(r["issues"][0])
        title = titles.get(str(n)) or ""
        item = next((i for i in items if str(i.get("n")) == str(n)), {})
        rows.append({"kind": "hold", "id": r["id"], "icon": "eye" if r["carrier"] == "hal9k" else "hand",   # the icon its carrier wears (#2572)
                     "carrier": r["carrier"], "issue": n, "repo": REPO,
                     "text": f"#{n}", "age": int(r["age"]), "stages": delivery_stages(n, base, closed=item.get("s") == "closed", gating=str(n) in gated),
                     "title": " · ".join(x for x in (r["label"], title) if x)})   # the id of the hold's own row; the holder and the title live in the tooltip
    hold_ids = {str(n) for r in holds for n in r["issues"]}   # the registry keys a hold on the issue; a row matches on it or on its item id
    for i in items:   # the panel agrees with the Doing column: a card no live run names is worked by hand (#1701)
        ids = {str(i.get("n")), str(i.get("i") or "")}
        if (cache_row_column(i, moves, window) == DOING and i.get("s") != "closed" and not (ids & held)
                and not (ids & hold_ids) and "ready-for-human" not in (i.get("l") or [])):
            rows.append({"kind": "hand", "id": f"hand-{i['n']}", "icon": "hand", "text": f"#{i['n']}",
                         "title": f"{i.get('t') or ''} · in Doing, no registered run"})
    if not rows: rows.append({"kind": "empty", "id": "", "text": "no live work"})   # an open loop with nothing to show says so
    t = board_cache.get("t") or 0
    return {"loop": st, "lines": rows, "boardAge": int(now - t) if t else None}

def task_count(i):
    """How far a card's own work got, `completed` over `total` (`3/5`), off the sub-issue summary the items route the
    pass already reads hands over (#153). Nothing when the issue has no tasks, and nothing when the summary is missing
    or carries no pair of numbers: a card never wears an invented `0/0`. The rule lives here, not in the page: the card
    renders the value its row carries (ADR-0137)."""
    s = i.get("si") or {}
    t, c = s.get("total"), s.get("completed")
    if isinstance(t, bool) or isinstance(c, bool) or not isinstance(t, int) or not isinstance(c, int): return None
    return f"{c}/{t}" if t > 0 else None
def board_items_jq(repo):
    """The board read's jq. A project may hold cards of several repositories and an issue number repeats between them, so an
    Issue item counts only when its repository is `repo`; a draft item has no repository. Every row carries that
    repository as `r`, so a reader of the cache can place a row without trusting the file's own claim (#1429). The
    issue's own close reason rides the same read as `cr` (#921): the key is its own, never `r`. After
    each page's items it emits one {"_page": n} line, n the page's own item count, so the read says whether its last page
    was short (#894)."""
    return ('''length as $n|(.[]|select((.content_type=="Issue" and ((.content.repository_url//"")|endswith("/" + $repo))) or .content_type=="DraftIssue")|{n:.content.number,r:$repo,fd:.content.id,i:.id,t:.content.title,s:.content.state,cr:((.content.state_reason//null)|if type=="string" then ascii_downcase else . end),c:.content.closed_at,st:((.fields[]|select(.name=="Status")|.value.name.raw)//null),k:(((.fields[]?|select(.name=="Kind")|.value.name|if type=="object" then .raw else . end)//"")|ascii_downcase|if .=="" then null else . end),p:((.fields[]?|select(.name=="Priority")|.value.name)//null),gt:(((.fields[]?|select(.name=="Gate Tries")|.value)//null)|if type=="number" and .>0 then . else null end),l:[.content.labels[]?.name],si:{total:.content.sub_issues_summary.total,completed:.content.sub_issues_summary.completed},o:.content.created_at}),{_page:$n}''').replace("$repo", json.dumps(repo))

GH_FLOOR = int(os.environ.get("SG_GH_FLOOR") or 300)   # below this many core requests left, a read pass skips its reads (#855)
LABELS_EVERY = 3600   # seconds between two reads of the repository's labels, and of its closed pull requests (#855)
REST_TIMEOUT = 120   # a REST page that stalls is abandoned at this deadline (ADR-0051); the pass keeps the rows it held
_etags = {}   # route -> (the ETag GitHub answered it with, the answer it carried); a 304 keeps that answer
_SKIPPING = set()   # the reads whose current floor episode has already been logged (#855)
def _under_floor(what):
    """True when a quota pool is under SG_GH_FLOOR: the pass is skipped, and one line says so per episode (#855). GraphQL
    is a pool of its own (#1648): a hub that floors on core alone keeps reading into a GraphQL refusal."""
    for pool in ("core", "graphql"):
        low = ghthrottle.pool_below(pool, GH_FLOOR)
        if low is None:
            continue
        if what not in _SKIPPING:
            _SKIPPING.add(what)
            print(f"hub: skipped {what}: {pool} quota {low} is under SG_GH_FLOOR {GH_FLOOR}; the next pass reads again", file=sys.stderr, flush=True)
        return True
    _SKIPPING.discard(what)
    return False
def _rest_page(route):
    """One REST page through the door, conditional: the ETag it was answered with rides as If-None-Match, and a 304 keeps
    the answer that ETag named. A page that does not answer raises, and the caller keeps what it had."""
    held = _etags.get(route)
    seen = {}
    def transport(r):
        res = (ghread.gh_conditional_transport(r, held[0], timeout=REST_TIMEOUT) if held
               else ghread.gh_transport(r, timeout=REST_TIMEOUT))
        m = re.match(r"HTTP/[\d.]+\s+(\d{3})", res.body)
        seen["status"], seen["etag"] = (int(m.group(1)) if m else None), ghread.headers_of(res.body).get("etag", "")
        return res
    out = ghread.read(route, transport)
    if seen["status"] == 304: return held[1]
    if not out.is_answer: raise RuntimeError(out.line())
    answer = out.answer or []
    if seen["etag"]: _etags[route] = (seen["etag"], answer)
    return answer
def _check_deadline(until):
    """Raises when the full read's overall deadline has passed; a None deadline is no bound."""
    if until is not None and time.time() >= until: raise TimeoutError("full read passed its deadline")
def _bounded(fetch, until):
    """A branch-lane fetcher that never starts a repository's read once the full read's deadline has passed."""
    def bounded(*args):
        _check_deadline(until)
        return fetch(*args)
    return bounded
def gh_list(path, query="", until=None):
    """Every page of a REST list, 100 to a page (ADR-0277): a page shorter than 100 is the last one."""
    rows, page = [], 1
    while True:
        _check_deadline(until)
        answer = _rest_page(f"{path}?{query + '&' if query else ''}per_page=100&page={page}")
        rows += answer
        if len(answer) < 100: return rows
        page += 1

# The relations a card's rule reads: its open blockers, and the parent issue with its labels, so the board knows a ticket
# of a spec without a read per card (#829, #1506). Owner, name and page size are filled in by the read.
OPEN_RELATIONS_QUERY = ('query($cursor:String){repository(owner:"%s",name:"%s"){issues(first:%d,states:OPEN,after:$cursor)'
                        '{pageInfo{hasNextPage endCursor}nodes{number blockedBy(first:20){nodes{number state}} '
                        'parent{number labels(first:20){nodes{name}}}}}}}')
RELATIONS_PAGE = 100       # the page size the relations read asks for
RELATIONS_MAX_PAGES = 20   # a read that would need more pages than this is incomplete, never a silent stop
def open_relations(until=None):
    """Every open issue's blockers and parent, page by page through `gh api graphql` (#829). It returns `(nodes, complete)`:
    a read that fails, asks for another page without a cursor, or runs past the page cap is incomplete, so a caller never
    reads the cards it did not reach as issues with no parent."""
    own, name = REPO.split("/", 1)
    nodes, after = [], None
    for _ in range(RELATIONS_MAX_PAGES):
        _check_deadline(until)
        argv = ["gh", "api", "graphql", "-f", "query=" + OPEN_RELATIONS_QUERY % (own, name, RELATIONS_PAGE)]
        if after: argv += ["-f", "cursor=" + after]
        out = subprocess.run(argv, capture_output=True, text=True, timeout=60).stdout   # a call that fails raises, and the pass keeps the rows it had
        try: page = (((json.loads(out) or {}).get("data") or {}).get("repository") or {}).get("issues") or {}
        except (TypeError, ValueError):
            return nodes, False
        if not isinstance(page, dict): return nodes, False
        nodes += [n for n in page.get("nodes") or [] if isinstance(n, dict)]
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"): return nodes, True
        after = info.get("endCursor")
        if not after: return nodes, False   # a page that asks for another and names no cursor is incomplete
    return nodes, False
def board_pass(fetch_open=None, fetch_landed=None, repos=None):
    """One board re-read: Board 4 statuses (REST) + every GraphQL page of Issue.blockedBy and Issue.parent + the open work branches and the
    landing ledger of every repository the hub is told to read (#1820). The full-read loop runs exactly this.

    The two reads are independent: the issues are the profile repository's board, the branch lane spans every configured
    repository. A read that fails keeps the last good rows it already had and says so (the pass's error, or the
    repository's own error on the lane), so a failed pass never writes a fresh empty board (#1772, #1820)."""
    if _under_floor("board pass"): return True
    items = lc = cases = None
    gaps = []   # why the flow sample is skipped when the reads answered but are not whole (#894); none when they are whole
    read_started = time.time()   # when the list read begins: each row keeps it as its observation instant (#974)
    until = read_started + FULL_DEADLINE
    try:
            out = board_seam().rows(
                board_items_jq(REPO),
                kind=True, tries=True, timeout=120)
            lines = [json.loads(l) for l in out.splitlines() if l.strip()]
            # The jq answer always ends each page with its count (board_items_jq), even for no card: a read with none answered nothing (#894).
            # A full last page is no sign of a missing page: a project of exactly 100 items answers the same way.
            if not any("_page" in x for x in lines):
                raise ValueError("items read unparseable: no page count came back")
            items = [x for x in lines if "_page" not in x]
            cases = [i["t"] for i in items if i.get("n") is None and i.get("k") == "test case"]   # a test case is a draft item: it has no number, so it is a row and never a card (#306)
            items = [i for i in items if i.get("n") is not None]
            have = {i["n"] for i in items}   # open issues that never reached the board have no state either
            for i in gh_list(f"repos/{REPO}/issues", "state=open", until):
                if i.get("pull_request") is None and i["number"] not in have:
                    s = i.get("sub_issues_summary") or {}
                    # `nc` says this row is not a card: it is an open issue the board does not carry (#1429). A reader
                    # that names board cards drops it, so an issue with no card is never read as one with no column.
                    items.append({"n": i["number"], "r": REPO, "nc": True, "t": i["title"], "s": i["state"], "c": None, "st": None, "l": [x["name"] for x in i["labels"]],
                                  "si": {"total": s.get("total"), "completed": s.get("completed")}, "o": i["created_at"]})
            nodes, complete = open_relations(until)   # every page of open issues; never one REST call per card
            if not complete: gaps.append("relations read incomplete: a page with no cursor, or past the page cap")
            try: apply_open_blockers(items, nodes)
            except Exception: pass
            # The spec a ticket belongs to: a ticket never moves alone (#829). A read that is not complete keeps what the
            # last good pass recorded, and marks the cards it never reached.
            try: apply_parent_specs(items, nodes, complete, board_cache.get("items") or [])
            except Exception: pass
            if time.time() - board_cache.get("lt", 0) >= LABELS_EVERY:
                lc = {x["name"]: x["color"].lower() for x in gh_list(f"repos/{REPO}/labels", until=until)}
                board_cache["lt"] = time.time()
    except Exception as e:
        with _board_lock:
            board_cache["err"], items = str(e)[:80], None   # a failed pass keeps the last good rows on the board (#1810)
            for part in ("status", "structure"): set_part(part, err=board_cache["err"])
    if items: apply_holders(items)
    if items is not None:
        with _board_lock:
            board_cache.update(t=time.time(), repo=REPO, items=merge_items(board_cache.get("items"), items, int(read_started)),
                               cases=cases or [], lc=lc or board_cache.get("lc", {}), err=None)
            for part in ("status", "structure"): set_part(part, err=None)
    rs = REPOS if repos is None else repos
    try:
            prs, landed, errs = prs_pass(_bounded(fetch_open or open_rows_of, until), _bounded(fetch_landed or landed_branches_of, until), rs)
            with _board_lock:
                board_cache.update(prs=prs, landed=landed, errs=errs)
                set_part("prs", err="; ".join(errs) or None)
                # a pass no repository answered is not a refresh: `t` keeps saying when the board last really moved, which
                # is the age the header shows (#1810), instead of claiming a fresh board nothing came back for.
                if len(errs) < len(rs): board_cache["t"] = time.time()
    except Exception as e:
        with _board_lock:
            board_cache["err"] = str(e)[:80]
            set_part("prs", err=board_cache["err"])
    with _board_lock: save_board()
    # Every pass writes its flow read, whole or not (#894): a failed read is a flow_skip that counts the last whole read's cards.
    rows = items if items is not None else (board_cache.get("items") or [])
    if items is None:
        gaps.insert(0, f"items read failed: {board_cache.get('err')}")
    _flow.append(_flow.history_path(BOARD, REPO), rows, incomplete="; ".join(gaps) or None)
    return items is not None
MERGED_INPUTS = ("st", "s", "l", "cr", "su", "iu")   # the column inputs of a row: Status and its instant, item instant, issue state, labels and close reason (#974)
def merge_items(held, fresh, observed):
    """The rows of one read merged into the rows the cache holds (ADR-0149). Every row takes the read's structure; its column
    inputs are written only when the read's observation (`observed`, whole epoch seconds) is newer than the one the cache
    holds for that row. A row carries the newest observation of what it holds, so the overlay compares a move with it."""
    kept = {i.get("n"): i for i in held or []}
    out = []
    for row in fresh:
        old = kept.get(row.get("n"))
        merged = {**row, "ot": observed}
        if old is not None and (old.get("ot") or 0) >= observed:
            for k in MERGED_INPUTS:
                if k in old: merged[k] = old[k]
                else: merged.pop(k, None)
            merged["ot"] = old["ot"]
        out.append(merged)
    return out

class SyncMissing(Exception):
    """The card GitHub no longer carries: a press keeps the row it held and names the absence (#1668)."""

def sync_instant(now=None):
    """The instant of one press's read, in the same ISO shape the board's rows carry (#1668)."""
    return dt.datetime.fromtimestamp(time.time() if now is None else now, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def label_names(labels):
    """One read's labels as the names the board carries: `ghread` answers REST-shaped `[{"name": ...}]`, and the cache
    holds strings, as every other reader of the same answer (#1682)."""
    return [x.get("name") if isinstance(x, dict) else x for x in (labels or []) if (x.get("name") if isinstance(x, dict) else x)]

def read_one_card(n):
    """One card re-read from GitHub now, as a cache item (#1668).

    One aggregate issue read carries the title, state, labels, the open blocking edges and the sub-issue count (ghread);
    the seam answers the card's own Status and holder by its item id, one call each. A read that does not answer raises,
    and the caller keeps the row it held; a card GitHub no longer carries raises SyncMissing. The read sends no write."""
    r = ghread.read(f"repos/{REPO}/issues/{n}")
    if not r.is_answer: raise RuntimeError(r.line()[:200] or "the read did not answer")
    d = r.answer
    if not isinstance(d, dict) or d.get("number") != n: raise SyncMissing(f"GitHub no longer carries #{n}")
    seam = board_seam()
    st = seam.read_one(n)                     # the column, one item read
    h = seam.holder_one(n)                    # the holder, one item read
    old = next((i for i in board_cache.get("items") or [] if i.get("n") == n), None) or {}
    item = dict(old)                          # fields the press does not read (item id, Kind, Gate Tries) stay
    item.update(n=n, r=old.get("r") or REPO, t=d.get("title"), s=d.get("state"), o=d.get("created_at"),
                l=label_names(d.get("labels")))   # names, never the read's own objects (#1682)
    if st is not None: item["st"] = st or None
    si = d.get("sub_issues_summary")
    if isinstance(si, dict): item["si"] = si
    if d.get("blocked_by_read") is not False:      # a read that carried no relation proves no edge, so the old one stays
        open_ns = sorted({b["number"] for b in d.get("blockers") or []
                          if isinstance(b, dict) and str(b.get("state") or "").lower() == "open" and isinstance(b.get("number"), int)})
        if open_ns: item["b"] = open_ns
        else: item.pop("b", None)
    if h is not None:
        if h.get("run"): item["h"] = h
        else: item.pop("h", None)
    return item

def synced_rows(n, at):
    """The board rows for one card, each naming the column it draws as `st` and this read's instant as `ri` (#1668)."""
    out = []
    for g in board().get("groups") or []:
        for r in g.get("items") or []:
            if r.get("n") == n: out.append({**r, "st": g.get("name"), "ri": at})
    return out

SYNC_TREE_DEPTH = 3          # the levels below a spec one press descends into (#1670)
SYNC_TREE_NODES = 50         # the cards one press reads, the spec included (#1670)
SYNC_TREE_SECONDS = 20.0     # one press's deadline: a read already begun finishes, none begins past it (#1670)
_sync_frontier = {}          # a capped spec's unread numbers, which its next press continues from (#1670)

def _has_subs(item):
    return bool((item.get("si") or {}).get("total"))

def _link_parent(item, parent, parent_spec):
    """Write the parent a press's walk knows as `pa` when that parent is a spec, as the board read does (ADR-0142)."""
    item.pop("pau", None)
    if parent_spec and isinstance(parent, int): item["pa"] = parent
    else: item.pop("pa", None)

def _link_root(root):
    """A press on a ticket keeps the spec the cached row names; with none, the card is `pau` until a read names one."""
    kept = next((i.get("pa") for i in board_cache.get("items") or [] if i.get("n") == root["n"]), None)
    if isinstance(kept, int): root["pa"] = kept; root.pop("pau", None)
    else: root.pop("pa", None); root["pau"] = True

def sync_tree(root):
    """The root and, when it is a spec, every sub-issue under it, read to the depth, node and time bounds (#1670).

    Answers (items read, the cap that stopped the walk or None, a note). A press past a bound keeps the numbers it did not
    read, and the next press continues from them as fresh roots, so a wide tree is read across presses, never in one."""
    n = root["n"]
    if _columns_module.SPEC_LABEL not in (root.get("l") or []):
        _link_root(root)
        return [root], None, ""
    started = time.monotonic()
    if n in _sync_frontier: pending = [(x, 0, p, ps) for x, p, ps in _sync_frontier.pop(n)]
    else:
        subs, err = sub_issues(n, root.get("r") or REPO)
        if err: return [root], None, err
        pending = [(s["number"], 1, n, True) for s in subs if isinstance(s.get("number"), int)]
    items, seen, deferred, rest = [root], {n}, [], []
    capped, failed = None, 0
    while pending:
        num, depth, parent, parent_spec = pending.pop(0)
        if num in seen: continue
        if len(items) >= SYNC_TREE_NODES:
            capped, rest = "nodes", [(num, parent, parent_spec)] + [(x, p, ps) for x, _, p, ps in pending]
            break
        if time.monotonic() - started >= SYNC_TREE_SECONDS:
            capped, rest = "deadline", [(num, parent, parent_spec)] + [(x, p, ps) for x, _, p, ps in pending]
            break
        seen.add(num)
        try: child = read_one_card(num)
        except Exception: failed += 1; continue
        _link_parent(child, parent, parent_spec)
        items.append(child)
        if not _has_subs(child): continue
        if depth >= SYNC_TREE_DEPTH:
            capped = capped or "depth"
            deferred.append((num, parent, parent_spec))
            continue
        subs, err = sub_issues(num, child.get("r") or REPO)
        if err: failed += 1; continue
        child_spec = _columns_module.SPEC_LABEL in (child.get("l") or [])
        pending += [(s["number"], depth + 1, num, child_spec) for s in subs if isinstance(s.get("number"), int)]
    frontier = deferred + rest
    if frontier: _sync_frontier[n] = frontier
    else: _sync_frontier.pop(n, None)
    note = f"{failed} sub-issue read(s) did not answer" if failed else ""
    return items, capped, note

def sync_card(n):
    """POST /api/sync: re-read one card from GitHub now, and a spec's sub-issues with it, and answer the rows refreshed (#1668, #1670).

    A press is a read. It sends no move, no close and no edit to GitHub, and it never touches the board cache's own read
    instant: each row it refreshes carries the instant of this read as `ri`. A failed read answers its error, empties
    nothing and keeps the row the page already had. A card GitHub no longer carries keeps its last good row and is named.
    The answer is {n, rows, count, at, err, missing, capped, note}; `capped` names the bound a spec's tree passed."""
    if isinstance(n, bool) or not isinstance(n, int) or n <= 0: return {"err": "n: a card number"}
    at = sync_instant()
    held = next((i for i in board_cache.get("items") or [] if i.get("n") == n), None)
    try:
        root = read_one_card(n)
    except SyncMissing as e:
        rows = synced_rows(n, at)
        if not rows and held is not None: rows = [{"n": n, "t": held.get("t"), "c": held.get("c"), "l": held.get("l") or [], "st": held.get("st"), "ri": at}]
        return {"n": n, "rows": rows, "count": 0, "at": at, "err": None, "missing": True, "capped": None, "note": str(e)}
    except Exception as e:
        return {"n": n, "rows": [], "count": 0, "at": at, "err": str(e)[:200] or "the read did not answer", "capped": None}
    items, capped, note = sync_tree(root)
    ids = {i["n"] for i in items}
    with _board_lock:
        board_cache["items"] = [i for i in board_cache.get("items") or [] if i.get("n") not in ids] + items
    rows = [r for i in items for r in synced_rows(i["n"], at)]
    if not rows:
        closed = str(root.get("s") or "").lower() == "closed"
        note = note or (f"#{n} is closed: it is no longer an open card" if closed else f"#{n} has no open column")
    return {"n": n, "rows": rows, "count": len(rows), "at": at, "err": None, "capped": capped, "note": note}

def set_part(name, err):
    """Stamp one part of the cache (status, structure, prs) with its read's instant and error: a failed read keeps the
    part's last good timestamp and names its error (#974)."""
    parts = board_cache.setdefault("parts", {})
    prev = parts.get(name) or {}
    parts[name] = {"t": time.time() if err is None else prev.get("t", 0), "err": err}
_WRITE_SEQ = itertools.count(1)   # one temp name per write, never one per writer: a thread id is reused once its thread ends (#512)
def _temp_name(path):
    """The temp name one write owns: the pid, so two processes never meet, and a counter, because a thread id is reused
    once its thread has ended (#512, #743)."""
    return path.with_name(f"{path.name}.{os.getpid()}.{next(_WRITE_SEQ)}.tmp")
def save_board():
    """The cache file writes the rows the hub holds. The temp name is the write's own (#512): hubmux runs one hub per
    profile in one process, so the pid alone is shared."""
    _tmp = _temp_name(BOARD); _tmp.write_text(json.dumps(board_cache)); os.replace(_tmp, BOARD)
FULL_EVERY = 600   # seconds between full board reads; a board-dirty flag reads at once
FULL_RETRY_FIRST = 15   # seconds before the next try of a failed full read; each further failure doubles it, never past FULL_EVERY (#2207)
SETTLE_EVERY = 16   # seconds before the pass a board move asks for: the items route lags a write by seconds (#1790)
DIRTY_MIN_SECS = 30.0   # the least gap between two full reads a card move asks for: a board of a thousand cards is a
                        # dozen pages, so one read per move spent the hub's CPU on a board that changed in one card (#1790)
FAST_EVERY = 15    # seconds between fast reads of the open cards
FULL_DEADLINE = 300   # the full read's overall deadline (ADR-0051): its page loops and branch fetches stop past it
BOARD_OVERDUE_SECS = FULL_EVERY + FULL_DEADLINE   # a board item read older than the cadence plus one full pass is overdue (#1661)
FAST_DEADLINE = 60   # the fast read's overall deadline (ADR-0051)
FAST_POINTS_PER_HOUR = 2400   # five pages of 100 cards at two points each, every 15 s (GitHub's documented cost, not measured live); spent, it stops for the hour
FAST_BUDGET = board_fast.Budget(FAST_POINTS_PER_HOUR)
_board_lock = threading.Lock()   # the fast read and the full read write one cache; the lock covers the write, never a read
def _fast_root():
    """The GraphQL root of the project's owner: `organization` for an organization, else `user`."""
    return "organization" if board_seam().binding.kind == "orgs" else "user"
def _graphql(query, variables, timeout):
    """One fast-read page through the GitHub door: a refusal, a failure or a partial answer raises, so no page moves a card."""
    out = ghread.graphql_read(query, variables, timeout=timeout, pass_name="fast-board")
    if not out.is_answer: raise RuntimeError(out.line())
    if out.partial: raise RuntimeError("partial answer: " + ", ".join(out.partial))
    return out.answer
def fast_board_pass():
    """One fast read: the column inputs of the cards the cache holds, written where the snapshot is newer. A card the last
    whole snapshot held and this one lacks is checked in one batched read (#976). A card this snapshot carries and the
    cache does not hold touches the board-dirty flag once per full read, so the read runs within 2 s (#985). A failed
    read moves nothing. Known limit, stated rather than hidden: the snapshot drops a card already in a terminal column,
    so such a card is neither asked for nor counted, and it waits for the full period."""
    observed = time.time()
    deadline = observed + FAST_DEADLINE
    try: nodes = board_fast.read_snapshot(_graphql, _fast_root(), OWNER, PROJECT_NO, _columns_module.TERMINAL, FAST_BUDGET, deadline)
    except board_fast.SnapshotError as e:
        with _board_lock:
            set_part("status", err=str(e)[:80])
        return
    rows = board_fast.rows_of(nodes, REPO)
    with _board_lock:
        lost = board_fast.missing(board_cache.get("fast_seen") or {}, rows, board_cache.get("items") or [])
    verdicts = board_fast.check(_graphql, OWNER, PROJECT_NO, lost, FAST_BUDGET, deadline)
    with _board_lock:
        unread = board_fast.unknown(rows, board_cache.get("items") or [])
        asked = dict(board_cache.get("fast_asked") or {}) if isinstance(board_cache.get("fast_asked"), dict) else {}
        read_t = board_cache.get("t") or 0
        # One ask per (database id, full read): a pass every 15 s never becomes a read every 15 s, and an ask a full read
        # has outrun (it was in flight and stamped its own end after the touch) is spent without effect, so a later pass
        # asks again under the new read (#985).
        fresh = [r for r in unread if r["fd"] is not None and not (asked.get(str(r["fd"])) is not None and asked[str(r["fd"])] >= read_t)]
        if fresh:
            try:
                (STATE / "board-dirty").touch()   # the full read runs within 2 s, never the whole period (#985)
                for r in fresh: asked[str(r["fd"])] = read_t
                board_cache["fast_asked"] = asked
            except OSError: pass
        board_cache["fast_pending"] = [r["fd"] for r in unread if r["fd"] is not None]
        items = board_fast.apply(board_cache.get("items") or [], rows, int(observed))
        board_cache["items"] = board_fast.settle(items, verdicts, int(observed))
        board_cache["fast_seen"] = board_fast.seen_of(rows, verdicts)
        set_part("status", err=None)
        save_board()
def refresh_fast():
    """fast_board_pass() every FAST_EVERY seconds, on its own thread, so a full read never holds it."""
    while True:
        time.sleep(FAST_EVERY)
        try: fast_board_pass()
        except Exception as e:
            with _board_lock: set_part("status", err=str(e)[:80])   # one failed fast pass never ends the loop
def retry_wait(failures):
    """Seconds a failed full read waits before its next try, counted from when that read ended (#2207)."""
    return min(FULL_EVERY, FULL_RETRY_FIRST * 2 ** (failures - 1))
def _cooldown_until():
    """The instant the shared GitHub cooldown holds reads until, or 0 when none holds (ghthrottle, #2207)."""
    return float(ghthrottle.Throttle(state_dir=STATE, clock=time.time).holding().get("until", 0))
def refresh_board():
    """board_pass() every FULL_EVERY seconds, or within 2 s of a board move made through scripts/board.sh (board-dirty flag).
    The items route lags a write by seconds, so a pass a move asked for is read again 16 s later: the card never waits a minute in its old column."""
    quick, fails = False, 0
    watched = read_wanted()
    while True:
        wait = FULL_EVERY
        if watched:   # the board read is a page's reading: with no page it does no work (#1790)
            try: failed = board_pass() is False
            except Exception as e:
                failed = True
                with _board_lock: board_cache["err"] = str(e)[:80]   # one failed pass never ends the loop (#512): the next one still runs
            fails = fails + 1 if failed else 0
            if fails: wait = retry_wait(fails)
        cooling = _cooldown_until()
        wait = max(min(wait, SETTLE_EVERY) if quick else wait, cooling - time.time())
        for _ in range(math.ceil(wait / 2)):
            read_pause(2)
            held = time.time() < cooling
            now_watched = read_wanted()
            if now_watched and not watched:
                watched = True
                if not held: quick = False; break   # the page's first read: the board is read now, not a cycle later (#1790)
                continue
            if not now_watched:
                watched = False
                continue                              # no page: the dirty flag is not read either
            if held: continue                         # a GitHub cooldown holds: no read starts before it ends (#2207)
            try:
                if (STATE / "board-dirty").stat().st_mtime > board_cache.get("t", 0):
                    if fails or time.time() - (board_cache.get("t") or 0) < DIRTY_MIN_SECS: continue   # a burst of moves asks once (#1790): the fast read carries each card's column in the meantime
                    quick = True; break
            except OSError: pass
        else: quick = False
