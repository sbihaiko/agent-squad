#!/usr/bin/env python3
"""Board reconciler: derive each tracked issue's Status from GitHub facts and move the card.
Facts only (no comment-text inference): issue closed -> Done; an open PR naming the issue as "Refs #N" -> Code Complete (Testing = e2e on the merge, set by the loop)
(only from Todo/In Progress/Blocked); an open issue whose `## Blocked by` lists an open #M -> Blocked (only from Todo), back to Todo when all are closed; an open `bug` issue absent from the board -> added as Todo.
Never downgrades (Done stays Done, Code Complete/Testing never go back). Dry run by default: RECONCILE=apply writes.
Scope: issues already on board 4 with number >= RECONCILE_MIN (default 1102) plus new `bug` issues."""
import json, os, re, subprocess, sys
REPO = os.environ.get("SG_REPO"); OWNER = os.environ.get("SG_OWNER"); BOARD = int(os.environ.get("SG_PROJECT") or 0)
if not (REPO and OWNER and BOARD): sys.exit("reconcile: set SG_REPO, SG_OWNER and SG_PROJECT (env.sh exports them)")
MIN = int(os.environ.get("RECONCILE_MIN", "1102")); APPLY = os.environ.get("RECONCILE") == "apply"
def gh(*a):
    r = subprocess.run(["gh", *a], capture_output=True, text=True)
    if r.returncode: print("gh error:", " ".join(a[:3]), r.stderr.strip()[:120], file=sys.stderr); return None
    return r.stdout
def js(*a):
    o = gh(*a); return json.loads(o) if o else None
board = js("project", "item-list", str(BOARD), "--owner", OWNER, "--limit", "1500", "--format", "json")
if not board: sys.exit("board unreadable")
def board_ids():
    """Project node id, Status field id and option ids, cached a day under SG_STATE (GraphQL budget)."""
    import time
    f = os.path.join(os.environ.get("SG_STATE", os.path.expanduser("~/.cache/squad-goal")), f"board-ids-{OWNER}-{BOARD}.json")
    try:
        if time.time() - os.path.getmtime(f) < 86400: return json.load(open(f))
    except OSError: pass
    pv = js("project", "view", str(BOARD), "--owner", OWNER, "--format", "json") or {}
    fl = js("project", "field-list", str(BOARD), "--owner", OWNER, "--format", "json") or {}
    st = next((x for x in fl.get("fields", []) if x.get("name") == "Status"), {})
    d = {"project": pv.get("id"), "field": st.get("id"), "opt": {o["name"]: o["id"] for o in st.get("options", [])}}
    if d["project"] and d["field"]: json.dump(d, open(f, "w"))
    return d
_ids = board_ids(); PROJECT, FIELD, OPT = _ids["project"], _ids["field"], _ids["opt"]
items = {i["content"]["number"]: i for i in board["items"] if i["content"].get("number") and i["content"].get("type") == "Issue" and i["content"]["number"] >= MIN}
prs = js("api", f"repos/{REPO}/pulls?state=open&per_page=100") or []
refs = {}
for p in prs:
    for n in re.findall(r"[Rr]efs #(\d+)", (p.get("title") or "") + "\n" + (p.get("body") or "")): refs.setdefault(int(n), p["number"])
def move(n, to, why):
    cur = items[n].get("status")
    print(f"{'MOVE' if APPLY else 'WOULD MOVE'} #{n} {cur} -> {to} ({why})")
    if APPLY: gh("project", "item-edit", "--project-id", PROJECT, "--id", items[n]["id"], "--field-id", FIELD, "--single-select-option-id", OPT[to])
_state = {}
def is_open(d):
    if d not in _state:
        o = js("api", f"repos/{REPO}/issues/{d}"); _state[d] = (o or {}).get("state") != "closed"
    return _state[d]
def section(body):
    m = re.search(r"^## Blocked by\s*\n(.*?)(?=^## |\Z)", body or "", re.S | re.M)
    return m.group(1) if m else ""
def blockers(body): return [int(x) for x in re.findall(r"(?<![\w/])#(\d+)", section(body))]
def has_freetext_blocker(body):
    sec = section(body); return bool(re.search(r"^\s*[-*]\s+(?!none\b)\S", sec, re.M | re.I)) and not blockers(body)
for n, it in sorted(items.items()):
    cur = it.get("status"); st = js("api", f"repos/{REPO}/issues/{n}")
    if not st: continue
    deps = blockers(st.get("body"))
    if st["state"] == "closed" and cur != "Done": move(n, "Done", "issue closed")
    elif st["state"] == "open" and n in refs and cur in ("Todo", "In Progress", "Blocked", None): move(n, "Code Complete", f"open PR #{refs[n]}")
    elif st["state"] == "open" and deps:
        open_deps = [d for d in deps if is_open(d)]
        if open_deps and cur in ("Todo", None): move(n, "Blocked", "blocked by open " + ", ".join(f"#{d}" for d in open_deps))
        elif not open_deps and cur == "Blocked": move(n, "Todo", "all blockers closed")
    elif st["state"] == "open" and cur in ("Todo", None) and has_freetext_blocker(st.get("body")): print(f"BOARD NOTE #{n}: 'Blocked by' names no #N, fix the issue body")
for i in js("api", f"repos/{REPO}/issues?state=open&labels=bug&per_page=100") or []:
    if "pull_request" in i or i["number"] < MIN or i["number"] in items: continue
    to = "Code Complete" if i["number"] in refs else "Todo"
    print(f"{'ADD' if APPLY else 'WOULD ADD'} #{i['number']} to board as {to} (new bug)")
    if APPLY:
        o = js("project", "item-add", str(BOARD), "--owner", OWNER, "--url", i["html_url"], "--format", "json")
        if o: gh("project", "item-edit", "--project-id", PROJECT, "--id", o["id"], "--field-id", FIELD, "--single-select-option-id", OPT[to])
