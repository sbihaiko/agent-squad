#!/usr/bin/env python3
"""Board reconciler: derive each tracked issue's Status from GitHub facts and move the card.
Facts only (no comment-text inference): issue closed -> Done; an open PR naming the issue as "Refs #N" -> Code Complete (Testing = e2e on the merge, set by the loop)
(only from Todo/In Progress/Blocked); an open issue whose `## Blocked by` lists an open #M -> Blocked (only from Todo), back to Todo when all are closed; an open `bug` issue absent from the board -> added as Todo.
Never downgrades (Done stays Done, Code Complete/Testing never go back). Dry run by default: RECONCILE=apply writes.
Scope: issues already on board 4 (open ones only from number >= RECONCILE_MIN, default 1102; a closed issue of any number is derived to Done) plus new `bug` issues."""
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
_here = os.path.dirname(os.path.abspath(__file__)); MOVE = os.path.join(_here, "board.sh")
def _status_field():
    o = js("api", f"/users/{OWNER}/projectsV2/{BOARD}/fields?per_page=100") or js("api", f"/orgs/{OWNER}/projectsV2/{BOARD}/fields?per_page=100") or []
    return next((str(x["id"]) for x in o if x.get("name") == "Status"), "")
_kind = "users"; _sf = _status_field()
_raw = gh("api", "--paginate", f"/{_kind}/{OWNER}/projectsV2/{BOARD}/items?per_page=100&fields[]={_sf}", "--jq",
          '.[]|select(.content_type=="Issue")|{n:.content.number,id:.id,status:((.fields[]?|select(.name=="Status")|.value.name.raw)//null)}')   # REST: no GraphQL quota
if _raw is None: sys.exit("board unreadable")
items = {i["n"]: i for i in (json.loads(l) for l in _raw.splitlines() if l.strip()) if i["n"]}   # the MIN floor applies to open issues, checked in the loop below
prs = js("api", f"repos/{REPO}/pulls?state=open&per_page=100") or []
refs = {}
for p in prs:
    for n in re.findall(r"[Rr]efs #(\d+)", (p.get("title") or "") + "\n" + (p.get("body") or "")): refs.setdefault(int(n), p["number"])
def move(n, to, why):
    cur = items[n].get("status")
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
for n, it in sorted(items.items()):
    cur = it.get("status"); st = js("api", f"repos/{REPO}/issues/{n}")
    if not st: continue
    if st["state"] != "closed" and n < MIN: continue
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
    if APPLY: subprocess.run([MOVE, "--add", OWNER, str(BOARD), to, str(i["number"])], capture_output=True)
