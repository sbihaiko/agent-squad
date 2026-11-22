# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
def strip_instant(strip, t):
    """The strip with its time-dependent `age` and `old` replaced by `at`, the poll's own instant: an unchanged board keeps its
    ETag while time passes, and the page counts the age (#973). A card the batched query left without facts carries no
    strip at all, and that absence is kept as it is."""
    if not strip: return strip
    return {**{k: v for k, v in strip.items() if k not in ("age", "old")}, "at": t or 0, "max": STRIP_MAX_AGE}

_served_board = {}
def board_served():
    """The served board body and its ETag, built once per change (#973). The key is the cache, the pending-move record and the
    overlay settings after which the board is a pure function; an error answer is never kept. Returns (body, etag or None)."""
    moves = read_moves() if overlay_on() else {}
    _HOLDER_CACHE.clear(); _ACTIVE_RUNS.clear()   # the key reads the same facts the body will: a memo from an earlier build would key this board on a liveness it no longer has (#1790)
    holders = [(i["n"], holder_fact(i)[1]) for i in board_cache["items"] if (i.get("h") or {}).get("run")]   # a holder's process moves with no board read (#1661)
    key = hashlib.sha256(json.dumps([board_cache, moves, overlay_on(), pending_window(), board_stale(), flight_snapshot(), board_read_overdue(board_cache.get("t")), holders, predictions()], sort_keys=True, default=str).encode()).hexdigest()
    if _served_board.get("key") == key: return _served_board["body"], _served_board["etag"]
    try:
        _HOLDERS_READ["v"] = True   # the key read every holder a moment ago: this build asks for none of them again
        body = json.dumps(board(moves)).encode()   # every card: the page filters the board by the bar (#899)
    except ValueError as e: return json.dumps({"error": str(e)}).encode(), None
    finally: _HOLDERS_READ["v"] = False
    etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    if _served_board.pop("settling", False): _served_board.pop("key", None); return body, etag
    _served_board.update(key=key, body=body, etag=etag)
    return body, etag

def label_wave(labels):   # the `wave:NN` label's number, or None
    w = [int(m.group(1)) for l in labels for m in [re.match(r"wave:(\d+)$", l)] if m]
    return min(w) if w else None

def board_summary(drawn):   # drawn: (number, column, parent spec, wave, is a spec) for every item the list read holds
    kids, waves, far = {}, {}, {}   # far: spec -> (rank, column, count) of the furthest column its tickets reached
    ends = {COLUMN["issue_closed"], COLUMN["abandoned"]}
    ranks = {name: i for i, name in enumerate(ORDER)}   # the axis the lanes draw, so "furthest" reads the same as a column
    gone = {n for n, st, _, _, sp in drawn if sp and st in ends}   # a ticket of a closed spec reads as No spec in the lanes (#972)
    for _, st, pa, wv, sp in drawn:
        if sp: continue
        for store, key in ((kids, pa), (waves, (None if pa in gone else pa, wv))):
            c = store.setdefault(key, [0, 0])
            c[1] += 1
            if st == COLUMN["issue_closed"]: c[0] += 1
        # A spec's card keeps its own column, so how far its tickets got is a count beside it, never its own column (#1906).
        # A given-up ticket did not reach past the work it left, so an Abandoned ticket names no furthest column.
        if pa is not None and pa not in gone and st in ranks and st != COLUMN["abandoned"]:
            have = far.get(pa)
            if have is None or ranks[st] > have[0]: far[pa] = (ranks[st], st, 1)
            elif ranks[st] == have[0]: far[pa] = (have[0], st, have[2] + 1)
    specs = [{"n": n, "st": st, "closed": kids.get(n, [0, 0])[0], "total": kids.get(n, [0, 0])[1],
              "far": (far.get(n) or (0, None, 0))[1], "far_n": (far.get(n) or (0, None, 0))[2]} for n, st, _, _, sp in drawn if sp]
    return {"specs": specs, "waves": [{"spec": pa, "wave": wv, "closed": c, "total": t} for (pa, wv), (c, t) in waves.items()]}

