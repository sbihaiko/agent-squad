#!/usr/bin/env python3
"""A child that finished with outcome pass moves its issue to Code Complete (called by watch.sh every loop).
The child's issue is read from its own spawn prompt, first match wins: "GitHub issue #N", "issue #N",
"GitHub issues N", "GitHub issue N", then a "gui/N" or "wt-as-N" branch/worktree name.
A pass whose prompt names no issue is logged once (CHILDCC UNMAPPED <run> <child>) instead of being skipped silently.
Live runs move at once; a run that already has its verdict (tiny and fork-only runs go pass -> verdict in under 1 s, so they are never seen live) is backfilled once,
but only when the card is still in Todo/In Progress/Blocked, never past Code Complete. Once per child (marker auto/cc-<run>-<child>); only an open issue moves; queues a comment with the child's first reason.
Testing starts later (merge + e2e, or a test.sh pass), so Code Complete never overrides a later column: an issue already
past it is left alone because the marker is written the first time the child is seen passing."""
import json, os, pathlib, re, subprocess, sys
st = pathlib.Path(os.environ.get("SG_STATE", pathlib.Path.home() / ".cache/squad-goal")); A = st / "auto"; A.mkdir(parents=True, exist_ok=True)
repo, owner, proj = os.environ["SG_REPO"], os.environ["SG_OWNER"], os.environ["SG_PROJECT"]
board = str(pathlib.Path(__file__).resolve().parent / "board.sh")
def card_status(n):   # REST, no GraphQL quota; "" when unreadable
    g = lambda *a: subprocess.run(["gh", *a], capture_output=True, text=True).stdout.strip()
    f = g("api", f"/users/{owner}/projectsV2/{proj}/fields?per_page=100", "--jq", '.[]|select(.name=="Status")|.id')
    return g("api", "--paginate", f"/users/{owner}/projectsV2/{proj}/items?per_page=100&fields[]={f}", "--jq",
             f'.[]|select(.content_type=="Issue" and .content.number=={n})|((.fields[]?|select(.name=="Status")|.value.name.raw)//"")')
def issue_from(df):
    m = (re.search(r"GitHub issue #(\d+)", df) or re.search(r"\bissue #(\d+)", df)
         or re.search(r"GitHub issues (\d+)", df) or re.search(r"GitHub issue (\d+)", df)
         or re.search(r"(?:gui/|wt-as-)(\d+)", df))
    return int(m.group(1)) if m else None
for r in (st / "active-runs.txt").read_text().split():
    rec = pathlib.Path(r if r.startswith("/") else "runs/" + r) / "record.jsonl"
    if not rec.exists(): continue
    finished = '"event": "verdict"' in rec.read_text()
    issue, seen, done, unmapped = {}, set(), [], []
    for l in rec.open():
        if '"spawn_accepted"' not in l and '"outcome"' not in l: continue
        try: d = json.loads(l)
        except Exception: continue
        if d.get("event") == "spawn_accepted":
            seen.add(d["child"]); n = issue_from(json.dumps(d.get("definition")))
            if n is not None: issue[d["child"]] = n
        elif d.get("event") == "outcome" and d.get("outcome") == "pass" and d.get("node") in issue:
            done.append((d["node"], (d.get("reasons") or [""])[0]))
        elif d.get("event") == "outcome" and d.get("outcome") == "pass" and d.get("node") in seen:
            unmapped.append(d["node"])
    for child, why in done:
        mk = A / f"cc-{pathlib.Path(r).name}-{child}"
        if mk.exists(): continue
        mk.touch(); n = issue[child]
        s = subprocess.run(["gh", "api", f"repos/{repo}/issues/{n}", "-q", ".state"], capture_output=True, text=True).stdout.strip()
        if s != "open": continue
        if finished and card_status(n) not in ("Todo", "In Progress", "Blocked"): continue   # backfill never moves a card past (or out of) its column
        subprocess.run([board, owner, proj, "Code Complete", str(n)], capture_output=True)
        (st / "cq" / f"{n}-cc-{mk.name}.md").write_text(f"Child {child} of squad run {pathlib.Path(r).name} finished: pass. Card -> Code Complete (Testing starts with the merge/e2e or the fork suite). {why[:600]}\n")
        print(f"CODE COMPLETE #{n} ({pathlib.Path(r).name} {child})")
    for child in unmapped:
        mk = A / f"unmap-{pathlib.Path(r).name}-{child}"
        if mk.exists(): continue
        mk.touch(); print(f"CHILDCC UNMAPPED {pathlib.Path(r).name} {child}")
