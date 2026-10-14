#!/usr/bin/env python3
"""Board reconciler: derive each tracked issue's Status from GitHub facts and move the card.
Facts only (no comment-text inference): the table defines every destination from the fact that earns it. Dry run by default: RECONCILE=apply writes.
Scope: issues already on board 4 (open ones only from number >= RECONCILE_MIN, default 1102; a closed issue of any number is derived to its terminal column) plus new `bug` issues.
A card never moves backwards: the column it carries is the furthest the work reached and is its own watermark, so a recomputed
column lower in the shipped flow is refused, reported by name, and no new ledger is kept. A merge taken back is a tag plus a new
issue, never a move back, so a card the facts pull back out of a terminal column keeps its column and carries the tag."""
import json, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # the seam module sits beside this script; a run under -I has the script's directory off sys.path
from board_columns import BY_FACT, COLUMNS
from issue_state import board_state
REPO = os.environ.get("SG_REPO"); OWNER = os.environ.get("SG_OWNER"); BOARD = int(os.environ.get("SG_PROJECT") or 0)
if not (REPO and OWNER and BOARD): sys.exit("reconcile: set SG_REPO, SG_OWNER and SG_PROJECT (env.sh exports them)")
MIN = int(os.environ.get("RECONCILE_MIN", "1102")); APPLY = os.environ.get("RECONCILE") == "apply"
FLOW = {column["name"]: column["flow"] for column in COLUMNS}   # the shipped column order (the flow the page draws left to right): a card moves forward along it and never back
TERMINAL = {column["name"] for column in COLUMNS if column["terminal"]}   # the columns the work is delivered at, each entered once
TAG = "reverted"   # what a card carries when its merge was taken back (#246): the tag and the new issue decide what happens next, never a silent move back
def gh(*a):
    r = subprocess.run(["gh", *a], capture_output=True, text=True)
    if r.returncode: print("gh error:", " ".join(a[:3]), r.stderr.strip()[:120], file=sys.stderr); return None
    return r.stdout
def js(*a):
    o = gh(*a); return json.loads(o) if o else None
_here = os.path.dirname(os.path.abspath(__file__)); MOVE = os.path.join(_here, "board.sh")
try: items = board_state(owner=OWNER, project=BOARD, repo=REPO, kind=None).read_all()   # the state each card carries, asked of the seam (a user or an org owner, cached a month); the carrier is the binding's business (#236)
except RuntimeError: sys.exit("board unreadable")
prs = js("api", f"repos/{REPO}/pulls?state=open&per_page=100") or []
refs = {}
for p in prs:
    for n in re.findall(r"[Rr]efs #(\d+)", (p.get("title") or "") + "\n" + (p.get("body") or "")): refs.setdefault(int(n), p["number"])
def move(n, to, why):
    cur = items.get(str(n)) or None
    if cur in FLOW and FLOW[to] < FLOW[cur]:   # the card itself is the watermark: its column is the furthest the work reached, and the shipped flow order fixes which way is back
        tag = cur in TERMINAL   # the work was delivered and the facts now pull the card back: the merge was taken back, so the card keeps its column and carries the tag
        print(f"BACKWARD #{n}: {cur} -> {to} refused, the card keeps {cur}" + (f" and carries the tag {TAG}" if tag else "") + f" ({why})")
        if tag:
            print(f"{'TAG' if APPLY else 'WOULD TAG'} #{n} {TAG}")
            if APPLY: gh("api", "--method", "POST", f"repos/{REPO}/issues/{n}/labels", "-f", f"labels[]={TAG}")
        return
    print(f"{'MOVE' if APPLY else 'WOULD MOVE'} #{n} {cur} -> {to} ({why})")
    if APPLY: subprocess.run([MOVE, OWNER, str(BOARD), to, str(n)], capture_output=True)
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
    elif st["state"] == "open" and i in refs and cur in (BY_FACT["unclaimed"], BY_FACT["claimed"], BY_FACT["blocked"], None): move(n, BY_FACT["open_pr"], f"open PR #{refs[i]}")
    elif st["state"] == "open" and deps:
        open_deps = [d for d in deps if is_open(d)]
        if open_deps and cur in (BY_FACT["unclaimed"], None): move(n, BY_FACT["blocked"], "blocked by open " + ", ".join(f"#{d}" for d in open_deps))
        elif not open_deps and cur == BY_FACT["blocked"]: move(n, BY_FACT["unclaimed"], "all blockers closed")
    elif st["state"] == "open" and cur in (BY_FACT["unclaimed"], None) and has_freetext_blocker(st.get("body")): print(f"BOARD NOTE #{n}: 'Blocked by' names no #N, fix the issue body")
for i in js("api", f"repos/{REPO}/issues?state=open&labels=bug&per_page=100") or []:
    if "pull_request" in i or i["number"] < MIN or str(i["number"]) in items: continue
    to = BY_FACT["open_pr"] if i["number"] in refs else BY_FACT["unclaimed"]
    print(f"{'ADD' if APPLY else 'WOULD ADD'} #{i['number']} to board as {to} (new bug)")
    if APPLY: subprocess.run([MOVE, "--add", OWNER, str(BOARD), to, str(i["number"])], capture_output=True)
