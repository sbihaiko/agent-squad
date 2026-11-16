#!/usr/bin/env python3
"""Board reconciler: derive each tracked issue's Status from GitHub facts and move the card.
Facts only (no comment-text inference): the table defines every destination from the fact that earns it. Dry run by default: RECONCILE=apply writes.
The vocabulary is the carrier's: under SG_BOARD_VOCAB=shipped the write names the shipped column (ADR-0141), a column the
shipped board has no name for is no write, and a write the carrier refused is named with its own text and fails the pass
(#454). A MOVE line beside a write that never landed is a log that lies.
Scope: issues already on board 4 (open ones only from number >= RECONCILE_MIN, default 1102; a closed issue of any number is derived to its terminal column) plus new `bug` issues.
A card never moves backwards: the column it carries is the furthest the work reached and is its own watermark, so a recomputed
column lower in the shipped flow is refused, reported by name, and no new ledger is kept. A merge taken back is a tag plus a new
issue, never a move back, so a card the facts pull back out of a terminal column keeps its column and carries the tag."""
import datetime as dt, json, os, pathlib, re, subprocess, sys
os.environ.setdefault("SG_GH_PASS", "reconcile")   # the call log names this pass (ADR-0277): unattributed spend cannot be pinned on its spender (#1648)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # the seam module sits beside this script; a run under -I has the script's directory off sys.path
from board_columns import BY_FACT, COLUMNS, ROLE, SHIPPED_NAME, past, terminal_column
from issue_state import board_state
import board_archive, ghread, ghthrottle   # the archive pass (#1931), the door's reader and log, the shared throttle, and the quota floors (#847, #1648)
REPO = os.environ.get("SG_REPO"); OWNER = os.environ.get("SG_OWNER"); BOARD = int(os.environ.get("SG_PROJECT") or 0)
if not (REPO and OWNER and BOARD): sys.exit("reconcile: set SG_REPO, SG_OWNER and SG_PROJECT (env.sh exports them)")
FLOOR = int(os.environ.get("SG_GH_FLOOR") or 300)   # below this many requests left in a pool, the pass skips its read for the cycle (#847)
for _pool in ("core", "graphql"):   # GraphQL is its own quota: a pass that floors on core alone walks into a refusal with 3,000 core requests left (#1648)
    _low = ghthrottle.pool_below(_pool, FLOOR)
    if _low is not None:
        print(f"RECONCILE: skipped this pass: {_pool} quota {_low} is under SG_GH_FLOOR {FLOOR}; the next pass reads again")
        sys.exit(0)
THROTTLE = ghthrottle.Throttle()   # the one shared bucket and cooldown: a read sent under a cooldown renews the limit it waits on (#1648)
_held = THROTTLE.holding()
if _held:
    print(f"RECONCILE: skipped this pass: a cooldown holds for {_held.get('pass') or 'another pass'} until {int(float(_held.get('until') or 0))}")
    sys.exit(0)
MIN = int(os.environ.get("RECONCILE_MIN", "1102")); APPLY = os.environ.get("RECONCILE") == "apply"
VOCAB = os.environ.get("SG_BOARD_VOCAB")   # the vocabulary the carrier holds, the switch the janitor reads too: `shipped` writes the shipped names
MOVES = []   # the cards this pass moves (or would move): the count it prints
REFUSED = []   # the cards whose write the carrier refused this pass (#454)
FLOW = {column["name"]: column["flow"] for column in COLUMNS}   # the shipped column order (the flow the page draws left to right): a card moves forward along it and never back
TERMINAL = {column["name"] for column in COLUMNS if column["terminal"]}   # the columns the work is delivered at, each entered once
TAG = "reverted"   # what a card carries when its merge was taken back (#246): the tag and the new issue decide what happens next, never a silent move back
ARCHIVE = []   # the closed cards in a terminal column: the archive pass's candidates (#1931)
def gh(*a):
    if a[:1] == ("api",) and not set(a) & {"--method", "-X", "-f", "-F", "--input"}:   # a read: through the throttle, which holds the pass back under a cooldown and logs the line (#847, #1648)
        out = THROTTLE.read(a[1])
        if out.is_answer: return out.body
        why = out.line()
    else:
        r = subprocess.run(["gh", *a], capture_output=True, text=True)
        if not r.returncode: return r.stdout
        why = r.stderr.strip()
    print("gh error:", " ".join(a[:3]), why[:120], file=sys.stderr); return None