PROMOTE_TO = COLUMN["issue_todo"]   # the column the arrow moves a card into: the owner's go signal (ADR-0142)
def promotable(item):
    """The one rule of the owner's move (#560, #829): a card is promotable when it is open, the board holds it in Backlog,
    and it is a spec or an issue that no spec holds. A ticket of a spec moves with its spec, never alone (ADR-0142). The
    board route serves this rule as the row's `pm` and `pmt`, and the promote route asks the same function before any
    write, so a card the board marks not promotable is refused by the route that would move it (ADR-0137, #829).
    `pa` is the parent spec the board read recorded on the row; `pau` marks a card whose parent the read never reached,
    and no read has named it since: an unknown parent is never read as no spec parent, so the rule fails closed."""
    if not isinstance(item, dict): return False
    if str(item.get("s") or "").lower() == "closed": return False
    if _columns_module.view_column(item.get("st"), False, item.get("l"))[0] != COLUMN["unscheduled"]: return False
    if _columns_module.SPEC_LABEL in (item.get("l") or []): return True
    if item.get("pa"): return False
    return not item.get("pau")
def promote_write(issues, add=False):
    """The one writer call of the owner's move: `<owner> <project> Todo <n>...`, the shape reconcile.py writes, so the
    board seam (`board.sh`) is the only reach of the carrier. `issues` is one number or a list: a spec and its tickets
    move in one call (#838). `add` asks the same door for `--add` first, for a card the board holds no item of: the row
    came from the open-issue read (#815). The call carries the state directory this hub resolved: hubctl starts the hub
    with no SG_STATE, and board.py reads the key to find its caches (#1750)."""
    nums = issues if isinstance(issues, list) else [issues]
    env = {**os.environ, "SG_STATE": str(STATE), "SG_REPO": REPO, "SG_MOVE_BY": "owner"}   # the owner's arrow names its dispatch context (#861); the repository is the one the hub resolved, since hubctl starts it with none and an add runs only with one (#1315)
    # The item ids the hub already holds go to the writer, so a move reads no whole-project list for an id (#1300).
    env["SG_BOARD_ITEM_HINTS"] = json.dumps({str(i["n"]): i["i"] for i in board_cache.get("items") or []
                                             if i.get("n") in nums and i.get("i") and (i.get("r") or REPO).lower() == REPO.lower()})   # this repository's cards only (the seam's own filter)
    argv = ["bash", str(pathlib.Path(__file__).resolve().parent / "board.sh")]
    if add: argv.append("--add")
    # board.py settles each card it moves (SG_BOARD_SETTLE_SECONDS per card), so the budget is the base plus one settle for
    # every card after the first (#838). A timeout may leave some of the cards moved; promote() says so.
    timeout = PROMOTE_TIMEOUT_BASE + _settle_seconds() * max(0, len(nums) - 1)
    return subprocess.run(argv + [OWNER, str(PROJECT_NO), PROMOTE_TO, *map(str, nums)], capture_output=True, text=True, timeout=timeout, env=env)
PROMOTE_TIMEOUT_BASE = 120.0   # seconds the writer gets for one card before any settle budget (#838)
def _settle_seconds():
    """SG_BOARD_SETTLE_SECONDS, the settle budget board.py gives each card (default 60), read as the writer reads it."""
    try: return max(0.0, float(os.environ.get("SG_BOARD_SETTLE_SECONDS", "60")))
    except ValueError: return 60.0
SUB_ISSUES_PAGE = 100          # the page size the sub-issue read asks for
SUB_ISSUES_MAX_PAGES = 20      # a spec with more sub-issues than this is refused, never half-moved (#838)
# The labels that hold a child out of the column flow (janitor.py): an owner decision, or a closure.
# A spec's move never puts such a ticket in Todo, where a dispatcher would take it as work (ADR-0142 sections 2 and 6).
TICKET_HOLD_LABELS = ("needs-owner", "needs-ratification", "wontfix")
def _repo_of(url, fallback):
    """The lower-case owner/name a REST `repository_url` names (`https://api.github.com/repos/<owner>/<name>`), or the
    fallback when the answer names none."""
    tail = str(url or "").rsplit("/repos/", 1)
    return (tail[1].strip("/") if len(tail) == 2 else fallback).lower()
