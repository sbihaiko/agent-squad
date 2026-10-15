#!/usr/bin/env python3
"""A child that finished with outcome pass moves its issue to Code Complete (called by watch.sh every loop).
A child of a run that cut a wave enters it first (#375, ADR-0145 rules 9 and 12): `wave.sh enter <run> <issue>` runs in the
child's own worktree, is the only route into the wave branch, and its entry is what moves the card; a child that did not
enter has no card to move, its issue went back to the queue. A run that cut no wave keeps the direct move.
The child's issue is read from its own spawn prompt, first match wins: "GitHub issue #N", "issue #N",
"GitHub issues N", "GitHub issue N", then a "gui/N" or "wt-as-N" branch/worktree name.
A pass whose prompt names no issue is logged once (CHILDCC UNMAPPED <run> <child>) instead of being skipped silently.
The runs the pass works are its arguments, else every live run: watch.sh's completion of a finished run hands it that one run, so the children that run finished are in its wave before that same step lands the wave (#375 review).
Live runs move at once; a run that already has its verdict (tiny and fork-only runs go pass -> verdict in under 1 s, so they are never seen live) is backfilled once,
but only when the card is still in Todo/In Progress/Blocked, never past Code Complete; a run with a wave not yet landed is not backfilled by card but by its wave marker, so a child that has not entered still enters. The once-marker is written when the child entered or needs no entry, never after a refused entry: the next pass tries it again. A run with more than one child also gets one parent issue naming the run, each child linked as a sub-issue through the seam (marker auto/parent-<run>). Once per child (marker auto/cc-<run>-<child>); only an open issue moves; queues a comment with the child's first reason.
Testing starts later (merge + e2e, or a test.sh pass), so Code Complete never overrides a later column: an issue already
past it is left alone because the marker is written the first time the child is seen passing."""
import json, os, pathlib, re, subprocess, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # the seam module sits beside this script; a run under -I has the script's directory off sys.path
from issue_state import board_state, RunParentChild
st = pathlib.Path(os.environ["SG_STATE"]); A = st / "auto"; A.mkdir(parents=True, exist_ok=True)
repo, owner, proj = os.environ["SG_REPO"], os.environ["SG_OWNER"], os.environ["SG_PROJECT"]
CQ = os.environ["SG_REPO"].replace("/", "__")   # a queued comment is named <owner>__<repo>#<n>-<tag>.md: cqflush.sh posts it to the repository it names
board = str(pathlib.Path(__file__).resolve().parent / "board.sh")
wave = str(pathlib.Path(__file__).resolve().parent / "wave.sh")   # the wave's verbs, beside this pass (#375)
state = board_state()   # the dispatch pass asks the seam what state an issue carries, never the carrier (#236)
def child_worktree(rd, child, d):   # the tree a child worked in, by the engine's own rule (squad/runners/children.py, Children.workspace)
    ws = d.get("workspace") if isinstance(d, dict) else None
    logical = "target" if not ws or ws == "." else str(ws).replace("/", "_")
    return rd / "ws" / f"{logical}-{child}"
def head_of(wt):   # the sha the child's worktree stands on, "" when it has none
    return subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
def enter(rd, child, n, d):
    """The wave's entry of a finished child, in the tree the engine made for it. Return (entered, why): (None, "") when the
    run cut no wave and the direct move stands, (True, the entry line) when the child is now in the wave branch, and
    (False, why) when it is in none -- a child that did not enter has no delivery to claim. A child a refused entry requeued
    is not tried again on the same HEAD (marker auto/requeued-<run>-<child>): the gates and the requeue wait for a new commit."""
    if not (A / f"waved-{rd.name}").exists() and not (A / f"wave-{rd.name}").exists(): return None, ""   # `waved-` is the durable record: a landing removes `wave-`, never this
    wt = child_worktree(rd, child, d)
    if not wt.is_dir(): return False, f"WAVE no entry for #{n}: the child's worktree {wt} is not there"
    rq = A / f"requeued-{rd.name}-{child}"   # the sha a refused entry left the child on: the same sha would fail the same gates again, so the pass waits for a new commit
    if rq.exists() and rq.read_text().strip() == head_of(wt): return False, ""
    e = subprocess.run(["bash", wave, "enter", rd.name, str(n)], cwd=str(wt), capture_output=True, text=True)
    out = (e.stdout + e.stderr).strip()
    if "WAVE REQUEUE" in out: rq.write_text(head_of(wt) + "\n")   # a hold (no review yet) is no requeue: it is tried again on the same sha
    return e.returncode == 0, out or f"WAVE no entry for #{n} (exit {e.returncode})"
