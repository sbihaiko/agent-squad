#!/usr/bin/env python3
"""Safety net for fork runs (#1411, #1831; called by watch.sh every loop).
A fork issue's deliverable is a local commit on gui/<N> in $SG_FORK_DIR. A child that ends with its commit on another branch
(fix/<N>-x: the MesenAI habit) is not asking anything the owner has to answer: the commit is placed on gui/<N> (fast-forward
only, never a push), the card goes to Code Complete and a note is queued on the issue. forkclose.sh only ever enumerates
refs/heads/gui/*, so a commit left elsewhere is stranded until someone re-points it by hand -- noticing that is not the
coordinator's job (#1823, #1821).
Whatever the outcome (pass too: a clean pass is not rescued by anything else), and whether or not the issue carries the
fork-only brand: the branch decides. A stray branch whose commits touch nothing but fork paths is fork work, and a
fork-only-branded issue's stray branch is fork work by the issue's own word. The card is the other half: only a card at the
landing stage (Code Complete/Testing, open) is rescued, so a branch a run is still working on (In Progress) is never taken
from under it. Once per child (marker auto/rescue-<run>-<child>)."""
import json, os, pathlib, re, subprocess, sys
st = pathlib.Path(os.environ.get("SG_STATE") or sys.exit("forkrescue.py: SG_STATE is not set; run it under env.sh, which names the repository's own directory")); A = st / "auto"; A.mkdir(parents=True, exist_ok=True); (st / "cq").mkdir(exist_ok=True)
repo, owner, proj = os.environ["SG_REPO"], os.environ["SG_OWNER"], os.environ["SG_PROJECT"]
CQ = os.environ["SG_REPO"].replace("/", "__")   # a queued comment is named <owner>__<repo>#<n>-<tag>.md: cqflush.sh posts it to the repository it names
fork, base, pat = os.environ.get("SG_FORK_DIR", ""), os.environ.get("SG_FORK_BASE", "main"), os.environ.get("SG_FORK_BRANCH", "gui/%n")
here = pathlib.Path(__file__).resolve().parent
forkonly = os.environ.get("SG_FORKONLY", str(here / "forkonly.sh")); board = os.environ.get("SG_BOARD", str(here / "board.sh"))
if not fork or not pathlib.Path(fork).is_dir(): sys.exit(0)
def git(*a): return subprocess.run(["/usr/bin/git", "-C", fork, *a], capture_output=True, text=True)
def ahead(b): return git("rev-list", "-1", f"{base}..{b}").stdout.strip() != ""
def is_fork_only(n): return subprocess.run(["bash", forkonly, str(n)], capture_output=True).returncode == 0
def fork_paths():   # forkonly.sh owns the set (sg_fork_paths, #1837); the literal here is the fallback when it cannot be asked
    r = subprocess.run(["bash", "-c", f'. "{forkonly}"; sg_fork_paths'], capture_output=True, text=True)
    return [p for p in r.stdout.split("\n") if p.strip()] or ["contrib/squad-goal/", ".claude-plugin/", "CHANGELOG.md"]
FORK_PATHS = fork_paths()   # a trailing / is a directory prefix; anything else is that exact root file (CHANGELOG.md)
def fork_paths_only(b):   # every file b changed since it left the base is fork tooling, and there is at least one: the branch itself says it is fork work
    mb = git("merge-base", base, b).stdout.strip()
    files = [p for p in git("diff", "--name-only", mb, b).stdout.split("\n") if p] if mb else []
    return bool(files) and all(any(p == x or (x.endswith("/") and p.startswith(x)) for x in FORK_PATHS) for p in files)
def ready_cards():   # the loop's own card source (hub.py's board cache): what is parked at the landing stage. No gh call, no budget.
    try: items = json.loads(pathlib.Path(os.environ.get("SG_BOARD_CACHE") or os.environ.get("SG_HUB_DIR", os.path.join(os.environ.get("SG_CHECKOUT", ""), "runs/.hub")) + "/hub-board.json").read_text()).get("items") or []
    except Exception: return set()
    return {int(i["n"]) for i in items if i.get("s") == "open" and i.get("st") in ("Code Complete", "Testing") and str(i.get("n", "")).isdigit()}
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
    issue, ended = {}, []
    for l in rec.open():
        if '"spawn_accepted"' not in l and '"outcome"' not in l: continue
        try: d = json.loads(l)
        except Exception: continue
        if d.get("event") == "spawn_accepted":
            df = json.dumps(d.get("definition")); m = re.search(r"GitHub issue #(\d+)", df) or re.search(r"\bissue #(\d+)", df)
            if m: issue[d["child"]] = int(m.group(1))
        elif d.get("event") == "outcome" and d.get("node") in issue:   # every outcome: a clean pass strands its commit just as needs-human does (#1821)
            ended.append((d["node"], d.get("outcome") or "", (d.get("reasons") or [""])[0]))
    ready = ready_cards()
    for child, outcome, why in ended:
        mk = A / f"rescue-{pathlib.Path(r).name}-{child}"
        if mk.exists(): continue
        n = issue[child]
        dst = pat.replace("%n", str(n))
        stray = [b for b in git("for-each-ref", "--format=%(refname:short)", f"refs/heads/*/{n}-*", f"refs/heads/{n}-*").stdout.split() if b != dst and ahead(b)]
        if not stray: continue
        if len(stray) > 1:   # two branches answering one issue number: which is the work is a guess, and a wrong merge is not reversible by name (#1837)
            print(f"FORKRESCUE #{n}: {len(stray)} branches answer #{n} ({', '.join(sorted(stray))}) and none of them is the ref the land reads ({dst}); the pass refuses to pick one")
            continue
        src = stray[0]; forkwork = fork_paths_only(src)
        if not forkwork and not is_fork_only(n): continue   # not fork work by its paths and not branded: not ours to move
        if n not in ready and not (forkwork and outcome != "pass"): continue   # a card still In Progress belongs to a live run; a child that ended without a pass has no other process that would ever re-point it
        head = git("rev-parse", "--short=9", src).stdout.strip()
        if not place(n, src): print(f"FORKRESCUE #{n}: {dst} has diverged from {src}; left for the coordinator"); continue
        mk.touch()
        subprocess.run([board, owner, proj, "Code Complete", str(n)], capture_output=True)
        (st / "cq" / f"{CQ}#{n}-forkrescue-{mk.name}.md").write_text(f"Fork run {pathlib.Path(r).name} ({child}) ended {outcome or 'without an outcome'} ({why[:300]}) with commit {head} on {src}, off the {dst} branch forkclose.sh lands. The commit is now on {dst} in the fork (fast-forward, nothing pushed) and the card moved to Code Complete; forkclose.sh merges it and runs the fork suite. No owner decision needed.\n")
        print(f"FORKRESCUE #{n} {src} ({head}) -> {dst}, Code Complete")