def sub_issues(issue, repo):
    """Every sub-issue of one issue, page by page through ghread (#838): a page that comes back full asks for the next
    one. A refusal or a failure on any page answers its reason and no list, so a half-read spec never moves."""
    subs = []
    for page in range(1, SUB_ISSUES_MAX_PAGES + 1):
        r = ghread.read(f"repos/{repo}/issues/{issue}/sub_issues?per_page={SUB_ISSUES_PAGE}&page={page}")
        if not r.is_answer: return None, r.line()[:300]
        if not isinstance(r.answer, list): return None, "sub-issues: the read did not answer a list"
        subs += r.answer
        if len(r.answer) < SUB_ISSUES_PAGE: return subs, None
    return None, f"sub-issues: more than {SUB_ISSUES_MAX_PAGES * SUB_ISSUES_PAGE} answered: nothing moved"
def spec_tickets(issue, item):
    """The tickets a spec moves with (ADR-0142, #838): its open sub-issues that the board cache holds in Backlog, in this
    repository. The sub-issue list is read through ghread, so a refusal or a failure is named and never read as "no ticket":
    it answers `(tickets, None)` or `(None, reason)`. A closed ticket, a ticket in another column, a ticket that is itself a
    spec, and a ticket the owner holds (TICKET_HOLD_LABELS) keep their column (ADR-0143, ADR-0142). A sub-issue of another
    repository is never a card of this board, even when its number is one (write-side: board.sh is bound to SG_REPO).
    It answers `(tickets, skipped, None)`, where `skipped` names the open tickets past Backlog, or `(None, None, reason)`."""
    closed = lambda v: str(v or "").lower() == "closed"
    repo = item.get("r") or REPO
    try: subs, err = sub_issues(issue, repo)
    except Exception as e: return None, None, f"sub-issues: {str(e)[:200] or 'the read did not answer'}"
    if err: return None, None, err
    cards = {((i.get("r") or REPO).lower(), i.get("n")): i for i in board_cache.get("items") or []}
    tickets, skipped = [], []
    for sub in subs:
        if not isinstance(sub, dict): continue
        c = cards.get((repo.lower(), sub.get("number")))
        if c is None or _repo_of(sub.get("repository_url"), repo) != repo.lower(): continue
        if closed(sub.get("state")) or closed(c.get("s")) or in_flight(c["n"]): continue   # a ticket already in flight is moving, not Backlog (#1300)
        labels = {*(c.get("l") or []), *(x.get("name") for x in sub.get("labels") or [] if isinstance(x, dict))}
        if _columns_module.SPEC_LABEL in labels or labels & set(TICKET_HOLD_LABELS): continue
        if _columns_module.view_column(c.get("st"), False, c.get("l"))[0] != COLUMN["unscheduled"]:
            skipped.append(c["n"])
            continue
        tickets.append(c)
    return tickets, skipped, None
def promote(issue):
    """Move one open card from Backlog to Todo through the board seam (#560). Only a card the hub holds in Backlog now
    is accepted: a closed issue keeps its column (ADR-0143), and a card already in Todo sends no write (ADR-0144). A card
    the board marks not promotable is refused (#829): a ticket of a spec moves with its spec, never alone (ADR-0142). A spec
    moves with its open Backlog tickets in one write (ADR-0142, #838): the owner's move starts the spec and its tickets
    follow. A spec with no ticket moves alone, and a failed ticket read moves nothing. A row the board read never carried
    is an open issue with no Project item, and the seam is asked for `--add` there: a plain move answers it "not on the
    board" and writes nothing (#815). A proven write updates the cache, so the cards move at the next refresh without a
    GitHub read right after the write. The answer names every card the write moved."""
    if isinstance(issue, bool) or not isinstance(issue, int) or issue <= 0: return {"err": "issue: a number"}
    item = next((i for i in board_cache.get("items") or [] if i.get("n") == issue), None)
    if item is None: return {"err": "not on the board"}
    if str(item.get("s") or "").lower() == "closed": return {"err": "the issue is closed: a closed card keeps its column"}
    if in_flight(issue): return {"err": "the card is already moving to Todo: the writer has not answered yet"}
    # The column the board draws, a proved move's overlay included: the same function the board route asks, so the rule
    # reads one column in both routes and a card drawn as moved is never written again (#829).
    st = drawn_column(item, read_moves() if overlay_on() else {}, pending_window())["st"]
    col, _ = _columns_module.view_column(st, False, item.get("l"))
    if col != COLUMN["unscheduled"]: return {"err": f"the card is in {col}, not {COLUMN['unscheduled']}: only a Backlog card moves"}
    if not promotable({**item, "st": st}): return {"err": "the card is a ticket of a spec: it moves with its spec, never alone (ADR-0142)"}
    tickets = []
    if _columns_module.SPEC_LABEL in (item.get("l") or []):
        tickets, _, err = spec_tickets(issue, item)
        if err: return {"err": err}
    cards = [item, *tickets]
    start_write(cards)
    return {"ok": True, "issue": issue, "to": PROMOTE_TO, "pending": [c["n"] for c in cards]}

