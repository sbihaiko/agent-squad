#!/usr/bin/env python3
"""Safety net for fork-only runs (#1411; called by watch.sh every loop).
A fork-only issue's deliverable is a local commit on gui/<N> in $SG_FORK_DIR. A child that ends needs-human or fail with its
commit on another branch (fix/<N>-x: the MesenAI habit) is not asking anything the owner has to answer: the commit is placed on
gui/<N> (fast-forward only, never a push), the card goes to Code Complete and a note is queued on the issue.
Once per child (marker auto/rescue-<run>-<child>). The fork-only rule is forkonly.sh (label, or the "Fork-only work" body line)."""
import json, os, pathlib, re, subprocess, sys
st = pathlib.Path(os.environ.get("SG_STATE", pathlib.Path.home() / ".cache/squad-goal")); A = st / "auto"; A.mkdir(parents=True, exist_ok=True); (st / "cq").mkdir(exist_ok=True)
repo, owner, proj = os.environ["SG_REPO"], os.environ["SG_OWNER"], os.environ["SG_PROJECT"]
fork, base, pat = os.environ.get("SG_FORK_DIR", ""), os.environ.get("SG_FORK_BASE", "local/next"), os.environ.get("SG_FORK_BRANCH", "gui/%n")
here = pathlib.Path(__file__).resolve().parent
forkonly = os.environ.get("SG_FORKONLY", str(here / "forkonly.sh")); board = os.environ.get("SG_BOARD", str(here / "board.sh"))
if not fork or not pathlib.Path(fork).is_dir(): sys.exit(0)
def git(*a): return subprocess.run(["/usr/bin/git", "-C", fork, *a], capture_output=True, text=True)
def ahead(b): return git("rev-list", "-1", f"{base}..{b}").stdout.strip() != ""
def is_fork_only(n): return subprocess.run(["bash", forkonly, str(n)], capture_output=True).returncode == 0
def worktree_of(b):
    w = None
    for l in git("worktree", "list", "--porcelain").stdout.splitlines():
        if l.startswith("worktree "): w = l[9:]
        elif l == f"branch refs/heads/{b}": return w
    return None
def place(n, src):   # gui/<N> := src; True when gui/<N> now holds the commit
    dst = pat.replace("%n", str(n))
    if git("rev-parse", "-q", "--verify", dst).returncode != 0: return git("branch", dst, src).returncode == 0
    if git("merge-base", "--is-ancestor", src, dst).returncode == 0: return True   # already there
    if git("merge-base", "--is-ancestor", dst, src).returncode != 0: return False   # diverged: not ours to rewrite
    w = worktree_of(dst)
    return (subprocess.run(["/usr/bin/git", "-C", w, "merge", "--ff-only", "-q", src], capture_output=True).returncode == 0) if w else git("branch", "-f", dst, src).returncode == 0
for r in (st / "active-runs.txt").read_text().split():
    rec = pathlib.Path(r if r.startswith("/") else "runs/" + r) / "record.jsonl"
    if not rec.exists(): continue
    issue, bad = {}, []
    for l in rec.open():
        if '"spawn_accepted"' not in l and '"outcome"' not in l: continue
        try: d = json.loads(l)
        except Exception: continue
        if d.get("event") == "spawn_accepted":
            df = json.dumps(d.get("definition")); m = re.search(r"GitHub issue #(\d+)", df) or re.search(r"\bissue #(\d+)", df)
            if m: issue[d["child"]] = int(m.group(1))
        elif d.get("event") == "outcome" and d.get("outcome") in ("needs-human", "fail", "failed") and d.get("node") in issue:
            bad.append((d["node"], (d.get("reasons") or [""])[0]))
    for child, why in bad:
        mk = A / f"rescue-{pathlib.Path(r).name}-{child}"
        if mk.exists(): continue
        n = issue[child]
        if not is_fork_only(n): continue
        dst = pat.replace("%n", str(n))
        stray = [b for b in git("for-each-ref", "--format=%(refname:short)", f"refs/heads/*/{n}-*", f"refs/heads/{n}-*").stdout.split() if b != dst and ahead(b)]
        if not stray: continue
        src = stray[0]; head = git("rev-parse", "--short=9", src).stdout.strip()
        if not place(n, src): print(f"FORKRESCUE #{n}: {dst} has diverged from {src}; left for the coordinator"); continue
        mk.touch()
        subprocess.run([board, owner, proj, "Code Complete", str(n)], capture_output=True)
        (st / "cq" / f"{n}-forkrescue-{mk.name}.md").write_text(f"Fork-only run {pathlib.Path(r).name} ({child}) ended without a clean pass ({why[:300]}) but left commit {head} on {src}. Fork-only work has no PR to place: the commit is now on {dst} in the fork (fast-forward, nothing pushed) and the card moved to Code Complete; forkclose.sh merges it and runs the fork suite. No owner decision needed.\n")
        print(f"FORKRESCUE #{n} {src} ({head}) -> {dst}, Code Complete")