def js(*a):
    o = gh(*a); return json.loads(o) if o else None
_here = os.path.dirname(os.path.abspath(__file__)); MOVE = os.path.join(_here, "board.sh")
try: STATE = board_state(owner=OWNER, project=BOARD, repo=REPO, kind=None); items = STATE.read_all()   # the state each card carries, asked of the seam (a user or an org owner, cached a month); the carrier is the binding's business (#236)
except RuntimeError: sys.exit("board unreadable")
LAND = os.environ.get("SG_LAND_BRANCH") or "dev"   # the branch a work branch lands in: a card is Done when its branch is in it
OPEN = {int(i["number"]): i for i in js("api", f"repos/{REPO}/issues?state=open") or []}   # the open issues, one read: a card it covers is open and needs no read of its own (#853)
# The cards the open list lacks are asked in one batched route, 100 aliases per request (#1648): a closed card used to cost
# its own read on every pass, and that walk emptied the GraphQL quota while the core quota stayed untouched.
EXTRA = {}
_missing = sorted(i for i in (int(k) for k in items) if i not in OPEN)
for _at in range(0, len(_missing), 100):
    _rows = js("api", f"repos/{REPO}/issues?numbers={','.join(map(str, _missing[_at:_at + 100]))}")
    if _rows is None: break   # a refused or failed batch ends the walk: the cooldown holds the next pass back
    for _it in _rows: EXTRA[int(_it["number"])] = _it
refs = {}   # issue -> its work branch, for a branch that holds commits the landing branch lacks (the branch read, never a pull request, #358)
compared = {}   # branch tip -> its compare: branches on one tip cost one compare
for b in js("api", f"repos/{REPO}/branches?per_page=100") or []:
    m = re.match(r"(?:fix|gui)/(\d+)(?:\D.*)?$", b.get("name") or "")
    if not m: continue
    sha = b["commit"]["sha"]
    if sha not in compared: compared[sha] = js("api", f"repos/{REPO}/compare/{LAND}...{sha}") or {}
    if compared[sha].get("status") in ("ahead", "diverged"): refs.setdefault(int(m.group(1)), b["name"])
def labels_of(n): return [x.get("name") for x in js("api", f"repos/{REPO}/issues/{n}/labels") or []]
HOLDER_LABEL = {"manual": "ready-for-human", "hal9k": "ready-for-HAL9K"}   # the label face of a hold, one per carrier (#1678)
def _handhold():
    """squad/handhold.py, the one reader of the hold file (#1678): from the repository this script sits in, then SG_SQUAD,
    SG_CHECKOUT, else the tree it runs in. The reconciler carries no second copy of the hold format."""
    roots = [r for r in (str(pathlib.Path(__file__).resolve().parents[3]), os.environ.get("SG_SQUAD"), os.environ.get("SG_CHECKOUT"),
             *map(str, (pathlib.Path.cwd(), *pathlib.Path.cwd().parents))) if r]
    root = next((r for r in roots if (pathlib.Path(r) / "squad" / "handhold.py").is_file()), None)
    if root is None: return None
    if root not in sys.path: sys.path.insert(0, root)
    try: from squad import handhold
    except Exception: return None
    return handhold
def hold_files():
    """{issue: carrier} for each hold file the module reads: the face the janitor reads. No state folder, no hold file or a
    module the tree cannot find names no hold."""
    HH = _handhold()
    if HH is None: return {}
    try: return {int(n): c["who"] for n, c in HH.holders(state=os.environ.get("SG_STATE")).items() if c.get("kind") == "hold"}
    except Exception: return {}