# The writes the hub has sent and not yet had answered (#1300). A card in flight draws in Todo with a pending mark, and a
# refusal the writer gave stays on its card until the owner moves it again. Both live in this process and reset on a restart,
# which is the one thing a restart may lose: the board read draws the card where GitHub holds it.
MOVE_FLIGHT = {}           # card number -> the column its write goes to, from the moment the route answers until the writer does
MOVE_REFUSED = {}          # card number -> the reason the writer refused its last move
PROVED = {}                # item id -> (column, instant) of the record entry the writer's proof confirmed (#1300): its mark is cleared
MOVE_LOCK = threading.Lock()
MOVE_THREADS = []          # the background writes this process started; wait_for_moves joins them
def proof_confirmed(item_id, column, entry):
    """Whether the writer proved the move this record entry holds for the item: the hub sent the write, the writer printed the
    card as moved, and the board record holds that move at `column` and instant. A move the hub did not send (a loop move, or
    a record a restart left) is never proved here, so its mark stays until a read agrees (#1300)."""
    if not isinstance(entry, dict): return False
    with MOVE_LOCK: return PROVED.get(str(item_id)) == (column, entry.get("at"))
def confirmed_proofs(cards, moved):
    """The record entries the writer's proof confirms, as {item id: (column, instant)}: one for each card the writer printed
    as moved whose item id the board record holds at the column the move set. A card with no item id, or a record that names
    no such move, confirms nothing, so its mark stays until a read agrees (#1300)."""
    if not overlay_on(): return {}
    moves = read_moves()
    out = {}
    for c in cards:
        if c["n"] not in moved or not c.get("i"): continue
        entry = moves.get(str(c["i"]))
        if not isinstance(entry, dict) or not entry.get("at"): continue
        column = _columns_module.SHIPPED_NAME.get(entry.get("column"), entry.get("column"))
        if column == PROMOTE_TO: out[str(c["i"])] = (column, entry["at"])
    return out
def in_flight(n):
    """Whether a write for card `n` is sent and not yet answered: the owner's move is not offered again meanwhile (#1300)."""
    with MOVE_LOCK: return n in MOVE_FLIGHT
def flight_snapshot():
    """The cards in flight (sorted) and the refusals the page draws, read together under one lock."""
    with MOVE_LOCK: return sorted(MOVE_FLIGHT), dict(MOVE_REFUSED)
def wait_for_moves(timeout=30.0):
    """Join every background write this process started. The suite calls it to read the board once the writer has answered."""
    with MOVE_LOCK: threads = list(MOVE_THREADS)
    for t in threads: t.join(timeout)
def start_write(cards, after=None):
    """Send one card set to the writer in the background and return at once (#1300). Each card is in flight until the writer
    answers, and the board draws it in Todo with a pending mark meanwhile. `after(moved, reason)` runs once the answer is
    known; the page never waits for it. A proved card moves in the cache, and a refused one keeps its column and its reason."""
    with MOVE_LOCK:
        for c in cards: MOVE_FLIGHT[c["n"]] = PROMOTE_TO; MOVE_REFUSED.pop(c["n"], None)
        MOVE_THREADS[:] = [t for t in MOVE_THREADS if t.is_alive()]
        t = threading.Thread(target=_settle_write, args=(cards, after), daemon=True, name=f"board-move-{cards[0]['n']}")
        MOVE_THREADS.append(t)
    t.start()
