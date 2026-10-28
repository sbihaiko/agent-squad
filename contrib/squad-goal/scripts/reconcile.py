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
import json, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # the seam module sits beside this script; a run under -I has the script's directory off sys.path
from board_columns import BY_FACT, COLUMNS, SHIPPED_NAME
from issue_state import board_state
import ghread, ghthrottle   # the door's reader and log, and the core quota the floor reads (#847)
REPO = os.environ.get("SG_REPO"); OWNER = os.environ.get("SG_OWNER"); BOARD = int(os.environ.get("SG_PROJECT") or 0)
if not (REPO and OWNER and BOARD): sys.exit("reconcile: set SG_REPO, SG_OWNER and SG_PROJECT (env.sh exports them)")
FLOOR = int(os.environ.get("SG_GH_FLOOR") or 300)   # below this many core requests left, the pass skips its read for the cycle (#847)
_low = ghthrottle.core_below(FLOOR)
if _low is not None:
    print(f"RECONCILE: skipped this pass: core quota {_low} is under SG_GH_FLOOR {FLOOR}; the next pass reads again")
    sys.exit(0)
MIN = int(os.environ.get("RECONCILE_MIN", "1102")); APPLY = os.environ.get("RECONCILE") == "apply"
VOCAB = os.environ.get("SG_BOARD_VOCAB")   # the vocabulary the carrier holds, the switch the janitor reads too: `shipped` writes the shipped names
MOVES = []   # the cards this pass moves (or would move): the count it prints
REFUSED = []   # the cards whose write the carrier refused this pass (#454)
FLOW = {column["name"]: column["flow"] for column in COLUMNS}   # the shipped column order (the flow the page draws left to right): a card moves forward along it and never back
TERMINAL = {column["name"] for column in COLUMNS if column["terminal"]}   # the columns the work is delivered at, each entered once
TAG = "reverted"   # what a card carries when its merge was taken back (#246): the tag and the new issue decide what happens next, never a silent move back
def gh(*a):
    if a[:1] == ("api",) and not set(a) & {"--method", "-X", "-f", "-F", "--input"}:   # a read: through the door, logged to api.log (#847)
        out = ghread.read(a[1]); ghread.log_call(None, a[1], ghread.MISS, out.kind)
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
try: items = board_state(owner=OWNER, project=BOARD, repo=REPO, kind=None).read_all()   # the state each card carries, asked of the seam (a user or an org owner, cached a month); the carrier is the binding's business (#236)
except RuntimeError: sys.exit("board unreadable")
LAND = os.environ.get("SG_LAND_BRANCH") or "dev"   # the branch a work branch lands in: a card is Done when its branch is in it
refs = {}   # issue -> its work branch, for a branch that holds commits the landing branch lacks (the branch read, never a pull request, #358)
for b in js("api", f"repos/{REPO}/branches?per_page=100") or []:
    m = re.match(r"(?:fix|gui)/(\d+)(?:\D.*)?$", b.get("name") or "")
    if not m: continue
    cmp = js("api", f"repos/{REPO}/compare/{LAND}...{b['commit']['sha']}") or {}
    if cmp.get("status") in ("ahead", "diverged"): refs.setdefault(int(m.group(1)), b["name"])
def labels_of(n): return [x.get("name") for x in js("api", f"repos/{REPO}/issues/{n}/labels") or []]
def shipped_name(to):
    """The name the carrier holds for a column: this module's own until the board carries the shipped vocabulary, and the
    shipped name after (the janitor reads the same switch, #249). The shipped board has no name for Blocked, which is the
    owner freeze over the column the work reached, never a column of its own (ADR-0141): it earns None, and no write."""
    return SHIPPED_NAME.get(to, to) if VOCAB == "shipped" else to
def move(n, to, why):
    cur = items.get(str(n)) or None
    if cur in FLOW and FLOW[to] < FLOW[cur]:   # the card itself is the watermark: its column is the furthest the work reached, and the shipped flow order fixes which way is back
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
def write(n, name, add=False):
    """One writer call, in the one shape the writer takes: `<owner> <project> <status> <n>`, or its `--add` form. The shape
    is built here and nowhere else, so a caller cannot drop a field of it -- the pass that shipped #454 called
    `board.sh Closed 1090`, and board.py read the status as the owner and died with an IndexError on every write (#457).
    A refusal is named with the carrier's own text and ends the pass non-zero: a MOVE line beside a write that never
    landed is a log that lies, which is what this pass filed for months (#454)."""
    pre = [MOVE] + (["--add"] if add else []) + [OWNER, str(BOARD)]
    r = subprocess.run([*pre, name, str(n)], capture_output=True, text=True)
    if r.returncode:
        REFUSED.append(n)
        print(f"WRITE REFUSED #{n} -> {name}: {(r.stderr or r.stdout).strip().replace(chr(10), ' ')[:200] or 'the writer said nothing'}")
_state = {}
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
    n = str(i); cur = cur or None; st = js("api", f"repos/{REPO}/issues/{n}")
    if not st: continue
    if st["state"] != "closed" and i < MIN: continue
    deps = blockers(st.get("body"))
    if st["state"] == "closed" and cur != BY_FACT["issue_closed"]: move(n, BY_FACT["issue_closed"], "issue closed")
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
print(f"moves: {len(MOVES)}")   # the count of cards whose column changes
sys.exit(1 if REFUSED else 0)   # a pass that could not write what it reported is a failed pass, never a quiet one