def card_status(n):   # the state the card carries, "" when it carries none or the carrier does not answer
    try: return state.read(n)
    except RuntimeError: return ""   # a refusal or a deadline miss is the carrier not answering, not a card that carries nothing
def issue_from(df):
    m = (re.search(r"GitHub issue #(\d+)", df) or re.search(r"\bissue #(\d+)", df)
         or re.search(r"GitHub issues (\d+)", df) or re.search(r"GitHub issue (\d+)", df)
         or re.search(r"(?:gui/|wt-as-)(\d+)", df))
    return int(m.group(1)) if m else None
for r in (sys.argv[1:] or (st / "active-runs.txt").read_text().split()):
    rec = pathlib.Path(r if r.startswith("/") else "runs/" + r) / "record.jsonl"
    if not rec.exists(): continue
    finished = '"event": "verdict"' in rec.read_text()
    issue, seen, done, unmapped, defn = {}, set(), [], [], {}
    for l in rec.open():
        if '"spawn_accepted"' not in l and '"outcome"' not in l: continue
        try: d = json.loads(l)
        except Exception: continue
        if d.get("event") == "spawn_accepted":
            seen.add(d["child"]); defn[d["child"]] = d.get("definition"); n = issue_from(json.dumps(d.get("definition")))
            if n is not None: issue[d["child"]] = n
        elif d.get("event") == "outcome" and d.get("outcome") == "pass" and d.get("node") in issue:
            done.append((d["node"], (d.get("reasons") or [""])[0]))
        elif d.get("event") == "outcome" and d.get("outcome") == "pass" and d.get("node") in seen:
            unmapped.append(d["node"])
    mk = A / f"parent-{pathlib.Path(r).name}"
    if not finished and not mk.exists() and len(set(issue.values())) > 1:   # a finished run gets no parent: no backfill (#340)   # a run that dispatched more than one child: one parent names the run, the seam writes and proves the links (#304)
        try: print(f"RUN PARENT #{state.group_run(pathlib.Path(r).name, issue.values())} ({pathlib.Path(r).name})"); mk.touch()
        except RunParentChild as e: print(f"RUN PARENT REFUSED {pathlib.Path(r).name}: {e}"); mk.touch()   # asking again can not change it
        except RuntimeError as e: print(f"RUN PARENT FAILED {pathlib.Path(r).name}: {e}")   # no marker: the next pass asks again
    for child, why in done:
        mk = A / f"cc-{pathlib.Path(r).name}-{child}"
        if mk.exists(): continue
        n = issue[child]
        s = subprocess.run(["gh", "api", f"repos/{repo}/issues/{n}", "-q", ".state"], capture_output=True, text=True).stdout.strip()
        if s != "open": mk.touch(); continue
        waved = (A / f"wave-{pathlib.Path(r).name}").exists()   # the run's wave is not landed yet: its marker, not the card, says the child still has an entry to make
        if (A / f"waved-{pathlib.Path(r).name}").exists() and not waved: mk.touch(); continue   # the wave is landed and this child is not in it: its issue went back to the queue, so no card moves (never the direct Code Complete move)
        if finished and not waved and card_status(n) not in ("Todo", "In Progress", "Blocked"): mk.touch(); continue   # backfill never moves a card past (or out of) its column
        entered, why = enter(pathlib.Path(r), child, n, defn.get(child))
        if why: print(why, flush=True)
        if entered is False: continue   # the child never entered the wave: no marker, so the next pass tries it again; no card moves, its issue went back to the queue
        mk.touch()
        if entered is None: subprocess.run([board, owner, proj, "Code Complete", str(n)], capture_output=True)   # no wave: the pass moves the card itself
        (st / "cq" / f"{CQ}#{n}-cc-{mk.name}.md").write_text(f"Child {child} of squad run {pathlib.Path(r).name} finished: pass. Card -> Code Complete (Testing starts with the merge/e2e or the fork suite). {why[:600]}\n")
        print(f"CODE COMPLETE #{n} ({pathlib.Path(r).name} {child})")
    for child in unmapped:
        mk = A / f"unmap-{pathlib.Path(r).name}-{child}"
        if mk.exists(): continue
        mk.touch(); print(f"CHILDCC UNMAPPED {pathlib.Path(r).name} {child}")