def _settle_write(cards, after):
    """The writer's answer for one card set, in the background. The flight ends whatever the answer, so a failure here can never
    leave a card drawn pending for good."""
    moved, reason, confirmed = [], "the writer did not answer", {}
    try:
        proved, reason = send_cards(cards)
        moved = [c["n"] for c in cards if c["n"] in proved]
        if moved:
            with _board_lock:
                for i in board_cache.get("items") or []:
                    if i.get("n") in moved: i["st"] = PROMOTE_TO   # the cache follows the proof, whichever list object the pass holds now
                for c in cards:
                    if c["n"] in moved: c["st"] = PROMOTE_TO
            confirmed = confirmed_proofs(cards, moved)   # the record the writer's proof confirms: its mark goes at this proof (#1300)
            save_board()
    except Exception as e:
        reason = str(e)[:200] or reason
    finally:
        with MOVE_LOCK:
            PROVED.update(confirmed)   # set before the flight ends, so no card draws pending between the answer and the proof
            for c in cards:
                MOVE_FLIGHT.pop(c["n"], None)
                if c["n"] not in moved and reason: MOVE_REFUSED[c["n"]] = reason
    if after:
        try: after(moved, reason if len(moved) < len(cards) else None)
        except Exception as e: print(f"hub: a board move's record failed: {e}", file=sys.stderr)

MOVED_LINE = re.compile(r"^#(\d+) -> ", re.M)   # board.py prints `#<n> -> <Status>` for each card it proved moved (or already carried)
def send_cards(cards):
    """The one writer call of a card set, answered as `(proved, reason)`. `proved` names the card numbers the writer printed as
    moved: every card on a clean answer, and on a failure only those it printed, so a failure part way names what moved. The
    reason is None on a clean answer."""
    try: r = promote_write([c["n"] for c in cards], add=any(not c.get("i") for c in cards))   # no item id: the row came from the open-issue read (#815)
    except subprocess.TimeoutExpired as e:
        # board.py may have moved some of the cards before the writer ran out of time. The cache is not written over a move
        # it cannot prove; the board-dirty flag asks the next pass to read the columns as they now stand (#838).
        try: (STATE / "board-dirty").touch()
        except OSError: pass
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return [int(n) for n in MOVED_LINE.findall(out)], f"the writer had not answered after {int(e.timeout)} s: the move may be partial, and the board is read again"
    except Exception as e: return [], str(e)[:200] or "the writer did not answer"
    if r.returncode == 0: return [c["n"] for c in cards], None
    lines = (r.stderr or r.stdout or f"exit {r.returncode}").strip().splitlines()
    return [int(n) for n in MOVED_LINE.findall(r.stdout or "")], (next((l for l in lines if l.startswith("#")), lines[0] if lines else "") or "the writer refused")[:300]
# The owner's Priority write (#2289). A write is the writer's `--priority` path (`board.sh --priority`), which sends one
# PATCH only when the value changes (ADR-0144) and reads the value back. The route answers at once; the card draws the
# chosen mark with a pending chip until the writer answers, and a refusal keeps the read's value and names its reason.
PRIORITY_LEVELS = ("urgent", "high", "normal", "low")
PRIORITY_READ_ONLY = "read-only from the tailnet: SQUAD_TAILNET_BOARD_PRIORITY_WRITE is not on (tailnet.board_priority_write)"
PRIORITY_AUDIT = STATE / "board-priority.jsonl"   # one line per write the owner asked for: the card, old value, new value, time (#2289)
PRIORITY_FLIGHT = {}    # card number -> level the owner chose, from the route's answer until the writer's
PRIORITY_REFUSED = {}   # card number -> (old level, the reason the writer refused, the board read the refusal came after)
PRIORITY_LOCK = threading.Lock()
PRIORITY_THREADS = []

def priority_call(n, level):
    """The one writer call of a Priority write: `--priority <owner> <project> <n> <level>` through the board seam, with the
    state directory and item id this hub already holds, so the writer reads no whole-project list for the id."""
    env = {**os.environ, "SG_STATE": str(STATE), "SG_REPO": REPO, "SG_MOVE_BY": "owner"}
    env["SG_BOARD_ITEM_HINTS"] = json.dumps({str(i["n"]): i["i"] for i in board_cache.get("items") or []
                                             if i.get("n") == n and i.get("i") and (i.get("r") or REPO).lower() == REPO.lower()})
    argv = ["bash", str(pathlib.Path(__file__).resolve().parent / "board.sh"), "--priority", OWNER, str(PROJECT_NO), str(n), level]
    return subprocess.run(argv, capture_output=True, text=True, timeout=PROMOTE_TIMEOUT_BASE + _settle_seconds(), env=env)

