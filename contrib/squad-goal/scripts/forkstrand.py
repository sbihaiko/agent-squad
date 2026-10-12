#!/usr/bin/env python3
"""The loop reports a fork commit no landing path reads (#1837, parent #1831; watch.sh runs it every loop, after forkrescue.py).
A fork-only child's deliverable is a local commit on the branch the land reads -- sg_fork_branch (forkonly.sh, #1836), the one
name forkclose.sh merges. A commit left on any other branch of the fork is invisible to the land: forkclose.sh enumerates
refs/heads/<pattern>* and keeps the names that are a bare number, so fix/<N>-<slug> (#1823, #1821) is never enumerated at all
and gui/<N>-<slug> is enumerated and then dropped. forkrescue.py moves the case a live run's record explains; a branch whose
run is long gone has nothing that ever moves it, and the card sits at Code Complete looking finished while the land waits for
a ref no process produces. This pass says so instead of parking the card: one report per head, on the issue ($SG_STATE/cq,
posted by cqflush.sh) and in the loop's log, naming the branch, the commit and the ref the land reads. It never guesses: two
branches answering one issue number are refused, both named, neither moved. Nothing is moved, renamed, re-pointed or closed
here -- the report is the whole deliverable, and a pass over a head it already named says nothing new."""
import json, os, pathlib, re, subprocess, sys
st = pathlib.Path(os.environ["SG_STATE"]); A = st / "auto"; A.mkdir(parents=True, exist_ok=True); (st / "cq").mkdir(exist_ok=True)   # exported by env.sh: one state directory per repository (#20)
fork, base = os.environ.get("SG_FORK_DIR", ""), os.environ.get("SG_FORK_BASE", "main")
if not fork or not pathlib.Path(fork).is_dir(): sys.exit(0)
here = pathlib.Path(__file__).resolve().parent; forkonly = os.environ.get("SG_FORKONLY", str(here / "forkonly.sh"))
def git(*a): return subprocess.run(["/usr/bin/git", "-C", fork, *a], capture_output=True, text=True)
def ask(fn, *a):   # forkonly.sh owns the branch name and the fork paths, so this pass reads them instead of repeating them (#1836)
    r = subprocess.run(["bash", "-c", f'. "{forkonly}"; {fn} "$@"', "_", *a], capture_output=True, text=True)
    return [l for l in r.stdout.split("\n") if l.strip()]
PAT = (ask("sg_fork_branch", "<N>") or ["gui/<N>"])[0]
PATHS = ask("sg_fork_paths") or ["contrib/squad-goal/", ".claude-plugin/", "CHANGELOG.md"]
def land(n): return PAT.replace("<N>", str(n))
def is_fork_path(p): return any(p == x or (x.endswith("/") and p.startswith(x)) for x in PATHS)
def ahead(b): return git("rev-list", "-1", f"{base}..{b}").stdout.strip() != ""
def fork_diff(b):   # every file b changed since it left the base is fork tooling, and there is at least one: the branch says it is fork work
    mb = git("merge-base", base, b).stdout.strip()
    files = [p for p in git("diff", "--name-only", mb, b).stdout.split("\n") if p] if mb else []
    return files if files and all(is_fork_path(p) for p in files) else []
def issue_of(b): return int(m.group(1)) if (m := re.search(r"(\d+)", b)) else None
def ready():   # the loop's own card source (hub.py's board cache): what is parked at the landing stage. No gh call, no budget.
    try: items = json.loads(pathlib.Path(os.environ.get("SG_HUB_DIR", os.path.join(os.environ.get("SG_CHECKOUT", ""), "runs/.hub")) + "/hub-board.json").read_text()).get("items") or []
    except Exception: return {}
    return {int(i["n"]): i.get("st") for i in items if i.get("s") == "open" and str(i.get("n", "")).isdigit() and i.get("st") in ("Code Complete", "Testing")}
def say(n, key, text):
    (st / "cq" / f"{n}-forkstrand-{key}.md").write_text(f"FORKSTRAND #{n}: {text}\n")   # cqflush.sh posts it on the issue
    print(f"FORKSTRAND #{n}: {text}")                                                   # and the loop's log carries it too
# A strand is a branch no landing path reads: ahead of the base, fork work by its own diff, and not the ref the land merges.
# The card is the other half: a strand for an issue parked at the landing stage is one nothing releases.
cards, strands = ready(), {}
for b in git("for-each-ref", "--format=%(refname:short)", "refs/heads/").stdout.split():
    n = issue_of(b)
    if n and n in cards and b != land(n) and ahead(b) and fork_diff(b): strands.setdefault(n, []).append(b)
for n in sorted(strands):
    brs = sorted(strands[n])
    head = {b: git("rev-parse", "--short=9", b).stdout.strip() for b in brs}
    key = "-".join(sorted(head.values()))
    mk = A / f"strand-{n}-{key}"
    if mk.exists(): continue   # once per head: the same stranded commit is never named twice
    if len(brs) == 1:
        say(n, key, f"the commit {head[brs[0]]} sits on {brs[0]}, a branch no landing path reads; the land reads refs/heads/{land(n)} (forkclose.sh). "
                    f"The card is at {cards[n]} and no process in the loop releases it, so nothing moves until the work is on refs/heads/{land(n)}.")
    else:
        say(n, key, "two branches answer #%d -- %s -- and neither is a ref the land reads (refs/heads/%s); the loop refuses to pick one. "
                    "Neither branch was moved: put the issue's work on refs/heads/%s and the land takes it from there."
                    % (n, ", ".join(f"{b} ({head[b]})" for b in brs), land(n), land(n)))
    mk.touch()