def label_names(issue):
    return {x.get("name") for x in (issue or {}).get("labels") or [] if isinstance(x, dict)}
def hold_parity():
    """The two faces of a hold must agree (#1678): the hold file, which the janitor reads, and the holder label, which a
    dispatch pass reads. A disagreement is a defect reported by name, and neither face is rewritten here: the hold module
    is the one writer of both, by its hold, renew and release verbs."""
    holds = hold_files()
    for n, by in sorted(holds.items()):
        want = HOLDER_LABEL.get(by)
        if want is None: continue
        row = OPEN.get(n) or EXTRA.get(n) or js("api", f"repos/{REPO}/issues/{n}")
        if not row: continue
        names = label_names(row)
        other = next((name for name in HOLDER_LABEL.values() if name != want and name in names), None)
        if other:
            print(f"HOLD DISAGREEMENT #{n}: the hold file says {by}, the issue carries {other}")
        elif want not in names:
            print(f"HOLD DISAGREEMENT #{n}: the hold file says {by}, the issue carries no {want}")
    for n in sorted(set(OPEN) | set(EXTRA)):
        if n in holds: continue
        row = OPEN.get(n) or EXTRA.get(n)
        if not row or "pull_request" in row: continue   # a pull request is no card, and it carries no hold
        names = label_names(row)
        named = next((name for name in HOLDER_LABEL.values() if name in names), None)
        if named:
            print(f"HOLD DISAGREEMENT #{n}: the issue carries {named}, no hold file names it")
hold_parity()
def shipped_name(to):
    """The name the carrier holds for a column: this module's own until the board carries the shipped vocabulary, and the
    shipped name after (the janitor reads the same switch, #249). The shipped board has no name for Blocked, which is the
    owner freeze over the column the work reached, never a column of its own (ADR-0141): it earns None, and no write."""
    return SHIPPED_NAME.get(to, to) if VOCAB == "shipped" else to
def move(n, to, why):
    cur = items.get(str(n)) or None
    # The card itself is the watermark: its column is the furthest the work reached, and the flow order fixes which way is back.
    # A shipped name is not in the legacy flow, so its direction is read on the shipped axis the definition holds (#1932).
    back = FLOW[to] < FLOW[cur] if cur in FLOW and to in FLOW else past(cur, shipped_name(to) or to)
    if back:
        tag = cur in TERMINAL   # the work was delivered and the facts now pull the card back: the merge was taken back, so the card keeps its column and carries the tag
        print(f"BACKWARD #{n}: {cur} -> {to} refused, the card keeps {cur}" + (f" and carries the tag {TAG}" if tag else "") + f" ({why})")
        if tag:
            print(f"{'TAG' if APPLY else 'WOULD TAG'} #{n} {TAG}")
            if APPLY and TAG not in labels_of(n): gh("api", "--method", "POST", f"repos/{REPO}/issues/{n}/labels", "-f", f"labels[]={TAG}")   # the tag is written once: a card that carries it already gets no write (#328)
        return
    name = shipped_name(to)
    if name is None:
        print(f"{'FREEZE' if APPLY else 'WOULD FREEZE'} #{n} {to} ({why}): the shipped board carries it as the owner freeze, whose marker the janitor writes")
        return
    if cur == name: return   # the card already sits in the column its facts earn: no line and no writer call (#458), checked here and never inside the writer
    MOVES.append(n)
    print(f"{'MOVE' if APPLY else 'WOULD MOVE'} #{n} {cur} -> {name} ({why})")
    if APPLY: write(n, name)
_LEDGER = []   # the ledger module beside this script, imported once; None when it does not load
def ledger_lib():
    if not _LEDGER:
        try: import issue_ledger; _LEDGER.append(issue_ledger)
        except ImportError: _LEDGER.append(None)
    return _LEDGER[0]