def wait_for_priorities(timeout=30.0):
    """Join every background Priority write this process started. The suite calls it to read the board once the writer answered."""
    with PRIORITY_LOCK: threads = list(PRIORITY_THREADS)
    for t in threads: t.join(timeout)

def priority_refusal(a, origin_ok, tn):
    """The reason a Priority write is refused, or '' when it may run: the access rule, then its own tailnet grant, then the
    origin and body. The loop-write grant is not read here (ADR-0162, #2289)."""
    if not a.allowed: return a.reason
    if a.tailnet and tn.get("board_priority_write") is not True: return PRIORITY_READ_ONLY
    return "" if origin_ok else "forbidden"

def tailnet_origin(tn):
    """The tailnet Origin a Priority write names: https on the project file's host, whether the file names it bare or with its scheme."""
    host = re.sub(r"^https?://", "", str(tn.get("host") or "").strip()).rstrip("/")
    return f"https://{host}" if host else ""

def priority_audit(issue, old, new, who, frm, reason):
    """One line per write the owner asked for, written once the writer answered: the card, the old and new value, the time, who
    asked from where, and the outcome. A refused write has `ok` false and its reason."""
    record = {"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "issue": issue, "repository": REPO,
              "from": old, "to": new, "who": who, "from_where": frm, "ok": not reason, "reason": reason}
    PRIORITY_AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with PRIORITY_AUDIT.open("a") as f: f.write(json.dumps(record) + "\n")

def priority_issue_row(n):
    """One issue as the inheritance rule reads it (#1979): its body, its label names and its state, from the issue read."""
    r = ghread.read(f"repos/{REPO}/issues/{n}")
    if not r.is_answer: raise RuntimeError(r.line()[:200] or f"#{n}: the issue read did not answer")
    d = r.answer if isinstance(r.answer, dict) else {}
    labels = [x.get("name") if isinstance(x, dict) else x for x in d.get("labels") or []]
    return {"body": d.get("body") or "", "sub_issues": [], "labels": labels, "state": str(d.get("state") or "").lower()}

def priority_family(spec):
    """The spec and its children as the inheritance rule reads them (#1979): the children the board cache links by `pa` and the
    spec's native sub-issues, each read for its body, labels and state. A closed child is not a target."""
    subs, err = sub_issues(spec, REPO)
    if err: raise RuntimeError(err)
    items = [i for i in board_cache.get("items") or [] if (i.get("r") or REPO).lower() == REPO.lower()]
    nums = {i["n"] for i in items if i.get("pa") == spec} | {s["number"] for s in subs if isinstance(s, dict) and isinstance(s.get("number"), int)}
    nums.discard(spec)
    by = {spec: priority_issue_row(spec)}
    for n in sorted(nums): by[n] = priority_issue_row(n)
    by[spec]["sub_issues"] = sorted(n for n in nums if by[n]["state"] == "open")
    return by

def priority_propagate(spec, level):
    """Carry a spec's new Priority to its open children by the inheritance rule (#1979). A failure here never undoes the
    spec's own write, which the writer already proved: it is logged once and the next change carries the value again."""
    try:
        priority_backfill.propagate(board_seam(), REPO, spec, int(time.time() * 1000), level, priority_family(spec))
    except Exception as e:
        log_once(f"hub: the Priority of spec #{spec} did not reach its children: {type(e).__name__}: {' '.join(str(e).split())}")

def _settle_priority(n, level, old, who, frm):
    """The writer's answer for one Priority write, in the background. A proved write changes the served value at once, so the
    card's next draw carries it with no pending chip and no board read; a refusal keeps the read's value and names its reason."""
    reason = None
    try:
        r = priority_call(n, level)
        if r.returncode != 0:
            lines = (r.stderr or r.stdout or f"exit {r.returncode}").strip().splitlines()
            reason = (next((l for l in lines if l.startswith("#")), lines[0] if lines else "") or "the writer refused")[:300]
    except subprocess.TimeoutExpired as e:
        reason = f"the writer had not answered after {int(e.timeout)} s: the board is read again"
        try: (STATE / "board-dirty").touch()
        except OSError: pass
    except Exception as e:
        reason = str(e)[:200] or "the writer did not answer"
    priority_audit(n, old, level, who, frm, reason or "")
    if reason is None:
        with _board_lock:
            for i in board_cache.get("items") or []:
                if i.get("n") == n: i["p"] = level
        save_board()
    with PRIORITY_LOCK:
        PRIORITY_FLIGHT.pop(n, None)
        if reason is None: PRIORITY_REFUSED.pop(n, None)
        else: PRIORITY_REFUSED[n] = (old, reason, board_cache.get("t"))
    if reason is None: priority_propagate(n, level)

def priority_write(body, who="owner", frm="loopback"):
    """One Priority write the owner asked for: validate, skip a value the card already carries (ADR-0144), and send it in the
    background; the audit line follows the writer's answer. A second ask while the card's write is in flight starts no write
    and answers the in-flight level. The answer names the card and the level, and whether a write was sent."""
    if not isinstance(body, dict): return {"err": "bad request"}
    n, level = body.get("issue"), body.get("priority")
    if isinstance(n, bool) or not isinstance(n, int) or n <= 0: return {"err": "issue: a number"}
    if level not in PRIORITY_LEVELS: return {"err": "priority: one of urgent, high, normal, low"}
    item = next((i for i in board_cache.get("items") or [] if i.get("n") == n), None)
    if item is None: return {"err": "not on the board"}
    if str(item.get("s") or "").lower() == "closed": return {"err": "the issue is closed: its Priority is not written"}
    old = item.get("p") or ""
    if old == level: return {"ok": True, "issue": n, "priority": level, "changed": False}   # no board write on an unchanged value
    with PRIORITY_LOCK:
        if n in PRIORITY_FLIGHT: return {"ok": True, "issue": n, "priority": PRIORITY_FLIGHT[n], "changed": False, "pending": True}
        PRIORITY_FLIGHT[n] = level
        PRIORITY_REFUSED.pop(n, None)
        PRIORITY_THREADS[:] = [t for t in PRIORITY_THREADS if t.is_alive()]
        t = threading.Thread(target=_settle_priority, args=(n, level, old, who, frm), daemon=True, name=f"board-priority-{n}")
        PRIORITY_THREADS.append(t)
    t.start()
    return {"ok": True, "issue": n, "priority": level, "changed": True, "pending": True}

def priority_route(a, raw):
    """The JSON body of a `/api/board/priority` write that the access rule and the origin already let through."""
    try: body = json.loads(raw or b"{}")
    except ValueError: return 400, {"err": "bad request"}
    out = priority_write(body, a.login or "owner", "tailnet" if a.tailnet else "loopback")
    return (400 if "err" in out else 200), out

BATCH_AUDIT = STATE / "board-batch.jsonl"   # one line per batch write the owner asked for: the count, the cards and who asked (#1098)
def promote_batch_plan():
    """The cards the batch moves, in write order, or the refusal. Every card the owner's move would start goes in, a spec
    first, then the open Backlog tickets of every spec, then the rest, so a spec's card never lands after its tickets'.
    A card reached twice moves once."""
    moves = read_moves() if overlay_on() else {}
    window = pending_window()
    starts = [i for i in board_cache.get("items") or [] if not in_flight(i["n"]) and promotable({**i, "st": drawn_column(i, moves, window)["st"]})]
    specs = [i for i in starts if _columns_module.SPEC_LABEL in (i.get("l") or [])]
    ordered, seen = [], set()
    def add(card):
        if card["n"] not in seen:
            seen.add(card["n"])
            ordered.append(card)
    for spec in specs: add(spec)
    for spec in specs:
        tickets, _, err = spec_tickets(spec["n"], spec)
        if err: return None, err
        for ticket in tickets: add(ticket)
    for card in starts: add(card)
    return ordered, None