_LEDGER_NAMED = []   # one line for a pass that cannot record its predictions: a setting the pass ignored never passes silently
def predictor(kind, n, name):
    """One board write through the one path that predicts it (#1841): `board` records the column before the API call, and
    `boardw` is the writer's own proof that it landed. The event is keyed by the issue, carries the instant, the column and
    this writer's name (ADR-0165). A ledger the pass cannot write predicts nothing, and one line on the stream names it:
    the card write itself still happens, and a pass that kept its cards would hide a defect."""
    lib = ledger_lib()
    try:
        if lib is None: raise RuntimeError("issue_ledger is not importable")
        key = f"{REPO}#{n}"
        return lib.boardw(key, "reconcile", name, "reconcile") if kind == "boardw" else lib.board(key, "reconcile", name, by="reconcile")
    except (RuntimeError, OSError, ValueError, KeyError) as e:
        if not _LEDGER_NAMED:
            _LEDGER_NAMED.append(1)
            print(f"RECONCILE: the ledger holds no prediction for this pass ({e}): the card writes still run", file=sys.stderr)
        return None
def write(n, name, add=False):
    """One writer call, in the one shape the writer takes: `<owner> <project> <status> <n>`, or its `--add` form. The shape
    is built here and nowhere else, so a caller cannot drop a field of it -- the pass that shipped #454 called
    `board.sh Closed 1090`, and board.py read the status as the owner and died with an IndexError on every write (#457).
    A refusal is named with the carrier's own text and ends the pass non-zero: a MOVE line beside a write that never
    landed is a log that lies, which is what this pass filed for months (#454). The write is a ledger event first (#1841),
    so a reader of the board draws its column over a cache that lags; the writer's own proof ends the prediction, and a
    write no answer proved keeps its event pending, where the hub names it as a defect once its bound passes."""
    pre = [MOVE] + (["--add"] if add else []) + [OWNER, str(BOARD)]
    predictor("board", n, name)
    r = subprocess.run([*pre, name, str(n)], capture_output=True, text=True)
    if r.returncode:
        REFUSED.append(n)
        print(f"WRITE REFUSED #{n} -> {name}: {(r.stderr or r.stdout).strip().replace(chr(10), ' ')[:200] or 'the writer said nothing'}")
    else:
        predictor("boardw", n, name)   # the carrier agreed: the prediction is written, and no reader draws it pending
_state = {n: True for n in OPEN}
_state.update({n: (v.get("state") != "closed") for n, v in EXTRA.items()})   # the batched answer settles the cards it covers (#1648)
def is_open(d):
    if d not in _state:
        o = js("api", f"repos/{REPO}/issues/{d}"); _state[d] = (o or {}).get("state") != "closed"
    return _state[d]
def section(body):
    m = re.search(r"^## Blocked by\s*\n(.*?)(?=^## |\Z)", body or "", re.S | re.M)
    return m.group(1) if m else ""
def blockers(body):
    # A list, not prose: reading stops at the first line that is neither blank nor a bullet, so a trailing line that
    # merely names another issue ("Blocks #N: ...") is not an edge (#1803, the parse stack.sh shares).
    out = []
    for line in section(body).splitlines():
        if not line.strip(): continue
        if not re.match(r"\s*([-*] |\d+\. )", line): break
        out.append(line)
    return [int(x) for x in re.findall(r"(?<![\w/])#(\d+)", "\n".join(out))]
def has_freetext_blocker(body):
    sec = section(body); return bool(re.search(r"^\s*[-*]\s+(?!none\b)\S", sec, re.M | re.I)) and not blockers(body)