def spec_batch_cards(number):
    """The cards one spec's start moves (#1126): the spec's card first, then its open Backlog tickets in board order, each card
    once. A spec already in Todo has started, so only its Backlog tickets move. The open tickets past Backlog are named as
    skipped. Returns `(cards, skipped, None)` or `(None, None, reason)`."""
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0: return None, None, "spec: a number"
    items = board_cache.get("items") or []
    item = next((i for i in items if i.get("n") == number), None)
    if item is None: return None, None, "not on the board"
    if str(item.get("s") or "").lower() == "closed": return None, None, f"the spec #{number} is closed: nothing to start"
    if in_flight(number): return None, None, f"the spec #{number} is already moving to Todo: the writer has not answered yet"
    st = drawn_column(item, read_moves() if overlay_on() else {}, pending_window())["st"]
    col, _ = _columns_module.view_column(st, False, item.get("l"))
    started = col == PROMOTE_TO
    if not started and col != COLUMN["unscheduled"]: return None, None, f"the spec #{number} is in {col}, not {COLUMN['unscheduled']}: only a Backlog spec starts"
    if _columns_module.SPEC_LABEL not in (item.get("l") or []): return None, None, f"#{number} is not a spec"
    tickets, skipped, err = spec_tickets(number, item)
    if err: return None, None, err
    if not started and not tickets and not skipped: return None, None, f"the spec #{number} has no open ticket: nothing to start"
    order = {id(c): k for k, c in enumerate(items)}
    cards, seen = [], set()
    for card in ([] if started else [item]) + sorted(tickets, key=lambda c: order[id(c)]):
        if card["n"] not in seen:
            seen.add(card["n"])
            cards.append(card)
    return cards, list(dict.fromkeys(skipped)), None
def promote_spec_batch(body, who):
    """One spec's batch (#1127, plan #1126). With no `confirm` the answer is the plan and nothing moves. With `confirm` equal to
    the count the plan answers now, one write moves the plan's cards, the spec's card first, through the writer the single
    move uses, and the answer comes back at once with the cards in flight (#1300). A count that changed is refused and nothing
    moves. Every request that carries `confirm` writes one audit line once the writer has answered, whatever the outcome, and a
    write that fails part way names the cards that moved and those that did not."""
    spec = body["spec"]
    cards, skipped, err = spec_batch_cards(spec)
    if "confirm" not in body:
        return {"err": err} if err else {"count": len(cards), "issues": [c["n"] for c in cards], "skipped": skipped}
    issues = [c["n"] for c in cards] if err is None else []
    def audit(outcome, moved=(), reason=None):
        record = {"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "who": who, "action": "promote-spec-batch",
                  "scope": spec, "count": len(issues), "issues": issues, "moved": list(moved), "outcome": outcome}
        if reason: record["err"] = reason
        with BATCH_AUDIT.open("a") as f: f.write(json.dumps(record) + "\n")
    if err:
        audit("refused", reason=err)
        return {"err": err}
    confirm = body["confirm"]
    if type(confirm) is not int or confirm != len(cards):
        reason = f"the batch is {len(cards)} cards now, not {confirm}: read it again"
        audit("refused", reason=reason)
        return {"err": reason, "count": len(cards)}
    if not cards:
        reason = f"the spec #{spec} has no card in Backlog to start"
        audit("refused", reason=reason)
        return {"err": reason, "count": 0}
    def done(moved, reason):
        audit("moved" if reason is None else ("partial" if moved else "failed"), moved, reason)
    start_write(cards, done)
    return {"ok": True, "count": len(cards), "pending": issues}

def promote_batch(body, who):
    """The owner's batch move (#1098). With no `confirm`, the hub answers the count it would move and the numbers, and nothing
    moves. With `confirm` equal to that count, one write moves every card of the plan, and the hub records the count and who
    asked. A count that changed since the owner read it is refused, so the owner confirms the number that moves. With a
    `spec` the batch is that one spec's (#1127)."""
    if "spec" in body: return promote_spec_batch(body, who)
    cards, err = promote_batch_plan()
    if err: return {"err": err}
    issues = [c["n"] for c in cards]
    if "confirm" not in body: return {"count": len(issues), "issues": issues}
    if not cards: return {"err": "no card is in Backlog"}
    if type(body["confirm"]) is not int or body["confirm"] != len(cards): return {"err": f"the batch is {len(cards)} cards now, not {body['confirm']}: read it again"}
    def done(moved, reason):   # the record is written once the writer has answered, never before it (#1300)
        record = {"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "who": who, "action": "promote-batch",
                  "count": len(cards), "issues": issues, "ok": reason is None}
        if reason: record["err"] = reason
        with BATCH_AUDIT.open("a") as f: f.write(json.dumps(record) + "\n")
    start_write(cards, done)
    return {"ok": True, "count": len(cards), "pending": issues}