for i, cur in sorted(((int(k), v) for k, v in items.items()), key=lambda kv: kv[0]):
    n = str(i); cur = cur or None
    if i in OPEN and i < MIN: continue   # an open card below RECONCILE_MIN is out of scope, and the aggregate already says it is open
    st = OPEN.get(i) or EXTRA.get(i) or js("api", f"repos/{REPO}/issues/{n}")   # the batch answers first; a card the batch missed costs one read (#1648)
    if not st: continue
    if st["state"] != "closed" and i < MIN: continue
    if VOCAB == "shipped" and st["state"] == "closed" and cur in board_archive.TERMINAL_COLUMNS and st.get("closed_at"):
        ARCHIVE.append({"issue": i, "repo": REPO, "column": cur, "state": "closed", "closed_at": st["closed_at"], "item": None})
    deps = blockers(st.get("body"))
    term = terminal_column(st.get("state"), st.get("state_reason"))   # the same function the janitor reads: Abandoned when the close was not a delivery, Closed when it was (spec #132)
    if term:
        # A card that already sits in its earned terminal column draws no line and no write, so an abandoned card is never walked to Closed (#1932).
        if cur != term: move(n, term if VOCAB == "shipped" else BY_FACT["issue_closed"], f"issue closed as {st.get('state_reason')}" if term == ROLE["abandoned"] else "issue closed")
    elif st["state"] == "open" and cur == BY_FACT["issue_closed"]: move(n, BY_FACT["unclaimed"], "the issue is open again, so the work was taken back")   # the facts earn the floor; `move` refuses to walk the card back and tags it instead
    elif st["state"] == "open" and i in refs and cur in (BY_FACT["unclaimed"], BY_FACT["claimed"], BY_FACT["blocked"], None): move(n, BY_FACT["wave_branch"], f"work branch {refs[i]} is ahead of {LAND}")
    elif st["state"] == "open" and deps:
        open_deps = [d for d in deps if is_open(d)]
        if open_deps and cur in (BY_FACT["unclaimed"], None): move(n, BY_FACT["blocked"], "blocked by open " + ", ".join(f"#{d}" for d in open_deps))
        elif not open_deps and cur == BY_FACT["blocked"]: move(n, BY_FACT["unclaimed"], "all blockers closed")
    elif st["state"] == "open" and cur in (BY_FACT["unclaimed"], None) and has_freetext_blocker(st.get("body")): print(f"BOARD NOTE #{n}: 'Blocked by' names no #N, fix the issue body")
for i in js("api", f"repos/{REPO}/issues?state=open&labels=bug&per_page=100") or []:
    if "pull_request" in i or i["number"] < MIN or str(i["number"]) in items: continue
    to = BY_FACT["wave_branch"] if i["number"] in refs else BY_FACT["unclaimed"]
    name = shipped_name(to) or to   # a new card is addable at any column the carrier holds; the shipped name is what it holds
    print(f"{'ADD' if APPLY else 'WOULD ADD'} #{i['number']} to board as {name} (new bug)")
    if APPLY: write(i["number"], name, add=True)
def archive_pass():
    """Archive the terminal cards whose issue closed past the age bound; children of an open spec stay on the board (#1931)."""
    protected = set()
    for n, row in OPEN.items():
        if "spec" in label_names(row): protected |= {int(c["number"]) for c in js("api", f"repos/{REPO}/issues/{n}/sub_issues") or []}
    chosen = board_archive.select(ARCHIVE, repo=REPO, now=dt.datetime.now(dt.UTC), after_days=board_archive.after_days(), protected=protected)
    if not chosen: return
    nodes = board_archive.item_nodes(STATE.binding)
    for c in chosen: c["item"] = nodes.get((REPO, c["issue"]))
    for c in chosen:
        if not c["item"]: print(f"ARCHIVE SKIPPED #{c['issue']}: the item read did not carry its card")
    board_archive.archive([c for c in chosen if c["item"]], write=lambda node: board_archive.archive_item(node, STATE.binding.api), pace=board_archive.pace_seconds(), say=print)
if APPLY and VOCAB == "shipped" and ARCHIVE: archive_pass()
print(f"moves: {len(MOVES)}")   # the count of cards whose column changes
sys.exit(1 if REFUSED else 0)   # a pass that could not write what it reported is a failed pass, never a quiet one
